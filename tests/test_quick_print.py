"""Печать из Проводника, перетаскивание, понятные кнопки, прерванная печать — без окон."""
import os

from core.quick_plan import entries_for, format_summary
from core.shell_integration import command_for, parse_print_arg

ANALYZED = {
    "C:/p/a.pdf": {"pages": [(1, "A4", 210, 297), (2, "A4", 210, 297), (3, "A1", 594, 841)]},
    "C:/p/b.pdf": {"pages": [(1, "A3", 297, 420), (2, "A2", 420, 594)]},
}


def test_format_summary_counts_missing_sheets():
    printed = {"C:/p/a.pdf": {1: {"printed": 2}}}
    s = format_summary(ANALYZED, printed, list(ANALYZED), copies=2)
    assert list(s) == ["A4", "A3", "A2", "A1"]                 # по порядку форматов
    assert s["A4"]["pages"] == 2 and s["A4"]["sheets"] == 2   # стр.1 уже 2/2 — осталась только стр.2 × 2
    assert s["A1"]["sheets"] == 2 and s["A1"]["laser"] is False
    s2 = format_summary(ANALYZED, printed, list(ANALYZED), copies=2, reprint=True)
    assert s2["A4"]["sheets"] == 4


def test_entries_only_selected_formats_laser_first():
    e = entries_for(ANALYZED, {}, list(ANALYZED), 1, ["A1", "A4"])
    assert [(os.path.basename(fp), f) for fp, f, _p, _c in e] == [("a.pdf", "A4"), ("a.pdf", "A1")]
    e = entries_for(ANALYZED, {"C:/p/a.pdf": {1: {"printed": 1}}}, ["C:/p/a.pdf"], 1, ["A4"])
    assert e == [("C:/p/a.pdf", "A4", [2], 1)]               # только недостающее
    e = entries_for(ANALYZED, {"C:/p/a.pdf": {1: {"printed": 1}}}, ["C:/p/a.pdf"], 1, ["A4"], reprint=True)
    assert e == [("C:/p/a.pdf", "A4", [1, 2], 1)]


def test_print_arg_and_command():
    assert parse_print_arg(["--print", r"C:\Проект\Комплект"]) == r"C:\Проект\Комплект"
    assert parse_print_arg(["--print=C:\\a.pdf"]) == "C:\\a.pdf"
    assert parse_print_arg(["--self-test"]) is None
    assert command_for(r"C:\WPM\WPM.exe", "%1") == r'"C:\WPM\WPM.exe" --print "%1"'


def test_print_request_roundtrip(tmp_path):
    from core import print_request
    p = tmp_path / "Комплект"
    p.mkdir()
    print_request.write_request(str(p))
    assert print_request.take_request() == str(p)
    assert print_request.take_request() is None               # запрос забирается один раз


def test_unprint_pages(tmp_path):
    from core.history import PrintHistory
    h = PrintHistory(str(tmp_path / "h.db"))
    h.add_printed("C:/x.pdf", [1, 2], 2, "A4")
    h.unprint_pages("C:/x.pdf", [1, 2], 1, "A4")
    assert h.get_printed("C:/x.pdf")[1]["printed"] == 1
    h.unprint_pages("C:/x.pdf", [1], 5, "A4")
    assert h.get_printed("C:/x.pdf")[1]["printed"] == 0       # не ниже нуля


def test_several_requests_collected(tmp_path):
    """Выделили 3 PDF — Проводник запускает WPM трижды; программа получает все три пути."""
    from core import print_request
    files = []
    for n in ("1.pdf", "2.pdf", "3.pdf"):
        f = tmp_path / n
        f.write_bytes(b"%PDF")
        files.append(str(f))
        print_request.write_request(str(f))
    assert print_request.take_requests() == files
    assert print_request.take_requests() == []


def test_shell_menu_folders_and_pdf():
    from core import shell_integration as si
    bases = [b for b, _ in si.TARGETS]
    assert any(b.endswith(r"Directory\shell") for b in bases)
    assert any(b.endswith(r"Directory\Background\shell") for b in bases)
    assert any(r".pdf\shell" in b for b in bases)
    assert not any(r".docx" in b or r"\*\shell" in b for b in bases)   # у других файлов пункта нет


def test_incompatible_formats_not_selected():
    analyzed = {"C:/p/x.pdf": {"pages": [(1, "A1", 594, 841), (2, "980x1200", 980, 1200), (3, "610x900", 610, 900)]}}
    s = format_summary(analyzed, {}, ["C:/p/x.pdf"], 1)
    assert s["A1"]["standard"] and s["A1"]["fits"]
    assert not s["980x1200"]["fits"]                     # шире рулона 914 мм
    assert not s["610x900"]["standard"] and s["610x900"]["fits"]
    e = entries_for(analyzed, {}, ["C:/p/x.pdf"], 1, ["A1", "980x1200", "610x900"])
    assert {f for _fp, f, _p, _c in e} == {"A1", "610x900"}   # не помещающийся не отправляется


def test_print_args_from_sendto():
    """«Отправить → WPM — напечатать» передаёт все выделенные пути после --print."""
    from core.shell_integration import parse_print_args
    assert parse_print_args(["--print", r"C:\a.pdf", r"C:\b.pdf", r"C:\Папка"]) == [r"C:\a.pdf", r"C:\b.pdf", r"C:\Папка"]
    assert parse_print_args(["--print=C:\\x.pdf"]) == ["C:\\x.pdf"]
    assert parse_print_args(["--self-test"]) == []
