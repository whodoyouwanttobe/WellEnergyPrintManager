"""Ход печати: задание «готово» только после ориентировочного времени печати, а не когда
очередь Windows его отдала; файл допечатан только когда закончились ВСЕ его задания."""
from core.print_progress import PrintTracker


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def make():
    c = Clock()
    return PrintTracker(clock=c), c


def test_spooled_is_not_finished_until_estimate_passes():
    tr, c = make()
    j = tr.add("a.pdf", "A4", 10, 2, "HP M436", "laser", est_seconds=120)
    tr.start(j)
    c.t = 5
    tr.spooled(j)                 # Windows уже отдала задание принтеру
    assert tr.tick() == [] and tr.active_count("a.pdf") == 1
    c.t = 119
    assert tr.tick() == []
    c.t = 121
    assert tr.tick() == ["a.pdf"] and tr.active_count() == 0


def test_file_finished_only_after_all_its_jobs():
    tr, c = make()
    a4 = tr.add("a.pdf", "A4", 5, 1, "HP", "laser", 30)
    a1 = tr.add("a.pdf", "A1", 2, 1, "Canon", "plotter", 100)
    for j in (a4, a1):
        tr.start(j)
        tr.spooled(j)
    c.t = 40
    assert tr.tick() == []            # A4 готов, но A1 ещё печатается
    assert tr.active_count("a.pdf") == 1
    c.t = 101
    assert tr.tick() == ["a.pdf"]


def test_printer_summary_text_data():
    tr, c = make()
    j1 = tr.add("a.pdf", "A3", 10, 2, "HP", "laser", 100)
    tr.add("a.pdf", "A4", 20, 2, "HP", "laser", 100)
    tr.start(j1)
    c.t = 50
    s = tr.printer_summary("HP")
    assert s["current"]["fmt"] == "A3" and s["jobs_left"] == 2
    assert s["formats_left"] == ["A4"]
    assert 0.2 < s["fraction"] < 0.3          # половина первого задания из двух
    assert s["sheets_left"] == 10 + 40 and 140 < s["seconds_left"] < 160


def test_error_counts_and_never_100_while_printing():
    tr, c = make()
    j = tr.add("b.pdf", "A1", 1, 1, "Canon", "plotter", 50)
    tr.start(j)
    c.t = 1000                                # дольше оценки, но очередь задание ещё не отдала
    assert tr.printer_summary("Canon")["fraction"] < 1.0
    tr.error(j)
    s = tr.printer_summary("Canon")
    assert s["idle"] and s["errors"] == 1


def test_clear_finished_keeps_busy_printers():
    tr, c = make()
    j = tr.add("a.pdf", "A4", 1, 1, "HP", "laser", 10)
    tr.start(j)
    tr.spooled(j)
    c.t = 11
    tr.tick()
    c.t = 30
    tr.clear_finished(older_than=15)
    assert tr.printers() == []


def test_file_state():
    tr, c = make()
    j = tr.add("a.pdf", "A4", 1, 1, "HP", "laser", 10)
    assert tr.file_state("a.pdf") == "queued"
    tr.start(j)
    assert tr.file_state("a.pdf") == "printing"
    tr.spooled(j)
    c.t = 20
    tr.tick()
    assert tr.file_state("a.pdf") is None
