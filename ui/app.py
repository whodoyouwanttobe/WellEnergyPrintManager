import customtkinter as ctk
from tkinter import filedialog, messagebox, simpledialog
import os
import threading
import queue
import logging
import traceback

from core.config import get_config, get_base_path
from core.version import APP_VERSION, APP_TITLE
from ui.components.dialog_utils import open_dialog
from core.analyzer import ScanJob
from core.analysis_cache import AnalysisCache
from core.timefmt import short_time, long_time
from core.print_time import estimate_plan, fmt_duration
from core.history import PrintHistory
from core.pdf_utils import build_print_plan
from ui.themes import apply_theme
from ui.components.file_tree import FileTreeView
from ui.components.result_view import ResultView
from ui.components.settings_dialog import SettingsDialog
from printing.queue_manager import PrintQueueManager
from printing.sumatra_engine import SumatraEngine
from printing.foxit_engine import FoxitEngine

class _CopiesVar(ctk.StringVar):
    """Поле «Копий». IntVar падал с TclError, когда поле временно пустое (пользователь стёр цифру)."""
    def get(self):
        try:
            return max(1, int(str(super().get()).strip()))
        except (ValueError, TypeError):
            return 1


class PrintManagerApp(ctk.CTk):
    def __init__(self, splash=None):
        super().__init__()
        # ГЛАВНАЯ ПРИЧИНА ПУСТЫХ ОКОН: заставка (main.py) — это отдельный tk.Tk(), созданный
        # раньше, поэтому tkinter считал «корнем по умолчанию» именно её. После splash.destroy()
        # корня по умолчанию не остаётся, и CustomTkinter не может создать шрифт (CTkFont) в
        # новых окнах: RuntimeError "Too early to use font: no default root window".
        # Окно уже на экране, а содержимое не создано -> пустая рамка. Делаем корнем приложение.
        import tkinter as _tk
        _tk._default_root = self
        # Очередь вызовов из фоновых потоков в главный поток Tk (Tk не потокобезопасен)
        self._ui_queue = queue.Queue()
        self.after(25, self._drain_ui_queue)
        self.splash = splash
        if self.splash:
            self.withdraw()
            
        self.cfg = get_config()
        apply_theme(self.cfg.get("theme", "dark"))
        
        self.title(APP_TITLE)
        
        # Set AppUserModelID so Windows 10/11 uses the proper taskbar icon and doesn't group it with python.exe or old cache
        # Changed to 3.5.1 to force Windows to bust its taskbar icon cache
        try:
            import ctypes
            myappid = f'wellenergy.printmanager.{APP_VERSION}'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass

        try:
            # When running from PyInstaller, _MEIPASS holds the bundled assets
            import sys
            base = getattr(sys, '_MEIPASS', get_base_path())
            icon_path = os.path.join(base, "ui", "assets", "icon.ico")
            
            # Set .ico for taskbar and window frame
            # (We rebuilt icon.ico with 256x256 as the FIRST image so Tkinter doesn't grab a blurry 16x16 one)
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception as e:
            print("Could not load icon:", e)
            
        # Окно не больше экрана (на ноутбуке с масштабом 150% 750 px могли не влезать)
        try:
            sh = self.winfo_screenheight()
            sw = self.winfo_screenwidth()
            self.geometry(f"{min(1100, sw - 40)}x{min(750, sh - 80)}")
        except Exception:
            self.geometry("1100x750")
        self.minsize(900, 560)
        
        self._scan_job = None
        self._scan_id = 0
        self._copies_after_id = None
        try:
            self.analysis_cache = AnalysisCache()
        except Exception:
            self.analysis_cache = None
        self.history = PrintHistory(self.cfg.get("db_path", "print_history.db"))
        self.queue_manager = PrintQueueManager()
        # Колбэки вызываются из потоков печати -> переносим их в главный поток
        self.queue_manager.on_job_start = lambda *a: self.ui_call(self.on_print_start, *a)
        self.queue_manager.on_job_complete = lambda *a: self.ui_call(self.on_print_complete, *a)
        self.queue_manager.on_job_error = lambda *a: self.ui_call(self.on_print_error, *a)
        self.queue_manager.on_printer_alert = self._on_printer_alert
        self.queue_manager.on_pause_request = self._on_pause_request
        self.queue_manager.start()
        
        self.current_folder = self.cfg.get("last_folder", "")
        self.analyzed_data = {}
        self.printed_pages = {}
        self.current_plan = None
        self.target_copies = _CopiesVar(master=self, value=str(self.cfg.get("last_copies", 1)))
        
        self.total_print_jobs = 0
        self.completed_print_jobs = 0
        self.print_start_time = 0
        
        self._file_times = {}   # печать в текущем сеансе: {fp: {"start": datetime, "end": datetime}}
        self._db_times = {}     # время из базы: {fp: {"print_first", "print_last", "fold_last"}}
        
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._start_printer_monitoring()
        self._check_updates_startup()
        
        # Главное окно показываем СРАЗУ, а анализ идёт уже внутри него с прогрессом
        # и кнопкой «Отменить». Раньше окно было скрыто за заставкой до конца анализа,
        # и при выборе огромной/не той папки приходилось ждать.
        self._close_splash()
        if self.current_folder and os.path.isdir(self.current_folder):
            self.after(300, self.start_analysis)
        elif self.current_folder:
            self.status_label.configure(text=f"Папка недоступна: {self.current_folder}")

    def _close_splash(self):
        if self.splash:
            try:
                self.splash.destroy()
            except Exception:
                pass
            self.splash = None
        self.deiconify()
        self.lift()
    # ───────────── потокобезопасность и ошибки ─────────────
    def ui_call(self, fn, *args):
        """Можно вызывать из любого потока: fn(*args) выполнится в главном потоке Tk."""
        self._ui_queue.put((fn, args))

    def _drain_ui_queue(self):
        try:
            for _ in range(200):
                fn, args = self._ui_queue.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    logging.error("Ошибка в UI-колбэке:\n%s", traceback.format_exc())
        except queue.Empty:
            pass
        try:
            self.after(25, self._drain_ui_queue)
        except Exception:
            pass  # окно уже уничтожено

    def report_callback_exception(self, exc, val, tb):
        """Любая ошибка в обработчике кнопки/события: в лог + сообщение, а не тишина."""
        text = "".join(traceback.format_exception(exc, val, tb))
        logging.error("Необработанная ошибка в интерфейсе:\n%s", text)
        # Предлагаем отправить отчёт — один раз на каждую уникальную ошибку за сеанс
        frame = traceback.extract_tb(tb)[-1] if tb else None
        signature = (exc.__name__, frame.filename if frame else "", frame.lineno if frame else 0)
        if not hasattr(self, "_reported_errors"):
            self._reported_errors = set()
        try:
            if signature in self._reported_errors:
                return
            self._reported_errors.add(signature)
            if messagebox.askyesno("Ошибка", f"{exc.__name__}: {val}\n\nПодробности записаны в журнал.\n\n"
                                   "Отправить отчёт разработчику?", parent=self):
                self._send_crash_report(f"{exc.__name__}: {val}", text)
        except Exception:
            pass

    def _send_crash_report(self, summary, tb_text):
        from core.bug_reporter import build_report, deliver
        report = build_report(summary, current_file=getattr(self, "current_selected_file", None),
                              traceback_text=tb_text, kind="crash")

        def work():
            status, msg = deliver(report)
            short = {"github": "Отчёт об ошибке отправлен", "telegram": "Отчёт отправлен в Telegram",
                     "saved": "Отчёт сохранён, отправится позже"}[status]
            self.ui_call(lambda: self.status_label.configure(text=short))
        threading.Thread(target=work, daemon=True).start()

    def _flush_reports_later(self):
        def work():
            try:
                from core.bug_reporter import flush_outbox
                n = flush_outbox()
                if n:
                    self.ui_call(lambda: self.status_label.configure(text=f"Отправлено сохранённых отчётов: {n}"))
            except Exception as e:
                logging.info(f"flush_outbox: {e}")
        threading.Thread(target=work, daemon=True).start()

    # ───────────── обновления ─────────────
    def _check_updates_startup(self):
        # Через 5 с после запуска, чтобы не мешать загрузке
        self.after(5000, lambda: self.check_updates(manual=False))
        self.after(8000, self._flush_reports_later)  # отчёты, не отправленные в прошлый раз

    def check_updates(self, manual=False, parent=None):
        from core.updater import check_for_updates
        parent = parent or self

        def on_none():
            if manual:
                self.ui_call(lambda: messagebox.showinfo("Обновления", "У вас последняя версия для выбранного канала.", parent=parent))

        def on_error(msg):
            if manual:
                self.ui_call(lambda: messagebox.showerror("Обновления", f"Не удалось проверить обновления:\n{msg}", parent=parent))

        check_for_updates(lambda info: self.ui_call(self._offer_update, info, parent),
                          manual=manual, on_none=on_none, on_error=on_error)

    def _offer_update(self, info, parent=None):
        from core.version import FULL_VERSION
        busy = self.total_print_jobs > 0 and self.completed_print_jobs < self.total_print_jobs
        text = (f"Доступна версия {info['version']} (канал: {info['channel']}).\n"
                f"Сейчас установлена: {FULL_VERSION}.\n\nИзменения:\n{info['changelog']}\n\n")
        if busy:
            text += "Идёт печать — обновление лучше установить после её окончания.\n\n"
        if not messagebox.askyesno("Доступно обновление", text + "Скачать и установить сейчас?", parent=parent or self):
            return
        self.status_label.configure(text="Загрузка обновления... 0%", text_color="#3498db")

        def progress(frac):
            self.ui_call(lambda: self.status_label.configure(text=f"Загрузка обновления... {int(frac * 100)}%"))

        def worker():
            from core.updater import download_update
            try:
                new_exe = download_update(info, progress)
                self.ui_call(self._install_downloaded, new_exe)
            except Exception as e:
                logging.error(f"Загрузка обновления: {e}")
                self.ui_call(lambda: (self.status_label.configure(text="Обновление не загружено", text_color="red"),
                                      messagebox.showerror("Обновление", str(e), parent=self)))

        threading.Thread(target=worker, daemon=True).start()

    def _install_downloaded(self, new_exe):
        from core.updater import install_update
        if not messagebox.askyesno("Обновление загружено", "Перезапустить программу и установить обновление?", parent=self):
            self.status_label.configure(text="Установка обновления отложена")
            return
        try:
            install_update(new_exe)
        except Exception as e:
            messagebox.showinfo("Обновление", str(e), parent=self)
            return
        self._on_close()

    def _on_close(self):
        """Корректное закрытие: останавливаем анализ (процессы-работники) и очередь печати."""
        try:
            if self._scan_job is not None:
                self._scan_job.cancel()
            self.queue_manager.stop()
        except Exception:
            pass
        self.destroy()

    def _open_bug_report(self):
        from ui.components.bug_report import BugReportDialog
        file = getattr(self, "current_selected_file", None)
        open_dialog(self, "_bug_dlg", lambda: BugReportDialog(self, current_file=file))
        
    def _start_printer_monitoring(self):
        def monitor():
            import time
            import win32print
            
            while True:
                laser = getattr(self, '_current_laser', "Default Printer")
                plotter = getattr(self, '_current_plotter', "Default Printer")
                
                for pname in [laser, plotter]:
                    if not pname or pname == "Default Printer" or pname == "Загрузка...":
                        continue
                        
                    is_laser = (pname == laser)
                    try:
                        h = win32print.OpenPrinter(pname)
                        try:
                            info = win32print.GetPrinter(h, 2)
                            st = info.get('Status', 0)
                            jobs = info.get('cJobs', 0)
                            
                            color, text = "🟢", "Готов"
                            if st & 0x00000080:  # Offline (128)
                                color, text = "⚪", "Отключен"
                            elif st & 0x00000002:  # Error (2)
                                color, text = "🔴", "Ошибка"
                            elif st & 0x00000010:  # Out of paper (16)
                                color, text = "🟡", "Нет бумаги"
                            elif st & 0x00000008:  # Paper jam (8)
                                color, text = "🔴", "Замятие"
                            elif st & 0x00000001:  # Paused (1)
                                color, text = "🟡", "Пауза"
                            elif st & 0x00000400:  # Printing (1024)
                                color, text = "🔵", "Печать"
                            elif jobs > 0:
                                color, text = "🔵", f"Очередь: {jobs}"
                                
                            def update_ui(is_l=is_laser, c=color, t=text):
                                lbl = self.laser_status_lbl if is_l else self.plotter_status_lbl
                                try:
                                    lbl.configure(text=f"{c} {t}", require_redraw=True)
                                except:
                                    pass
                            self.ui_call(update_ui)
                            
                        finally:
                            win32print.ClosePrinter(h)
                    except Exception as e:
                        def set_error(is_l=is_laser):
                            lbl = self.laser_status_lbl if is_l else self.plotter_status_lbl
                            try:
                                lbl.configure(text="⚪ Ошибка", require_redraw=True)
                            except:
                                pass
                        self.ui_call(set_error)
                        
                time.sleep(2)
                
        import threading
        threading.Thread(target=monitor, daemon=True).start()
    def _get_system_printers(self):
        # Раньше здесь запускался внешний 'python -c ...' — в собранном exe на чужом ПК
        # python может отсутствовать, а Windows ищет python.exe в т.ч. в текущей папке
        # (подмена исполняемого файла). Вызываем win32print напрямую — мы и так в фоновом потоке.
        try:
            import win32print
            flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            printers = [p[2] for p in win32print.EnumPrinters(flags)]
            return printers if printers else ["Default Printer"]
        except Exception as e:
            logging.warning(f"EnumPrinters failed: {e}")
            return ["Default Printer"]

    def _build_ui(self):
        # Top toolbar
        toolbar = ctk.CTkFrame(self)
        toolbar.pack(fill="x", padx=10, pady=5)
        
        btn_open = ctk.CTkButton(toolbar, text="Открыть папку", command=self.open_folder)
        btn_open.pack(side="left", padx=5, pady=5)
        
        ctk.CTkLabel(toolbar, text="Копий:").pack(side="left", padx=(20, 5), pady=5)
        copies_entry = ctk.CTkEntry(toolbar, textvariable=self.target_copies, width=50)
        copies_entry.pack(side="left", padx=5, pady=5)
        copies_entry.bind("<KeyRelease>", self._on_copies_changed)
        
        btn_bug = ctk.CTkButton(toolbar, text="Сообщить об ошибке", fg_color="#b71c1c", hover_color="#d32f2f", command=self._open_bug_report)
        btn_bug.pack(side="left", padx=(20, 5), pady=5)
        
        btn_settings = ctk.CTkButton(toolbar, text="Настройки", command=self.open_settings)
        btn_settings.pack(side="right", padx=5, pady=5)
        
        self.laser_printer_var = ctk.StringVar(master=self, value=self.cfg.get("printers", {}).get("laser", {}).get("name", "Default Printer"))
        self.plotter_printer_var = ctk.StringVar(master=self, value=self.cfg.get("printers", {}).get("plotter", {}).get("name", "Default Printer"))
        printers = ["Загрузка..."]
        
        def load_printers():
            real_printers = self._get_system_printers()
            def update_ui():
                if self.laser_printer_var.get() not in real_printers: self.laser_printer_var.set(real_printers[0])
                if self.plotter_printer_var.get() not in real_printers: self.plotter_printer_var.set(real_printers[0])
                try:
                    self.laser_combo.configure(values=real_printers)
                    self.plotter_combo.configure(values=real_printers)
                except AttributeError:
                    pass
            self.ui_call(update_ui)
            
        import threading
        threading.Thread(target=load_printers, daemon=True).start()
        
        self._current_laser = self.laser_printer_var.get()
        self._current_plotter = self.plotter_printer_var.get()
        
        # When packing side="right", elements are added from right to left.
        # Order we want (L to R): Принтер: [combo] | Плоттер: [combo] | [Настройки]
        # Order to pack: [Настройки] then [plotter combo] then [Плоттер:] then [laser combo] then [Принтер:]
        
        self.plotter_status_lbl = ctk.CTkLabel(toolbar, text="⚪", width=20)
        self.plotter_status_lbl.pack(side="right", padx=(0, 5))
        
        def _on_plotter_changed(v):
            self._current_plotter = v
            printers_cfg = self.cfg.get("printers", {})
            if "plotter" not in printers_cfg: printers_cfg["plotter"] = {}
            printers_cfg["plotter"]["name"] = v
            self.cfg.set("printers", printers_cfg)
            self._update_plan_and_ui()  # пересчитать время под другую модель
            
        self.plotter_combo = ctk.CTkOptionMenu(toolbar, variable=self.plotter_printer_var, values=printers, width=130, command=_on_plotter_changed)
        self.plotter_combo.pack(side="right", padx=(5, 0), pady=5)
        ctk.CTkLabel(toolbar, text="Плоттер:").pack(side="right", padx=(20, 5), pady=5)
        
        self.laser_status_lbl = ctk.CTkLabel(toolbar, text="⚪", width=20)
        self.laser_status_lbl.pack(side="right", padx=(0, 5))
        
        def _on_laser_changed(v):
            self._current_laser = v
            printers_cfg = self.cfg.get("printers", {})
            if "laser" not in printers_cfg: printers_cfg["laser"] = {}
            printers_cfg["laser"]["name"] = v
            self.cfg.set("printers", printers_cfg)
            self._update_plan_and_ui()  # пересчитать время под другую модель
            
        self.laser_combo = ctk.CTkOptionMenu(toolbar, variable=self.laser_printer_var, values=printers, width=130, command=_on_laser_changed)
        self.laser_combo.pack(side="right", padx=(5, 0), pady=5)
        ctk.CTkLabel(toolbar, text="Принтер:").pack(side="right", padx=(20, 5), pady=5)
        # Main split view
        paned = ctk.CTkFrame(self)
        paned.pack(fill="both", expand=True, padx=10, pady=5)
        paned.grid_rowconfigure(0, weight=1)
        paned.grid_columnconfigure(0, weight=1)
        paned.grid_columnconfigure(1, weight=2)
        
        # Left side - files
        left_frame = ctk.CTkFrame(paned)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        
        ctk.CTkLabel(left_frame, text="Файлы:", font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=5, pady=5)
        self.file_tree = FileTreeView(left_frame, 
                                      on_file_select=self.on_file_selected,
                                      on_open_file=self.open_file_externally,
                                      on_mark_printed=self.mark_file_printed,
                                      on_mark_folded=self.mark_file_folded,
                                      on_print_file=self.print_single_file,
                                      on_preview_file=self.preview_single_file,
                                      on_set_copies=self.set_file_copies)
        self.file_tree.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Right side - results
        right_frame = ctk.CTkFrame(paned)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        
        ctk.CTkLabel(right_frame, text="Страницы:", font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=5, pady=5)
        self.result_view = ResultView(right_frame, 
                                      on_print_group=self.print_format_group,
                                      on_mark_group=self.mark_format_group,
                                      on_print_page=self.print_single_page,
                                      on_set_copies=self.set_page_copies,
                                      on_change_format=self.change_page_format)
        self.result_view.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Bottom pane for print plan
        bottom_pane = ctk.CTkFrame(self, height=120)
        bottom_pane.pack(fill="x", padx=10, pady=5)
        bottom_pane.grid_rowconfigure(0, weight=1)
        bottom_pane.grid_columnconfigure(0, weight=1)
        bottom_pane.grid_columnconfigure(1, weight=1)
        
        # Load watermark image and make it faint
        watermark_path = os.path.join(os.path.dirname(__file__), "assets", "watermark.png")
        try:
            from PIL import Image
            wm_img = Image.open(watermark_path).convert("RGBA")
            # Faint transparency (alpha=15 out of 255)
            wm_img.putalpha(wm_img.split()[3].point(lambda p: p * 0.06))
            self.wm_ctk = ctk.CTkImage(wm_img, size=(120, 120))
            wm_label = ctk.CTkLabel(bottom_pane, text="", image=self.wm_ctk)
            wm_label.place(relx=0.5, rely=0.5, anchor="center")
        except Exception as e:
            print("Watermark load error:", e)
        
        self.hp_plan = ctk.CTkTextbox(bottom_pane, height=110, font=("Consolas", 12), fg_color="transparent", bg_color="transparent")
        self.hp_plan.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.cn_plan = ctk.CTkTextbox(bottom_pane, height=110, font=("Consolas", 12), fg_color="transparent", bg_color="transparent")
        self.cn_plan.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        
        # Bottom bar - print
        bottom_bar = ctk.CTkFrame(self)
        bottom_bar.pack(fill="x", padx=10, pady=5)
        
        bottom_bar.columnconfigure(0, weight=1)
        
        # Row 0: Status + Stats
        stats_frame = ctk.CTkFrame(bottom_bar, fg_color="transparent")
        stats_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        
        self.status_label = ctk.CTkLabel(stats_frame, text="Готов", text_color="#f39c12", font=("Segoe UI", 12, "bold"))
        self.status_label.pack(side="left", padx=(0, 10))
        
        # Прогресс анализа папки + отмена (видно только во время анализа)
        self.scan_frame = ctk.CTkFrame(stats_frame, fg_color="transparent")
        self.scan_bar = ctk.CTkProgressBar(self.scan_frame, mode="indeterminate", width=180, height=10)
        self.scan_bar.pack(side="left", padx=(0, 8))
        self.btn_cancel_scan = ctk.CTkButton(self.scan_frame, text="✖ Отменить анализ", width=130, height=24,
                                             fg_color="#7f8c8d", hover_color="#95a5a6", command=self.cancel_analysis)
        self.btn_cancel_scan.pack(side="left")
        
        self.stats_label = ctk.CTkLabel(stats_frame, text="", font=("Segoe UI", 12, "bold"))
        self.stats_label.pack(side="left")
        
        self.time_label = ctk.CTkLabel(stats_frame, text="")
        self.time_label.pack(side="right")

        # Ориентировочное время всего плана (обновляется при изменении копий, принтера, отметок)
        self.estimate_label = ctk.CTkLabel(bottom_bar, text="", text_color="#5dade2",
                                           font=("Segoe UI", 12, "bold"), anchor="w")
        self.estimate_label.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 4))
        
        # Row 1: Progress Bars
        self.progress_container = ctk.CTkFrame(bottom_bar, fg_color="transparent")
        self.progress_container.grid(row=1, column=0, sticky="ew", padx=10, pady=(5, 5))
        self.progress_container.columnconfigure(0, weight=1)
        
        self.overall_bar = ctk.CTkProgressBar(self.progress_container, height=12)
        self.overall_bar.grid(row=0, column=0, sticky="ew", pady=2)
        self.overall_bar.set(0)
        
        self.printer_bars = {} # {printer_name: (label, bar)}
        
        # Row 2: Buttons
        btn_frame = ctk.CTkFrame(bottom_bar, fg_color="transparent")
        btn_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        
        self.btn_report = ctk.CTkButton(btn_frame, text="📊 Отчёт", fg_color="#3498db", hover_color="#2980b9", width=80, command=self.show_report)
        self.btn_report.pack(side="left", padx=5)
        
        right_btns = ctk.CTkFrame(btn_frame, fg_color="transparent")
        right_btns.pack(side="right")
        
        self.btn_trial = ctk.CTkButton(right_btns, text="Пробная печать (Просмотр)", fg_color="#f39c12", hover_color="#e67e22", command=self.show_trial_print)
        self.btn_trial.pack(side="left", padx=5)
        
        self.btn_print = ctk.CTkButton(right_btns, text="Печать всего плана", fg_color="#2ecc71", hover_color="#27ae60", command=self.print_all)
        self.btn_print.pack(side="left", padx=5)

        # Порядок упаковки важен: Tk отдаёт место тем, кто упакован РАНЬШЕ. Раньше нижняя
        # панель с кнопками упаковывалась последней и на маленьком экране/при масштабе 125–150%
        # просто обрезалась. Теперь низ закреплён, а сжимается средняя часть со списками.
        for w in (paned, bottom_pane, bottom_bar):
            w.pack_forget()
        bottom_bar.pack(side="bottom", fill="x", padx=10, pady=5)
        bottom_pane.pack(side="bottom", fill="x", padx=10, pady=5)
        paned.pack(fill="both", expand=True, padx=10, pady=5)
        
    def open_folder(self):
        # Выбор папки доступен и ВО ВРЕМЯ анализа: старый анализ отменяется.
        folder = filedialog.askdirectory(initialdir=self.current_folder or None, parent=self)
        if folder:
            self.current_folder = folder
            self.cfg.set("last_folder", folder)
            self.start_analysis()

    def _on_copies_changed(self, event=None):
        # Пересчёт плана — не на каждое нажатие клавиши, а через 400 мс после последнего
        if self._copies_after_id:
            self.after_cancel(self._copies_after_id)
        self._copies_after_id = self.after(400, self._apply_copies)

    def _apply_copies(self):
        self._copies_after_id = None
        self.cfg.set("last_copies", self.target_copies.get())
        self._update_plan_and_ui()

    # ───────────── анализ папки ─────────────
    def start_analysis(self):
        if self._scan_job is not None:
            self._scan_job.cancel()
        self._scan_id += 1
        scan_id = self._scan_id
        folder = self.current_folder

        self.file_tree.clear()
        self.file_tree.set_base_dir(folder)
        self.result_view.clear()
        self.analyzed_data = {}
        self.current_plan = None
        for box in (self.hp_plan, self.cn_plan):
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.configure(state="disabled")
        self.status_label.configure(text=f"Поиск PDF в «{os.path.basename(folder) or folder}»...", text_color="#f39c12")
        self.scan_frame.pack(side="left", padx=(0, 10), after=self.status_label)
        self.scan_bar.configure(mode="indeterminate")
        self.scan_bar.start()
        self.btn_print.configure(state="disabled")
        self.btn_trial.configure(state="disabled")
        self._scan_pending = []
        self._scan_last_ui = 0.0

        def on_progress(phase, done, total, fp):
            if scan_id != self._scan_id:
                return
            if phase == "analyze":
                self._scan_pending.append(fp)
            import time
            now = time.monotonic()
            if phase == "found" or now - self._scan_last_ui > 0.15 or (total and done == total):
                self._scan_last_ui = now
                batch, self._scan_pending = self._scan_pending, []
                self.ui_call(self._on_scan_progress, scan_id, phase, done, total, batch)

        def on_done(results, errors, total):
            printed = self.history.get_all_printed(folder)  # чтение БД тоже в фоне
            times = self.history.get_times(folder)
            self.ui_call(self._on_scan_complete, scan_id, results, errors, total, printed, times)

        def on_error(msg):
            self.ui_call(self._on_scan_failed, scan_id, msg)

        self._scan_job = ScanJob(folder, self.cfg.get("include_subfolders", False),
                                 on_progress, on_done, on_error, cache=self.analysis_cache).start()

    def cancel_analysis(self):
        if self._scan_job is not None:
            self._scan_job.cancel()
            self._scan_job = None
        self._scan_id += 1  # игнорируем всё, что ещё придёт от старого анализа
        self._finish_scan_ui()
        self.status_label.configure(text="Анализ отменён. Выберите папку заново.", text_color="#e67e22")

    def _finish_scan_ui(self):
        self.scan_bar.stop()
        self.scan_frame.pack_forget()
        self.btn_print.configure(state="normal")
        self.btn_trial.configure(state="normal")

    def _on_scan_progress(self, scan_id, phase, done, total, batch):
        if scan_id != self._scan_id:
            return
        if phase == "search":
            self.status_label.configure(text=f"Поиск PDF... найдено {done}")
            return
        if phase == "found":
            self.status_label.configure(text=f"Найдено PDF: {total}. Запуск анализа..." if total else "В папке нет PDF")
            return
        for fp in batch:
            self.file_tree.add_file(fp, "⏳ проанализирован")
        if total:
            if str(self.scan_bar.cget("mode")) != "determinate":
                self.scan_bar.stop()
                self.scan_bar.configure(mode="determinate")
            self.scan_bar.set(done / total)
            name = os.path.basename(batch[-1]) if batch else ""
            self.status_label.configure(text=f"Анализ {done}/{total}  {name[:40]}")

    def _on_scan_failed(self, scan_id, msg):
        if scan_id != self._scan_id:
            return
        self._scan_job = None
        self._finish_scan_ui()
        self.status_label.configure(text=f"Ошибка анализа: {msg}", text_color="red")

    def _on_scan_complete(self, scan_id, results, errors, total, printed, times=None):
        if scan_id != self._scan_id:
            return  # результат устаревшего (отменённого) анализа
        self._scan_job = None
        self._finish_scan_ui()
        self.analyzed_data = results
        self.printed_pages = printed
        self._db_times = times or {}
        # файлы, которые не попали в дерево через прогресс (например, все из кэша одной пачкой)
        for fp in results:
            if not self.file_tree.has_file(fp):
                self.file_tree.add_file(fp, "⏳")
        total_pages = sum(len(data["pages"]) for data in results.values())
        text = f"Готово. Файлов: {len(results)} | Страниц: {total_pages}"
        if errors:
            text += f" | Не прочитано: {len(errors)}"
            logging.warning("Не удалось проанализировать: %s", errors)
        self.status_label.configure(text=text, text_color="#f39c12")
        self._update_plan_and_ui()

    def _update_plan_and_ui(self):
        try: tc = self.target_copies.get()
        except: tc = 1
        
        # Prepare files_analysis for build_print_plan
        files_analysis = {fp: data["pages"] for fp, data in self.analyzed_data.items()}
        self.current_plan = build_print_plan(files_analysis, self.printed_pages, tc)
        
        # Ориентировочное время печати (по форматам, копиям и модели принтера/плоттера)
        page_sizes = {(fp, info[0]): (info[2], info[3])
                      for fp, data in self.analyzed_data.items() for info in data["pages"]}
        est = estimate_plan(self.current_plan, page_sizes, self.laser_printer_var.get(),
                            self.plotter_printer_var.get(), self.cfg.get("speed_profiles"))
        self.plan_estimate = est
        entry_time = {(kind, i): sec for kind, i, sec in est["per_entry"]}

        # Update text panes
        self.hp_plan.configure(state="normal")
        self.hp_plan.delete("1.0", "end")
        for i, (fp, fn, pgs, inf) in enumerate(self.current_plan["laser"]):
            o = "альб." if inf["landscape"] else "книж."
            t = entry_time.get(("laser", i))
            self.hp_plan.insert("end", f"{fn}: стр. {inf['pages_str']} ({inf['format']}, {o}) x{inf['missing']}"
                                       + (f"  ≈{fmt_duration(t)}" if t else "") + "\n")
        self.hp_plan.insert("end", f"\nИтого HP: {self.current_plan['stats']['laser_total']} л."
                                   + (f"  ≈ {fmt_duration(est['laser'])}  ({est['laser_model']})" if est["laser"] else ""))
        self.hp_plan.configure(state="disabled")

        self.cn_plan.configure(state="normal")
        self.cn_plan.delete("1.0", "end")
        for i, (fp, fn, pgs, inf) in enumerate(self.current_plan["plotter"]):
            o = "альб." if inf["landscape"] else "книж."
            t = entry_time.get(("plotter", i))
            self.cn_plan.insert("end", f"{fn}: стр. {inf['pages_str']} ({inf['format']}, {o}) x{inf['missing']}"
                                       + (f"  ≈{fmt_duration(t)}" if t else "") + "\n")
        self.cn_plan.insert("end", f"\nИтого Canon: {self.current_plan['stats']['plotter_total']} л."
                                   + (f"  ≈ {fmt_duration(est['plotter'])}  ({est['plotter_model']})" if est["plotter"] else ""))
        self.cn_plan.configure(state="disabled")

        if est["sum"] > 0:
            self.estimate_label.configure(
                text=f"⏱ Печать плана ≈ {fmt_duration(est['wall'])}"
                     f"   (HP {fmt_duration(est['laser'])} | плоттер {fmt_duration(est['plotter'])}, параллельно)")
        else:
            self.estimate_label.configure(text="⏱ Всё напечатано" if self.analyzed_data else "")
        
        # Refresh current selection
        if hasattr(self, 'current_selected_file') and self.current_selected_file:
            self.on_file_selected(self.current_selected_file)
            
        # Update file tree status symbols and calculate stats
        total_files = len(self.analyzed_data)
        completed_files = 0
        laser_total = 0
        laser_printed = 0
        plotter_total = 0
        plotter_printed = 0
        
        total_printed_pages = 0
        total_folded_pages = 0
        total_complexity = 0.0
        current_complexity = 0.0
        from core.pdf_utils import get_complexity_weight
        for fp, data in self.analyzed_data.items():
            pc = len(data["pages"])
            done = self.printed_pages.get(fp, {})
            # Sum up copies
            done_cnt = sum(min(done.get(info[0], {}).get("printed", 0), tc) for info in data["pages"])
            folded_cnt = sum(min(done.get(info[0], {}).get("folded", 0), tc) for info in data["pages"])
            total_needed = pc * tc
            
            fmt_counts = {}
            for info in data["pages"]:
                fmt = info[1]
                fmt_counts[fmt] = fmt_counts.get(fmt, 0) + 1
            
            # Колонка «Время»: коротко; подробности — в подсказке при наведении
            fully_folded = pc > 0 and folded_cnt >= total_needed
            session = self._file_times.get(fp)
            time_str = short_time(self._db_times.get(fp), session, fully_folded)
            time_tip = long_time(self._db_times.get(fp), session)

            if pc > 0 and folded_cnt >= total_needed:
                status = "Готово 🏁"
            elif pc > 0 and done_cnt >= total_needed:
                status = "Напечатан ✅"
            elif done_cnt > 0:
                status = "Частично"
            else:
                status = "В очереди"
            self.file_tree.update_status(
                fp, status,
                pages=pc if pc > 0 else "",
                formats=[f"{f}: {c} стр." for f, c in fmt_counts.items()]
                        + ([f"Осталось напечатать ≈ {fmt_duration(est['per_file'][fp])}"]
                           if est["per_file"].get(fp) else []),
                folded=f"{folded_cnt}/{total_needed}" if pc > 0 else "",
                time_str=time_str,
                time_tip=time_tip)
                
            if pc > 0 and done_cnt >= total_needed:
                completed_files += 1
                
            for info in data["pages"]:
                p_num = info[0]
                fmt = info[1]
                
                # Check DB format
                page_db = done.get(p_num, {})
                printed_c = min(page_db.get("printed", 0) if isinstance(page_db, dict) else page_db, tc)
                folded_c = min(page_db.get("folded", 0) if isinstance(page_db, dict) else 0, tc)
                
                is_laser = fmt in ["A4", "A3"]
                
                if is_laser:
                    laser_total += tc
                    laser_printed += printed_c
                else:
                    plotter_total += tc
                    plotter_printed += printed_c
                    
                total_printed_pages += printed_c
                total_folded_pages += folded_c
                
                weight = get_complexity_weight(fmt)
                total_complexity += weight * tc * 2 # x2 because print=1x, fold=1x
                current_complexity += (weight * printed_c) + (weight * folded_c)

        total_pages_needed = laser_total + plotter_total
        total_pages_done = laser_printed + plotter_printed
        overall_pct = int(current_complexity / total_complexity * 100) if total_complexity > 0 else 0
        
        files_left = total_files - completed_files
        stats_text = (f"Распечатано: {total_printed_pages}/{total_pages_needed} | "
                      f"Сшито: {total_folded_pages}/{total_pages_needed} | "
                      f"Общий прогресс (с учетом сложности): {overall_pct}%")
        
        if self.total_print_jobs > 0 and self.completed_print_jobs < self.total_print_jobs:
            self.overall_bar.set(self.completed_print_jobs / self.total_print_jobs)
            batch_pct = int(self.completed_print_jobs / self.total_print_jobs * 100)
            self.status_label.configure(text=f"Отправка заданий... ({batch_pct}%)", text_color="#f39c12")
        self.stats_label.configure(text=stats_text)
        
        # Yellow progress bar is overall complexity progress
        if total_complexity > 0:
            self.overall_bar.set(overall_pct / 100.0)
        else:
            self.overall_bar.set(0)
                
            # Clear active printer bars when not printing
            for widget in self.progress_container.winfo_children():
                if widget != self.overall_bar:
                    widget.destroy()

    def _reload_file_history(self, filepath):
        """Перечитать из базы отметки и время по одному файлу (после печати/отметки)."""
        self.printed_pages[filepath] = self.history.get_printed(filepath)
        self._db_times[filepath] = self.history.get_times_for(filepath)

    def on_file_selected(self, filepath):
        self.current_selected_file = filepath
        data = self.analyzed_data.get(filepath)
        if data:
            try: tc = self.target_copies.get()
            except: tc = 1
            self.result_view.display_results(filepath, data["pages"], data["formats"], 
                                             self.printed_pages.get(filepath, {}), tc)
            
    def open_file_externally(self, filepath, use_foxit=False, open_dir=False):
        import subprocess
        filepath = os.path.normpath(filepath)
        if os.path.exists(filepath):
            try:
                if open_dir:
                    subprocess.Popen(['explorer', '/select,', filepath])
                    return
                    
                if use_foxit:
                    foxit_paths = [
                        r"C:\Program Files (x86)\Foxit Software\Foxit PDF Reader\FoxitPDFReader.exe",
                        r"C:\Program Files (x86)\Foxit Software\Foxit PDF Editor\FoxitPDFEditor.exe",
                        r"C:\Program Files\Foxit Software\Foxit PDF Editor\FoxitPDFEditor.exe",
                        r"C:\Program Files\Foxit Software\Foxit PDF Reader\FoxitPDFReader.exe"
                    ]
                    foxit_exe = next((p for p in foxit_paths if os.path.exists(p)), None)
                    if foxit_exe:
                        subprocess.Popen([foxit_exe, filepath])
                    else:
                        os.startfile(filepath)
                else:
                    os.startfile(filepath)
            except Exception as e:
                print(f"Failed to open file: {e}")
            
    def set_file_copies(self, filepath, copies):
        data = self.analyzed_data.get(filepath)
        if data:
            for p_info in data["pages"]:
                self.history.set_printed(filepath, [p_info[0]], copies, p_info[1])
            self._reload_file_history(filepath)
            self._update_plan_and_ui()

    def set_folded_copies(self, filepath, copies):
        data = self.analyzed_data.get(filepath)
        if data:
            all_pages = [p[0] for p in data["pages"]]
            for p in all_pages:
                self.history.set_folded_copies(filepath, p, copies)
            self._reload_file_history(filepath)
            self._update_plan_and_ui()

    def mark_file_printed(self, filepath, state):
        data = self.analyzed_data.get(filepath)
        if not data: return
        try: tc = self.target_copies.get()
        except: tc = 1
        
        if state:
            for p_info in data["pages"]:
                self.history.set_printed(filepath, [p_info[0]], tc, p_info[1])
        else:
            self.history.remove_printed(filepath)
            
        self._reload_file_history(filepath)
        self._update_plan_and_ui()

    def mark_file_folded(self, filepath, state):
        data = self.analyzed_data.get(filepath)
        if not data: return
        try: tc = self.target_copies.get()
        except: tc = 1
        
        all_pages = [p[0] for p in data["pages"]]
        for p in all_pages:
            self.history.set_folded_copies(filepath, p, tc if state else 0)
            
        self._reload_file_history(filepath)
        self._update_plan_and_ui()
        
    def print_format_group(self, filepath, fmt, pages_list):
        try: tc = self.target_copies.get()
        except: tc = 1
        import tkinter.simpledialog as sd
        val = sd.askinteger("Печать группы", f"Количество копий для формата {fmt}:", initialvalue=tc, minvalue=1)
        if val is None:
            return
        self._execute_print(filepath, fmt, pages_list, copies=val)
        
    def mark_format_group(self, filepath, fmt, pages_list, state):
        try: tc = self.target_copies.get()
        except: tc = 1
        
        if state:
            self.history.set_printed(filepath, pages_list, tc, fmt)
        else:
            self.history.set_printed(filepath, pages_list, 0, fmt)
            
        self._reload_file_history(filepath)
        self._update_plan_and_ui()
        
    def print_single_page(self, filepath, pn):
        data = self.analyzed_data.get(filepath)
        if not data: return
        page_info = next((i for i in data["pages"] if i[0] == pn), None)
        if page_info:
            fmt = page_info[1]
            try: tc = self.target_copies.get()
            except: tc = 1
            import tkinter.simpledialog as sd
            val = sd.askinteger("Печать страницы", f"Количество копий для страницы {pn}:", initialvalue=tc, minvalue=1)
            if val is None:
                return
            self._execute_print(filepath, fmt, [pn], copies=val)
            
    def _on_printer_alert(self, printer_name, error_msg):
        msg = f"⚠️ ОШИБКА ПРИНТЕРА '{printer_name}': {error_msg}"
        self.ui_call(lambda: self.status_label.configure(text=msg, text_color="red"))
            
    def _execute_print(self, filepath, fmt, pages_list, copies=1):
        if self.completed_print_jobs >= self.total_print_jobs or self.total_print_jobs == 0:
            self.total_print_jobs = 0
            self.completed_print_jobs = 0
            self.print_start_time = 0
            self.overall_bar.set(0)
            
        from core.pdf_utils import is_laser
        global_scale = float(self.cfg.get("global_scale", 100.0))
        rasterize_dpi = int(self.cfg.get("rasterize_dpi", 300))
        
        engine = self._get_engine(fmt)
        
        if is_laser(fmt):
            printer_name = self.laser_printer_var.get()
            settings = self.cfg.get("printers", {}).get("laser", {}).get("settings", "")
            # Force noscale to prevent frames from printing
            settings = settings.replace("fit", "noscale").replace("shrink", "noscale")
            if "noscale" not in settings:
                if settings: settings += ",noscale"
                else: settings = "noscale"
            # Batching logic for large laser jobs (A4/A3)
            total_pages = len(pages_list) * copies
            max_batch = 150
            
            if total_pages > max_batch:
                # We split into batches. For simplicity, we create multiple PrintJobs.
                # Between them, we insert a PauseJob.
                
                # If copies > 1, we can just do batches of copies if 1 copy > max_batch.
                # To be general, we will just chunk the total sequence of printed pages.
                
                # The simplest approach is to divide the copies if len(pages_list) < max_batch,
                # or divide the pages if copies == 1.
                # However, PDF tools usually print "page 1-N, C copies". 
                # If we want to split a job of 400 pages (1 copy) into 150 + 150 + 100:
                # Job 1: pages[0:150], 1 copy
                # Job 2: pages[150:300], 1 copy
                # Job 3: pages[300:400], 1 copy
                
                # What if len(pages_list) = 1, copies = 400?
                # Job 1: pages[0:1], 150 copies
                # Job 2: pages[0:1], 150 copies
                # Job 3: pages[0:1], 100 copies
                
                # What if len(pages) = 100, copies = 4? Total = 400.
                # A single copy is 100. We can do 1 copy, pause, 1 copy, pause...
                # So we determine chunking based on which dimension to split.
                
                if copies > 1 and len(pages_list) <= max_batch:
                    # Send batches of copies
                    copies_sent = 0
                    while copies_sent < copies:
                        chunk_copies = min(max_batch // len(pages_list), copies - copies_sent)
                        if chunk_copies < 1: chunk_copies = 1 # fallback
                        
                        self.total_print_jobs += 1
                        self.queue_manager.add_job(filepath, printer_name, engine, settings, copies=chunk_copies, pages=pages_list, format_name=fmt, scale=global_scale, rasterize_dpi=rasterize_dpi)
                        copies_sent += chunk_copies
                        
                        if copies_sent < copies:
                            self.queue_manager.add_pause(printer_name, "Заберите напечатанные листы из лотка ПРИНТЕРА и нажмите ОК для продолжения.")
                else:
                    # Send batches of pages (assuming 1 copy or we duplicate copies into flat list? No, if copies > 1 and len > 150, that's complex. Let's just chunk pages, and loop copies.)
                    pages_per_chunk = max_batch
                    for c in range(copies):
                        for i in range(0, len(pages_list), pages_per_chunk):
                            chunk = pages_list[i:i + pages_per_chunk]
                            self.total_print_jobs += 1
                            self.queue_manager.add_job(filepath, printer_name, engine, settings, copies=1, pages=chunk, format_name=fmt, scale=global_scale, rasterize_dpi=rasterize_dpi)
                            
                            # Add pause if this is not the very last chunk of the very last copy
                            is_last_chunk = (i + pages_per_chunk >= len(pages_list))
                            is_last_copy = (c == copies - 1)
                            if not (is_last_chunk and is_last_copy):
                                self.queue_manager.add_pause(printer_name, "Заберите напечатанные листы из лотка ПРИНТЕРА и нажмите ОК для продолжения.")
            else:
                self.total_print_jobs += 1
                self.queue_manager.add_job(filepath, printer_name, engine, settings, copies=copies, pages=pages_list, format_name=fmt, scale=global_scale, rasterize_dpi=rasterize_dpi)
        else:
            printer_name = self.plotter_printer_var.get()
            settings = self.cfg.get("printers", {}).get("plotter", {}).get("settings", "")
            self.total_print_jobs += 1
            self.queue_manager.add_job(filepath, printer_name, engine, settings, copies=copies, pages=pages_list, format_name=fmt, scale=global_scale, rasterize_dpi=rasterize_dpi)
            
        self.queue_manager.start()

    def _on_pause_request(self, pause_job):
        def show_msg():
            ans = messagebox.askokcancel(pause_job.title, pause_job.message, parent=self)
            if not ans:
                pause_job.cancelled = True
            pause_job.event.set()
        self.ui_call(show_msg)
        
    def set_page_copies(self, filepath, pn, override=None):
        try: tc = self.target_copies.get()
        except: tc = 1
        
        if override is not None:
            copies = override
        else:
            current = self.printed_pages.get(filepath, {}).get(pn, 0)
            copies = simpledialog.askinteger("Копии", f"Сколько копий страницы {pn} напечатано?", 
                                             initialvalue=current, minvalue=0)
            
        if copies is not None:
            data = self.analyzed_data.get(filepath)
            fmt = "UNKNOWN"
            if data:
                page_info = next((i for i in data["pages"] if i[0] == pn), None)
                if page_info: fmt = page_info[1]
                
            self.history.set_printed(filepath, [pn], copies, fmt)
            self._reload_file_history(filepath)
            self._update_plan_and_ui()
            
    def change_page_format(self, filepath, pn, new_fmt):
        data = self.analyzed_data.get(filepath)
        if not data: return
        for i, info in enumerate(data["pages"]):
            if info[0] == pn:
                lst = list(info)
                lst[1] = new_fmt
                data["pages"][i] = tuple(lst)
                break
                
        # RECALCULATE FORMATS so UI summary updates correctly
        from core.pdf_utils import group_pages_by_format
        data["formats"] = group_pages_by_format(data["pages"])
        
        # Manually select the file again to refresh the view immediately
        if hasattr(self, 'current_selected_file') and self.current_selected_file == filepath:
            self.on_file_selected(filepath)
            
        self._update_plan_and_ui()

    def open_settings(self):
        from ui.components.settings_dialog import SettingsDialog
        open_dialog(self, "_settings_dlg", lambda: SettingsDialog(self))
        
    def show_report(self):
        from ui.components.report_view import ReportDialog
        open_dialog(self, "_report_dlg", lambda: ReportDialog(self, self.history, getattr(self, 'current_plan', None)))
        
    def show_trial_print(self):
        if not self.current_plan:
            messagebox.showinfo("Пробная печать", "План печати пуст.")
            return
            
        from ui.components.visualizer import PrintVisualizer
        open_dialog(self, "_visualizer_dlg", lambda: PrintVisualizer(self, self.current_plan))

    def print_single_file(self, filepath):
        """Print all pages of a single file (right-click action)."""
        data = self.analyzed_data.get(filepath)
        if not data:
            messagebox.showwarning("Печать", "Файл не проанализирован.")
            return
        
        try: tc = self.target_copies.get()
        except: tc = 1

        import tkinter.simpledialog as sd
        val = sd.askinteger("Печать файла", f"Сколько копий {os.path.basename(filepath)}?", initialvalue=tc, minvalue=1)
        if val is None:
            return
        copies = val

        # Group pages by format and print each group
        from core.pdf_utils import is_laser
        fmt_groups = {}
        for info in data["pages"]:
            fmt = info[1]
            fmt_groups.setdefault(fmt, []).append(info[0])
        
        for fmt, pages in fmt_groups.items():
            self._execute_print(filepath, fmt, pages, copies=copies)
        
        messagebox.showinfo("Печать", f"Задания для {os.path.basename(filepath)} добавлены в очередь.")

    def preview_single_file(self, filepath):
        """Open the visualizer for a single file (right-click action)."""
        data = self.analyzed_data.get(filepath)
        if not data:
            messagebox.showwarning("Предпросмотр", "Файл не проанализирован.")
            return
        
        try: tc = self.target_copies.get()
        except: tc = 1

        from core.pdf_utils import build_print_plan, is_laser
        files_analysis = {filepath: data["pages"]}
        single_plan = build_print_plan(files_analysis, self.printed_pages, tc)
        
        from ui.components.visualizer import PrintVisualizer
        dlg = open_dialog(self, "_preview_dlg", lambda: PrintVisualizer(self, single_plan))
        if dlg is not None:
            dlg.title(f"Предпросмотр: {os.path.basename(filepath)}")

    def _get_engine(self, fmt=None):
        if fmt in ["A4", "A3"] or fmt is None:
            return FoxitEngine(self.cfg.get("foxit_path", ""))
        else:
            return SumatraEngine(self.cfg.get("sumatra_path", ""))
            
            
    def print_all(self):
        if not self.current_plan:
            messagebox.showinfo("Печать", "План печати пуст.")
            return
            
        self.queue_manager.start()
        
        for fp, fn, pgs, inf in self.current_plan["laser"]:
            if inf["missing"] > 0:
                self._execute_print(fp, inf["format"], pgs, copies=inf["missing"])
                self.update() # Yield to UI to prevent freezing
                
        for fp, fn, pgs, inf in self.current_plan["plotter"]:
            if inf["missing"] > 0:
                self._execute_print(fp, inf["format"], pgs, copies=inf["missing"])
                self.update() # Yield to UI to prevent freezing
                
        messagebox.showinfo("Печать", "Все задания добавлены в очередь.")
            
    def on_print_start(self, job):
        import datetime
        import time
        fp = job.filepath
        if not hasattr(self, '_file_times'):
            self._file_times = {}
        t = self._file_times.setdefault(fp, {})
        if "start" not in t or "end" in t:  # новый заход печати этого файла
            t["start"] = datetime.datetime.now()
            t.pop("end", None)
            
        if self.print_start_time == 0:
            self.print_start_time = time.time()
        self.ui_call(lambda: self.file_tree.update_status(job.filepath, "Печать..."))
        
    def on_print_error(self, job, error_msg):
        self.ui_call(lambda: messagebox.showerror("Ошибка печати", f"Ошибка при печати {os.path.basename(job.filepath)}:\n{error_msg}"))
        self.ui_call(lambda: self.file_tree.update_status(job.filepath, "Ошибка"))
        
    def on_print_complete(self, job, actual_pages_printed=None):
        import time
        import datetime
        fp = getattr(job, 'filepath', None)
        if fp:
            if not hasattr(self, '_file_times'):
                self._file_times = {}
            if fp not in self._file_times:
                self._file_times[fp] = {}
            self._file_times[fp]["end"] = datetime.datetime.now()
            
        self.completed_print_jobs += 1
        
        fmt = getattr(job, 'format_name', "Auto")
        
        pages_to_add = job.pages
        if not pages_to_add:
            data = self.analyzed_data.get(job.filepath)
            if data:
                pages_to_add = [i[0] for i in data["pages"]]
                
        if pages_to_add:
            total_job_pages = len(pages_to_add) * job.copies
            if actual_pages_printed is None or actual_pages_printed >= total_job_pages:
                completed_copies = job.copies
                remainder = 0
            elif actual_pages_printed == 0:
                completed_copies = 0
                remainder = 0
            else:
                completed_copies = actual_pages_printed // len(pages_to_add)
                remainder = actual_pages_printed % len(pages_to_add)
                
            if completed_copies > 0:
                self.history.add_printed(job.filepath, pages_to_add, completed_copies, fmt)
            
            if remainder > 0:
                self.history.add_printed(job.filepath, pages_to_add[:remainder], 1, fmt)
        
        self._reload_file_history(job.filepath)
        
        def update_ui():
            self._update_plan_and_ui()
            if self.total_print_jobs > 0:
                elapsed = time.time() - self.print_start_time
                if self.completed_print_jobs > 0:
                    avg_time = elapsed / self.completed_print_jobs
                    left_time = avg_time * (self.total_print_jobs - self.completed_print_jobs)
                    mins = int(left_time // 60)
                    secs = int(left_time % 60)
                    self.time_label.configure(text=f"Ост: {mins}м {secs}с")
                    
                if self.completed_print_jobs >= self.total_print_jobs:
                    self.status_label.configure(text="Печать завершена!")
                    self.time_label.configure(text="")
        
        self.ui_call(update_ui)
