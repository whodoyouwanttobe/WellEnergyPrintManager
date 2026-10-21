"""Отслеживание хода печати по каждому принтеру.

Почему не по очереди Windows: лазерный принтер забирает задание из очереди за секунды
(у него своя память), а печатает ещё минуты. Поэтому «Печать завершена» раньше
появлялась слишком рано. Здесь задание считается напечатанным, когда:
  • очередь Windows его отдала (spooled) И
  • прошло ориентировочное время печати этого задания (по модели и формату).

Состояния задания: queued → sending → printing → done / error
"""
import itertools
import time

STATE_TITLES = {"queued": "в очереди", "sending": "отправка", "printing": "печать",
                "done": "готово", "error": "ошибка"}


class PrintTracker:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._ids = itertools.count(1)
        self.jobs = {}          # id -> dict
        self.changed = False    # было ли изменение состояния заданий с прошлого tick()
        self._stopped = {}      # принтер -> когда остановился (нет бумаги, замятие…)
        self._done = []         # задания, допечатанные с прошлого pop_done()

    # ───────────── события ─────────────
    def add(self, filepath, fmt, pages, copies, printer, kind, est_seconds, page_list=None, slots=None):
        jid = next(self._ids)
        self.jobs[jid] = {
            "id": jid, "file": filepath, "fmt": fmt, "pages": int(pages), "copies": int(copies),
            "printer": printer, "kind": kind, "est": max(float(est_seconds), 5.0),
            "state": "queued", "started": None, "spooled": None, "finished": None,
            "printed": 0, "total": 0, "page_list": list(page_list or []),
            "slots": float(slots) if slots is not None else float(int(pages) * int(copies)),
        }
        return jid

    def start(self, jid):
        j = self.jobs.get(jid)
        if j and j["state"] == "queued":
            j["state"], j["started"] = "sending", self._clock()

    def progress(self, jid, printed, total):
        j = self.jobs.get(jid)
        if j:
            j["printed"], j["total"] = int(printed or 0), int(total or 0)

    def spooled(self, jid):
        """Очередь Windows отдала задание принтеру."""
        j = self.jobs.get(jid)
        if j and j["state"] in ("queued", "sending"):
            now = self._clock()
            if j["started"] is None:
                j["started"] = now
            j["state"], j["spooled"] = "printing", now

    def error(self, jid):
        j = self.jobs.get(jid)
        if j and j["state"] not in ("done",):
            j["state"], j["finished"] = "error", self._clock()
            self.changed = True

    def set_stopped(self, printer, stopped):
        """Принтер встал (нет бумаги, замятие, открыта крышка…) или снова пошёл.
        Пока стоит, расчётное время печати его заданий не идёт: иначе задание считалось бы
        «напечатанным» по таймеру, хотя листы ещё не вышли, и окно «Заберите листы»
        или отметка «готово» появились бы раньше времени."""
        now = self._clock()
        if stopped and printer not in self._stopped:
            self._stopped[printer] = now
        elif not stopped and printer in self._stopped:
            delta = now - self._stopped.pop(printer)
            for j in list(self.jobs.values()):
                if j["printer"] == printer and j["state"] in ("sending", "printing") and j["started"] is not None:
                    j["started"] += delta

    def is_stopped(self, printer):
        return printer in self._stopped

    def _now_for(self, printer, now):
        return self._stopped.get(printer, now)

    def tick(self):
        """Переводит в «готово» задания, чьё ориентировочное время печати истекло.
        Возвращает список файлов, по которым печать полностью закончилась."""
        now = self._clock()
        touched = set()
        for j in self.jobs.values():
            if j["state"] == "printing" and j["printer"] not in self._stopped \
                    and now >= j["started"] + j["est"]:
                j["state"], j["finished"] = "done", now
                touched.add(j["file"])
                self.changed = True
                self._done.append(dict(j))
        return [f for f in touched if self.active_count(f) == 0]

    def clear_finished(self, printer=None, older_than=10.0):
        """Убирает завершённые задания простаивающих принтеров (через older_than секунд)."""
        now = self._clock()
        for p in self.printers():
            if printer and p != printer:
                continue
            if self._active(p):
                continue
            for jid in [j["id"] for j in self.jobs.values() if j["printer"] == p]:
                fin = self.jobs[jid]["finished"]
                if fin is not None and now - fin >= older_than:
                    del self.jobs[jid]

    # ───────────── запросы ─────────────
    def printers(self):
        seen = []
        for j in self.jobs.values():
            if j["printer"] not in seen:
                seen.append(j["printer"])
        return seen

    def _active(self, printer):
        return [j for j in self.jobs.values() if j["printer"] == printer and j["state"] in ("queued", "sending", "printing")]

    def active_count(self, filepath=None):
        return sum(1 for j in self.jobs.values()
                   if j["state"] in ("queued", "sending", "printing") and (filepath is None or j["file"] == filepath))

    def file_state(self, filepath):
        """'printing' — что-то из файла печатается, 'queued' — ждёт очереди, None — ничего."""
        states = {j["state"] for j in self.jobs.values() if j["file"] == filepath}
        if states & {"sending", "printing"}:
            return "printing"
        if "queued" in states:
            return "queued"
        return None

    def printing_now(self, printer):
        """Есть ли у принтера задание, которое отправляется или ещё печатается (по расчёту).
        Вызывается и из потока очереди печати — поэтому работаем со снимком."""
        for _ in range(3):
            try:
                return printer in self._stopped or any(
                    j["printer"] == printer and j["state"] in ("sending", "printing")
                    for j in list(self.jobs.values()))
            except RuntimeError:
                continue
        return False

    def active_formats(self, filepath):
        """Форматы файла, которые ещё печатаются или ждут очереди."""
        return {j["fmt"] for j in self.jobs.values()
                if j["file"] == filepath and j["state"] in ("queued", "sending", "printing")}

    def active_pages(self, filepath):
        """Страницы файла, которые ещё печатаются или ждут очереди."""
        out = set()
        for j in self.jobs.values():
            if j["file"] == filepath and j["state"] in ("queued", "sending", "printing"):
                out.update(j["page_list"])
        return out

    def pop_done(self):
        done, self._done = self._done, []
        return done

    def pop_changed(self):
        ch, self.changed = self.changed, False
        return ch

    def is_busy(self):
        return self.active_count() > 0

    def _job_fraction(self, j, now):
        if j["state"] in ("done", "error"):
            return 1.0
        if j["state"] == "queued" or j["started"] is None:
            return 0.0
        now = self._now_for(j["printer"], now)
        frac = (now - j["started"]) / j["est"]
        if j["total"]:
            frac = max(frac, j["printed"] / j["total"] * 0.9)
        return max(0.0, min(frac, 0.97))   # 100% — только когда задание реально закончено

    def printer_summary(self, printer):
        """Сводка для полосы прогресса одного принтера."""
        now = self._clock()
        jobs = [j for j in self.jobs.values() if j["printer"] == printer]
        if not jobs:
            return None
        total_est = sum(j["est"] for j in jobs if j["state"] != "error")
        done_est = sum(j["est"] * self._job_fraction(j, now) for j in jobs if j["state"] != "error")
        active = [j for j in jobs if j["state"] in ("queued", "sending", "printing")]
        current = next((j for j in active if j["state"] in ("sending", "printing")), active[0] if active else None)
        remaining_sheets = 0
        remaining_s = 0.0
        for j in active:
            f = self._job_fraction(j, now)
            remaining_sheets += round(j["pages"] * j["copies"] * (1 - f))
            remaining_s += max(j["est"] * (1 - f), 3.0)
        queued_formats = []
        for j in active:
            if j is not current and j["fmt"] not in queued_formats:
                queued_formats.append(j["fmt"])
        return {
            "printer": printer,
            "fraction": (done_est / total_est) if total_est else 1.0,
            "current": current,
            "jobs_left": len(active),
            "formats_left": queued_formats,
            "sheets_left": remaining_sheets,
            "seconds_left": remaining_s,
            "errors": sum(1 for j in jobs if j["state"] == "error"),
            "idle": not active,
        }
