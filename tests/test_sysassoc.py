import sys

import pytest

from core import sysassoc


def test_label_fallback_when_unknown(monkeypatch):
    monkeypatch.setattr(sysassoc, "default_app", lambda ext=".pdf": (None, None))
    assert sysassoc.open_default_label() == "Открыть файл (программа по умолчанию)"


def test_label_with_app_name(monkeypatch):
    monkeypatch.setattr(sysassoc, "default_app", lambda ext=".pdf": ("Foxit PDF Editor", r"C:\\Foxit\\FoxitPDFEditor.exe"))
    assert sysassoc.open_default_label() == "Открыть в Foxit PDF Editor (по умолчанию)"


def test_long_name_is_shortened(monkeypatch):
    monkeypatch.setattr(sysassoc, "default_app", lambda ext=".pdf": ("X" * 80, None))
    assert len(sysassoc.open_default_label()) < 80


@pytest.mark.skipif(sys.platform != "win32", reason="только Windows")
def test_real_windows_query_does_not_crash():
    name, exe = sysassoc.default_app(".pdf")
    assert name is None or isinstance(name, str)
