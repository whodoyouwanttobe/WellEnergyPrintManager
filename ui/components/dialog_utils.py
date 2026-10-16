"""Общие помощники для диалоговых окон (Настройки, Отчёт, Пробная печать, Баг-репорт).

Почему это нужно:
  * Если в __init__ диалога возникает исключение, tk.Toplevel уже создан на экране,
    а содержимое — нет. В собранном exe (--windowed) ошибка никуда не выводится,
    и пользователь видит пустое окно. open_dialog() ловит такую ошибку,
    уничтожает недостроенное окно и показывает текст ошибки.
  * grab_set() делает окно модальным. Если окно закрывается без grab_release()
    или messagebox появляется ПОД модальным окном, приложение «зависает».
"""
import logging
import tkinter as tk
import traceback
from tkinter import messagebox

log = logging.getLogger(__name__)


def make_modal(win, master):
    """Безопасно делает окно модальным: после того как оно реально показано на экране."""
    try:
        win.transient(master)
    except tk.TclError:
        pass

    def _close():
        try:
            win.grab_release()
        except tk.TclError:
            pass
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", _close)
    win.bind("<Escape>", lambda e: _close())

    def _grab():
        if not win.winfo_exists():
            return
        try:
            win.lift()
            win.focus_force()
            win.grab_set()
        except tk.TclError:
            # Окно ещё не отображено (viewable) — пробуем чуть позже
            win.after(50, _grab)

    # wait_visibility() блокирует, поэтому просто откладываем захват до отрисовки
    win.after_idle(lambda: win.after(50, _grab))
    return _close


def open_dialog(master, attr_name, factory):
    """Открывает диалог, не допуская дублей и пустых «призрачных» окон.

    master    — главное окно приложения
    attr_name — имя атрибута, где храним ссылку (например '_settings_dlg')
    factory   — функция без аргументов, которая создаёт и возвращает окно
    """
    existing = getattr(master, attr_name, None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                existing.focus_force()
                return existing
        except tk.TclError:
            pass

    before = set(master.winfo_children())
    try:
        dlg = factory()
    except Exception as e:
        log.error("Не удалось открыть окно %s:\n%s", attr_name, traceback.format_exc())
        # Уничтожаем недостроенные окна, чтобы не оставались пустые рамки
        for w in set(master.winfo_children()) - before:
            if isinstance(w, tk.Toplevel):
                try:
                    w.grab_release()
                    w.destroy()
                except tk.TclError:
                    pass
        messagebox.showerror("Ошибка", f"Не удалось открыть окно:\n{type(e).__name__}: {e}", parent=master)
        setattr(master, attr_name, None)
        return None

    setattr(master, attr_name, dlg)
    return dlg
