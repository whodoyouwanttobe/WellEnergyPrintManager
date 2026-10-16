import datetime as dt

from core.timefmt import short_time, long_time
from core.history import PrintHistory

NOW = dt.datetime(2026, 10, 16, 15, 0)
T = lambda d, h, m: dt.datetime(2026, 10, d, h, m)


def test_empty():
    assert short_time({}, now=NOW) == ""


def test_range_today_and_other_day():
    assert short_time({"print_first": T(16, 10, 15), "print_last": T(16, 10, 40)}, now=NOW) == "10:15–10:40"
    assert short_time({"print_first": T(15, 10, 15), "print_last": T(15, 10, 40)}, now=NOW) == "15.10 10:15–10:40"
    # печатали в разные дни — показываем последнюю печать
    assert short_time({"print_first": T(14, 9, 0), "print_last": T(15, 10, 40)}, now=NOW) == "15.10 10:40"


def test_in_progress_and_folded():
    assert short_time({}, session={"start": T(16, 14, 5)}, now=NOW) == "▶ 14:05"
    assert short_time({"fold_last": T(16, 11, 5)}, fully_folded=True, now=NOW) == "✓ 11:05"
    assert short_time({"fold_last": T(12, 11, 5)}, fully_folded=True, now=NOW) == "✓ 12.10"
    assert short_time({}, fully_folded=True, now=NOW) == "✓"  # старые записи без времени сшивки


def test_long_time():
    text = long_time({"print_first": T(16, 10, 15), "print_last": T(16, 10, 40), "fold_last": T(16, 11, 5)})
    assert "Печать: 16.10.2026 10:15 – 10:40" in text and "Сшито: 16.10.2026 11:05" in text


def test_history_times_and_fold_logged(tmp_path):
    h = PrintHistory(str(tmp_path / "h.db"))
    h.add_printed("C:/p/a.pdf", [1, 2], 1, "A4")
    h.set_folded_copies("C:/p/a.pdf", 1, 1)
    t = h.get_times("C:/p")["C:/p/a.pdf"]
    assert t["print_first"] and t["print_last"] and t["fold_last"]
    assert h.get_times_for("C:/p/a.pdf").keys() == t.keys()


def test_long_time_by_format():
    t = {"print_first": T(16, 10, 0), "print_last": T(16, 11, 0),
         "by_format": {"A1": {"print_first": T(16, 11, 0), "print_last": T(16, 11, 0)},
                       "A4": {"print_first": T(16, 10, 0), "print_last": T(16, 10, 20),
                              "fold_last": T(16, 12, 0)}}}
    text = long_time(t)
    assert "По форматам:" in text
    assert "A4: печать 16.10 10:00–10:20, сшит 16.10 12:00" in text
    assert "A1: печать 16.10 11:00" in text
    assert text.index("A4:") < text.index("A1:")


def test_history_times_by_format(tmp_path):
    h = PrintHistory(str(tmp_path / "h.db"))
    h.add_printed("C:/p/a.pdf", [1], 1, "A4")
    h.add_printed("C:/p/a.pdf", [2], 1, "A1")
    h.set_folded_copies("C:/p/a.pdf", 1, 1)
    by = h.get_times_for("C:/p/a.pdf")["by_format"]
    assert set(by) == {"A4", "A1"} and by["A4"].get("fold_last")
