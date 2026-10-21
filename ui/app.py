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
from ui.components import choice_dialog
from core.analyzer import ScanJob
from core.analysis_cache import AnalysisCache
from core.timefmt import short_time, long_time
from core.print_time import (estimate_plan, fmt_duration, merged_profiles, detect_model,
                             laser_job_seconds, plotter_job_seconds)
from core.print_progress import PrintTracker
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
    def __init__(self, splash=None, print_request=None):
        super().__init__()
        self._print_request = print_request     # «Напечатать в WPM» из Проводника при запуске
        self._quick_dialog = None
        self._quick_session = None
        self._qp_scan = None           # фоновый анализ для окна «Напечатать в WPM»
        self._qp_scan_queue = []
        self._job_data = {}            # анализ файлов из заданий (могут быть не из открытой папки)
        self.tasks = []                # «Очередь заданий» внизу окна
        self._task_ids = None
        self._select_after_scan = None
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
        self.queue_manager.on_job_progress = lambda job, printed, total, size: self.ui_call(
            self.print_tracker.progress, getattr(job, "tracker_id", None), printed, total)
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
        self.print_tracker = PrintTracker()   # ход печати по принтерам (полосы прогресса)
        from core.output_tray import TrayCounter
        # сколько листов лежит в лотке выдачи каждого принтера (сохраняется между запусками)
        self.trays = TrayCounter(self.cfg.get("tray_fill", {}) or {}, on_change=self._on_tray_changed)
        self._had_print_activity = False
        
        self._file_times = {}   # печать в текущем сеансе: {fp: {"start": datetime, "end": datetime}}
        self._db_times = {}     # время из базы: {fp: {"print_first", "print_last", "fold_last"}}
        
        self._tour = None       # активное обучение (ui/components/tour.py)
        self._build_ui()
        self._bind_hotkeys()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._start_printer_monitoring()
        self._check_updates_startup()
        
        # Главное окно показываем СРАЗУ, а анализ идёт уже внутри него с прогрессом
        # и кнопкой «Отменить». Раньше окно было скрыто за заставкой до конца анализа,
        # и при выборе огромной/не той папки приходилось ждать.
        # ВАЖНО: показывать окно только ПОСЛЕ запуска mainloop. CustomTkinter в Windows при
        # старте mainloop прячет окно, если до этого вызывался withdraw() (а мы его вызываем,
        # пока висит заставка) — в 4.9.0 окно из-за этого «пропадало» сразу после заставки,
        # хотя программа продолжала работать в фоне.
        self.after(100, self._close_splash)
        # Печать, прерванная в прошлый раз (программа закрылась или ПК выключился посреди печати)
        import uuid
        self._session_id = uuid.uuid4().hex[:8]
        self._stale_inflight = list(self.cfg.get("inflight_jobs", []) or [])
        if self._stale_inflight:
            self.cfg.set("inflight_jobs", [])
            self.after(3000, self._ask_stale_inflight)
        self.after(1000, self._poll_print_request)
        if self._print_request:
            # если выделили несколько PDF, остальные файлы приходят запросами за долю секунды —
            # ждём чуть дольше и собираем их в одно окно
            self.after(900, self._start_quick_from_launch)
        elif self.current_folder and os.path.isdir(self.current_folder):
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
        self._withdraw_called_before_window_exists = False  # флаг CustomTkinter, см. комментарий выше
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except Exception:
            pass
        # Перетаскивание папки/PDF в окно и пункт в меню Проводника (только Windows)
        try:
            from ui.dnd_win import enable_drop
            enable_drop(self, self._on_drop)
        except Exception:
            logging.exception("Перетаскивание")
        self._sync_explorer_menu()
        # Первый запуск — предложить обучение (один раз). При печати из Проводника — не мешаем.
        from ui.components.tour import TOUR_VERSION
        if int(self.cfg.get("tour_seen_version", 0) or 0) < TOUR_VERSION and not os.environ.get("WPM_NO_TOUR") \
                and not self._print_request:
            self.after(1500, lambda: self.start_tour(offer=True))
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
    # ───────────── обновления ─────────────
    # Логика:
    #  • при запуске (первая проверка) и если не идёт печать — сразу окно с изменениями;
    #  • позже (проверка раз в 30 мин) — только кнопка «⬆ Обновление x.y.z» в панели, без окон;
    #  • если идёт печать — «Обновить после печати»: скачиваем сразу, а устанавливаем
    #    и перезапускаемся автоматически, когда очередь печати опустеет.
    # Проверка: через 5 с после запуска, затем каждые 3 мин (условные запросы GitHub,
    # «ничего не изменилось» не тратит лимит) → новая версия замечается максимум за 3 минуты.
    # «Позже» в окне обновления откладывает напоминание на 30 минут.
    UPDATE_FIRST_CHECK_MS = 5 * 1000
    UPDATE_CHECK_INTERVAL_MS = 3 * 60 * 1000
    UPDATE_REMIND_MS = 30 * 60 * 1000

    def _check_updates_startup(self):
        self._update_info = None
        self._update_state = None      # None / available / downloading / ready / scheduled
        self._update_exe = None
        self._update_after_print = False
        self._update_remind_job = None
        self.after(self.UPDATE_FIRST_CHECK_MS, lambda: self.check_updates(startup=True))
        self.after(8000, self._flush_reports_later)  # отчёты, не отправленные в прошлый раз
        self.after(self.UPDATE_CHECK_INTERVAL_MS, self._periodic_update_check)
        self.after(3000, self._update_watchdog)
        self.after(2500, self._verify_update_install)

    def _periodic_update_check(self):
        if self._update_state in (None, "available"):
            self.check_updates()
        self.after(self.UPDATE_CHECK_INTERVAL_MS, self._periodic_update_check)

    def _print_busy_jobs(self):
        try:
            return self.queue_manager.pending_count()
        except Exception:
            return 0

    def check_updates(self, manual=False, parent=None, startup=False):
        from core.updater import check_for_updates
        parent = parent or self

        def on_none():
            if manual:
                self.ui_call(lambda: messagebox.showinfo("Обновления", "У вас последняя версия для выбранного канала.", parent=parent))

        def on_error(msg):
            if manual:
                self.ui_call(lambda: messagebox.showerror("Обновления", f"Не удалось проверить обновления:\n{msg}", parent=parent))

        check_for_updates(lambda info: self.ui_call(self._on_update_found, info, startup, manual),
                          manual=manual, on_none=on_none, on_error=on_error)

    def _on_update_found(self, info, startup=False, manual=False):
        if self._update_state in ("downloading", "ready", "scheduled"):
            return  # уже занимаемся обновлением
        self._update_info = info
        self._update_state = "available"
        from core.version import FULL_VERSION
        logging.info("Найдено обновление %s (установлена %s)", info["version"], FULL_VERSION)
        self._show_update_button(f"⬆ Обновление {info['version'].lstrip('v')}")
        # Окно — только сразу после запуска (и если не идёт печать) или по ручной проверке
        if manual or (startup and self._print_busy_jobs() == 0 and getattr(self, "_tour", None) is None):
            self.show_update_dialog()

    def _show_update_button(self, text, color="#e67e22"):
        self.btn_update.configure(text=text, fg_color=color)
        if not self.btn_update.winfo_ismapped():
            self.btn_update.pack(side="left", padx=(10, 5), pady=5, after=self.btn_bug)

    def show_update_dialog(self):
        if not self._update_info:
            return
        if self._update_state in ("downloading", "scheduled"):
            messagebox.showinfo("Обновление", "Обновление уже загружается и установится автоматически "
                                "после окончания печати.", parent=self)
            return
        from core.version import FULL_VERSION
        from ui.components.update_dialog import UpdateDialog
        open_dialog(self, "_update_dlg", lambda: UpdateDialog(
            self, self._update_info, FULL_VERSION, self._print_busy_jobs(), self._on_update_choice))

    def check_updates_soon(self):
        """После смены канала/репозитория/токена в Настройках — проверить через 5 секунд."""
        self.after(self.UPDATE_FIRST_CHECK_MS, lambda: self.check_updates(startup=True))

    def _remind_update(self):
        self._update_remind_job = None
        if self._update_state == "available" and self._print_busy_jobs() == 0:
            self.show_update_dialog()

    def _on_update_choice(self, choice):
        if choice == "later":
            # Напомнить через 30 минут (если к тому времени не обновились и не идёт печать)
            if self._update_remind_job:
                self.after_cancel(self._update_remind_job)
            self._update_remind_job = self.after(self.UPDATE_REMIND_MS, self._remind_update)
            return
        self._update_after_print = (choice == "after")
        if self._update_state == "ready" and self._update_exe:
            self._try_install()
            return
        self._download_update()

    def _download_update(self):
        info = self._update_info
        self._update_state = "downloading"
        self._show_update_button("⬇ Загрузка обновления 0%", "#3498db")

        def progress(frac):
            self.ui_call(lambda: self._show_update_button(f"⬇ Загрузка обновления {int(frac * 100)}%", "#3498db"))

        def worker():
            from core.updater import download_update
            try:
                exe = download_update(info, progress)
                self.ui_call(self._on_update_downloaded, exe)
            except Exception as e:
                logging.error(f"Загрузка обновления: {e}")

                def fail():
                    self._update_state = "available"
                    self._show_update_button(f"⬆ Обновление {info['version'].lstrip('v')}")
                    messagebox.showerror("Обновление", f"Не удалось скачать обновление:\n{e}", parent=self)
                self.ui_call(fail)

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_downloaded(self, exe):
        self._update_exe = exe
        self._update_state = "ready"
        self._try_install()

    def _try_install(self):
        """Устанавливаем сразу, если очередь печати пуста; иначе ждём её окончания."""
        if self._print_busy_jobs() > 0:
            self._update_state = "scheduled"
            self._show_update_button("⏳ Обновится после печати", "#8e44ad")
            self.status_label.configure(text="Обновление загружено — установится после окончания печати")
            return
        self.status_label.configure(text="Установка обновления и перезапуск...", text_color="#3498db")
        self.after(1500, self._install_now)

    def _install_now(self):
        from core.updater import install_update
        try:
            self._remember_pending_update()
            install_update(self._update_exe, restart=True)
        except Exception as e:
            messagebox.showinfo("Обновление", str(e), parent=self)
            self._update_state = "ready"
            return
        self._update_state = "installing"
        self._on_close()

    def _remember_pending_update(self):
        """Запомнить, какую версию ставим: после перезапуска проверим, что она действительно запустилась."""
        try:
            self.cfg.set("pending_update_tag", (self._update_info or {}).get("version", ""))
        except Exception:
            pass

    def _verify_update_install(self):
        """Если после обновления запустилась НЕ та версия — сказать об этом (раньше снова молча
        появлялась кнопка «Обновление», и было непонятно, почему)."""
        from core.updater import parse_tag, current_parsed
        from core.version import FULL_VERSION
        pending = self.cfg.get("pending_update_tag") or ""
        if not pending:
            return
        self.cfg.set("pending_update_tag", "")
        p = parse_tag(pending)
        if p and p != current_parsed():
            logging.error("Обновление до %s не установилось: запущена %s", pending, FULL_VERSION)
            messagebox.showwarning(
                "Обновление",
                f"Обновление до {pending.lstrip('v')} не установилось — запущена версия {FULL_VERSION}.\n\n"
                "Скорее всего, файл программы был занят. Закройте все окна программы и нажмите "
                "«⬆ Обновление» ещё раз. Если повторится — «Сообщить об ошибке».", parent=self)
        else:
            logging.info("Обновление до %s установлено", pending)

    def _update_watchdog(self):
        """Раз в 3 с: если обновление ждёт окончания печати и печать закончилась — ставим."""
        try:
            if self._update_state == "scheduled" and self._print_busy_jobs() == 0:
                self.status_label.configure(text="Печать завершена — перезапуск для обновления через 5 с...",
                                            text_color="#3498db")
                self._update_state = "ready"
                self.after(5000, self._try_install)
        finally:
            self.after(3000, self._update_watchdog)

    def _on_close(self):
        """Корректное закрытие: останавливаем анализ (процессы-работники) и очередь печати.
        Если обновление скачано, но не установлено — ставим его без перезапуска."""
        if getattr(self, "_update_state", None) in ("ready", "scheduled") and self._update_exe:
            try:
                from core.updater import install_update
                self._remember_pending_update()
                install_update(self._update_exe, restart=False)
            except Exception as e:
                logging.info(f"Обновление при закрытии не установлено: {e}")
        try:
            if self._scan_job is not None:
                self._scan_job.cancel()
            self.queue_manager.stop()
        except Exception:
            pass
        self.destroy()

    def destroy(self):
        # Останавливаем фоновые циклы (мониторинг принтеров), чтобы они не жили после окна
        self._closing = True
        try:
            self.queue_manager.stop()
        except Exception:
            pass
        super().destroy()

    def _open_bug_report(self):
        from ui.components.bug_report import BugReportDialog
        file = getattr(self, "current_selected_file", None)
        open_dialog(self, "_bug_dlg", lambda: BugReportDialog(self, current_file=file))
        
    def _start_printer_monitoring(self):
        """Фоновый опрос принтера и плоттера раз в 2 с: очередь Windows + SNMP самого принтера.
        Результат разбирается в главном потоке (_apply_printer_state) вместе с трекером печати."""
        from printing.printer_probe import PrinterProbe
        from ui.components.notify import Notifier
        self.notifier = Notifier(self, on_click=lambda key: self._bring_to_front())
        self._printer_states = {}      # имя -> итоговое состояние (core.printer_status.combine)
        self._printer_tips = {"laser": "", "plotter": ""}
        self._notified_minor = set()   # «мало чернил» и т.п. — сообщаем один раз за сеанс
        self._notified = {}            # принтер -> {проблема: подробность}, о которых уже сообщили
        self._problem_since = {}       # принтер -> {проблема: когда появилась}
        self.printer_probe = PrinterProbe(
            get_overrides=lambda: self.cfg.get("printer_hosts", {}) or {},
            community=self.cfg.get("snmp_community", "public") or "public",
            enabled=lambda: bool(self.cfg.get("snmp_enabled", True)))
        for role, lbl in (("laser", self.laser_status_lbl), ("plotter", self.plotter_status_lbl)):
            lbl.bind("<Enter>", lambda e, r=role, w=lbl: self._show_printer_tip(r, w))
            lbl.bind("<Leave>", lambda e: self._hide_printer_tip())
            lbl.bind("<Button-3>", lambda e, r=role: self._printer_menu(r, e))

        def monitor():
            import time
            while not getattr(self, "_closing", False):
                laser = getattr(self, '_current_laser', "Default Printer")
                plotter = getattr(self, '_current_plotter', "Default Printer")
                for role, pname in (("laser", laser), ("plotter", plotter)):
                    if not pname or pname in ("Default Printer", "Загрузка..."):
                        continue
                    try:
                        spooler, snmp, host = self.printer_probe.probe(pname)
                    except Exception:
                        logging.error("Опрос принтера %s:\n%s", pname, traceback.format_exc())
                        spooler, snmp, host = None, None, None
                    self.ui_call(self._apply_printer_state, role, pname, spooler, snmp, host)
                time.sleep(2)

        import threading
        threading.Thread(target=monitor, daemon=True).start()

    def _bring_to_front(self):
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _apply_printer_state(self, role, name, spooler, snmp, host):
        from core.printer_status import combine, PROBLEMS
        if name != getattr(self, "_current_laser" if role == "laser" else "_current_plotter", None):
            return   # пока опрашивали, принтер в списке сменили
        # Опрос не удался (нет связи с очередью и принтером) — это не «всё устранено»:
        # прошлое состояние и уведомления не трогаем; «нет связи» — только если так 10+ секунд
        fails = getattr(self, "_probe_fails", {})
        self._probe_fails = fails
        if spooler is None and snmp is None:
            fails[name] = fails.get(name, 0) + 1
            if fails[name] < 5 and name in self._printer_states:
                return
        else:
            fails[name] = 0
        tracker = self.print_tracker.printer_summary(name)
        st = combine(spooler, snmp, tracker, plotter=(role == "plotter"))
        # Принтер встал (нет бумаги/замятие…) — замораживаем расчётное время его заданий
        self.print_tracker.set_stopped(name, st["stopped"])
        lbl = self.laser_status_lbl if role == "laser" else self.plotter_status_lbl
        short = st["text"] if len(st["text"]) <= 26 else st["text"][:25] + "…"
        try:
            lbl.configure(text=f"{st['color']} {short}", require_redraw=True)
        except Exception:
            pass
        snmp_line = (f"Сеть (SNMP): {host} — " + ("отвечает" if snmp else "не отвечает")) if host else \
            ("Сеть (SNMP): адрес не найден — впишите IP в Настройки → Принтеры"
             if self.cfg.get("snmp_enabled", True) else "Сеть (SNMP): выключено")
        self._printer_tips[role] = "\n".join([f"{'Принтер' if role == 'laser' else 'Плоттер'}: {name}",
                                              snmp_line, self._tray_line(role, name), ""] + st["details"])

        if spooler is None and snmp is None:
            return       # без данных не решаем, что проблемы устранены
        # Уведомления: только о НОВЫХ проблемах и об их устранении.
        # Общие «ошибка»/«нужно действие» по флагам Windows часто мигают и «залипают» —
        # о них сообщаем, только если держатся 20+ секунд.
        import time as _time
        DEBOUNCE = {"error": 20, "intervention": 20, "paper_problem": 20}
        self._printer_states[name] = st
        notified = self._notified.setdefault(name, {})
        since = self._problem_since.setdefault(name, {})
        now_t = _time.monotonic()
        for k in list(since):
            if k not in st["problems"]:
                since.pop(k)
        for k in st["problems"]:
            since.setdefault(k, now_t)
        busy = bool(tracker and not tracker.get("idle")) or bool((spooler or {}).get("jobs"))
        who = ("Принтер" if role == "laser" else "Плоттер") + f" «{name}»"
        for key, extra in sorted(st["problems"].items(), key=lambda kv: -PROBLEMS[kv[0]][0]):
            sev, _c, title = PROBLEMS[key]
            if key in notified and (notified[key] == extra or key not in ("no_ink", "low_ink", "no_paper")):
                continue
            if now_t - since[key] < DEBOUNCE.get(key, 0):
                continue
            if key in ("offline", "paused") and not busy:
                continue            # выключенный принтер без заданий — не повод всплывать
            notified[key] = extra
            if sev <= 1:
                tag = (name, key, extra)
                if tag in self._notified_minor:
                    continue
                self._notified_minor.add(tag)
            text = (f"Нет чернил: {extra}" if key == "no_ink" and extra else
                    (f"{title}: {extra}" if extra else title))
            if busy and sev >= 3:
                text += "\nПечать стоит — устраните, и принтер продолжит сам."
            self.notifier.show(f"{name}:{key}", f"{PROBLEMS[key][1]} {who}", text, severity=sev)
            self.status_label.configure(text=f"⚠ {who}: {text.splitlines()[0]}",
                                        text_color="#e74c3c" if sev >= 3 else "#f39c12")
        had = bool(notified)
        for key in [k for k in notified if k not in st["problems"]]:
            notified.pop(key)
            self.notifier.close(f"{name}:{key}")
            if PROBLEMS[key][0] >= 2:
                self.notifier.show(f"{name}:{key}:ok", f"✓ {who}", f"Устранено: {PROBLEMS[key][2]}", severity=0)
        if had and not any(PROBLEMS[k][0] >= 2 for st2 in self._printer_states.values() for k in st2["problems"]):
            if str(self.status_label.cget("text")).startswith("⚠"):
                self.status_label.configure(text="✓ Принтеры в порядке", text_color="#2ecc71")

    def _printer_menu(self, role, event):
        import tkinter as tk
        name = getattr(self, "_current_laser" if role == "laser" else "_current_plotter", "")
        what = "лоток" if role == "laser" else "корзину"
        m = tk.Menu(self, tearoff=0)
        m.add_command(label=f"Я забрал листы — {what} пуст{'' if role == 'laser' else 'а'}",
                      command=lambda: self.tray_emptied(name))
        m.add_command(label="Указать, сколько лежит сейчас…", command=lambda: self._ask_tray_amount(role, name))
        m.tk_popup(event.x_root, event.y_root)

    def _ask_tray_amount(self, role, name):
        from tkinter import simpledialog
        unit = "листов" if role == "laser" else "листов (в пересчёте на A2)"
        v = simpledialog.askfloat("Лоток", f"Сколько сейчас лежит, {unit}?", parent=self,
                                  initialvalue=self.trays.in_tray(name), minvalue=0)
        if v is not None and name:
            self.trays.set_in_tray(name, v)

    def _on_tray_changed(self, printer):
        try:
            self.cfg.set("tray_fill", self.trays.to_dict())
        except Exception:
            pass
        if hasattr(self, "laser_tray_lbl"):
            self._refresh_tray_labels()

    def _refresh_tray_labels(self):
        for role, lbl in (("laser", getattr(self, "laser_tray_lbl", None)),
                          ("plotter", getattr(self, "plotter_tray_lbl", None))):
            if lbl is None:
                continue
            name = getattr(self, "_current_laser" if role == "laser" else "_current_plotter", "")
            cap = self._tray_capacity(role)
            if not cap or not name or name in ("Default Printer", "Загрузка..."):
                lbl.configure(text="")
                continue
            now = self.trays.in_tray(name)
            queued = max(0.0, self.trays.current(name) - now)
            pct = now / cap
            color = "#2ecc71" if pct < 0.6 else ("#f39c12" if pct < 0.9 else "#e74c3c")
            icon = "📥" if role == "laser" else "🧺"
            txt = f"{icon} {now:g}/{cap}"
            if queued >= 0.5:
                txt += f" (+{queued:g})"
            lbl.configure(text=txt, text_color=color)

    def tray_emptied(self, name):
        """Ручное подтверждение, что лоток/корзина пусты (счётчик сам не обнуляется)."""
        if name:
            q = self.queue_manager.queues.get(name)
            idle = not self.print_tracker.printing_now(name) and not (q and q.unfinished_tasks)
            self.trays.emptied(name, idle=idle)
            self.status_label.configure(text=f"Лоток «{name}»: отмечен пустым", text_color="#2ecc71")
            self._refresh_tray_labels()

    def _tray_line(self, role, name):
        cap = self._tray_capacity(role)
        if not cap:
            return "Лоток выдачи: без учёта"
        now = self.trays.in_tray(name)
        queued = max(0.0, self.trays.current(name) - now)
        extra = f", ещё ≈{queued:g} в очереди" if queued >= 0.5 else ""
        if role == "plotter":
            return f"Корзина: лежит ≈{now:g} из {cap} (в листах A2){extra} · щелчок по 🧺 — «забрал листы»"
        return f"Лоток выдачи: лежит ≈{now:g} из {cap} л. до паузы{extra} · щелчок по 📥 — «забрал листы»"

    def _show_printer_tip(self, role, widget):
        text = self._printer_tips.get(role) or "Состояние ещё не получено"
        self._hide_printer_tip()
        import tkinter as tk
        tip = tk.Toplevel(self)
        tip.overrideredirect(True)
        tk.Label(tip, text=text, justify="left", bg="#2c3e50", fg="white", font=("Segoe UI", 10),
                 padx=8, pady=6).pack()
        tip.update_idletasks()
        x = min(widget.winfo_rootx(), self.winfo_screenwidth() - tip.winfo_reqwidth() - 10)
        tip.geometry(f"+{max(x, 0)}+{widget.winfo_rooty() + widget.winfo_height() + 4}")
        self._printer_tip = tip

    def _hide_printer_tip(self):
        tip = getattr(self, "_printer_tip", None)
        if tip is not None:
            try:
                tip.destroy()
            except Exception:
                pass
            self._printer_tip = None

    def _laser_choices(self):
        """Список в поле «Принтер»: принтеры из «Настройки → Принтеры» (1–2 шт.),
        а если там ничего не задано — все принтеры Windows."""
        system = list(getattr(self, "_system_printers", []) or [])
        configured = [p for p in (self.cfg.get("laser_printers", []) or []) if p]
        return configured or system or ["Default Printer"]

    def apply_printer_setup(self):
        """После сохранения «Настройки → Принтеры»: обновить списки вверху окна."""
        choices = self._laser_choices()
        try:
            self.laser_combo.configure(values=choices)
        except AttributeError:
            pass
        if self.laser_printer_var.get() not in choices:
            self.laser_printer_var.set(choices[0])
            self._on_laser_changed(choices[0])
        plotter = self.cfg.get("printers", {}).get("plotter", {}).get("name", "")
        if plotter and plotter != self.plotter_printer_var.get():
            self.plotter_printer_var.set(plotter)
            self._on_plotter_changed(plotter)

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
        
        btn_open = ctk.CTkButton(toolbar, text="📂 Открыть папку", command=self.open_folder)
        btn_open.pack(side="left", padx=5, pady=5)
        self.btn_open = btn_open
        
        ctk.CTkLabel(toolbar, text="Копий:").pack(side="left", padx=(20, 5), pady=5)
        copies_entry = ctk.CTkEntry(toolbar, textvariable=self.target_copies, width=50)
        copies_entry.pack(side="left", padx=5, pady=5)
        copies_entry.bind("<KeyRelease>", self._on_copies_changed)
        self.copies_entry = copies_entry
        
        # «Сообщить об ошибке» теперь в меню «?» (раньше красная кнопка отвлекала от «Открыть папку»)
        self.btn_bug = copies_entry
        # Значок обновления — появляется, только когда есть новая версия
        self.btn_update = ctk.CTkButton(toolbar, text="", fg_color="#e67e22", hover_color="#d35400",
                                        command=self.show_update_dialog)
        
        # Справка и обучение — справа
        self.btn_help = ctk.CTkButton(toolbar, text="?", width=34, font=("Segoe UI", 14, "bold"),
                                      fg_color="#5d6d7e", hover_color="#85929e", command=self._help_menu)
        self.btn_help.pack(side="right", padx=(2, 5), pady=5)
        self.btn_tour = ctk.CTkButton(toolbar, text="🎓 Обучение", width=100, fg_color="#5d6d7e",
                                      hover_color="#85929e", command=lambda: self.start_tour(mode="quick"))
        self.btn_tour.pack(side="right", padx=2, pady=5)
        btn_settings = ctk.CTkButton(toolbar, text="Настройки", width=100, command=self.open_settings)
        btn_settings.pack(side="right", padx=5, pady=5)
        self.btn_settings = btn_settings
        
        self.laser_printer_var = ctk.StringVar(master=self, value=self.cfg.get("printers", {}).get("laser", {}).get("name", "Default Printer"))
        self.plotter_printer_var = ctk.StringVar(master=self, value=self.cfg.get("printers", {}).get("plotter", {}).get("name", "Default Printer"))
        printers = ["Загрузка..."]
        
        def load_printers():
            real_printers = self._get_system_printers()
            def update_ui():
                self._system_printers = real_printers
                laser_choices = self._laser_choices()
                if self.laser_printer_var.get() not in laser_choices:
                    self.laser_printer_var.set(laser_choices[0])
                    self._current_laser = laser_choices[0]
                if self.plotter_printer_var.get() not in real_printers: self.plotter_printer_var.set(real_printers[0])
                try:
                    self.laser_combo.configure(values=laser_choices)
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
            self.plotter_status_lbl.configure(text="⚪ Опрос…")
            self.after(10, self._refresh_tray_labels)
            if hasattr(self, "_printer_tips"):
                self._printer_tips["plotter"] = ""
            self._current_plotter = v
            printers_cfg = self.cfg.get("printers", {})
            if "plotter" not in printers_cfg: printers_cfg["plotter"] = {}
            printers_cfg["plotter"]["name"] = v
            self.cfg.set("printers", printers_cfg)
            self._update_plan_and_ui()  # пересчитать время под другую модель
            
        self._on_plotter_changed = _on_plotter_changed
        self.plotter_combo = ctk.CTkOptionMenu(toolbar, variable=self.plotter_printer_var, values=printers, width=130, command=_on_plotter_changed)
        self.plotter_combo.pack(side="right", padx=(5, 0), pady=5)
        ctk.CTkLabel(toolbar, text="Плоттер:").pack(side="right", padx=(20, 5), pady=5)
        
        self.laser_status_lbl = ctk.CTkLabel(toolbar, text="⚪", width=20)
        self.laser_status_lbl.pack(side="right", padx=(0, 5))
        
        def _on_laser_changed(v):
            self._current_laser = v
            # состояние показываем для выбранного принтера — старое сразу убираем
            self.laser_status_lbl.configure(text="⚪ Опрос…")
            self.after(10, self._refresh_tray_labels)
            if hasattr(self, "_printer_tips"):
                self._printer_tips["laser"] = ""
            printers_cfg = self.cfg.get("printers", {})
            if "laser" not in printers_cfg: printers_cfg["laser"] = {}
            printers_cfg["laser"]["name"] = v
            self.cfg.set("printers", printers_cfg)
            self._update_plan_and_ui()  # пересчитать время под другую модель
            
        self._on_laser_changed = _on_laser_changed
        self.laser_combo = ctk.CTkOptionMenu(toolbar, variable=self.laser_printer_var, values=printers, width=130, command=_on_laser_changed)
        self.laser_combo.pack(side="right", padx=(5, 0), pady=5)
        ctk.CTkLabel(toolbar, text="Принтер:").pack(side="right", padx=(20, 5), pady=5)
        # Заполнение лотка принтера и корзины плоттера — рядом с состоянием; щелчок — «забрал листы»
        self.laser_tray_lbl = ctk.CTkLabel(toolbar, text="", cursor="hand2")
        self.laser_tray_lbl.pack(side="right", padx=(0, 6), before=self.laser_status_lbl)
        self.plotter_tray_lbl = ctk.CTkLabel(toolbar, text="", cursor="hand2")
        self.plotter_tray_lbl.pack(side="right", padx=(0, 6), before=self.plotter_status_lbl)
        for role, w in (("laser", self.laser_tray_lbl), ("plotter", self.plotter_tray_lbl)):
            w.bind("<Button-1>", lambda e, r=role: self._printer_menu(r, e))
            w.bind("<Button-3>", lambda e, r=role: self._printer_menu(r, e))
        self.after(200, self._refresh_tray_labels)
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
        # События для интерактивного обучения (add="+" — не мешают основным обработчикам)
        self.file_tree.tree.bind("<Button-3>", lambda e: self._tour_event("file_menu"), add="+")
        self.result_view.result_text.bind("<Button-3>", lambda e: self._tour_event("format_menu"), add="+")
        self.result_view.detail_tree.bind("<ButtonPress-1>", lambda e: self._tour_event("page_preview"), add="+")
        
        # Bottom pane for print plan
        bottom_pane = ctk.CTkFrame(self, height=120)
        self.plan_pane = bottom_pane
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
        self.bottom_bar = bottom_bar
        
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
        # Полосы хода печати по каждому принтеру (появляются только во время печати)
        self.print_progress_frame = ctk.CTkFrame(self.progress_container, fg_color="transparent")
        self.print_progress_frame.grid(row=1, column=0, sticky="ew")
        self.print_progress_frame.columnconfigure(1, weight=1)
        # Очередь заданий: что стоит в печати и в каком порядке (видна, только когда есть задания)
        self.tasks_frame = ctk.CTkFrame(self.progress_container, fg_color="transparent")
        self.tasks_frame.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self.tasks_frame.grid_remove()
        self.after(1000, self._print_progress_loop)
        
        # Row 2: Buttons
        btn_frame = ctk.CTkFrame(bottom_bar, fg_color="transparent")
        btn_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        
        self.btn_report = ctk.CTkButton(btn_frame, text="📊 Отчёт", fg_color="#3498db", hover_color="#2980b9", width=80, command=self.show_report)
        self.btn_report.pack(side="left", padx=5)
        
        right_btns = ctk.CTkFrame(btn_frame, fg_color="transparent")
        right_btns.pack(side="right")
        
        self.btn_trial = ctk.CTkButton(right_btns, text="Пробная печать (Просмотр)", fg_color="#f39c12", hover_color="#e67e22", command=self.show_trial_print)
        self.btn_trial.pack(side="left", padx=5)
        
        # Главная кнопка — крупная и сразу говорит, сколько и как долго
        self.btn_print = ctk.CTkButton(right_btns, text="🖨 Печать всего плана", fg_color="#2ecc71", hover_color="#27ae60",
                                       height=44, font=("Segoe UI", 15, "bold"), command=self.print_all)
        self.btn_print.pack(side="left", padx=5)

        # Порядок упаковки важен: Tk отдаёт место тем, кто упакован РАНЬШЕ. Раньше нижняя
        # панель с кнопками упаковывалась последней и на маленьком экране/при масштабе 125–150%
        # просто обрезалась. Теперь низ закреплён, а сжимается средняя часть со списками.
        for w in (paned, bottom_pane, bottom_bar):
            w.pack_forget()
        bottom_bar.pack(side="bottom", fill="x", padx=10, pady=5)
        bottom_pane.pack(side="bottom", fill="x", padx=10, pady=5)
        paned.pack(fill="both", expand=True, padx=10, pady=5)
        
    # ───────────── перетаскивание, Проводник, быстрая печать ─────────────
    def _on_drop(self, paths):
        """Папку или PDF перетащили в окно."""
        if not paths:
            return
        p = paths[0]
        if os.path.isdir(p):
            folder, select = p, None
        elif p.lower().endswith(".pdf") and os.path.isfile(p):
            folder, select = os.path.dirname(p), p
        else:
            messagebox.showinfo("Перетаскивание", "Перетащите папку с PDF или PDF-файл.", parent=self)
            return
        self._select_after_scan = select
        self.current_folder = folder
        self.cfg.set("last_folder", folder)
        self.start_analysis()
        self._tour_event("folder_opened")

    def _sync_explorer_menu(self):
        import sys
        if not getattr(sys, "frozen", False):
            return       # из исходников меню Проводника не трогаем
        from core import shell_integration
        if self.cfg.get("explorer_menu", True):
            shell_integration.register()
        else:
            shell_integration.unregister()

    def _start_quick_from_launch(self):
        from core.print_request import take_requests
        paths = [self._print_request] + [p for p in take_requests() if p != self._print_request]
        self.begin_quick_print(paths, fresh=True)

    def _poll_print_request(self):
        """Запросы «Напечатать в WPM», пришедшие, когда программа уже была открыта
        (или следующие файлы при выделении нескольких — Проводник запускает WPM на каждый)."""
        try:
            from core.print_request import take_requests
            paths = take_requests()
            if paths:
                self.begin_quick_print(paths, fresh=False)
        except Exception:
            logging.error("Запрос печати из Проводника:\n%s", traceback.format_exc())
        if not getattr(self, "_closing", False):
            self.after(1000, self._poll_print_request)

    def _file_data(self, fp):
        """Анализ файла: из открытой папки или из заданий «Напечатать в WPM» (другие папки)."""
        return self.analyzed_data.get(fp) or self._job_data.get(fp) or {}

    def begin_quick_print(self, paths, fresh=False):
        """Окно «Напечатать в WPM». Если оно уже открыто — выделенное ДОПИСЫВАЕТСЯ в него
        (очередь выделенного). Печать из него ставится в конец общей очереди печати."""
        from core.job_sources import group_sources
        from ui.components.quick_print import QuickPrintDialog
        if isinstance(paths, str):
            paths = [paths]
        sources, skipped = group_sources([p for p in paths if p and os.path.exists(p)])
        if not sources:
            messagebox.showinfo("Напечатать в WPM", "Печатаются только PDF-файлы и папки с PDF.", parent=self)
            return
        dlg = self._quick_dialog
        if dlg is None or not dlg.winfo_exists():
            dlg = QuickPrintDialog(self, on_print=self._quick_print_go, on_cancel=self._quick_cancel)
            self._quick_dialog = dlg
            self._quick_fresh = fresh
            busy = self.print_tracker.active_count() > 0 or self._print_busy_jobs() > 0
            if busy:
                dlg.set_busy_text("Сейчас идёт печать — новое задание встанет в конец очереди.")
            elif fresh or not self.analyzed_data:
                try:
                    self.iconify()      # программа откроется после «Печать»
                except Exception:
                    pass
        added = dlg.add_sources(sources, skipped)
        for idx in added:
            self._qp_scan_queue.append((dlg, idx))
        self._qp_next_scan()
        dlg.lift()
        try:
            dlg.focus_force()
        except Exception:
            pass

    def _qp_next_scan(self):
        """Анализ выделенного — по одному источнику, в фоне, не трогая открытую папку."""
        if self._qp_scan is not None or not self._qp_scan_queue:
            return
        dlg, idx = self._qp_scan_queue.pop(0)
        try:
            if not dlg.winfo_exists():
                return self._qp_next_scan()
        except Exception:
            return self._qp_next_scan()
        src = dlg.sources[idx]
        folder = src["folder"]
        token = object()
        self._qp_scan_token = token

        def on_progress(phase, done, total, fp):
            if phase in ("analyze", "found"):
                self.ui_call(lambda: dlg.winfo_exists() and dlg.source_progress(idx, done, total))

        def on_done(results, errors, total):
            printed = self.history.get_all_printed(folder)
            self.ui_call(self._qp_scan_done, token, dlg, idx, results, printed, None)

        def on_error(msg):
            self.ui_call(self._qp_scan_done, token, dlg, idx, {}, {}, msg)

        include_sub = self.cfg.get("include_subfolders", False) if src["files"] is None else False
        dlg.source_progress(idx, 0, 0)
        self._qp_scan = ScanJob(folder, include_sub, on_progress, on_done, on_error,
                                cache=self.analysis_cache).start()

    def _qp_scan_done(self, token, dlg, idx, results, printed, error):
        if token is not getattr(self, "_qp_scan_token", None):
            return
        self._qp_scan = None
        self._job_data.update(results)
        try:
            if dlg.winfo_exists():
                dlg.source_done(idx, results, printed, error)
        except Exception:
            logging.error("Окно быстрой печати:\n%s", traceback.format_exc())
        self._qp_next_scan()

    def _show_main(self):
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _quick_cancel(self):
        self._quick_dialog = None
        self._qp_scan_queue.clear()
        if self._qp_scan is not None:
            self._qp_scan.cancel()
            self._qp_scan = None
        busy = self.print_tracker.active_count() > 0 or self._print_busy_jobs() > 0
        if getattr(self, "_quick_fresh", False) and not busy and not self.tasks:
            self._on_close()        # программу открыли только ради этой печати
        else:
            self._show_main()

    def _quick_print_go(self, formats, copies, reprint, payload):
        from core.quick_plan import entries_for
        self._quick_dialog = None
        data, printed, files = payload["data"], payload["printed"], payload["files"]
        self._job_data.update(data)
        entries = entries_for(data, printed, files, copies, formats, reprint=reprint)
        sheets = sum(len(pgs) * c for _fp, _f, pgs, c in entries)
        # главное окно: если ничего не открыто/не печатается — показываем первую папку задания
        busy = self.print_tracker.active_count() > 0 or self._print_busy_jobs() > 0
        src0 = payload["sources"][0] if payload["sources"] else None
        if src0 and not busy and (not self.analyzed_data or getattr(self, "_quick_fresh", False)
                                  or os.path.normcase(src0["folder"]) == os.path.normcase(self.current_folder or "")):
            self.target_copies.set(str(copies))
            self.cfg.set("last_copies", str(copies))
            self.current_folder = src0["folder"]
            self.cfg.set("last_folder", src0["folder"])
            self._select_after_scan = (src0["files"] or [None])[0]
            self.start_analysis()
        self._show_main()
        if not entries:
            return
        titles = [s["title"] for s in payload["sources"]]
        title = titles[0] if len(titles) == 1 else f"{titles[0]} и ещё {len(titles) - 1}"
        fmts = ", ".join(sorted(formats))
        with self._task(f"{title} · {fmts} · ×{copies}"):
            for fp, fmt, pgs, c in entries:
                self._execute_print(fp, fmt, pgs, copies=c)
        sess = self._quick_session or {"sheets": 0, "started": False}
        sess["sheets"] += sheets
        self._quick_session = sess
        self.status_label.configure(text=f"Задание добавлено в очередь печати: {sheets} л.", text_color="#5dade2")

    def _check_quick_session_done(self):
        sess = self._quick_session
        if not sess:
            return
        busy = self.print_tracker.active_count() > 0 or self._print_busy_jobs() > 0
        if busy:
            sess["started"] = True
            return
        if not sess["started"] or (self._quick_dialog is not None and self._quick_dialog.winfo_exists()):
            return
        self._quick_session = None          # спрашиваем ОДИН раз — когда допечатано ВСЁ
        ans = choice_dialog.ask_choice(
            self, "Печать завершена",
            f"Печать завершена ({sess['sheets']} л.).\n\nЗакрыть программу?",
            [("stay", "Оставить программу открытой", "main"), ("close", "Закрыть программу", "alt")],
            default="stay")
        if ans == "close":
            self._on_close()

    # ───────────── очередь заданий (что за чем печатается) ─────────────
    from contextlib import contextmanager

    @contextmanager
    def _task(self, title):
        """Всё, что отправлено на печать внутри блока, — одно задание в «Очереди заданий».
        Новые задания всегда встают в КОНЕЦ очереди печати каждого принтера."""
        prev = self._task_ids
        ids = []
        self._task_ids = ids
        try:
            yield
        finally:
            self._task_ids = prev
            if ids:
                self.tasks.append({"title": title, "ids": ids, "done_at": None})
                self._render_tasks()

    def _task_state(self, t):
        jobs = [self.print_tracker.jobs.get(i) for i in t["ids"]]
        known = [j for j in jobs if j is not None]
        if not known:          # трекер уже убрал допечатанные задания
            return "done", 1.0, 0
        total = sum(j["slots"] for j in known) or 1
        if all(j["state"] in ("done", "error") for j in known):
            return ("error" if any(j["state"] == "error" for j in known) else "done"), 1.0, 0
        if any(j["state"] in ("sending", "printing") for j in known):
            done = sum(j["slots"] * (1.0 if j["state"] == "done" else 0.0) for j in known)
            left = sum(round(j["pages"] * j["copies"]) for j in known if j["state"] not in ("done", "error"))
            return "printing", done / total, left
        left = sum(round(j["pages"] * j["copies"]) for j in known if j["state"] not in ("done", "error"))
        return "queued", 0.0, left

    def _render_tasks(self):
        frame = getattr(self, "tasks_frame", None)
        if frame is None:
            return
        import time as _t
        now = _t.monotonic()
        rows = []
        for t in self.tasks:
            st, frac, left = self._task_state(t)
            if st in ("done", "error") and t["done_at"] is None:
                t["done_at"] = now
            rows.append((t, st, frac, left))
        # готовые показываем ещё минуту, потом убираем
        self.tasks = [t for t, st, _f, _l in rows if not (st in ("done", "error") and now - t["done_at"] > 60)]
        rows = [r for r in rows if r[0] in self.tasks]
        for w in frame.winfo_children():
            w.destroy()
        if not rows:
            frame.grid_remove()
            return
        frame.grid()
        ctk.CTkLabel(frame, text="Очередь заданий:", font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x")
        queue_pos = 0
        for t, st, frac, left in rows[-8:]:
            if st == "printing":
                mark, col = f"🖨 печатается · осталось ≈{left} л.", "#5dade2"
            elif st == "queued":
                queue_pos += 1
                mark, col = f"⏳ в очереди ({queue_pos}) · {left} л.", "#f39c12"
            elif st == "error":
                mark, col = "✗ ошибка печати", "#e74c3c"
            else:
                mark, col = "✓ напечатано", "#2ecc71"
            ctk.CTkLabel(frame, text=f"{rows.index((t, st, frac, left)) + 1}. {t['title']}  —  {mark}",
                         anchor="w", text_color=col, font=("Segoe UI", 11)).pack(fill="x")

    # ───────────── печать, прерванная в прошлый раз ─────────────
    def _inflight_add(self, job, pages, copies, fmt):
        if not pages or copies <= 0:
            return
        lst = list(self.cfg.get("inflight_jobs", []) or [])
        lst.append({"id": f"{self._session_id}:{getattr(job, 'tracker_id', '')}", "file": job.filepath,
                    "pages": list(pages), "copies": int(copies), "fmt": fmt,
                    "printer": getattr(job, "printer_name", "")})
        self.cfg.set("inflight_jobs", lst)

    def _inflight_done(self, tracker_ids):
        keys = {f"{self._session_id}:{t}" for t in tracker_ids}
        lst = list(self.cfg.get("inflight_jobs", []) or [])
        new = [e for e in lst if e.get("id") not in keys]
        if len(new) != len(lst):
            self.cfg.set("inflight_jobs", new)

    def _ask_stale_inflight(self):
        items = self._stale_inflight
        self._stale_inflight = []
        if not items:
            return
        lines = [f"• {os.path.basename(e['file'])} — {e.get('fmt', '')}, {len(e['pages'])} стр. × {e['copies']}"
                 for e in items[:8]]
        if len(items) > 8:
            lines.append(f"… и ещё {len(items) - 8}")
        ans = choice_dialog.ask_choice(
            self, "Печать была прервана",
            "В прошлый раз программа закрылась, когда принтер ещё печатал. Эти листы отмечены "
            "напечатанными, но могли не выйти:\n\n" + "\n".join(lines) + "\n\nОни напечатались?",
            [("ok", "Да, всё вышло", "main"),
             ("undo", "Нет — снять отметки, чтобы допечатать", "danger"),
             (None, "Не знаю — оставить как есть", "cancel")], default="ok")
        if ans == "undo":
            for e in items:
                self.history.unprint_pages(e["file"], e["pages"], e["copies"], e.get("fmt", ""))
            for fp in {e["file"] for e in items}:
                if fp in self.analyzed_data:
                    self._reload_file_history(fp)
            self._update_plan_and_ui()
            self.status_label.configure(text="Отметки прерванной печати сняты — их можно допечатать",
                                        text_color="#f39c12")

    def open_folder(self):
        # Выбор папки доступен и ВО ВРЕМЯ анализа: старый анализ отменяется.
        folder = filedialog.askdirectory(initialdir=self.current_folder or None, parent=self)
        if folder:
            self.current_folder = folder
            self.cfg.set("last_folder", folder)
            self.start_analysis()
            self._tour_event("folder_opened")

    def _on_copies_changed(self, event=None):
        # Пересчёт плана — не на каждое нажатие клавиши, а через 400 мс после последнего
        if self._copies_after_id:
            self.after_cancel(self._copies_after_id)
        self._copies_after_id = self.after(400, self._apply_copies)

    def _apply_copies(self):
        self._copies_after_id = None
        self.cfg.set("last_copies", self.target_copies.get())
        self._update_plan_and_ui()
        self._tour_event("copies_changed")

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
        self._tour_event("scan_done")

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
        sel = self._select_after_scan
        self._select_after_scan = None
        if sel and sel in self.analyzed_data:
            self.select_file(sel)

    def select_file(self, fp):
        """Выделить файл в списке и показать его страницы (печать файла из Проводника)."""
        try:
            item = self.file_tree._items.get(fp)
            if item:
                self.file_tree.tree.selection_set(item)
                self.file_tree.tree.see(item)
        except Exception:
            pass
        self.on_file_selected(fp)

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
        total_left = self.current_plan["stats"].get("grand_total", 0)
        if total_left:
            self.btn_print.configure(text=f"🖨 Напечатать {total_left} л.  (≈{fmt_duration(est['wall'])})")
        else:
            self.btn_print.configure(text="✓ Всё напечатано" if self.analyzed_data else "🖨 Печать всего плана")
        
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
        from core.pdf_utils import get_complexity_weight, is_heavy_page, heavy_threshold
        heavy_thr = heavy_threshold()
        for fp, data in self.analyzed_data.items():
            pc = len(data["pages"])
            done = self.printed_pages.get(fp, {})
            # Sum up copies
            done_cnt = sum(min(done.get(info[0], {}).get("printed", 0), tc) for info in data["pages"])
            folded_cnt = sum(min(done.get(info[0], {}).get("folded", 0), tc) for info in data["pages"])
            total_needed = pc * tc
            
            fmt_counts = {}
            fmt_printed = {}
            for info in data["pages"]:
                fmt = info[1]
                fmt_counts[fmt] = fmt_counts.get(fmt, 0) + 1
                fmt_printed[fmt] = fmt_printed.get(fmt, 0) + min(done.get(info[0], {}).get("printed", 0), tc)
            # Подсказка колонки «Напеч.»: какие форматы напечатаны (✓ полностью, ◐ частично, ✗ нет)
            printed_tip = []
            busy_fmts = self.print_tracker.active_formats(fp)
            for f, c in fmt_counts.items():
                need, got = c * tc, fmt_printed.get(f, 0)
                if f in busy_fmts:   # отправлено, но принтер ещё печатает — галочку не ставим
                    printed_tip.append(f"🖨 {f}: печатается ({got}/{need} л.)")
                    continue
                mark = "✓" if got >= need else ("◐" if got > 0 else "✗")
                printed_tip.append(f"{mark} {f}: {got}/{need} л.")
            
            # Колонка «Время»: коротко; подробности — в подсказке при наведении
            fully_folded = pc > 0 and folded_cnt >= total_needed
            session = self._file_times.get(fp)
            time_str = short_time(self._db_times.get(fp), session, fully_folded)
            time_tip = long_time(self._db_times.get(fp), session)
            # Напечатана только часть страниц — помечаем, чтобы время не выглядело как «всё готово»
            if time_str and not fully_folded and not time_str.startswith("▶") and 0 < done_cnt < total_needed:
                time_str = "◐ " + time_str
                time_tip = "Напечатана только часть страниц\n" + time_tip

            active_jobs = self.print_tracker.active_count(fp)
            pstate = self.print_tracker.file_state(fp)
            if pstate == "printing":
                status = f"🖨 Печать ({active_jobs})" if active_jobs > 1 else "🖨 Печать"
            elif pstate == "queued":
                status = "⏳ Ждёт печати"
            elif pc > 0 and folded_cnt >= total_needed:
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
                printed=f"{done_cnt}/{total_needed}" if pc > 0 else "",
                printed_tip=printed_tip,
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
                
                weight = get_complexity_weight(fmt, is_heavy_page(info, heavy_thr))
                total_complexity += weight * tc * 2 # x2 because print=1x, fold=1x
                current_complexity += (weight * printed_c) + (weight * folded_c)

        total_pages_needed = laser_total + plotter_total
        total_pages_done = laser_printed + plotter_printed
        overall_pct = int(current_complexity / total_complexity * 100) if total_complexity > 0 else 0
        
        files_left = total_files - completed_files
        stats_text = (f"Распечатано: {total_printed_pages}/{total_pages_needed} | "
                      f"Сшито: {total_folded_pages}/{total_pages_needed} | "
                      f"Общий прогресс (с учетом сложности): {overall_pct}%")
        
        self.stats_label.configure(text=stats_text)
        
        # Yellow progress bar is overall complexity progress
        if total_complexity > 0:
            self.overall_bar.set(overall_pct / 100.0)
        else:
            self.overall_bar.set(0)

    def _reload_file_history(self, filepath):
        """Перечитать из базы отметки и время по одному файлу (после печати/отметки)."""
        self.printed_pages[filepath] = self.history.get_printed(filepath)
        self._db_times[filepath] = self.history.get_times_for(filepath)

    def on_file_selected(self, filepath):
        self.current_selected_file = filepath
        self._tour_event("file_selected")
        data = self.analyzed_data.get(filepath)
        if data:
            try: tc = self.target_copies.get()
            except: tc = 1
            self.result_view.display_results(filepath, data["pages"], data["formats"], 
                                             self.printed_pages.get(filepath, {}), tc,
                                             printing_formats=self.print_tracker.active_formats(filepath),
                                             printing_pages=self.print_tracker.active_pages(filepath))
            
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
        """Печать группы формата (правый клик в сводке). Если часть группы уже напечатана —
        спрашиваем, допечатать недостающее или печатать всю группу заново
        (раньше всегда печаталась вся группа)."""
        try: tc = self.target_copies.get()
        except: tc = 1
        import tkinter.simpledialog as sd
        done = self.printed_pages.get(filepath, {})

        def printed(p):
            d = done.get(p, 0)
            return d.get("printed", 0) if isinstance(d, dict) else int(d or 0)

        missing_by_count = {}
        for p in pages_list:
            m = tc - printed(p)
            if m > 0:
                missing_by_count.setdefault(m, []).append(p)
        missing_sheets = sum(m * len(pg) for m, pg in missing_by_count.items())
        total_sheets = len(pages_list) * tc
        already = total_sheets - missing_sheets

        if already > 0:
            if missing_sheets == 0:
                if choice_dialog.ask_choice(self, "Печать группы",
                                            f"Формат {fmt} уже напечатан полностью ({tc} коп.).",
                                            [("all", f"Напечатать формат {fmt} ещё раз", "danger"),
                                             (None, "Отмена", "cancel")]) != "all":
                    return
            else:
                ans = choice_dialog.ask_choice(
                    self, "Печать группы",
                    f"Формат {fmt}: уже напечатано {already} из {total_sheets} листов.",
                    [("missing", f"Допечатать недостающее ({missing_sheets} л.)", "main"),
                     ("all", f"Напечатать весь формат заново ({total_sheets} л.)", "danger"),
                     (None, "Отмена", "cancel")], default="missing")
                if ans is None:
                    return
                if ans == "missing":
                    with self._task(f"📄 {os.path.basename(filepath)} · {fmt} · недостающее"):
                        for m, pgs in sorted(missing_by_count.items()):
                            self._execute_print(filepath, fmt, pgs, copies=m)
                    return
        val = sd.askinteger("Печать группы", f"Количество копий для формата {fmt}:", initialvalue=tc, minvalue=1,
                            parent=self)
        if val is None:
            return
        with self._task(f"📄 {os.path.basename(filepath)} · {fmt} · ×{val}"):
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
            with self._task(f"📄 {os.path.basename(filepath)} · стр. {pn} · ×{val}"):
                self._execute_print(filepath, fmt, [pn], copies=val)
            
    def _on_printer_alert(self, printer_name, error_msg):
        """Сигнал из очереди печати во время задания (может прийти из любого потока)."""
        def show():
            from core.printer_status import problems_from_text, PROBLEMS
            keys = problems_from_text(error_msg)
            if not keys or keys == {"error"}:
                logging.info("Принтер %s: %s (без уведомления — общий флаг ошибки)", printer_name, error_msg)
                return
            key = next(iter(keys))
            self.status_label.configure(text=f"⚠ Принтер «{printer_name}»: {error_msg}", text_color="#e74c3c")
            n = getattr(self, "notifier", None)
            if n is not None:   # тот же ключ, что у монитора, — второго окна не будет
                n.show(f"{printer_name}:{key}", f"{PROBLEMS[key][1]} Принтер «{printer_name}»",
                       f"{error_msg}\nПечать стоит — устраните, и принтер продолжит сам.", severity=PROBLEMS[key][0])
        self.ui_call(show)
            
    def _tour_active(self):
        t = getattr(self, "_tour", None)
        return t is not None and getattr(t, "active", False)

    def _print_done_info(self, text):
        if self._tour_active():
            text = (f"🎓 Обучение: это была имитация — на принтеры ничего не отправлено.\n\n"
                    f"Всего ушло бы ≈{self._sim_sheets} л. Вне обучения: «{text}»")
        messagebox.showinfo("Печать", text, parent=self)

    def _simulate_print(self, filepath, fmt, pages_list, copies):
        """Во время обучения печать не отправляется — только показываем, что ушло бы."""
        from core.pdf_utils import is_laser
        printer = self.laser_printer_var.get() if is_laser(fmt) else self.plotter_printer_var.get()
        n = len(pages_list) * copies
        self._sim_sheets = getattr(self, "_sim_sheets", 0) + n
        n_ = getattr(self, "notifier", None)
        if n_ is not None:
            n_.show("tour:sim", "🎓 Обучение: печать не отправлена",
                    f"Ушло бы на «{printer}»: {os.path.basename(filepath)}, {fmt}, "
                    f"{len(pages_list)} стр. × {copies} коп.\nВсего в имитации: {self._sim_sheets} л.", severity=1)
        logging.info("Обучение: имитация печати %s %s %s стр. x%s", filepath, fmt, len(pages_list), copies)
        self._tour_event("print_simulated")

    def _execute_print(self, filepath, fmt, pages_list, copies=1):
        if self._tour_active():
            self._simulate_print(filepath, fmt, pages_list, copies)
            return
        if self.completed_print_jobs >= self.total_print_jobs or self.total_print_jobs == 0:
            self.total_print_jobs = 0
            self.completed_print_jobs = 0
            self.print_start_time = 0
            self.overall_bar.set(0)
            
        from core.pdf_utils import is_laser
        global_scale = float(self.cfg.get("global_scale", 100.0))
        rasterize_dpi = int(self.cfg.get("rasterize_dpi", 300))
        
        engine = self._get_engine(fmt)
        from core.output_tray import plan_job, plotter_slots

        if is_laser(fmt):
            kind = "laser"
            printer_name = self.laser_printer_var.get()
            settings = self.cfg.get("printers", {}).get("laser", {}).get("settings", "")
            # Force noscale to prevent frames from printing
            settings = settings.replace("fit", "noscale").replace("shrink", "noscale")
            if "noscale" not in settings:
                if settings: settings += ",noscale"
                else: settings = "noscale"
            slot = 1
        else:
            kind = "plotter"
            printer_name = self.plotter_printer_var.get()
            settings = self.cfg.get("printers", {}).get("plotter", {}).get("settings", "")
            # место в корзине — по площади листа в листах A2 (берём самый большой лист задания)
            want = set(pages_list)
            sizes = [(p[2], p[3]) for p in self._file_data(filepath).get("pages", []) if p[0] in want]
            slot = max([plotter_slots(fmt, w, h) for w, h in sizes] or [plotter_slots(fmt)])

        # Лоток выдачи: общий счётчик для всех заданий принтера (см. core/output_tray.py)
        capacity = self._tray_capacity(kind)
        fill = self.trays.current(printer_name)
        steps, fill = plan_job(len(pages_list), copies, fill, capacity, slot)
        for st in steps:
            if st[0] == "print":
                _, a, b, k = st
                self.total_print_jobs += 1
                self._queue_job(filepath, printer_name, engine, settings, k, pages_list[a:b], fmt,
                                global_scale, rasterize_dpi, slot=slot)
            else:
                self.queue_manager.add_pause(
                    printer_name,
                    (f"В лотке принтера «{printer_name}» уже ≈{st[1]:g} л. — он почти полон.\n\n"
                     if kind == "laser" else
                     f"Корзина плоттера «{printer_name}» заполнена (≈{st[1]:g} л. в пересчёте на A2).\n\n") +
                    f"Заберите напечатанные листы и нажмите ОК — печать продолжится только после этого.\n"
                    f"Отмена — снять оставшиеся задания этого принтера.",
                    title="Заберите листы",
                    wait_until=lambda p=printer_name: self._printer_finished(p))
        self.trays.set(printer_name, fill)
            
        self.queue_manager.start()

    def _printer_finished(self, printer):
        """Для окна «Заберите листы»: предыдущие листы реально вышли?
        Нет — если по расчёту ещё печатает, если принтер стоит (нет бумаги, замятие)
        или если сам принтер/Windows говорит, что печатает."""
        if self.print_tracker.printing_now(printer):
            return False
        st = getattr(self, "_printer_states", {}).get(printer) or {}
        busy = st.get("device_busy") or ""
        return not (st.get("stopped") or busy.startswith(("Печатает", "Получает", "Обработка", "Прогрев")))

    def _tray_capacity(self, kind):
        """Сколько «мест» в лотке можно занять до паузы (паспорт × процент заполнения)."""
        from core.output_tray import effective_capacity, LASER_CAPACITY, PLOTTER_CAPACITY, DEFAULT_FILL_PCT
        default = LASER_CAPACITY if kind == "laser" else PLOTTER_CAPACITY
        try:
            cap = int(self.cfg.get(f"{kind}_tray_capacity", default))
            pct = int(self.cfg.get("tray_fill_pct", DEFAULT_FILL_PCT))
        except (TypeError, ValueError):
            cap, pct = default, DEFAULT_FILL_PCT
        # Корзину плоттера (≈8 свёрнутых A2) задают уже «по опыту» — без дополнительного запаса
        return effective_capacity(cap, pct if kind == "laser" else 100)

    def _queue_job(self, filepath, printer_name, engine, settings, copies, pages, fmt, scale, dpi, slot=1):
        """Ставит задание в очередь печати и регистрирует его в трекере прогресса."""
        from core.pdf_utils import is_laser
        kind = "laser" if is_laser(fmt) else "plotter"
        est = self._estimate_job_seconds(filepath, fmt, pages, copies, printer_name, kind)
        job = self.queue_manager.add_job(filepath, printer_name, engine, settings, copies=copies, pages=pages,
                                         format_name=fmt, scale=scale, rasterize_dpi=dpi)
        if job is not None:
            # Тяжёлые страницы на плоттере растеризуем перед отправкой (Foxit долго готовит вектор)
            if kind == "plotter":
                from core.pdf_utils import is_heavy_page, heavy_threshold
                want = set(pages or [])
                thr = heavy_threshold()
                job.heavy = any(is_heavy_page(p, thr) for p in self._file_data(filepath).get("pages", [])
                                if not want or p[0] in want)
            job.tracker_id = self.print_tracker.add(filepath, fmt, len(pages or []), copies,
                                                    printer_name, kind, est, page_list=pages,
                                                    slots=len(pages or []) * copies * slot)
            if self._task_ids is not None:
                self._task_ids.append(job.tracker_id)
        self._had_print_activity = True
        return job

    def _estimate_job_seconds(self, filepath, fmt, pages, copies, printer_name, kind):
        prof = merged_profiles(self.cfg.get("speed_profiles"))
        over = float(prof["job_overhead_s"])
        model = prof[kind][detect_model(kind, printer_name)]
        if kind == "laser":
            return laser_job_seconds(len(pages or []), fmt, copies, model, over)
        data = self._file_data(filepath)
        sizes_by_page = {p[0]: (p[2], p[3]) for p in data.get("pages", [])}
        sizes = [sizes_by_page.get(p, (594.0, 841.0)) for p in (pages or [])]
        return plotter_job_seconds(sizes, copies, model, over)

    def _print_progress_loop(self):
        """Раз в секунду: обновить полосы печати; когда файл допечатан — отметить время окончания."""
        try:
            finished_files = self.print_tracker.tick()
            changed = self.print_tracker.pop_changed()   # допечатался формат/задание — обновить отметки
            done_jobs = self.print_tracker.pop_done()
            for j in done_jobs:                          # листы легли в лоток
                self.trays.delivered(j["printer"], j["slots"])
            if done_jobs:
                self._inflight_done([j["id"] for j in done_jobs])
            self._check_quick_session_done()
            if self.tasks:
                self._render_tasks()
            for p in list(self.trays.fill):
                q = self.queue_manager.queues.get(p)
                if not self.print_tracker.printing_now(p) and not (q and q.unfinished_tasks) \
                        and not any(j["printer"] == p and j["state"] == "queued" for j in list(self.print_tracker.jobs.values())):
                    self.trays.sync_idle(p)
            if finished_files or changed:
                import datetime
                now = datetime.datetime.now()
                for fp in finished_files:
                    self._file_times.setdefault(fp, {})["end"] = now
                self._update_plan_and_ui()
            self._render_print_progress()
            self.print_tracker.clear_finished(older_than=15.0)
        except Exception:
            logging.error("Прогресс печати:\n%s", traceback.format_exc())
        finally:
            self.after(1000, self._print_progress_loop)

    def _render_print_progress(self):
        frame = self.print_progress_frame
        printers = self.print_tracker.printers()
        # убрать строки принтеров, которых больше нет
        for name in list(self.printer_bars):
            if name not in printers:
                for w in self.printer_bars.pop(name):
                    w.destroy()
        busy_any = False
        for row, name in enumerate(printers):
            info = self.print_tracker.printer_summary(name)
            if info is None:
                continue
            if name not in self.printer_bars:
                lbl_name = ctk.CTkLabel(frame, text="", width=150, anchor="w", font=("Segoe UI", 11, "bold"))
                bar = ctk.CTkProgressBar(frame, height=10, progress_color="#3498db")
                lbl_info = ctk.CTkLabel(frame, text="", anchor="w", font=("Segoe UI", 11))
                self.printer_bars[name] = (lbl_name, bar, lbl_info)
            lbl_name, bar, lbl_info = self.printer_bars[name]
            lbl_name.grid(row=row, column=0, sticky="w", padx=(0, 6))
            bar.grid(row=row, column=1, sticky="ew", padx=(0, 8))
            lbl_info.grid(row=row, column=2, sticky="w")
            short = name if len(name) <= 22 else name[:21] + "…"
            lbl_name.configure(text=f"🖨 {short}")
            bar.set(info["fraction"])
            if info["idle"]:
                text = "✓ напечатано" + (f", ошибок: {info['errors']}" if info["errors"] else "")
                bar.configure(progress_color="#2ecc71" if not info["errors"] else "#e67e22")
            else:
                busy_any = True
                bar.configure(progress_color="#3498db")
                cur = info["current"]
                state = {"queued": "в очереди", "sending": "подготовка/отправка", "printing": "печать"}[cur["state"]]
                text = (f"{state}: {cur['fmt']} — {cur['pages']} л. × {cur['copies']}  "
                        f"({os.path.basename(cur['file'])[:28]})")
                rest = []
                if info["jobs_left"] > 1:
                    fm = ", ".join(info["formats_left"][:4]) + ("…" if len(info["formats_left"]) > 4 else "")
                    rest.append(f"ещё {info['jobs_left'] - 1} зад. ({fm})")
                rest.append(f"осталось ≈{info['sheets_left']} л., {fmt_duration(info['seconds_left'])}")
                text += "  |  " + ", ".join(rest)
            lbl_info.configure(text=text)
        if busy_any:
            self.time_label.configure(text=f"Печать: осталось ≈{fmt_duration(max((self.print_tracker.printer_summary(p) or {}).get('seconds_left', 0) for p in printers))}")
        elif self._had_print_activity and printers:
            self._had_print_activity = False
            self.time_label.configure(text="")
            self.status_label.configure(text="Печать завершена ✓", text_color="#2ecc71")

    def _on_pause_request(self, pause_job):
        def show_msg():
            # Окно должно быть видно, даже если программа свёрнута или под другими окнами
            self._bring_to_front()
            try:
                from ui.components.notify import flash_taskbar
                flash_taskbar(self)
                self.bell()
            except Exception:
                pass
            ans = messagebox.askokcancel(pause_job.title, pause_job.message, parent=self)
            if not ans:
                pause_job.cancelled = True
                if getattr(pause_job, "printer_name", ""):
                    self.trays.reset(pause_job.printer_name)   # очередь снята, человек у принтера — лоток пуст
            elif getattr(pause_job, "printer_name", ""):
                self.trays.pause_ok(pause_job.printer_name)    # ОК — листы забрали
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
        
    # ───────────── справка и обучение ─────────────
    def _help_menu(self, event=None):
        import tkinter as tk
        m = tk.Menu(self, tearoff=0)
        m.add_command(label="Справка и вопросы  (F1)", command=self.open_help)
        m.add_separator()
        m.add_command(label="🎓 Быстрое обучение (8 шагов)", command=lambda: self.start_tour(mode="quick"))
        m.add_command(label="🎓 Подробное обучение", command=lambda: self.start_tour(mode="full"))
        m.add_command(label="Горячие клавиши", command=self._show_hotkeys)
        m.add_separator()
        m.add_command(label="Проверить обновления", command=lambda: self.check_updates(manual=True))
        m.add_command(label="Сообщить об ошибке…", command=self._open_bug_report)
        b = self.btn_help
        m.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    HOTKEYS = [("Ctrl+O", "открыть папку"), ("Ctrl+P", "напечатать весь план"),
               ("Ctrl+T", "пробная печать (просмотр)"), ("Ctrl+R", "отчёт"),
               ("F1", "справка"), ("F5", "проанализировать папку заново")]

    def _print_all_confirm(self):
        """Ctrl+P — с подтверждением: случайное нажатие не отправит весь комплект."""
        if not self.current_plan:
            return
        n = self.current_plan["stats"].get("grand_total", 0)
        if not n:
            messagebox.showinfo("Печать", "Всё уже напечатано.", parent=self)
            return
        if choice_dialog.ask_choice(self, "Печать", f"Напечатать весь план — {n} л.?",
                                    [("go", f"🖨 Напечатать {n} л.", "main"), (None, "Отмена", "cancel")],
                                    default="go") == "go":
            self.print_all()

    def _show_hotkeys(self):
        messagebox.showinfo("Горячие клавиши", "\n".join(f"{k} — {v}" for k, v in self.HOTKEYS) +
                            "\n\nПапку можно просто перетащить в окно программы, а в Проводнике — "
                            "правый клик по папке или PDF → «Напечатать в WPM».", parent=self)

    def _bind_hotkeys(self):
        def guard(fn):
            def h(e=None):
                try:
                    if self.grab_current() is None:
                        fn()
                except Exception:
                    logging.error("Горячая клавиша:\n%s", traceback.format_exc())
                return "break"
            return h
        for seq, fn in (("<Control-o>", self.open_folder), ("<Control-O>", self.open_folder),
                        ("<Control-p>", self._print_all_confirm), ("<Control-P>", self._print_all_confirm),
                        ("<Control-t>", self.show_trial_print), ("<Control-T>", self.show_trial_print),
                        ("<Control-r>", self.show_report), ("<Control-R>", self.show_report),
                        ("<F1>", self.open_help),
                        ("<F5>", lambda: self.current_folder and self.start_analysis())):
            self.bind_all(seq, guard(fn))

    def open_help(self, topic=None):
        from ui.components.help_window import HelpWindow
        dlg = open_dialog(self, "_help_dlg", lambda: HelpWindow(self, open_topic=topic))
        if dlg is not None and topic:
            try:
                dlg._open_topic(topic)
            except Exception:
                pass
        self._tour_event("help_opened")
        return dlg

    def start_tour(self, offer=False, mode="quick"):
        from ui.components.tour import Tour, STEPS, QUICK_STEPS
        from ui.components.tour import TOUR_VERSION
        self.cfg.set("tour_seen_version", TOUR_VERSION)   # предлагаем один раз (до следующей версии обучения)
        old = getattr(self, "_tour", None)
        if old is not None:
            old.close()
        self._sim_sheets = 0
        try:
            self._tour = Tour(self, steps=QUICK_STEPS if mode == "quick" else STEPS, offer=offer)
        except Exception:
            logging.error("Не удалось запустить обучение:\n%s", traceback.format_exc())
            self._tour = None
        return self._tour

    def _tour_event(self, name):
        tour = getattr(self, "_tour", None)
        if tour is not None:
            try:
                tour.event(name)
            except Exception:
                logging.error("Ошибка обучения:\n%s", traceback.format_exc())

    def show_report(self):
        from ui.components.report_view import ReportDialog
        open_dialog(self, "_report_dlg", lambda: ReportDialog(self, self.history, getattr(self, 'current_plan', None)))
        
    def show_trial_print(self):
        if not self.current_plan:
            messagebox.showinfo("Пробная печать", "План печати пуст.")
            return
            
        from ui.components.visualizer import PrintVisualizer
        self._tour_event("trial_opened")
        open_dialog(self, "_visualizer_dlg", lambda: PrintVisualizer(self, self.current_plan))

    def print_single_file(self, filepath):
        """Печать одного файла (правый клик). Если часть уже напечатана — спрашиваем,
        допечатать недостающее или печатать весь файл заново (раньше всегда печатался весь)."""
        data = self.analyzed_data.get(filepath)
        if not data:
            messagebox.showwarning("Печать", "Файл не проанализирован.", parent=self)
            return
        try:
            tc = self.target_copies.get()
        except Exception:
            tc = 1
        name = os.path.basename(filepath)

        # Что ещё не напечатано до нужного числа копий
        plan = build_print_plan({filepath: data["pages"]}, self.printed_pages, tc)
        missing = [(inf["format"], pgs, inf["missing"]) for _fp, _fn, pgs, inf in plan["laser"] + plan["plotter"]]
        missing_sheets = sum(len(pgs) * m for _f, pgs, m in missing)
        total_sheets = len(data["pages"]) * tc
        already = total_sheets - missing_sheets

        if already > 0:
            if missing_sheets == 0:
                if choice_dialog.ask_choice(self, "Печать файла",
                                            f"«{name}» уже напечатан полностью ({tc} коп.).",
                                            [("all", "Напечатать файл ещё раз целиком", "danger"),
                                             (None, "Отмена", "cancel")]) != "all":
                    return
                mode = "all"
            else:
                ans = choice_dialog.ask_choice(
                    self, "Печать файла",
                    f"«{name}»: уже напечатано {already} из {total_sheets} листов.",
                    [("missing", f"Допечатать недостающее ({missing_sheets} л.)", "main"),
                     ("all", f"Напечатать весь файл заново ({total_sheets} л.)", "danger"),
                     (None, "Отмена", "cancel")], default="missing")
                if ans is None:
                    return
                mode = ans
        else:
            mode = "all"

        if mode == "missing":
            with self._task(f"📄 {name} · недостающее"):
                for fmt, pgs, m in missing:
                    self._execute_print(filepath, fmt, pgs, copies=m)
            self._print_done_info(f"«{name}»: в очередь добавлено недостающее — {missing_sheets} л.")
            return

        import tkinter.simpledialog as sd
        copies = sd.askinteger("Печать файла", f"Сколько копий «{name}»?", initialvalue=tc, minvalue=1, parent=self)
        if copies is None:
            return
        fmt_groups = {}
        for info in data["pages"]:
            fmt_groups.setdefault(info[1], []).append(info[0])
        with self._task(f"📄 {name} · весь файл · ×{copies}"):
            for fmt, pages in fmt_groups.items():
                self._execute_print(filepath, fmt, pages, copies=copies)
        self._print_done_info(f"Задания для «{name}» добавлены в очередь.")

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
        name = os.path.basename((self.current_folder or "").rstrip("\\/")) or "план"
        with self._task(f"📁 {name} · весь план"):
            for fp, fn, pgs, inf in self.current_plan["laser"]:
                if inf["missing"] > 0:
                    self._execute_print(fp, inf["format"], pgs, copies=inf["missing"])
                    self.update() # Yield to UI to prevent freezing

            for fp, fn, pgs, inf in self.current_plan["plotter"]:
                if inf["missing"] > 0:
                    self._execute_print(fp, inf["format"], pgs, copies=inf["missing"])
                    self.update() # Yield to UI to prevent freezing
                
        self._print_done_info("Все задания добавлены в конец очереди печати.")
            
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
        self.print_tracker.start(getattr(job, "tracker_id", None))
        self._update_plan_and_ui()
        
    def on_print_error(self, job, error_msg):
        self.print_tracker.error(getattr(job, "tracker_id", None))
        self.ui_call(lambda: messagebox.showerror("Ошибка печати", f"Ошибка при печати {os.path.basename(job.filepath)}:\n{error_msg}"))
        self.ui_call(lambda: self.file_tree.update_status(job.filepath, "Ошибка"))
        
    def on_print_complete(self, job, actual_pages_printed=None):
        import time
        import datetime
        fp = getattr(job, 'filepath', None)
        # Очередь Windows отдала задание принтеру. Физически он ещё печатает — время окончания
        # файла ставит _print_progress_loop, когда закончатся ВСЕ задания этого файла.
        self.print_tracker.spooled(getattr(job, "tracker_id", None))
        self.completed_print_jobs += 1
        
        fmt = getattr(job, 'format_name', "Auto")
        
        pages_to_add = job.pages
        if not pages_to_add:
            data = self._file_data(job.filepath)
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
                self._inflight_add(job, pages_to_add, completed_copies, fmt)
            
            if remainder > 0:
                self.history.add_printed(job.filepath, pages_to_add[:remainder], 1, fmt)
                self._inflight_add(job, pages_to_add[:remainder], 1, fmt)
        
        self._reload_file_history(job.filepath)
        
        self.ui_call(self._update_plan_and_ui)
