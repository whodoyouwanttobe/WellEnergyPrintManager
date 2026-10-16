import customtkinter as ctk
from tkinter import ttk
import tkinter as tk
import os

class FileTreeView(ctk.CTkFrame):
    def __init__(self, master, on_file_select=None, on_open_file=None, on_mark_printed=None,
                 on_print_file=None, on_preview_file=None, on_set_copies=None, **kwargs):
        super().__init__(master)
        self.on_file_select = on_file_select
        self.on_open_file = on_open_file
        self.on_mark_printed = on_mark_printed
        self.on_mark_folded = kwargs.get('on_mark_folded', None)
        self.on_print_file = on_print_file
        self.on_preview_file = on_preview_file
        self.on_set_copies = on_set_copies
        self.base_dir = ''
        
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("Treeview", 
                        background="#2b2b2b",
                        foreground="white",
                        rowheight=25,
                        fieldbackground="#2b2b2b",
                        bordercolor="#343638",
                        borderwidth=0)
        style.map('Treeview', background=[('selected', '#1f538d')])
        style.configure("Treeview.Heading",
                        background="#565b5e",
                        foreground="white",
                        relief="flat")
        style.map("Treeview.Heading", background=[('active', '#3484F0')])

        # Колонки: имя | страниц | сшито | статус | время.
        # Разбивка по форматам показывается подсказкой при наведении на «Стр.»
        self.tree = ttk.Treeview(self, columns=("name", "pages", "folded", "status", "time"),
                                 show="tree headings", style="Treeview")
        self.tree.heading("#0", text="")
        self.tree.heading("name", text="Файл", anchor="w")
        self.tree.heading("pages", text="Стр.")
        self.tree.heading("folded", text="Сшито")
        self.tree.heading("status", text="Статус")
        self.tree.heading("time", text="Время")
        self.tree.column("#0", width=22, minwidth=22, stretch=False)
        self.tree.column("name", width=300, anchor="w")
        self.tree.column("pages", width=48, minwidth=40, anchor="center", stretch=False)
        self.tree.column("folded", width=64, minwidth=50, anchor="center", stretch=False)
        self.tree.column("status", width=130, anchor="center")
        self.tree.column("time", width=120, anchor="center")
        self._meta = {}      # filepath -> {"pages", "formats", "folded", "time"}
        self._tip = None     # всплывающая подсказка с форматами
        self._tip_item = None
        
        self.tree.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        
        scrollbar = ctk.CTkScrollbar(self, command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        self.tree.tag_configure("done", foreground="#2ecc71")
        self.tree.tag_configure("partial", foreground="#90EE90")
        self.tree.tag_configure("ready", foreground="#1E8449")
        self.tree.tag_configure("error", foreground="#e74c3c")
        
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Motion>", self._on_motion)
        self.tree.bind("<Leave>", lambda e: self._hide_tip())
        
        # Правая кнопка мыши
        self.tree.bind("<Button-3>", self._on_right_click)
        
        self.context_menu = tk.Menu(self, tearoff=0, bg="#2b2b2b", fg="white", activebackground="#1f538d")
        self.context_menu.add_command(label="Открыть файл (программа по умолчанию)", command=self._cmd_open_file)
        self.context_menu.add_command(label="Открыть в Foxit", command=self._cmd_open_foxit)
        self.context_menu.add_command(label="Открыть папку", command=self._cmd_open_folder)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="🖨️ Печать этого файла", command=self._cmd_print_file)
        self.context_menu.add_command(label="👁️ Предпросмотр этого файла", command=self._cmd_preview_file)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Указать кол-во распечатанных", command=self._cmd_set_copies)
        self.context_menu.add_command(label="Отметить как напечатанный (все 1)", command=lambda: self._cmd_mark(True))
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Указать кол-во сшитых", command=self._cmd_set_folded)
        self.context_menu.add_command(label="Отметить как сшитый (все 1)", command=lambda: self._cmd_mark_folded(True))
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Снять отметки печати и сшивания (0)", command=lambda: self._cmd_mark_all_false())
        
        self._right_clicked_item = None

    def _on_right_click(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self._right_clicked_item = item
            # Подпись первого пункта — какая программа реально открывает PDF в системе
            # (берём каждый раз заново: пользователь мог сменить программу по умолчанию)
            try:
                from core.sysassoc import open_default_label
                tags = self.tree.item(item, "tags")
                ext = os.path.splitext(tags[0])[1] if tags else ".pdf"
                self.context_menu.entryconfigure(0, label=open_default_label(ext or ".pdf"))
            except Exception:
                pass
            self.context_menu.post(event.x_root, event.y_root)

    def _cmd_open_file(self):
        if self._right_clicked_item and self.on_open_file:
            filepath = self.tree.item(self._right_clicked_item, "tags")[0]
            self.on_open_file(filepath)
            
    def _cmd_open_foxit(self):
        if self._right_clicked_item and self.on_open_file:
            tags = self.tree.item(self._right_clicked_item, "tags")
            if tags:
                self.on_open_file(tags[0], use_foxit=True)

    def _cmd_open_folder(self):
        if self._right_clicked_item and self.on_open_file:
            tags = self.tree.item(self._right_clicked_item, "tags")
            if tags:
                self.on_open_file(tags[0], open_dir=True)
                
    def _cmd_set_copies(self):
        if self._right_clicked_item and self.on_set_copies:
            tags = self.tree.item(self._right_clicked_item, "tags")
            if tags:
                import tkinter.simpledialog as sd
                c = sd.askinteger("Копии", "Введите количество распечатанных копий для файла:")
                if c is not None:
                    self.on_set_copies(tags[0], c)
                    
    def _cmd_mark(self, state):
        if self._right_clicked_item and self.on_mark_printed:
            filepath = self.tree.item(self._right_clicked_item, "tags")[0]
            self.on_mark_printed(filepath, state)

    def _cmd_mark_folded(self, state):
        if self._right_clicked_item and self.on_mark_folded:
            filepath = self.tree.item(self._right_clicked_item, "tags")[0]
            self.on_mark_folded(filepath, state)
            
    def _cmd_mark_all_false(self):
        if self._right_clicked_item:
            filepath = self.tree.item(self._right_clicked_item, "tags")[0]
            if self.on_mark_printed:
                self.on_mark_printed(filepath, False)
            if getattr(self, 'on_mark_folded', None):
                self.on_mark_folded(filepath, False)
    
    def _cmd_print_file(self):
        if self._right_clicked_item and self.on_print_file:
            tags = self.tree.item(self._right_clicked_item, "tags")
            if tags:
                self.on_print_file(tags[0])
    
    def _cmd_preview_file(self):
        if self._right_clicked_item and self.on_preview_file:
            tags = self.tree.item(self._right_clicked_item, "tags")
            if tags:
                self.on_preview_file(tags[0])

    def _cmd_set_copies(self):
        if self._right_clicked_item:
            tags = self.tree.item(self._right_clicked_item, "tags")
            if tags:
                filepath = tags[0]
                import customtkinter as ctk
                dialog = ctk.CTkInputDialog(text=f"Количество копий для файла:", title="Копии")
                val = dialog.get_input()
                if val is not None:
                    try:
                        copies = int(val)
                        if hasattr(self, 'on_set_copies') and self.on_set_copies:
                            self.on_set_copies(filepath, copies)
                        elif getattr(self.master, 'set_file_copies', None):
                            self.master.set_file_copies(filepath, copies)
                    except ValueError:
                        pass

    def _cmd_set_folded(self):
        if self._right_clicked_item:
            tags = self.tree.item(self._right_clicked_item, "tags")
            if tags:
                filepath = tags[0]
                import customtkinter as ctk
                dialog = ctk.CTkInputDialog(text=f"Количество сшитых копий для файла:", title="Сшито")
                val = dialog.get_input()
                if val is not None:
                    try:
                        copies = int(val)
                        if getattr(self.master, 'set_folded_copies', None):
                            self.master.set_folded_copies(filepath, copies)
                    except ValueError:
                        pass

    def _on_select(self, event):
        if self.on_file_select:
            selected = self.tree.selection()
            if selected:
                tags = self.tree.item(selected[0], "tags")
                if tags:  # у папок тегов нет
                    self.on_file_select(tags[0])

    def clear(self):
        self.base_dir = ''
        self._items = {}
        self._meta = {}
        self._hide_tip()
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)  # одной командой, а не по одному

    def has_file(self, filepath):
        return filepath in getattr(self, "_items", {})

    def set_base_dir(self, folder):
        self.base_dir = folder

    def add_file(self, filepath, status="Ожидание"):
        if not self.base_dir:
            self.base_dir = os.path.dirname(filepath)
            
        try:
            rel_path = os.path.relpath(filepath, self.base_dir)
        except ValueError:
            rel_path = os.path.basename(filepath)
            
        parts = rel_path.split(os.sep)
        
        parent = ""
        current_path = ""
        # Create folder nodes
        for i, part in enumerate(parts[:-1]):
            current_path = os.path.join(current_path, part)
            node_id = f"dir_{current_path}"
            if not self.tree.exists(node_id):
                self.tree.insert(parent, "end", node_id, text="", values=(f"📁 {part}", "", "", "", ""), open=True)
            parent = node_id
            
        # Insert file
        if not hasattr(self, "_items"):
            self._items = {}
        if filepath in self._items and self.tree.exists(self._items[filepath]):
            return
        filename = parts[-1]
        self._items[filepath] = self.tree.insert(parent, "end", values=(filename, "", "", status, ""), tags=(filepath,))

    def update_status(self, filepath, new_status, info_text="", pages=None, formats=None,
                      folded=None, time_str=None, time_tip=None):
        """pages — число страниц, formats — список строк вида 'A4 (12)' для подсказки,
        folded — текст '3/10', time_str — текст для колонки «Время».
        Не переданные значения сохраняются с прошлого вызова."""
        meta = self._meta.setdefault(filepath, {})
        if pages is not None:
            meta["pages"] = pages
        if formats is not None:
            meta["formats"] = list(formats)
        if folded is not None:
            meta["folded"] = folded
        if time_tip is not None:
            meta["time_tip"] = time_tip
        if time_str is not None:
            meta["time"] = time_str
        elif info_text and ("Начало:" in info_text or "Готово:" in info_text):
            meta["time"] = info_text
        # Раньше для КАЖДОГО файла дерево обходилось целиком (O(N²)) — на сотнях файлов
        # интерфейс замирал на секунды после анализа и после каждой печати.
        item = getattr(self, "_items", {}).get(filepath)
        if item is not None and self.tree.exists(item):
            self._apply_status(item, filepath, new_status, info_text)
            return

        def _search(node):
            for item in self.tree.get_children(node):
                tags = self.tree.item(item, "tags")
                if tags and tags[0] == filepath:
                    self._apply_status(item, filepath, new_status, info_text)
                    return True
                if _search(item):
                    return True
            return False
        _search("")

    def _apply_status(self, item, filepath, new_status, info_text):
        meta = self._meta.get(filepath, {})
        tag_list = [filepath]
        status_lower = new_status.lower()
        if "ошиб" in status_lower:
            tag_list.append("error")
        elif "готово" in status_lower:
            tag_list.append("ready")
        elif "частично" in status_lower or "расп" in status_lower or "печать" in status_lower:
            tag_list.append("done" if "завершена печать" in status_lower else "partial")
        elif "напечатан" in status_lower or "завершен" in status_lower:
            tag_list.append("done")

        values = (os.path.basename(filepath),
                  meta.get("pages", ""),
                  meta.get("folded", ""),
                  new_status,
                  meta.get("time", ""))
        values = tuple("" if v is None else str(v) for v in values)
        tags = tuple(tag_list)
        if tuple(self.tree.item(item, "values")) != values or tuple(self.tree.item(item, "tags")) != tags:
            self.tree.item(item, values=values, tags=tags)

    # ───────────── подсказка с форматами ─────────────
    def _on_motion(self, event):
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        # "#2" — колонка «Стр.» (форматы), "#5" — «Время» (подробно); "#0" — колонка дерева
        if not item or column not in ("#2", "#5"):
            self._hide_tip()
            return
        tags = self.tree.item(item, "tags")
        meta = self._meta.get(tags[0], {}) if tags else {}
        text = "\n".join(meta.get("formats") or []) if column == "#2" else meta.get("time_tip", "")
        if not text:
            self._hide_tip()
            return
        item = (item, column)
        if self._tip_item == item and self._tip is not None:
            self._tip.geometry(f"+{event.x_root + 14}+{event.y_root + 12}")
            return
        self._show_tip(text, event.x_root + 14, event.y_root + 12)
        self._tip_item = item

    def _show_tip(self, text, x, y):
        if self._tip is None or not self._tip.winfo_exists():
            self._tip = tk.Toplevel(self)
            self._tip.wm_overrideredirect(True)
            try:
                self._tip.attributes("-topmost", True)
            except tk.TclError:
                pass
            self._tip_label = tk.Label(self._tip, justify="left", bg="#3a3d40", fg="white",
                                       font=("Segoe UI", 10), padx=8, pady=4, relief="solid", borderwidth=1)
            self._tip_label.pack()
        self._tip_label.configure(text=text)
        self._tip.geometry(f"+{x}+{y}")
        self._tip.deiconify()
        self._tip.lift()

    def _hide_tip(self):
        self._tip_item = None
        if self._tip is not None:
            try:
                self._tip.withdraw()
            except tk.TclError:
                self._tip = None
