"""Поведение обновлений в интерфейсе: окно при запуске, только значок позже, установка после печати."""
import os
import sys
import time

import pytest

pytestmark = pytest.mark.gui
needs_display = pytest.mark.skipif(sys.platform != "win32" and not os.environ.get("DISPLAY"),
                                   reason="нет дисплея для окон Tk")

INFO = {"version": "v9.9.9", "changelog": "- Новая функция\n- Исправление", "channel": "stable",
        "asset": {}, "sha_asset": None}


@pytest.fixture
def app(monkeypatch):
    from tkinter import messagebox
    for n in ("showinfo", "showerror", "showwarning"):
        monkeypatch.setattr(messagebox, n, lambda *a, **k: None)
    from core.config import get_config
    get_config().data.update(check_updates=False, last_folder="")
    from ui.app import PrintManagerApp
    a = PrintManagerApp(splash=None)
    a.update()
    a._busy = 0
    monkeypatch.setattr(a, "_print_busy_jobs", lambda: a._busy)
    yield a
    a._update_state = None
    a.destroy()


def pump(a, sec=0.3):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        a.update()
        time.sleep(0.02)


@needs_display
def test_startup_idle_shows_dialog_and_badge(app):
    app._on_update_found(INFO, startup=True)
    pump(app)
    assert app.btn_update.winfo_ismapped() and "9.9.9" in app.btn_update.cget("text")
    assert app._update_dlg is not None and app._update_dlg.winfo_exists()
    app._update_dlg._choose("later")


@needs_display
def test_startup_while_printing_only_badge(app):
    app._busy = 3
    app._on_update_found(INFO, startup=True)
    pump(app)
    assert app.btn_update.winfo_ismapped()
    assert getattr(app, "_update_dlg", None) is None


@needs_display
def test_periodic_check_only_badge(app):
    app._on_update_found(INFO, startup=False)
    pump(app)
    assert app.btn_update.winfo_ismapped()
    assert getattr(app, "_update_dlg", None) is None


@needs_display
def test_install_waits_for_print_queue(app, monkeypatch):
    installed = []
    import core.updater as up
    monkeypatch.setattr(up, "install_update", lambda exe, restart=True: installed.append((exe, restart)))
    monkeypatch.setattr(app, "_on_close", lambda: installed.append("closed"))
    app._update_info = INFO
    app._busy = 2
    app._on_update_downloaded("C:/tmp/new.exe")
    assert app._update_state == "scheduled" and not installed
    app._busy = 0                 # печать закончилась
    app._update_watchdog()
    assert app._update_state == "ready"
    pump(app, 7.0)                # 5 с паузы + 1.5 с перед установкой
    assert ("C:/tmp/new.exe", True) in installed and "closed" in installed
