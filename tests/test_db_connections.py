"""Все соединения с SQLite должны закрываться — иначе на Windows файл БД остаётся занятым."""
import sqlite3

import core.history as history_mod
import core.analysis_cache as cache_mod


class _Tracked:
    opened = []

    def __init__(self, real):
        self._real = real
        self.closed = False
        _Tracked.opened.append(self)

    def close(self):
        self.closed = True
        self._real.close()

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, *a):
        return self._real.__exit__(*a)


def _patch(monkeypatch):
    real_connect = sqlite3.connect
    _Tracked.opened = []
    fake = lambda *a, **k: _Tracked(real_connect(*a, **k))
    monkeypatch.setattr(history_mod.sqlite3, "connect", fake)
    monkeypatch.setattr(cache_mod.sqlite3, "connect", fake)


def test_history_closes_connections(monkeypatch, tmp_path):
    _patch(monkeypatch)
    h = history_mod.PrintHistory(str(tmp_path / "h.db"))
    h.set_printed("C:/a.pdf", [1, 2], 2, "A4")
    h.get_printed("C:/a.pdf")
    h.get_all_printed("C:/")
    assert _Tracked.opened and all(c.closed for c in _Tracked.opened)


def test_cache_closes_connections(monkeypatch, tmp_path):
    _patch(monkeypatch)
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    c = cache_mod.AnalysisCache(str(tmp_path / "c.db"))
    c.put(str(pdf), [(1, "A4", 210.0, 297.0, "1", False, False)])
    assert c.get(str(pdf))
    assert _Tracked.opened and all(x.closed for x in _Tracked.opened)
