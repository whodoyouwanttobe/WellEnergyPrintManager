import customtkinter as ctk
import tkinter as tk
from tkinter import ttk
from core.pdf_utils import is_laser

class ResultView(ctk.CTkFrame):
    def __init__(self, master, on_print_group=None, on_mark_group=None, 
                 on_print_page=None, on_set_copies=None, on_change_format=None):
        super().__init__(master)
        
        self.on_print_group = on_print_group
        self.on_mark_group = on_mark_group
        
        self.on_print_page = on_print_page
        self.on_set_copies = on_set_copies
        self.on_change_format = on_change_format
        
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        
        # Сводка форматов (верхняя часть)
        self.result_text = ctk.CTkTextbox(self, height=120, font=("Consolas", 12))
        self.result_text.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        self.result_text.configure(state="disabled")
        
        # Таблица деталей страниц (нижняя часть)
        self._build_tree()
        
        # Меню форматов (сводка)
        self.format_menu = tk.Menu(self, tearoff=0, bg="#2b2b2b", fg="white", activebackground="#1f538d")
        
        self._right_clicked_fmt = None
        self._right_clicked_pages = None
        self._right_clicked_lbls = None
        self._right_clicked_filepath = None
        
        self.result_text.bind("<Button-3>", self._on_result_text_right_click)
        self.result_text_map = {}


        # Меню страниц (Treeview)
        self.page_menu = tk.Menu(self, tearoff=0, bg="#2b2b2b", fg="white", activebackground="#1f538d")
        self.page_menu.add_command(label="Печать этой страницы", command=self._cmd_print_page)
        self.page_menu.add_command(label="Задать кол-во напечатанных...", command=self._cmd_set_copies)
        self.page_menu.add_command(label="Сбросить напечатанное (0 шт)", command=lambda: self._cmd_set_copies(override=0))
        self.page_menu.add_separator()
        self.page_menu.add_command(label="Изменить формат вручную...", command=self._cmd_change_format_manual)
        for f in ["A4", "A3", "A2", "A1", "A0"]:
            self.page_menu.add_command(label=f"Установить формат {f}", command=lambda fmt=f: self._cmd_change_format(fmt))
            
        self.detail_tree.bind("<Button-3>", self._on_detail_tree_right_click)
        self.detail_tree.bind("<ButtonPress-1>", self._on_left_click_preview)
        self.detail_tree.bind("<ButtonRelease-1>", self._on_left_release_preview)
        self._right_clicked_pn = None
        self._preview_window = None

    def _build_tree(self):
        style = ttk.Style(self)
        style.configure("Detail.Treeview", background="#2b2b2b", foreground="white",
                        rowheight=25, fieldbackground="#2b2b2b", borderwidth=0)
        style.map('Detail.Treeview', background=[('selected', '#1f538d')])
        style.configure("Detail.Treeview.Heading", background="#565b5e", foreground="white", relief="flat")
        
        tree_frame = ctk.CTkFrame(self)
        tree_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)
        
        self.detail_tree = ttk.Treeview(tree_frame, columns=("format", "width", "height", "orientation", "printer", "status"), style="Detail.Treeview")
        self.detail_tree.heading("#0", text="Стр.")
        self.detail_tree.heading("format", text="Формат")
        self.detail_tree.heading("width", text="Ш (мм)")
        self.detail_tree.heading("height", text="В (мм)")
        self.detail_tree.heading("orientation", text="Ориентация")
        self.detail_tree.heading("printer", text="Принтер")
        self.detail_tree.heading("status", text="Статус")
        
        self.detail_tree.column("#0", width=50, anchor=tk.CENTER)
        self.detail_tree.column("format", width=180, anchor=tk.CENTER)
        self.detail_tree.column("width", width=80, anchor=tk.CENTER)
        self.detail_tree.column("height", width=80, anchor=tk.CENTER)
        self.detail_tree.column("orientation", width=100, anchor=tk.CENTER)
        self.detail_tree.column("printer", width=80, anchor=tk.CENTER)
        self.detail_tree.column("status", width=100, anchor=tk.CENTER)
        
        self.detail_tree.grid(row=0, column=0, sticky="nsew")
        
        scrollbar = ctk.CTkScrollbar(tree_frame, command=self.detail_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.detail_tree.configure(yscrollcommand=scrollbar.set)
        
        self.detail_tree.tag_configure("done", foreground="#2ecc71")
        self.detail_tree.tag_configure("warn", foreground="#f39c12")

    def clear(self):
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.configure(state="disabled")
        for item in self.detail_tree.get_children():
            self.detail_tree.delete(item)
        self.result_text_map.clear()
        self._right_clicked_filepath = None

    def _on_left_click_preview(self, event):
        item = self.detail_tree.identify_row(event.y)
        if not item or not self._right_clicked_filepath:
            return
            
        import tkinter as tk
        try:
            # Стр. column is text
            pn_str = self.detail_tree.item(item, "text").split()[0]
            if not pn_str.isdigit():
                return
            pn = int(pn_str)
            
            import fitz
            from PIL import Image, ImageTk
            import os
            
            if not os.path.exists(self._right_clicked_filepath):
                return
                
            doc = fitz.open(self._right_clicked_filepath)
            page = doc.load_page(pn - 1)
            # 0.2 scale makes a ~20% size thumbnail
            pix = page.get_pixmap(matrix=fitz.Matrix(0.3, 0.3))
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            doc.close()
            
            if self._preview_window:
                self._preview_window.destroy()
                
            self._preview_window = tk.Toplevel(self)
            self._preview_window.wm_overrideredirect(True)
            
            # Position near cursor
            x = event.x_root + 15
            y = event.y_root + 15
            self._preview_window.geometry(f"+{x}+{y}")
            self._preview_window.attributes('-topmost', True)
            
            self._preview_photo = ImageTk.PhotoImage(img, master=self)
            lbl = tk.Label(self._preview_window, image=self._preview_photo, bg="black", borderwidth=1, relief="solid")
            lbl.pack()
            
        except Exception as e:
            import logging
            logging.error(f"Preview error: {e}")

    def _on_left_release_preview(self, event):
        if self._preview_window:
            self._preview_window.destroy()
            self._preview_window = None
            self._preview_photo = None

    def display_results(self, filepath, info, grouped_formats, printed_dict, target_copies):
        self.clear()
        self._right_clicked_filepath = filepath
        
        if not info:
            return

        def _get_printed_c(p):
            pd = printed_dict.get(p, 0)
            return pd.get("printed", 0) if isinstance(pd, dict) else pd

        # 1. Заполнение верхней сводки
        self.result_text.configure(state="normal")
        line_idx = 1
        for fmt, pages_str in grouped_formats.items():
            # Находим список страниц для этого формата
            pages_list = [t[0] for t in info if t[1] == fmt]
            lbls_list = [str(t[4]) if len(t) >= 5 else str(t[0]) for t in info if t[1] == fmt]
            has_logical = any(str(p) != str(l) for p, l in zip(pages_list, lbls_list))
            
            self.result_text_map[line_idx] = (fmt, pages_str, pages_list, lbls_list, has_logical)
            dest = "HP" if is_laser(fmt) else "Canon"
            
            # Подсчет напечатанных копий
            done_cnt = sum(min(_get_printed_c(p), target_copies) for p in pages_list)
            total_needed = len(pages_list) * target_copies
            status = "✔ Завершен" if done_cnt >= total_needed and total_needed > 0 else f"{done_cnt}/{total_needed} расп."
            
            from core.pdf_utils import get_complexity_weight
            weight = get_complexity_weight(fmt)
            warning_text = " ⚠ Тяжёлые страницы — будет применена растеризация (ускоренная отправка)" if weight >= 12 else ""
            
            self.result_text.insert("end", f"[{dest}] {fmt}  {pages_str}  ({status}){warning_text}\n")
            line_idx += 1
            
        self.result_text.configure(state="disabled")
        
        # 2. Заполнение таблицы
        from core.pdf_utils import suggest_nearest_larger_format
        import re
        
        for info_tuple in info:
            pn = info_tuple[0]
            fmt = info_tuple[1]
            w = info_tuple[2]
            h = info_tuple[3]
            lbl = info_tuple[4] if len(info_tuple) >= 5 else str(pn)
            land = info_tuple[5] if len(info_tuple) >= 6 else True
            
            # Check if format is non-standard
            display_fmt = fmt
            is_standard = re.match(r"^A[0-4](x\d+)?$", fmt)
            if not is_standard:
                nearest = suggest_nearest_larger_format(w, h)
                if nearest:
                    display_fmt = f"⚠ {fmt} (Ближе к {nearest})"
                else:
                    display_fmt = f"⚠ {fmt}"
            
            o = "Альб." if land else "Книж."
            dest = "HP" if is_laser(fmt) else "Canon"
            
            printed_copies = _get_printed_c(pn)
            tag = "done" if printed_copies >= target_copies else ""
            
            # Extract new tuple fields with safe fallbacks
            is_black = info_tuple[6] if len(info_tuple) >= 7 else False
            warning = " (реком. Foxit)" if is_black and is_laser(fmt) else ""
            if is_black and is_laser(fmt) and tag != "done":
                tag = "warn"
            
            # Show logical label in parentheses next to physical page if they differ
            display_pn = str(pn) if str(pn) == str(lbl) else f"{pn} ({lbl})"
            
            from core.pdf_utils import get_complexity_weight
            weight = get_complexity_weight(fmt)
            complexity_warn = " ⚠ Тяжёлая страница — растеризация при печати" if weight >= 12 else ""
            
            self.detail_tree.insert("", "end", text=display_pn,
                                    values=(f"{display_fmt}{warning}{complexity_warn}", f"{w:.0f}", f"{h:.0f}", o, dest, f"{printed_copies}/{target_copies}"),
                                    tags=(tag,))

    def _on_result_text_right_click(self, event):
        index = self.result_text.index(f"@{event.x},{event.y}")
        line_idx = int(index.split('.')[0])
        if line_idx in self.result_text_map:
            self._right_clicked_fmt, _, self._right_clicked_pages, self._right_clicked_lbls, has_logical = self.result_text_map[line_idx]
            
            self.format_menu.delete(0, "end")
            self.format_menu.add_command(label="Печать группы", command=self._cmd_print_group)
            
            if has_logical:
                self.format_menu.add_command(label="Скопировать страницы (физические)", command=lambda: self._cmd_copy_pages(logical=False))
                self.format_menu.add_command(label="Скопировать страницы (логические)", command=lambda: self._cmd_copy_pages(logical=True))
            else:
                self.format_menu.add_command(label="Скопировать страницы", command=lambda: self._cmd_copy_pages(logical=False))
                
            self.format_menu.add_command(label="Отметить как напечатанную", command=lambda: self._cmd_mark_group(True))
            self.format_menu.add_command(label="Сбросить отметку", command=lambda: self._cmd_mark_group(False))
            
            self.format_menu.post(event.x_root, event.y_root)

    def _cmd_print_group(self):
        if self.on_print_group and self._right_clicked_fmt:
            self.on_print_group(self._right_clicked_filepath, self._right_clicked_fmt, self._right_clicked_pages)
            
    def _cmd_copy_pages(self, logical=False):
        pages = self._right_clicked_lbls if logical else self._right_clicked_pages
        if pages:
            if all(str(p).isdigit() for p in pages):
                pages_int = [int(p) for p in pages]
                pages_str = self._compress_page_ranges(pages_int)
            else:
                pages_str = ", ".join(str(p) for p in pages)
                
            self.clipboard_clear()
            self.clipboard_append(pages_str)
            self.update() # Keep clipboard contents after window loses focus

    @staticmethod
    def _compress_page_ranges(pages):
        """Convert [1,2,3,5,7,8,9] to '1-3, 5, 7-9'"""
        if not pages:
            return ""
        sorted_pages = sorted(set(pages))
        ranges = []
        start = sorted_pages[0]
        end = start
        for p in sorted_pages[1:]:
            if p == end + 1:
                end = p
            else:
                ranges.append(f"{start}-{end}" if start != end else str(start))
                start = p
                end = p
        ranges.append(f"{start}-{end}" if start != end else str(start))
        return ", ".join(ranges)

    def _cmd_mark_group(self, state):
        if self.on_mark_group and self._right_clicked_fmt:
            self.on_mark_group(self._right_clicked_filepath, self._right_clicked_fmt, self._right_clicked_pages, state)

    def _on_detail_tree_right_click(self, event):
        item = self.detail_tree.identify_row(event.y)
        if item:
            self.detail_tree.selection_set(item)
            pn_str = self.detail_tree.item(item, "text").split()[0]
            self._right_clicked_pn = int(pn_str)
            self.page_menu.post(event.x_root, event.y_root)

    def _cmd_print_page(self):
        if self.on_print_page and self._right_clicked_pn:
            self.on_print_page(self._right_clicked_filepath, self._right_clicked_pn)
            
    def _cmd_set_copies(self, override=None):
        if self.on_set_copies and self._right_clicked_pn:
            self.on_set_copies(self._right_clicked_filepath, self._right_clicked_pn, override)
            
    def _cmd_change_format(self, new_fmt):
        if self.on_change_format and self._right_clicked_pn:
            self.on_change_format(self._right_clicked_filepath, self._right_clicked_pn, new_fmt)

    def _cmd_change_format_manual(self):
        import customtkinter as ctk
        if self.on_change_format and self._right_clicked_pn:
            dialog = ctk.CTkInputDialog(text="Введите новый формат (например, A3x2 или B4):", title="Ручной ввод формата")
            val = dialog.get_input()
            if val and val.strip():
                self.on_change_format(self._right_clicked_filepath, self._right_clicked_pn, val.strip())
