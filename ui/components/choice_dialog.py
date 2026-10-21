"""Вопрос с кнопками, подписанными по действию («Допечатать недостающее (12 л.)»,
«Напечатать всё заново», «Отмена») вместо «Да / Нет / Отмена» — их не надо читать
в тексте, чтобы понять, что произойдёт.

ask_choice(parent, title, text, [(ключ, подпись, цвет), ...]) → ключ нажатой кнопки
или None (закрыли окно / Esc).
"""
import tkinter as tk

import customtkinter as ctk

COLORS = {"main": ("#2ecc71", "#27ae60"), "alt": ("#3498db", "#2980b9"),
          "danger": ("#e67e22", "#d35400"), "cancel": ("#7f8c8d", "#95a5a6")}


class ChoiceDialog(tk.Toplevel):
    def __init__(self, master, title, text, buttons, default=None):
        super().__init__(master)
        self.result = None
        self.title(title)
        self.resizable(False, False)
        try:
            self.transient(master)
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        box = ctk.CTkFrame(self, corner_radius=0)
        box.pack(fill="both", expand=True)
        ctk.CTkLabel(box, text=text, justify="left", anchor="w", wraplength=440,
                     font=("Segoe UI", 13)).pack(fill="x", padx=18, pady=(16, 12))
        self._buttons = {}
        for key, label, color in buttons:
            fg, hover = COLORS.get(color or "alt", COLORS["alt"])
            b = ctk.CTkButton(box, text=label, height=36, fg_color=fg, hover_color=hover,
                              font=("Segoe UI", 13, "bold" if color == "main" else "normal"),
                              command=lambda k=key: self._choose(k))
            b.pack(fill="x", padx=18, pady=3)
            self._buttons[key] = b
        ctk.CTkFrame(box, height=10, fg_color="transparent").pack()
        self.protocol("WM_DELETE_WINDOW", lambda: self._choose(None))
        self.bind("<Escape>", lambda e: self._choose(None))
        if default is not None:
            self.bind("<Return>", lambda e: self._choose(default))
        self.update_idletasks()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - self.winfo_reqwidth()) // 2
            y = master.winfo_rooty() + (master.winfo_height() - self.winfo_reqheight()) // 3
            self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        except tk.TclError:
            pass

    def _choose(self, key):
        self.result = key
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def ask_choice(parent, title, text, buttons, default=None):
    dlg = ChoiceDialog(parent, title, text, buttons, default)
    try:
        dlg.wait_visibility()
        dlg.grab_set()
        dlg.focus_force()
    except tk.TclError:
        pass
    parent.wait_window(dlg)
    return dlg.result
