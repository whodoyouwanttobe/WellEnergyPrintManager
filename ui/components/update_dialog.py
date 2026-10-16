"""Окно «Доступно обновление»: версия, список изменений и выбор, когда обновиться."""
import tkinter as tk

import customtkinter as ctk

from ui.components.dialog_utils import make_modal


class UpdateDialog(tk.Toplevel):
    """on_choice(choice): 'now' — обновить сейчас, 'after' — после окончания печати, 'later' — позже."""

    def __init__(self, master, info, current_version, busy_jobs, on_choice):
        super().__init__(master)
        self.title("Доступно обновление")
        self.geometry("560x460")
        self.minsize(460, 360)
        self._on_choice = on_choice
        self._close = make_modal(self, master)
        self.protocol("WM_DELETE_WINDOW", lambda: self._choose("later"))

        root = ctk.CTkFrame(self, corner_radius=0)
        root.pack(fill="both", expand=True)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=1)

        channel = {"stable": "стабильная", "beta": "бета", "dev": "разработка"}.get(info.get("channel"), "")
        ctk.CTkLabel(root, text=f"Новая версия {info['version']}" + (f"  ({channel})" if channel else ""),
                     font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 0))
        ctk.CTkLabel(root, text=f"Сейчас установлена: {current_version}", text_color="gray").grid(
            row=1, column=0, sticky="nw", padx=14, pady=(0, 6))

        box = ctk.CTkTextbox(root, font=("Segoe UI", 12), wrap="word")
        box.grid(row=2, column=0, sticky="nsew", padx=14, pady=4)
        box.insert("1.0", "Что нового:\n\n" + (info.get("changelog") or "Описание изменений не указано."))
        box.configure(state="disabled")

        if busy_jobs:
            ctk.CTkLabel(root, text=f"Сейчас идёт печать (заданий в очереди: {busy_jobs}).\n"
                                    "Обновление скачается сразу, а установится и перезапустит программу\n"
                                    "автоматически, когда печать закончится.",
                         text_color="#f39c12", justify="left").grid(row=3, column=0, sticky="w", padx=14, pady=(6, 0))

        btns = ctk.CTkFrame(root, fg_color="transparent")
        btns.grid(row=4, column=0, sticky="e", padx=14, pady=12)
        ctk.CTkButton(btns, text="Позже", width=100, fg_color="gray", hover_color="#555",
                      command=lambda: self._choose("later")).pack(side="left", padx=(0, 8))
        if busy_jobs:
            ctk.CTkButton(btns, text="Обновить после печати", width=190, fg_color="#e67e22", hover_color="#d35400",
                          command=lambda: self._choose("after")).pack(side="left")
        else:
            ctk.CTkButton(btns, text="Обновить сейчас", width=160, fg_color="#27ae60", hover_color="#1e8449",
                          command=lambda: self._choose("now")).pack(side="left")

    def _choose(self, choice):
        self._close()
        if self._on_choice:
            self._on_choice(choice)
