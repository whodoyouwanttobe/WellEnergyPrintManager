"""Состояние принтера/плоттера: замятие, бумага, чернила (по цветам), занят ли он.

Источники (от точного к грубому):
  1. SNMP самого принтера (core/snmp.py): ошибки, уровни чернил, лотки/рулон, тексты
     предупреждений принтера, «печатает/прогрев/простой».
  2. Очередь Windows: биты статуса принтера и заданий + текст драйвера у задания
     (часто именно там пишется «Замятие бумаги» / «Paper jam»).
  3. Трекер печати программы: задание отправлено и по расчёту ещё печатается — значит
     принтер занят, даже если Windows уже пишет «Готов».

Всё здесь — чистые функции без Windows, их проверяют тесты.
"""
import re

# ───────────── OID ─────────────
HR_DEVICE_STATUS = "1.3.6.1.2.1.25.3.2.1.5"          # 2 running, 3 warning, 4 testing, 5 down
HR_PRINTER_STATUS = "1.3.6.1.2.1.25.3.5.1.1"         # 1 other, 2 unknown, 3 idle, 4 printing, 5 warmup
HR_PRINTER_ERRORS = "1.3.6.1.2.1.25.3.5.1.2"         # битовая строка ошибок
SUP_CLASS = "1.3.6.1.2.1.43.11.1.1.4"                # 3 — ёмкость, которая заполняется (отработка)
SUP_TYPE = "1.3.6.1.2.1.43.11.1.1.5"
SUP_DESCR = "1.3.6.1.2.1.43.11.1.1.6"
SUP_MAX = "1.3.6.1.2.1.43.11.1.1.8"
SUP_LEVEL = "1.3.6.1.2.1.43.11.1.1.9"
COLORANT_VALUE = "1.3.6.1.2.1.43.12.1.1.4"           # название цвета (cyan, magenta…)
SUP_COLORANT_IDX = "1.3.6.1.2.1.43.11.1.1.3"
IN_MAX = "1.3.6.1.2.1.43.8.2.1.9"
IN_LEVEL = "1.3.6.1.2.1.43.8.2.1.10"
IN_NAME = "1.3.6.1.2.1.43.8.2.1.13"
IN_DESCR = "1.3.6.1.2.1.43.8.2.1.18"
ALERT_SEVERITY = "1.3.6.1.2.1.43.18.1.1.2"           # 3 critical, 4 warning, 5 warningBinaryChangeEvent
ALERT_CODE = "1.3.6.1.2.1.43.18.1.1.7"
ALERT_DESCR = "1.3.6.1.2.1.43.18.1.1.8"
CONSOLE_TEXT = "1.3.6.1.2.1.43.16.5.1.2"             # текст на дисплее принтера

# ───────────── Windows: PRINTER_STATUS_* и JOB_STATUS_* ─────────────
PS = {
    "paused": 0x1, "error": 0x2, "pending_deletion": 0x4, "paper_jam": 0x8, "paper_out": 0x10,
    "manual_feed": 0x20, "paper_problem": 0x40, "offline": 0x80, "io_active": 0x100, "busy": 0x200,
    "printing": 0x400, "output_bin_full": 0x800, "not_available": 0x1000, "waiting": 0x2000,
    "processing": 0x4000, "initializing": 0x8000, "warming_up": 0x10000, "toner_low": 0x20000,
    "no_toner": 0x40000, "page_punt": 0x80000, "user_intervention": 0x100000,
    "out_of_memory": 0x200000, "door_open": 0x400000, "server_unknown": 0x800000, "power_save": 0x1000000,
}
PRINTER_ATTRIBUTE_WORK_OFFLINE = 0x400
JS = {
    "paused": 0x1, "error": 0x2, "deleting": 0x4, "spooling": 0x8, "printing": 0x10, "offline": 0x20,
    "paperout": 0x40, "printed": 0x80, "deleted": 0x100, "blocked": 0x200, "user_intervention": 0x400,
    "restart": 0x800, "complete": 0x1000,
}

# ───────────── проблемы ─────────────
# key: (важность, цвет, текст). Важность: 3 — авария (печать стоит), 2 — внимание, 1 — инфо.
PROBLEMS = {
    "jam": (3, "🔴", "Замятие бумаги"),
    "door_open": (3, "🔴", "Открыта крышка/дверца"),
    "no_ink": (3, "🔴", "Нет чернил/тонера"),
    "no_paper": (3, "🟡", "Нет бумаги"),
    "offline": (3, "⚪", "Отключен (нет связи)"),
    "error": (3, "🔴", "Ошибка"),
    "intervention": (2, "🟡", "Требуется действие у принтера"),
    "output_full": (2, "🟡", "Лоток выдачи переполнен"),
    "paper_problem": (2, "🟡", "Проблема с бумагой"),
    "tray_missing": (2, "🟡", "Нет лотка"),
    "waste_full": (2, "🟡", "Картридж обслуживания заполнен"),
    "manual_feed": (2, "🟡", "Ручная подача: вставьте лист"),
    "paused": (2, "🟡", "Пауза"),
    "memory": (2, "🟡", "Не хватает памяти принтера"),
    "low_ink": (1, "🟠", "Мало чернил/тонера"),
    "low_paper": (1, "🟠", "Бумага заканчивается"),
    "service": (1, "🟠", "Требуется обслуживание"),
}

# Проблемы, при которых принтер стоит и не печатает (время печати на это время замораживаем)
STOPPING = {"jam", "door_open", "no_ink", "no_paper", "offline", "error", "intervention",
            "output_full", "paper_problem", "tray_missing", "manual_feed", "paused", "memory", "waste_full"}

COLOR_RU = [
    (r"maint|обслуж|waste|отработ", "Картридж обслуживания"),
    (r"print\s*head|печатающ", "Печатающая головка"),
    (r"drum|барабан", "Барабан"),
    (r"photo\s*black|фото.?ч[её]рн|\bpbk\b", "Фото-чёрные (PBK)"),
    (r"matte?\s*black|мат\w*\s*ч[её]рн|\bmbk\b", "Матовые чёрные (MBK)"),
    (r"light\s*cyan|светло.?голуб", "Светло-голубые"),
    (r"light\s*magenta|светло.?пурпур", "Светло-пурпурные"),
    (r"cyan|голуб|\bc\b", "Голубые (C)"),
    (r"magenta|пурпур|\bm\b", "Пурпурные (M)"),
    (r"yellow|ж[её]лт|\by\b", "Жёлтые (Y)"),
    (r"black|ч[её]рн|\bbk\b|\bk\b", "Чёрные (BK)"),
]


def _text(v):
    if isinstance(v, (bytes, bytearray)):
        for enc in ("utf-8", "cp1251", "latin-1"):
            try:
                return bytes(v).decode(enc).strip("\x00 ").strip()
            except UnicodeDecodeError:
                continue
    return str(v or "").strip()


def supply_name(descr, colorant=""):
    src = f"{_text(descr)} {_text(colorant)}".lower()
    for pat, ru in COLOR_RU:
        if re.search(pat, src):
            return ru
    return _text(descr) or "Расходник"


def decode_hr_errors(raw):
    """hrPrinterDetectedErrorState (RFC 2790/3805) → множество ключей проблем."""
    b = bytes(raw or b"") + b"\x00\x00"
    bits = {i for i in range(16) if b[i // 8] & (0x80 >> (i % 8))}
    out = set()
    m = {0: "low_paper", 1: "no_paper", 2: "low_ink", 3: "no_ink", 4: "door_open", 5: "jam",
         6: "offline", 7: "service", 8: "tray_missing", 9: "tray_missing", 10: "no_ink",
         11: "output_full", 12: "output_full", 13: "no_paper", 14: "service"}
    for bit in bits:
        out.add(m[bit])
    if "no_paper" in out:
        out.discard("low_paper")
    if "no_ink" in out:
        out.discard("low_ink")
    if "output_full" in out and 12 not in bits:    # только «почти полон»
        out.discard("output_full")
    return out


def decode_supplies(rows, colorants=None, low_pct=10):
    """Таблица prtMarkerSupplies → список {name, pct, state, waste}.
    Уровень: -3 «что-то есть», -2 неизвестно, -1 без ограничения."""
    colorants = colorants or {}
    out = []
    for idx, r in sorted(rows.items(), key=lambda kv: [int(x) for x in kv[0].split(".") if x.isdigit()]):
        descr = r.get(SUP_DESCR, b"")
        level, mx = r.get(SUP_LEVEL), r.get(SUP_MAX)
        cidx = r.get(SUP_COLORANT_IDX)
        colorant = colorants.get(f"1.{cidx}", "") if cidx else ""
        name = supply_name(descr, colorant)
        waste = r.get(SUP_CLASS) == 3 or name == "Картридж обслуживания"
        pct = None
        if isinstance(level, int) and isinstance(mx, int) and mx > 0 and level >= 0:
            pct = max(0, min(100, round(level * 100 / mx)))
        state = "ok"
        if waste:
            if pct is not None and pct >= 95:
                state = "full"
        elif level == 0 or pct == 0:
            state = "empty"
        elif pct is not None and pct <= low_pct:
            state = "low"
        out.append({"name": name, "pct": pct, "state": state, "waste": waste, "raw": _text(descr)})
    return out


def decode_inputs(rows):
    """Лотки/рулон → список {name, state}: empty / ok."""
    out = []
    for idx, r in sorted(rows.items()):
        name = _text(r.get(IN_DESCR) or r.get(IN_NAME) or f"Лоток {idx}")
        level = r.get(IN_LEVEL)
        state = "empty" if level == 0 else "ok"
        out.append({"name": name, "state": state, "level": level, "max": r.get(IN_MAX)})
    return out


def decode_alerts(rows):
    """Активные предупреждения принтера (его собственный текст, часто по-русски)."""
    out = []
    for idx, r in sorted(rows.items()):
        txt = _text(r.get(ALERT_DESCR))
        sev = r.get(ALERT_SEVERITY)
        if txt and txt.lower() not in ("ready", "готов", "sleep mode", "режим ожидания"):
            out.append({"text": txt, "critical": sev == 3, "code": r.get(ALERT_CODE)})
    return out


_DRIVER_TEXT = [
    (r"jam|замят|застр", "jam"),
    (r"paper\s*out|out of paper|нет бумаги|закончил\w* бумаг|load paper|загрузите|no paper|roll", "no_paper"),
    (r"toner|ink|чернил|тонер|картридж|cartridge", "no_ink"),
    (r"door|cover|крышк|дверц", "door_open"),
    (r"offline|автономн|отключ|not connected|нет связи", "offline"),
    (r"intervention|вмешательств|требуется", "intervention"),
    (r"error|ошибк", "error"),
]


def problems_from_text(text):
    t = (text or "").lower()
    for pat, key in _DRIVER_TEXT:
        if re.search(pat, t):
            return {key}
    return set()


def decode_spooler(status, attributes=0, jobs=()):
    """Статус очереди Windows → (проблемы, занят?, текст занятости, тексты драйвера)."""
    probs, texts = set(), []
    s = int(status or 0)
    if s & PS["paper_jam"]:
        probs.add("jam")
    if s & PS["paper_out"]:
        probs.add("no_paper")
    if s & PS["no_toner"]:
        probs.add("no_ink")
    if s & PS["toner_low"]:
        probs.add("low_ink")
    if s & PS["door_open"]:
        probs.add("door_open")
    if s & (PS["offline"] | PS["not_available"] | PS["server_unknown"]) or attributes & PRINTER_ATTRIBUTE_WORK_OFFLINE:
        probs.add("offline")
    if s & PS["user_intervention"]:
        probs.add("intervention")
    if s & PS["output_bin_full"]:
        probs.add("output_full")
    if s & PS["paper_problem"]:
        probs.add("paper_problem")
    if s & PS["manual_feed"]:
        probs.add("manual_feed")
    if s & PS["out_of_memory"]:
        probs.add("memory")
    if s & PS["paused"]:
        probs.add("paused")
    if s & PS["error"] and not probs - {"low_ink", "paused"}:
        probs.add("error")
    busy = None
    if s & PS["printing"]:
        busy = "Печатает"
    elif s & PS["warming_up"]:
        busy = "Прогрев"
    elif s & (PS["processing"] | PS["busy"] | PS["io_active"]):
        busy = "Обработка задания"
    elif s & PS["initializing"]:
        busy = "Запуск"
    n_jobs = 0
    for j in jobs or ():
        n_jobs += 1
        js = int(j.get("Status", 0) or 0)
        txt = _text(j.get("pStatus"))
        if txt:
            texts.append(txt)
            probs |= problems_from_text(txt)
        if js & JS["paperout"]:
            probs.add("no_paper")
        if js & JS["offline"]:
            probs.add("offline")
        if js & JS["user_intervention"]:
            probs.add("intervention")
        if js & (JS["error"] | JS["blocked"]) and not probs:
            probs.add("error")
        if js & JS["printing"]:
            busy = busy or "Печатает"
        elif js & JS["spooling"]:
            busy = busy or "Получает задание"
    if n_jobs and not busy:
        busy = f"В очереди: {n_jobs}"
    return probs, busy, texts


def combine(spooler=None, snmp=None, tracker=None, plotter=False):
    """Итоговое состояние для значка и уведомлений.

    spooler: {"status", "attributes", "jobs"} или None (не удалось открыть очередь)
    snmp:    {"errors": bytes, "printer_status": int, "device_status": int,
              "supplies": [...], "inputs": [...], "alerts": [...], "console": str} или None
    tracker: сводка PrintTracker.printer_summary() или None
    → {"color", "text", "problems": {key: подробность}, "busy", "details": [строки], "supplies"}
    """
    problems = {}
    details = []
    busy = None
    driver_texts = []

    if spooler is not None:
        p, busy, driver_texts = decode_spooler(spooler.get("status", 0), spooler.get("attributes", 0),
                                               spooler.get("jobs", ()))
        for k in p:
            problems[k] = ""
    supplies = []
    if snmp:
        for k in decode_hr_errors(snmp.get("errors")):
            problems.setdefault(k, "")
        supplies = snmp.get("supplies") or []
        empty = [s["name"] for s in supplies if s["state"] == "empty"]
        low = [f'{s["name"]} {s["pct"]}%' for s in supplies if s["state"] == "low"]
        full = [s["name"] for s in supplies if s["state"] == "full"]
        if empty:
            problems["no_ink"] = ", ".join(empty)
        elif "no_ink" in problems and not problems["no_ink"]:
            problems["no_ink"] = ""
        if low:
            problems["low_ink"] = ", ".join(low)
        if full:
            problems["waste_full"] = ", ".join(full)
        trays = [t["name"] for t in snmp.get("inputs") or [] if t["state"] == "empty"]
        if trays and "no_paper" in problems:
            problems["no_paper"] = ", ".join(trays)
        alerts = snmp.get("alerts") or []
        for a in alerts:
            for k in problems_from_text(a["text"]):
                if k == "error" and problems:
                    continue
                if k == "no_ink" and supplies:
                    continue          # по чернилам точнее таблица расходников
                problems.setdefault(k, a["text"])
        ps = snmp.get("printer_status")
        if ps == 4:
            busy = "Печатает"
        elif ps == 5:
            busy = "Прогрев"
        elif ps == 3 and busy and busy.startswith("Печатает") and not (spooler or {}).get("jobs"):
            busy = None
        if snmp.get("device_status") == 5 and not problems:
            problems["error"] = ""
        if snmp.get("console"):
            details.append(f"Дисплей: {snmp['console']}")
        for a in alerts:
            details.append(("⚠ " if a["critical"] else "• ") + a["text"])
    if "low_ink" in problems and "no_ink" in problems and not problems["low_ink"]:
        problems.pop("low_ink")
    if problems.get("error") == "" and driver_texts:
        problems["error"] = driver_texts[0]
    for t in driver_texts:                      # текст драйвера точнее: «Загрузите бумагу в лоток 2»
        for k in problems_from_text(t):
            if k in problems and not problems[k]:
                problems[k] = t

    device_busy = busy        # что говорит сам принтер/Windows (без расчёта программы)
    # Трекер программы: задание ещё печатается по расчёту — принтер занят
    if tracker and not tracker.get("idle"):
        cur = tracker.get("current") or {}
        left = tracker.get("seconds_left") or 0
        mins = max(1, round(left / 60))
        t = f"Печатает {cur.get('fmt', '')}".strip() + f" · ≈{mins} мин"
        if not busy or busy.startswith("В очереди") or busy == "Печатает":
            busy = t

    if plotter and "no_paper" in problems and not problems["no_paper"]:
        problems["no_paper"] = "рулон"

    # Итог — по самой важной проблеме
    if problems:
        key = max(problems, key=lambda k: (PROBLEMS[k][0], k == "jam", k == "no_ink"))
        sev, color, title = PROBLEMS[key]
        extra = problems[key]
        text = title if not extra else f"{title}: {extra}"
        if key == "no_ink" and extra:
            text = f"Нет чернил: {extra}"
        if sev <= 1 and busy:            # «мало чернил» не прячет «печатает»
            color, text = "🔵", f"{busy} · {text}"
    elif busy:
        color, text = "🔵", busy
    elif snmp and snmp.get("printer_status") == 3 or spooler is not None or snmp:
        color, text = "🟢", "Готов"
    else:
        color, text = "⚪", "Нет связи с очередью"

    head = [f"🔵 {busy}"] if busy else []
    head += [f"{PROBLEMS[k][1]} {PROBLEMS[k][2]}" + (f": {v}" if v else "")
             for k, v in sorted(problems.items(), key=lambda kv: -PROBLEMS[kv[0]][0])]
    details = head + details
    if supplies:
        details.append("Расходники:")
        for s in supplies:
            mark = {"empty": "✗", "low": "◐", "full": "✗"}.get(s["state"], "✓")
            pct = f"{s['pct']}%" if s["pct"] is not None else ("есть" if s["state"] == "ok" else "—")
            if s["waste"]:
                pct = f"заполнен на {s['pct']}%" if s["pct"] is not None else pct
            details.append(f"  {mark} {s['name']}: {pct}")
    for t in driver_texts:
        details.append(f"Драйвер: {t}")
    stopped = any(k in STOPPING for k in problems)
    return {"color": color, "text": text, "problems": problems, "busy": busy,
            "device_busy": device_busy, "stopped": stopped,
            "details": details, "supplies": supplies}


def new_problems(old, new, min_severity=1):
    """Какие проблемы появились (для всплывающих уведомлений). → [(key, подробность)]."""
    old = old or {}
    out = []
    for k, v in (new or {}).items():
        if PROBLEMS[k][0] < min_severity:
            continue
        if k not in old or (v and v != old.get(k) and k in ("no_ink", "low_ink", "no_paper")):
            out.append((k, v))
    out.sort(key=lambda kv: -PROBLEMS[kv[0]][0])
    return out


def resolved_problems(old, new, min_severity=2):
    return [k for k in (old or {}) if k not in (new or {}) and PROBLEMS[k][0] >= min_severity]
