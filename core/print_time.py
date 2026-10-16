"""Ориентировочное время печати плана.

Считается по количеству листов, их формату (площади), числу копий и скорости
конкретной модели. Скорости — паспортные (см. ссылки ниже) плюс накладные расходы
на запуск задания; их можно поправить в Настройках → «Скорость», если на практике
получается быстрее или медленнее.

  HP LaserJet Pro MFP M435  — A4 до 31 стр/мин, A3 ~15 стр/мин, первая страница ~9 с
  HP LaserJet MFP M436      — A4 до 23 стр/мин, A3 12 стр/мин, первая страница ~8,8 с
  Canon imagePROGRAF TM-305 — A0 CAD на обычной бумаге: Fast 0:46, Standard 1:14
"""
import copy
import math
import re

A0_AREA_M2 = 0.841 * 1.189

DEFAULT_PROFILES = {
    "laser": {
        "M435": {"title": "HP LaserJet Pro MFP M435", "A4_ppm": 31, "A3_ppm": 15, "first_page_s": 9},
        "M436": {"title": "HP LaserJet MFP M436", "A4_ppm": 23, "A3_ppm": 12, "first_page_s": 9},
        "default": {"title": "Другой лазерный принтер", "A4_ppm": 20, "A3_ppm": 10, "first_page_s": 10},
    },
    "plotter": {
        "TM-305": {"title": "Canon imagePROGRAF TM-305", "sec_per_a0": 74, "sheet_overhead_s": 8},
        "default": {"title": "Другой плоттер", "sec_per_a0": 90, "sheet_overhead_s": 10},
    },
    # Запуск Foxit/Sumatra, подготовка файла, пауза DevMode-хака (~5 с) — на КАЖДЫЙ запуск печати
    "job_overhead_s": 8,
}


def merged_profiles(overrides=None):
    """Значения по умолчанию + правки пользователя из конфига (ключ speed_profiles)."""
    prof = copy.deepcopy(DEFAULT_PROFILES)
    for kind in ("laser", "plotter"):
        for model, vals in ((overrides or {}).get(kind) or {}).items():
            if isinstance(vals, dict):
                prof[kind].setdefault(model, {}).update(vals)
    if overrides and "job_overhead_s" in overrides:
        prof["job_overhead_s"] = overrides["job_overhead_s"]
    return prof


def detect_model(kind, printer_name):
    name = (printer_name or "").upper().replace(" ", "")
    if kind == "laser":
        if "M435" in name:
            return "M435"
        if "M436" in name:
            return "M436"
    else:
        if re.search(r"TM-?3\d\d", name):
            return "TM-305"
    return "default"


def laser_job_seconds(pages, fmt, copies, model_prof, job_overhead):
    """Foxit печатает каждую копию отдельным запуском, поэтому накладные — на каждую копию."""
    ppm = model_prof["A3_ppm"] if str(fmt).upper().startswith("A3") else model_prof["A4_ppm"]
    one_copy = job_overhead + model_prof["first_page_s"] + max(pages - 1, 0) * 60.0 / ppm
    return one_copy * copies


def plotter_job_seconds(sheet_sizes_mm, copies, model_prof, job_overhead):
    """sheet_sizes_mm — [(ширина, высота), ...] листов задания."""
    t = job_overhead
    for w, h in sheet_sizes_mm:
        area = (w / 1000.0) * (h / 1000.0)
        t += area / A0_AREA_M2 * model_prof["sec_per_a0"] + model_prof["sheet_overhead_s"]
    return t * copies


def estimate_plan(plan, page_sizes, laser_name, plotter_name, overrides=None):
    """plan — результат build_print_plan; page_sizes — {(файл, стр): (ширина_мм, высота_мм)}.
    Возвращает словарь с секундами: laser, plotter, wall (принтеры работают параллельно),
    sum, per_file {файл: секунды}, per_entry [(тип, индекс, секунды)], модели."""
    prof = merged_profiles(overrides)
    lm, pm = detect_model("laser", laser_name), detect_model("plotter", plotter_name)
    lp, pp = prof["laser"][lm], prof["plotter"][pm]
    over = float(prof["job_overhead_s"])
    res = {"laser": 0.0, "plotter": 0.0, "per_file": {}, "per_entry": [],
           "laser_model": lp.get("title", lm), "plotter_model": pp.get("title", pm)}
    if not plan:
        res.update(wall=0.0, sum=0.0)
        return res

    for i, (fp, _fn, pgs, inf) in enumerate(plan.get("laser", [])):
        if inf.get("missing", 0) <= 0:
            continue
        s = laser_job_seconds(len(pgs), inf.get("format", "A4"), inf["missing"], lp, over)
        res["laser"] += s
        res["per_file"][fp] = res["per_file"].get(fp, 0.0) + s
        res["per_entry"].append(("laser", i, s))

    for i, (fp, _fn, pgs, inf) in enumerate(plan.get("plotter", [])):
        if inf.get("missing", 0) <= 0:
            continue
        sizes = [page_sizes.get((fp, p), (594.0, 841.0)) for p in pgs]
        s = plotter_job_seconds(sizes, inf["missing"], pp, over)
        res["plotter"] += s
        res["per_file"][fp] = res["per_file"].get(fp, 0.0) + s
        res["per_entry"].append(("plotter", i, s))

    res["wall"] = max(res["laser"], res["plotter"])
    res["sum"] = res["laser"] + res["plotter"]
    return res


def fmt_duration(seconds):
    """'<1 мин', '7 мин', '1 ч 05 мин'."""
    if seconds <= 0:
        return "0 мин"
    if seconds < 60:
        return "<1 мин"
    minutes = math.ceil(seconds / 60.0)
    if minutes < 60:
        return f"{minutes} мин"
    return f"{minutes // 60} ч {minutes % 60:02d} мин"
