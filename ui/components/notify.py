"""Всплывающие уведомления о принтерах (замятие, нет бумаги/чернил, отключен…).

Не модальные (не блокируют печать и программу), появляются в правом нижнем углу экрана
поверх всех окон — их видно, даже если программа свёрнута. Важные (авария) висят, пока
их не закроют или пока проблема не устранится; остальные исчезают сами.
Окно программы при этом мигает на панели задач, звучит системный сигнал.
"""
import logging
import sys
import tkinter as tk

import customtkinter as ctk

log = logging.getLogger(__name__)

COLORS = {3: "#c0392b", 2: "#d68910", 1: "#2e86c1", 0: "#27ae60"}


def flash_taskbar(win):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                        ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        # FLASHW_ALL | FLASHW_TIMERNOFG — мигать, пока окно не станет активным
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x3 | 0xC, 0, 0)
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


class Toast(tk.Toplevel):
    WIDTH = 380

    def __init__(self, manager, key, title, text, severity, sticky):
        super().__init__(manager.master)
        self.manager, self.key, self.severity = manager, key, severity
        self.overrideredirect(True)
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        color = COLORS.get(severity, COLORS[1])
        self.configure(bg=color)
        box = ctk.CTkFrame(self, fg_color="#1f2a36", corner_radius=0)
        box.pack(fill="both", expand=True, padx=(6, 1), pady=1)
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x", padx=10, pady=(8, 0))
        self.title_lbl = ctk.CTkLabel(head, text=title, font=("Segoe UI", 13, "bold"),
                                      text_color="#ecf0f1", anchor="w", wraplength=self.WIDTH - 70, justify="left")
        self.title_lbl.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(head, text="✕", width=26, height=22, fg_color="#5d6d7e", hover_color="#85929e",
                      command=self.close).pack(side="right")
        self.text_lbl = ctk.CTkLabel(box, text=text, justify="left", anchor="w", wraplength=self.WIDTH - 30,
                                     text_color="#d5dbdb", font=("Segoe UI", 12))
        self.text_lbl.pack(fill="x", padx=10, pady=(2, 10))
        for w in (box, self.title_lbl, self.text_lbl):
            w.bind("<Button-1>", lambda e: self.manager.on_click and self.manager.on_click(self.key))
        if not sticky:
            self.after(8000, self.close)

    def update_text(self, title, text):
        self.title_lbl.configure(text=title)
        self.text_lbl.configure(text=text)

    def close(self):
        self.manager._forget(self)
        try:
            self.destroy()
        except tk.TclError:
            pass


class Notifier:
    def __init__(self, master, on_click=None):
        self.master = master
        self.on_click = on_click
        self.toasts = []
        self.history = []          # последние уведомления (для журнала/тестов)

    def show(self, key, title, text="", severity=1, sticky=None):
        """key — для замены/закрытия (например «HP M436:jam»)."""
        sticky = severity >= 3 if sticky is None else sticky
        self.history.append((key, title, text, severity))
        del self.history[:-50]
        log.info("Уведомление: %s — %s %s", key, title, text)
        for t in self.toasts:
            if t.key == key:
                t.update_text(title, text)
                return t
        try:
            t = Toast(self, key, title, text, severity, sticky)
        except tk.TclError:
            return None
        self.toasts.append(t)
        self._layout()
        if severity >= 2:
            try:
                self.master.bell()
            except tk.TclError:
                pass
            flash_taskbar(self.master)
        return t

    def close(self, key_prefix):
        for t in [t for t in self.toasts if t.key.startswith(key_prefix)]:
            t.close()

    def _forget(self, toast):
        if toast in self.toasts:
            self.toasts.remove(toast)
            self._layout()

    def _layout(self):
        try:
            sw, sh = self.master.winfo_screenwidth(), self.master.winfo_screenheight()
        except tk.TclError:
            return
        y = sh - 60
        for t in reversed(self.toasts[-5:]):
            try:
                t.update_idletasks()
                h = t.winfo_reqheight()
                y -= h + 8
                t.geometry(f"{Toast.WIDTH}x{h}+{sw - Toast.WIDTH - 16}+{max(y, 0)}")
            except tk.TclError:
                pass
        for t in self.toasts[:-5]:      # больше пяти — самые старые закрываем
            t.close()
