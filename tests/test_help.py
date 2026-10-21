"""Справка (docs/help.md), повторная печать файла, условные запросы обновлений."""
import contextlib
import io
import json
import os
import types
import urllib.error

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HELP = os.path.join(ROOT, "docs", "help.md")


# ───────────── справка ─────────────
def test_help_file_has_three_tabs_and_content():
    from core.help_content import load_sections
    s = load_sections(HELP)
    assert list(s) == ["Вопросы и ответы", "Документация", "Что нового"]
    assert all(len(items) >= 2 for items in s.values())
    assert all(body for items in s.values() for _t, body in items)
    # служебный комментарий в справку не попадает
    assert not any("<!--" in b for items in s.values() for _t, b in items)


def test_help_search_finds_in_titles_and_bodies():
    from core.help_content import load_sections, search
    s = load_sections(HELP)
    hits = search(s, "растеризац")
    assert hits and hits[0][1].startswith("Зачем нужна растеризация")
    assert any(tab == "Документация" for tab, _t, _b in search(s, "Пробная печать"))
    assert search(s, "абракадабра-нет-такого") == []


def test_help_mentions_partial_reprint_answer():
    from core.help_content import load_sections, search
    hits = search(load_sections(HELP), "частично напечатал")
    assert hits and "недостающ" in hits[0][2]


def test_revision_date_prefers_build_stamp(monkeypatch):
    import core.version
    from core import help_content
    monkeypatch.setattr(core.version, "DOCS_REVISION", "01.02.2026")
    assert help_content.revision_date() == "01.02.2026"
    monkeypatch.setattr(core.version, "DOCS_REVISION", "")
    d = help_content.revision_date()
    assert len(d) == 10 and d[2] == "." and d[5] == "."


def test_ci_docs_revision_format():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ci_set_version", os.path.join(ROOT, "scripts", "ci_set_version.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rev = mod.docs_revision()   # без git — пустая строка, с git — ДД.ММ.ГГГГ
    assert rev == "" or (len(rev) == 10 and rev[2] == "." and rev[5] == ".")


def test_build_bundles_docs():
    assert "--add-data=docs;docs" in open(os.path.join(ROOT, "build.py"), encoding="utf-8").read()


# ───────────── «Печать этого файла» после частичной печати ─────────────
class _Copies:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


def _fake_app(printed):
    fp = "C:/p/doc.pdf"
    calls = []
    app = types.SimpleNamespace(
        analyzed_data={fp: {"pages": [(1, "A4", 210, 297), (2, "A4", 210, 297), (3, "A1", 841, 594)]}},
        printed_pages={fp: printed},
        target_copies=_Copies(1),
        _execute_print=lambda f, fmt, pgs, copies=1: calls.append((fmt, list(pgs), copies)),
        _print_done_info=lambda text: None,
        _task=lambda title: contextlib.nullcontext(),
    )
    return app, fp, calls


from ui.components import choice_dialog


@pytest.fixture
def quiet(monkeypatch):
    from tkinter import messagebox
    shown = {}
    for n in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(messagebox, n, lambda *a, **k: None)
    return monkeypatch, messagebox, shown


def test_single_file_partial_prints_only_missing(quiet):
    mp, mb, _ = quiet
    from ui.app import PrintManagerApp
    app, fp, calls = _fake_app({1: {"printed": 1}, 2: {"printed": 1}})   # A4 готов, A1 нет
    asked = []
    mp.setattr(choice_dialog, "ask_choice", lambda *a, **k: asked.append(a) or "missing")   # только недостающее
    PrintManagerApp.print_single_file(app, fp)
    assert asked and "2 из 3" in asked[0][2]
    assert calls == [("A1", [3], 1)]


def test_single_file_partial_no_reprints_all(quiet):
    mp, mb, _ = quiet
    import tkinter.simpledialog as sd
    from ui.app import PrintManagerApp
    app, fp, calls = _fake_app({1: {"printed": 1}})
    mp.setattr(choice_dialog, "ask_choice", lambda *a, **k: "all")     # «Нет» — весь файл
    mp.setattr(sd, "askinteger", lambda *a, **k: 1)
    PrintManagerApp.print_single_file(app, fp)
    assert sorted(calls) == [("A1", [3], 1), ("A4", [1, 2], 1)]


def test_single_file_partial_cancel_prints_nothing(quiet):
    mp, mb, _ = quiet
    from ui.app import PrintManagerApp
    app, fp, calls = _fake_app({1: {"printed": 1}})
    mp.setattr(choice_dialog, "ask_choice", lambda *a, **k: None)
    PrintManagerApp.print_single_file(app, fp)
    assert calls == []


def test_single_file_fully_printed_asks_before_reprint(quiet):
    mp, mb, _ = quiet
    from ui.app import PrintManagerApp
    app, fp, calls = _fake_app({1: {"printed": 1}, 2: {"printed": 1}, 3: {"printed": 1}})
    mp.setattr(choice_dialog, "ask_choice", lambda *a, **k: None)
    PrintManagerApp.print_single_file(app, fp)
    assert calls == []


# ───────────── обновления: условные запросы (304 не тратит лимит) ─────────────
class _Resp(io.BytesIO):
    def __init__(self, data, etag):
        super().__init__(json.dumps(data).encode())
        self.headers = {"ETag": etag}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_releases_uses_etag_and_304(monkeypatch):
    from core import updater
    updater._ETAG_CACHE.clear()
    seen = []

    def fake_urlopen(req, timeout=0):
        inm = req.get_header("If-none-match")
        seen.append(inm)
        if inm == '"abc"':
            raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)
        return _Resp([{"tag_name": "v1.0.0"}], '"abc"')

    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_urlopen)
    first = updater.fetch_releases("o/r")
    second = updater.fetch_releases("o/r")
    assert first == second == [{"tag_name": "v1.0.0"}]
    assert seen == [None, '"abc"']


# ───────────── «Печать группы» после частичной печати ─────────────
def _group_app(printed, tc=1):
    calls = []
    app = types.SimpleNamespace(printed_pages={"C:/p/doc.pdf": printed}, target_copies=_Copies(tc),
                                _execute_print=lambda f, fmt, pgs, copies=1: calls.append((fmt, list(pgs), copies)),
                                _task=lambda title: contextlib.nullcontext())
    return app, calls


def test_group_partial_yes_prints_only_missing(quiet):
    mp, mb, _ = quiet
    from ui.app import PrintManagerApp
    app, calls = _group_app({1: {"printed": 2}, 2: {"printed": 1}}, tc=2)
    asked = []
    mp.setattr(choice_dialog, "ask_choice", lambda *a, **k: asked.append(a[2]) or "missing")
    PrintManagerApp.print_format_group(app, "C:/p/doc.pdf", "A4", [1, 2, 3])
    assert "3 из 6" in asked[0]
    assert sorted(calls) == [("A4", [2], 1), ("A4", [3], 2)]     # стр.1 уже 2/2 — не печатается


def test_group_partial_no_prints_all(quiet):
    mp, mb, _ = quiet
    import tkinter.simpledialog as sd
    from ui.app import PrintManagerApp
    app, calls = _group_app({1: {"printed": 1}})
    mp.setattr(choice_dialog, "ask_choice", lambda *a, **k: "all")
    mp.setattr(sd, "askinteger", lambda *a, **k: 1)
    PrintManagerApp.print_format_group(app, "C:/p/doc.pdf", "A4", [1, 2, 3])
    assert calls == [("A4", [1, 2, 3], 1)]


def test_group_cancel_and_fully_printed(quiet):
    mp, mb, _ = quiet
    from ui.app import PrintManagerApp
    app, calls = _group_app({1: {"printed": 1}})
    mp.setattr(choice_dialog, "ask_choice", lambda *a, **k: None)
    PrintManagerApp.print_format_group(app, "C:/p/doc.pdf", "A4", [1, 2])
    app2, calls2 = _group_app({1: {"printed": 1}, 2: {"printed": 1}})
    mp.setattr(choice_dialog, "ask_choice", lambda *a, **k: None)
    PrintManagerApp.print_format_group(app2, "C:/p/doc.pdf", "A4", [1, 2])
    assert calls == [] and calls2 == []
