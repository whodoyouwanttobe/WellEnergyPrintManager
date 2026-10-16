"""Регрессия 4.9.0: в Windows главное окно пряталось сразу после заставки.

CustomTkinter при старте mainloop в Windows прячет окно, если до этого вызывали withdraw().
Тест включает Windows-ветку CustomTkinter (sys.platform='win32' + заглушка ctypes.windll)
и проверяет, что после заставки окно видно.
"""
import ctypes
import os
import sys

import pytest

pytestmark = pytest.mark.gui


@pytest.mark.skipif(sys.platform != "win32" and not os.environ.get("DISPLAY"), reason="нет дисплея")
def test_main_window_visible_after_splash(monkeypatch):
    from tkinter import messagebox
    for n in ("askyesno", "showerror", "showinfo", "showwarning"):
        monkeypatch.setattr(messagebox, n, lambda *a, **k: False)
    from core.config import get_config
    get_config().data.update(check_updates=False, last_folder="")

    import main as M
    from ui.app import PrintManagerApp
    if sys.platform != "win32":
        class _WinDllStub:
            def __getattr__(self, name):
                return self

            def __call__(self, *a, **k):
                return 0
        monkeypatch.setattr(ctypes, "windll", _WinDllStub(), raising=False)
        monkeypatch.setattr(sys, "platform", "win32")

    splash = M.show_splash()
    app = PrintManagerApp(splash=splash)
    result = {}

    def check():
        result["state"] = app.state()
        result["viewable"] = app.winfo_viewable()
        app.quit()

    app.after(1500, check)
    app.mainloop()
    # Закрываем окно, ПОКА заглушки ещё действуют: иначе ошибка при закрытии
    # (из-за поддельного Windows-окружения) откроет настоящее модальное окно и тест зависнет.
    try:
        app.destroy()
    except Exception:
        pass
    monkeypatch.undo()
    assert result == {"state": "normal", "viewable": 1}, (
        "Главное окно спрятано после заставки — повторилась ошибка 4.9.0 "
        "(окно нужно показывать только после запуска mainloop, см. PrintManagerApp._close_splash)")
