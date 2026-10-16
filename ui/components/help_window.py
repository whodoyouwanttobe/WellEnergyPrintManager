"""Окно «?» — вопросы и ответы, документация, что нового. Текст — docs/help.md."""
import re
import tkinter as tk

import customtkinter as ctk

from core.help_content import load_sections, revision_date, search


class HelpWindow(tk.Toplevel):
    def __init__(self, master, open_topic=None):
        super().__init__(master)
        from core.version import FULL_VERSION
        self.title("Справка — WellEnergy Print Manager")
        self.geometry("900x620")
        self.minsize(700, 450)
        try:
            self.transient(master)
        except tk.TclError:
            pass
        self.bind("<Escape>", lambda e: self.destroy())

        try:
            self.sections = load_sections()
        except Exception as e:
            self.sections = {"Ошибка": [("Справка не найдена", f"Не удалось прочитать docs/help.md:\n{e}")]}
        self.tabs = list(self.sections)

        root = ctk.CTkFrame(self, corner_radius=0)
        root.pack(fill="both", expand=True)
        root.grid_columnconfigure(1, weight=1)
        root.grid_rowconfigure(2, weight=1)

        head = ctk.CTkFrame(root, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 4))
        ctk.CTkLabel(head, text="Справка", font=("Segoe UI", 18, "bold")).pack(side="left")
        self.revision_label = ctk.CTkLabel(
            head, text=f"   документация, редакция от {revision_date()}  ·  версия программы {FULL_VERSION}",
            text_color="gray")
        self.revision_label.pack(side="left")
        ctk.CTkButton(head, text="✕", width=32, fg_color="#7f8c8d", hover_color="#95a5a6",
                      command=self.destroy).pack(side="right")

        bar = ctk.CTkFrame(root, fg_color="transparent")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=4)
        self.tab_var = ctk.StringVar(master=self, value=self.tabs[0] if self.tabs else "")
        self.tab_buttons = ctk.CTkSegmentedButton(bar, values=self.tabs, variable=self.tab_var,
                                                  command=lambda v: self._show_tab(v))
        self.tab_buttons.pack(side="left")
        self.search_var = ctk.StringVar(master=self)
        self.search_entry = ctk.CTkEntry(bar, width=260, textvariable=self.search_var,
                                         placeholder_text="🔍 Поиск по справке…")
        self.search_entry.pack(side="right")
        self.search_entry.bind("<KeyRelease>", lambda e: self._on_search())

        self.list_frame = ctk.CTkScrollableFrame(root, width=280)
        self.list_frame.grid(row=2, column=0, sticky="nsw", padx=(12, 6), pady=(4, 12))
        self.text = ctk.CTkTextbox(root, wrap="word", font=("Segoe UI", 13))
        self.text.grid(row=2, column=1, sticky="nsew", padx=(6, 12), pady=(4, 12))
        self.text.tag_config("h", foreground="#5dade2")
        self.text.tag_config("b", foreground="#f5f5f5")
        self.text.tag_config("muted", foreground="gray")
        self._items = []

        if open_topic and self._open_topic(open_topic):
            return
        if self.tabs:
            self._show_tab(self.tabs[0])

    # ───────────── навигация ─────────────
    def _fill_list(self, entries):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._items = entries
        for i, (tab, title, body) in enumerate(entries):
            ctk.CTkButton(self.list_frame, text=title, anchor="w", fg_color="transparent",
                          hover_color="#34495e", text_color=("black", "white"), height=28,
                          command=lambda i=i: self._show_item(i)).pack(fill="x", pady=1)
        if entries:
            self._show_item(0)
        else:
            self._render("Ничего не найдено", "Попробуйте другие слова.")

    def _show_tab(self, tab):
        self.search_var.set("")
        self.tab_var.set(tab)
        self._fill_list([(tab, t, b) for t, b in self.sections.get(tab, [])])

    def _on_search(self):
        q = self.search_var.get()
        if not q.strip():
            self._show_tab(self.tab_var.get() or self.tabs[0])
            return
        self._fill_list(search(self.sections, q))

    def _show_item(self, i):
        tab, title, body = self._items[i]
        self._render(title, body, tab if self.search_var.get().strip() else None)

    def _open_topic(self, topic):
        """Открыть раздел по части заголовка (используется из обучения)."""
        for tab, items in self.sections.items():
            for idx, (title, body) in enumerate(items):
                if topic.lower() in title.lower():
                    self._show_tab(tab)
                    self._show_item(idx)
                    return True
        return False

    # ───────────── отрисовка простого markdown ─────────────
    def _render(self, title, body, tab=None):
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        if tab:
            t.insert("end", f"{tab}\n", "muted")
        t.insert("end", title + "\n\n", "h")
        for line in body.splitlines():
            line = re.sub(r"`([^`]*)`", r"\1", line)
            if line.lstrip().startswith("- "):
                indent = len(line) - len(line.lstrip())
                line = " " * indent + "•  " + line.lstrip()[2:]
            parts = re.split(r"(\*\*[^*]+\*\*)", line)
            for p in parts:
                if p.startswith("**") and p.endswith("**"):
                    t.insert("end", p[2:-2], "b")
                else:
                    t.insert("end", p)
            t.insert("end", "\n")
        t.configure(state="disabled")
