import tkinter as tk
import customtkinter as ctk
from tkinter import ttk, messagebox
import sqlite3
import datetime
import os
import fitz
import re
from ui.components.dialog_utils import make_modal

def _cyr_font_kwargs():
    """Шрифт с кириллицей для PyMuPDF. Встроенный 'helv' кириллицу не умеет (печатались '????')."""
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for name in ("arial.ttf", "segoeui.ttf", "tahoma.ttf"):
        path = os.path.join(windir, "Fonts", name)
        if os.path.exists(path):
            return {"fontname": "cyr", "fontfile": path}
    return {"fontname": "helv"}


class ReportDialog(tk.Toplevel):
    def __init__(self, parent, history_obj, current_plan):
        super().__init__(parent)
        self.title("Отчёты и Аналитика")
        self.geometry("800x600")
        self.history = history_obj
        self.current_plan = current_plan
        self.minsize(700, 500)
        self._close = make_modal(self, parent)
        
        self.bg_frame = ctk.CTkFrame(self)
        self.bg_frame.pack(fill="both", expand=True)
        
        # Configure grid
        self.bg_frame.grid_rowconfigure(0, weight=1)
        self.bg_frame.grid_columnconfigure(0, weight=1)
        self.bg_frame.grid_columnconfigure(1, weight=2)
        
        self.dates_list = ctk.CTkScrollableFrame(self.bg_frame)
        self.dates_list.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        
        self.details_frame = ctk.CTkFrame(self.bg_frame)
        self.details_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        
        self.load_data()
        self.setup_ui()
        
    def load_data(self):
        self.transactions = []
        try:
            from core.history import db_connect
            with db_connect(self.history.db_path) as conn:
                c = conn.cursor()
                try:
                    c.execute("SELECT timestamp, filepath, page_number, action, copies, format FROM transactions ORDER BY timestamp DESC")
                    self.transactions = c.fetchall()
                except sqlite3.OperationalError:
                    pass # Table doesn't exist yet
        except Exception as e:
            print(f"Failed to load transactions: {e}")
            
        # Group by date (YYYY-MM-DD)
        self.by_date = {}
        for row in self.transactions:
            ts_str, fp, pn, action, copies, fmt = row
            try:
                dt = datetime.datetime.fromisoformat(ts_str)
                date_str = dt.strftime("%Y-%m-%d")
                if date_str not in self.by_date:
                    self.by_date[date_str] = []
                self.by_date[date_str].append(row)
            except Exception:
                pass
                
        # Estimate time per complexity unit
        self.calc_estimation()
        
    def calc_estimation(self):
        # pair print and fold actions for the same file+page
        events = {}
        for row in self.transactions:
            ts_str, fp, pn, action, copies, fmt = row
            try:
                dt = datetime.datetime.fromisoformat(ts_str)
                key = (fp, pn)
                if key not in events:
                    events[key] = {'print': None, 'fold': None, 'fmt': str(fmt or "").replace(" [V]", "")}
                
                # Keep earliest print and latest fold
                if action == 'print':
                    if not events[key]['print'] or dt < events[key]['print']:
                        events[key]['print'] = dt
                elif action == 'fold':
                    if not events[key]['fold'] or dt > events[key]['fold']:
                        events[key]['fold'] = dt
                        
            except Exception:
                pass
                
        deltas = []
        for key, data in events.items():
            if data['print'] and data['fold']:
                delta = (data['fold'] - data['print']).total_seconds()
                if 0 < delta < 86400: # between 0 and 24 hours
                    from core.pdf_utils import get_complexity_weight
                    weight = get_complexity_weight(data['fmt'])
                    if weight > 0:
                        deltas.append(delta / weight)
                        
        if deltas:
            # Average seconds per unit of complexity
            self.avg_sec_per_unit = sum(deltas) / len(deltas)
        else:
            # Default to 30 seconds per unit if no data
            self.avg_sec_per_unit = 30.0
            
    def setup_ui(self):
        ctk.CTkLabel(self.dates_list, text="Выберите дату:", font=("Segoe UI", 14, "bold")).pack(pady=5)
        
        for date_str in sorted(self.by_date.keys(), reverse=True):
            btn = ctk.CTkButton(self.dates_list, text=date_str, command=lambda d=date_str: self.show_details(d))
            btn.pack(pady=2, fill="x")
            
        self.details_frame.grid_rowconfigure(3, weight=1)
        self.details_frame.grid_columnconfigure(0, weight=1)

        self.lbl_title = ctk.CTkLabel(self.details_frame, text="Детали за день", font=("Segoe UI", 18, "bold"))
        self.lbl_title.grid(row=0, column=0, pady=10, sticky="n")
        
        self.lbl_stats = ctk.CTkLabel(self.details_frame, text="", justify="left", font=("Segoe UI", 14))
        self.lbl_stats.grid(row=1, column=0, pady=10, padx=10, sticky="new")
        
        self.lbl_est = ctk.CTkLabel(self.details_frame, text="", justify="left", font=("Segoe UI", 14), text_color="#2ecc71")
        self.lbl_est.grid(row=2, column=0, pady=10, padx=10, sticky="new")
        
        self.btn_frame = ctk.CTkFrame(self.details_frame, fg_color="transparent")
        self.btn_frame.grid(row=4, column=0, sticky="ew", pady=10)
        
        self.btn_print = ctk.CTkButton(self.btn_frame, text="🖨 Распечатать отчёт", command=self.print_report, state="disabled")
        self.btn_print.pack(pady=5)
        
        self.btn_print_todo = ctk.CTkButton(self.btn_frame, text="📋 Распечатать список на сшивку", command=self.print_todo_list)
        self.btn_print_todo.pack(pady=5)

        self.btn_close = ctk.CTkButton(self.btn_frame, text="Закрыть", fg_color="#e74c3c",
                                       hover_color="#c0392b", command=self._close)
        self.btn_close.pack(pady=(15, 5))
        
        if self.by_date:
            self.show_details(sorted(self.by_date.keys(), reverse=True)[0])
            
    def show_details(self, date_str):
        self.selected_date = date_str
        self.lbl_title.configure(text=f"Отчёт за {date_str}")
        
        rows = self.by_date[date_str]
        
        printed = 0
        folded = 0
        formats_count = {}
        
        for row in rows:
            _, _, _, action, copies, fmt = row
            if action == 'print':
                printed += copies
            elif action == 'fold':
                folded += copies
                
            fmt = str(fmt).upper().replace(" ", "").replace("[V]", "")   # старое «A4 [V]» = A4
            if not fmt: fmt = "UNKNOWN"
            if fmt not in formats_count:
                formats_count[fmt] = {'print': 0, 'fold': 0}
            if action in formats_count[fmt]:
                formats_count[fmt][action] += copies
                
        pct = (folded / printed * 100) if printed > 0 else 100
        
        stats = f"Напечатано листов: {printed}\n"
        stats += f"Сшито листов: {folded}\n"
        stats += f"Прогресс за день: {pct:.1f}%\n\n"
        
        stats += "Разбивка по форматам:\n"
        for f, cnts in sorted(formats_count.items()):
            stats += f"  - {f}: напечатано {cnts['print']}, сшито {cnts['fold']}\n"
            
        self.lbl_stats.configure(text=stats)
        
        # calculate remaining time for current plan
        if self.current_plan:
            remaining_complexity = 0.0
            
            for fp, fn, pgs, inf in self.current_plan.get("laser", []):
                missing = inf["missing"]
                if missing > 0:
                    from core.pdf_utils import get_complexity_weight
                    w = get_complexity_weight(inf["format"])
                    remaining_complexity += w * missing * len(pgs)
                    
            for fp, fn, pgs, inf in self.current_plan.get("plotter", []):
                missing = inf["missing"]
                if missing > 0:
                    from core.pdf_utils import get_complexity_weight
                    w = get_complexity_weight(inf["format"])
                    remaining_complexity += w * missing * len(pgs)
                    
            sec_left = remaining_complexity * self.avg_sec_per_unit
            hours = int(sec_left // 3600)
            minutes = int((sec_left % 3600) // 60)
            
            self.lbl_est.configure(text=f"Ориентировочное время до завершения текущего плана:\n"
                                        f"~ {hours} ч. {minutes} мин.\n"
                                        f"(основано на средней скорости работы пользователя)")
        else:
            self.lbl_est.configure(text="")
            
        self.btn_print.configure(state="normal")
        
    def print_report(self):
        # Generate A4 PDF using PyMuPDF
        import tempfile
        doc = fitz.open()
        page = doc.new_page(width=595, height=842) # A4
        
        p = fitz.Point(50, 50)
        
        # Add basic text
        text = f"ОТЧЕТ ЗА {self.selected_date}\n\n"
        text += self.lbl_stats.cget("text")
        text += "\n" + self.lbl_est.cget("text")
        
        page.insert_textbox(fitz.Rect(50, 50, 545, 792), text, fontsize=12, **_cyr_font_kwargs())
            
        fd, temp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        doc.save(temp_path)
        doc.close()
        
        self._send_in_background(temp_path, "Отчёт", use_engine=False)

    def print_todo_list(self):
        import tempfile
        import datetime
        
        try:
            tc = int(self.master.target_copies.get())
        except:
            tc = 1
            
        todo_entries = []
        
        if hasattr(self.master, "analyzed_data") and hasattr(self.master, "printed_pages"):
            for fp, data in self.master.analyzed_data.items():
                pc = len(data["pages"])
                done = self.master.printed_pages.get(fp, {})
                done_cnt = sum(min(done.get(info[0], {}).get("printed", 0), tc) for info in data["pages"])
                total_needed = pc * tc
                
                # "Завершена печать" means printed but not fully folded
                if pc > 0 and done_cnt >= total_needed:
                    filename = os.path.basename(fp)
                    
                    # Group pages by format
                    fmt_pages = {}
                    for info in data["pages"]:
                        pn = info[0]
                        fmt = info[1] if len(info) > 1 else "?"
                        if fmt not in fmt_pages:
                            fmt_pages[fmt] = []
                        fmt_pages[fmt].append(pn)
                    
                    todo_entries.append({
                        "filename": filename,
                        "total_pages": pc,
                        "copies": tc,
                        "formats": fmt_pages
                    })
                    
        doc = fitz.open()
        page = doc.new_page(width=595, height=842) # A4
        
        y = 50
        
        # Title
        title = f"СПИСОК НА СШИВКУ ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M')})"
        page.insert_text(fitz.Point(50, y), title, fontsize=14, **_cyr_font_kwargs())
        y += 30
        
        if todo_entries:
            for entry in todo_entries:
                # File name header
                header = f"{entry['filename']}  |  {entry['total_pages']} стр. x {entry['copies']} компл."
                page.insert_text(fitz.Point(50, y), header, fontsize=11, **_cyr_font_kwargs())
                y += 18
                
                # Formats and pages
                for fmt, pages_list in entry["formats"].items():
                    pages_str = self._compress_page_ranges(pages_list)
                    line = f"    {fmt}: стр. {pages_str}"
                    page.insert_text(fitz.Point(50, y), line, fontsize=10, **_cyr_font_kwargs())
                    y += 15
                    
                y += 8  # spacing between files
                
                # Check if we need a new page
                if y > 780:
                    page = doc.new_page(width=595, height=842)
                    y = 50
        else:
            msg = "Все распечатанные файлы уже сшиты!"
            page.insert_text(fitz.Point(50, y), msg, fontsize=12, **_cyr_font_kwargs())
            
        fd, temp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        doc.save(temp_path)
        doc.close()
        
        self._send_in_background(temp_path, "Список на сшивку", use_engine=True)

    def _send_in_background(self, temp_path, label, use_engine):
        """Отправка на принтер в фоне: Foxit/PDFtoPrinter могут работать десятки секунд,
        а раньше это блокировало интерфейс и окно «не закрывалось»."""
        import threading, subprocess, sys
        laser_printer = self.master.laser_printer_var.get()
        engine = self.master._get_engine() if use_engine else None
        ui_call = getattr(self.master, "ui_call", self.after_idle)

        def notify(kind, text):
            def _show():
                parent = self if self.winfo_exists() else self.master
                getattr(messagebox, kind)("Печать", text, parent=parent)
            ui_call(_show)

        def worker():
            try:
                ok = False
                if engine is not None:
                    ok = engine.print_file(temp_path, laser_printer, "", 1)
                else:
                    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
                    p2p_path = os.path.join(base, "tools", "PDFtoPrinter.exe")
                    if os.path.exists(p2p_path):
                        subprocess.run([p2p_path, temp_path, laser_printer],
                                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=120)
                        ok = True
                if ok:
                    notify("showinfo", f"{label} отправлен(о) на принтер: {laser_printer}")
                else:
                    import win32api
                    win32api.ShellExecute(0, "print", temp_path, None, ".", 0)
                    notify("showinfo", f"{label} отправлен(о) на принтер по умолчанию.")
            except Exception as e:
                notify("showerror", f"Не удалось распечатать: {e}")

        threading.Thread(target=worker, daemon=True).start()

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
