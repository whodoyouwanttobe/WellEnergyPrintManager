import tkinter as tk
import customtkinter as ctk
from tkinter import messagebox
from core.config import get_config
from ui.components.dialog_utils import make_modal

class SettingsDialog(tk.Toplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Настройки")
        self.geometry("660x600")
        self.minsize(480, 380)
        self._close = make_modal(self, master)
        
        # ВАЖНО: не называть атрибут self.config — CustomTkinter при создании первого
        # CTk-виджета внутри tk.Toplevel ПЕРЕЗАПИСЫВАЕТ master.config своей функцией.
        # Из-за этого падал __init__ и окно оставалось пустым (см. crash.log).
        self.cfg = get_config()
        
        self.bg_frame = ctk.CTkFrame(self)
        self.bg_frame.pack(fill="both", expand=True)
        
        self.bg_frame.grid_rowconfigure(0, weight=1)
        self.bg_frame.grid_columnconfigure(0, weight=1)

        # Tabs
        self.tabview = ctk.CTkTabview(self.bg_frame)
        self.tabview.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        
        btn_frame = ctk.CTkFrame(self.bg_frame)
        btn_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=10)
        
        ctk.CTkButton(btn_frame, text="Сохранить", command=self.save).pack(side="right", padx=5)
        ctk.CTkButton(btn_frame, text="Отмена", command=self._close).pack(side="right")
        
        self.tab_general = self.tabview.add("Общие")
        self.tab_engines = self.tabview.add("Движки")
        self.tab_system = self.tabview.add("Система")
        self.tab_speed = self.tabview.add("Скорость")
        self.tab_printers = self.tabview.add("Принтеры")
        
        self._build_general_tab()
        self._build_engines_tab()
        self._build_system_tab()
        self._build_speed_tab()
        self._build_printers_tab()
        
    def _build_general_tab(self):
        # Global scale
        ctk.CTkLabel(self.tab_general, text="Глобальный масштаб (%):").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.entry_scale = ctk.CTkEntry(self.tab_general)
        self.entry_scale.insert(0, str(self.cfg.get("global_scale", 99.94)))
        self.entry_scale.grid(row=0, column=1, padx=10, pady=10)
        
        # DPI rasterization slider
        ctk.CTkLabel(self.tab_general, text="DPI растеризации тяжёлых чертежей:").grid(row=1, column=0, padx=10, pady=(10, 0), sticky="w")
        
        dpi_frame = ctk.CTkFrame(self.tab_general, fg_color="transparent")
        dpi_frame.grid(row=1, column=1, padx=10, pady=(10, 0), sticky="w")
        
        self.dpi_value_label = ctk.CTkLabel(dpi_frame, text=str(int(self.cfg.get("rasterize_dpi", 300))), width=40, font=("Segoe UI", 14, "bold"))
        self.dpi_value_label.pack(side="right", padx=(5, 0))
        
        self.dpi_slider = ctk.CTkSlider(dpi_frame, from_=150, to=600, number_of_steps=9, width=200,
                                         command=lambda v: self.dpi_value_label.configure(text=str(int(v))))
        self.dpi_slider.set(int(self.cfg.get("rasterize_dpi", 300)))
        self.dpi_slider.pack(side="left")
        
        dpi_hint = ctk.CTkLabel(self.tab_general, text="300 — оптимально для чертежей. 150 — быстро, но мелкий текст может размыться. 600 — максимальное качество.",
                                font=("Segoe UI", 10), text_color="gray", wraplength=350, justify="left")
        dpi_hint.grid(row=2, column=0, columnspan=2, padx=10, pady=(2, 10), sticky="w")

        # Порог «тяжёлой» страницы — в тысячах векторных команд (линий/кривых)
        from core.pdf_utils import HEAVY_OPS_DEFAULT
        ctk.CTkLabel(self.tab_general, text="Тяжёлая страница — от, тыс. линий:").grid(row=3, column=0, padx=10, pady=(10, 0), sticky="w")
        self.entry_heavy = ctk.CTkEntry(self.tab_general, width=100)
        self.entry_heavy.insert(0, str(int(self.cfg.get("heavy_ops_threshold", HEAVY_OPS_DEFAULT)) // 1000))
        self.entry_heavy.grid(row=3, column=1, padx=10, pady=(10, 0), sticky="w")
        ctk.CTkLabel(self.tab_general, text="Сколько линий у каждой страницы — в колонке «Сложн.» справа. Тяжёлые страницы "
                     "помечаются ⚡ и на плоттере растеризуются перед печатью. Повторный анализ не нужен.",
                     font=("Segoe UI", 10), text_color="gray", wraplength=350, justify="left").grid(
            row=4, column=0, columnspan=2, padx=10, pady=(2, 10), sticky="w")
        
    CHANNELS = {"Стабильная (рабочая)": "stable", "Бета (тестирование)": "beta", "Разработка (dev)": "dev"}

    def _build_system_tab(self):
        from core.updater import DEFAULT_REPO
        from core.version import FULL_VERSION, BUILD_CHANNEL
        t = self.tab_system
        t.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(t, text=f"Установлена версия: {FULL_VERSION} ({BUILD_CHANNEL})",
                     text_color="gray").grid(row=0, column=0, columnspan=2, padx=10, pady=(5, 2), sticky="w")

        self.check_updates_var = ctk.BooleanVar(master=self, value=self.cfg.get("check_updates", True))
        ctk.CTkCheckBox(t, text="Проверять обновления при запуске", variable=self.check_updates_var).grid(
            row=1, column=0, columnspan=2, padx=10, pady=4, sticky="w")

        channel = self.cfg.get("update_channel") or ("beta" if self.cfg.get("beta_channel") else "stable")
        label_by_value = {v: k for k, v in self.CHANNELS.items()}
        ctk.CTkLabel(t, text="Канал обновлений:").grid(row=2, column=0, padx=10, pady=4, sticky="w")
        self.channel_var = ctk.StringVar(master=self, value=label_by_value.get(channel, "Стабильная (рабочая)"))
        ctk.CTkOptionMenu(t, variable=self.channel_var, values=list(self.CHANNELS), width=220).grid(
            row=2, column=1, padx=10, pady=4, sticky="w")

        ctk.CTkLabel(t, text="GitHub репозиторий:").grid(row=3, column=0, padx=10, pady=4, sticky="w")
        self.entry_repo = ctk.CTkEntry(t, width=260, placeholder_text=DEFAULT_REPO)
        repo = self.cfg.get("update_repo", "")
        if repo and "/" in repo:
            self.entry_repo.insert(0, repo)
        self.entry_repo.grid(row=3, column=1, padx=10, pady=4, sticky="w")

        ctk.CTkLabel(t, text="Токен GitHub (для приватного):").grid(row=4, column=0, padx=10, pady=4, sticky="w")
        self.entry_token = ctk.CTkEntry(t, width=260, show="•")
        self.entry_token.insert(0, self.cfg.get("update_token", ""))
        self.entry_token.grid(row=4, column=1, padx=10, pady=4, sticky="w")

        ctk.CTkButton(t, text="Проверить обновления сейчас", command=self._check_now).grid(
            row=5, column=0, columnspan=2, padx=10, pady=(6, 10), sticky="w")

        # Отчёты об ошибках -> GitHub Issues
        ctk.CTkLabel(t, text="Токен для отчётов об ошибках:").grid(row=6, column=0, padx=10, pady=4, sticky="w")
        self.entry_bug_token = ctk.CTkEntry(t, width=260, show="•")
        self.entry_bug_token.insert(0, self.cfg.get("bug_token", ""))
        self.entry_bug_token.grid(row=6, column=1, padx=10, pady=4, sticky="w")
        ctk.CTkButton(t, text="Отправить тестовый отчёт", command=self._test_report).grid(
            row=7, column=0, columnspan=2, padx=10, pady=(4, 10), sticky="w")

        # Telegram — запасной канал
        ctk.CTkLabel(t, text="Telegram (резерв, без VPN может не работать)", text_color="gray").grid(
            row=8, column=0, columnspan=2, padx=10, pady=(6, 0), sticky="w")
        ctk.CTkLabel(t, text="Telegram Bot Token:").grid(row=9, column=0, padx=10, pady=4, sticky="w")
        self.entry_tg_token = ctk.CTkEntry(t, width=260, show="•")
        self.entry_tg_token.insert(0, self.cfg.get("tg_token", ""))
        self.entry_tg_token.grid(row=9, column=1, padx=10, pady=4, sticky="w")

        ctk.CTkLabel(t, text="Telegram Chat ID:").grid(row=10, column=0, padx=10, pady=4, sticky="w")
        self.entry_tg_chat = ctk.CTkEntry(t, width=260)
        self.entry_tg_chat.insert(0, self.cfg.get("tg_chat_id", ""))
        self.entry_tg_chat.grid(row=10, column=1, padx=10, pady=4, sticky="w")

    def _test_report(self):
        self._store_update_settings()
        self.cfg.save()
        import threading
        from core.bug_reporter import build_report, deliver
        report = build_report("Тестовый отчёт из настроек — проверка связи. Можно закрыть.")
        ui = getattr(self.master, "ui_call", self.after_idle)

        def work():
            status, text = deliver(report)
            def show():
                parent = self if self.winfo_exists() else self.master
                (messagebox.showinfo if status == "github" else messagebox.showwarning)("Тестовый отчёт", text, parent=parent)
            ui(show)
        threading.Thread(target=work, daemon=True).start()

    # Поля вкладки «Скорость»: (тип, модель, ключ, подпись)
    SPEED_FIELDS = [
        ("laser", "M435", "A4_ppm", "HP M435 — A4, стр/мин"),
        ("laser", "M435", "A3_ppm", "HP M435 — A3, стр/мин"),
        ("laser", "M436", "A4_ppm", "HP M436 — A4, стр/мин"),
        ("laser", "M436", "A3_ppm", "HP M436 — A3, стр/мин"),
        ("plotter", "TM-305", "sec_per_a0", "Canon TM-305 — секунд на лист A0"),
        ("plotter", "TM-305", "sheet_overhead_s", "Canon TM-305 — обрезка/подача, с на лист"),
        (None, None, "job_overhead_s", "Запуск одного задания печати, с"),
    ]

    def _build_speed_tab(self):
        from core.print_time import merged_profiles
        prof = merged_profiles(self.cfg.get("speed_profiles"))
        t = self.tab_speed
        ctk.CTkLabel(t, text="Используется для расчёта примерного времени печати плана.\n"
                             "Модель определяется по имени выбранного принтера/плоттера.\n"
                             "Если на практике выходит быстрее или медленнее — поправьте числа.",
                     justify="left", text_color="gray").grid(row=0, column=0, columnspan=2, padx=10, pady=(6, 8), sticky="w")
        self.speed_entries = {}
        for r, (kind, model, key, label) in enumerate(self.SPEED_FIELDS, start=1):
            value = prof[kind][model][key] if kind else prof[key]
            ctk.CTkLabel(t, text=label).grid(row=r, column=0, padx=10, pady=3, sticky="w")
            e = ctk.CTkEntry(t, width=80)
            e.insert(0, str(value))
            e.grid(row=r, column=1, padx=10, pady=3, sticky="w")
            self.speed_entries[(kind, model, key)] = e
        ctk.CTkButton(t, text="Сбросить к паспортным значениям", fg_color="gray",
                      command=self._reset_speeds).grid(row=len(self.SPEED_FIELDS) + 1, column=0, columnspan=2,
                                                       padx=10, pady=(10, 6), sticky="w")

    def _reset_speeds(self):
        from core.print_time import merged_profiles
        prof = merged_profiles(None)
        for (kind, model, key), e in self.speed_entries.items():
            e.delete(0, "end")
            e.insert(0, str(prof[kind][model][key] if kind else prof[key]))

    def _store_speeds(self):
        """Возвращает False, если в полях не числа."""
        out = {"laser": {}, "plotter": {}}
        for (kind, model, key), e in self.speed_entries.items():
            try:
                v = float(e.get().replace(",", "."))
                if v <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Настройки", "На вкладке «Скорость» должны быть положительные числа.", parent=self)
                return False
            if kind:
                out[kind].setdefault(model, {})[key] = v
            else:
                out[key] = v
        self.cfg.data["speed_profiles"] = out
        return True

    def _check_now(self):
        self._store_update_settings()
        self.cfg.save()
        if hasattr(self.master, "check_updates"):
            self.master.check_updates(manual=True, parent=self)

    def _store_update_settings(self):
        self.cfg.data["check_updates"] = bool(self.check_updates_var.get())
        self.cfg.data["update_channel"] = self.CHANNELS.get(self.channel_var.get(), "stable")
        self.cfg.data["beta_channel"] = self.cfg.data["update_channel"] != "stable"
        self.cfg.data["update_repo"] = self.entry_repo.get().strip()
        self.cfg.data["update_token"] = self.entry_token.get().strip()
        self.cfg.data["bug_token"] = self.entry_bug_token.get().strip()
        self.cfg.data["tg_token"] = self.entry_tg_token.get().strip()
        self.cfg.data["tg_chat_id"] = self.entry_tg_chat.get().strip()

    def _build_printers_tab(self):
        """Опрос принтеров по сети (SNMP): замятие, бумага, чернила по цветам, «печатает»."""
        t = self.tab_printers
        t.grid_columnconfigure(1, weight=1)
        self.snmp_var = ctk.BooleanVar(master=self, value=bool(self.cfg.get("snmp_enabled", True)))
        ctk.CTkCheckBox(t, text="Узнавать у принтеров замятие, бумагу и чернила по сети (SNMP)",
                        variable=self.snmp_var).grid(row=0, column=0, columnspan=2, padx=10, pady=(8, 2), sticky="w")
        ctk.CTkLabel(t, text="Адрес берётся из порта принтера в Windows. Если в подсказке у значка принтера "
                     "написано «адрес не найден», впишите IP (он есть в меню принтера: Сеть → IPv4).",
                     font=("Segoe UI", 10), text_color="gray", wraplength=420, justify="left").grid(
            row=1, column=0, columnspan=2, padx=10, pady=(0, 8), sticky="w")
        hosts = dict(self.cfg.get("printer_hosts", {}) or {})
        m = self.master
        self._host_entries = {}
        for r, (title, attr) in enumerate((("IP принтера", "_current_laser"), ("IP плоттера", "_current_plotter")), start=2):
            name = getattr(m, attr, "") or ""
            ctk.CTkLabel(t, text=f"{title}\n({name[:30] or 'не выбран'}):", justify="left").grid(
                row=r, column=0, padx=10, pady=4, sticky="w")
            e = ctk.CTkEntry(t, width=160, placeholder_text="авто")
            if hosts.get(name):
                e.insert(0, hosts[name])
            e.grid(row=r, column=1, padx=10, pady=4, sticky="w")
            if name:
                self._host_entries[name] = e
        ctk.CTkLabel(t, text="SNMP community:").grid(row=4, column=0, padx=10, pady=4, sticky="w")
        self.entry_community = ctk.CTkEntry(t, width=160)
        self.entry_community.insert(0, self.cfg.get("snmp_community", "public") or "public")
        self.entry_community.grid(row=4, column=1, padx=10, pady=4, sticky="w")

        # Лоток выдачи — чтобы не переполнялся (HP M435/M436 — 250 л. по паспорту, корзина TM-305 — ≈8 свёрнутых A2)
        from core.output_tray import LASER_CAPACITY, PLOTTER_CAPACITY, DEFAULT_FILL_PCT
        self._tray_entries = {}
        for r, (key, title, default) in enumerate((
                ("laser_tray_capacity", "Лоток выдачи принтера, листов:", LASER_CAPACITY),
                ("plotter_tray_capacity", "Корзина плоттера, листов A2 (0 — без пауз):", PLOTTER_CAPACITY),
                ("tray_fill_pct", "Заполнять лоток принтера на, %:", DEFAULT_FILL_PCT)), start=5):
            ctk.CTkLabel(t, text=title).grid(row=r, column=0, padx=10, pady=4, sticky="w")
            e = ctk.CTkEntry(t, width=80)
            e.insert(0, str(self.cfg.get(key, default)))
            e.grid(row=r, column=1, padx=10, pady=4, sticky="w")
            self._tray_entries[key] = e
        ctk.CTkLabel(t, text="Когда лоток заполнится, печать встанет на паузу и программа попросит забрать листы — "
                     "уже после того, как они реально напечатаются; дальше печать идёт только после ОК. "
                     "Корзина плоттера считается в листах A2: A1 = 2, A0 = 4, A3 = 0.5.", font=("Segoe UI", 10), text_color="gray", wraplength=420,
                     justify="left").grid(row=8, column=0, columnspan=2, padx=10, pady=(2, 8), sticky="w")

    def _store_printer_settings(self):
        self.cfg.data["snmp_enabled"] = bool(self.snmp_var.get())
        self.cfg.data["snmp_community"] = self.entry_community.get().strip() or "public"
        hosts = dict(self.cfg.get("printer_hosts", {}) or {})
        for name, e in self._host_entries.items():
            v = e.get().strip()
            if v:
                hosts[name] = v
            else:
                hosts.pop(name, None)
        self.cfg.data["printer_hosts"] = hosts
        for key, e in self._tray_entries.items():
            try:
                self.cfg.data[key] = max(0, int(float(e.get().replace(",", "."))))
            except ValueError:
                pass
        probe = getattr(self.master, "printer_probe", None)
        if probe is not None:
            probe.community = self.cfg.data["snmp_community"]
            probe.reset()

    def _build_engines_tab(self):
        # Print Engine selection
        ctk.CTkLabel(self.tab_engines, text="Движок печати:").grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.engine_var = ctk.StringVar(master=self, value=self.cfg.get("print_engine", "sumatra"))
        
        self.radio_sumatra = ctk.CTkRadioButton(self.tab_engines, text="SumatraPDF (Встроенный)", variable=self.engine_var, value="sumatra")
        self.radio_sumatra.grid(row=1, column=0, padx=10, pady=5, sticky="w")
        
        self.radio_foxit = ctk.CTkRadioButton(self.tab_engines, text="Foxit PDF Editor (Корпоративный)", variable=self.engine_var, value="foxit")
        self.radio_foxit.grid(row=2, column=0, padx=10, pady=5, sticky="w")
        
        self.radio_pdf2printer = ctk.CTkRadioButton(self.tab_engines, text="PDFtoPrinter (Рекомендуется для плоттера)", variable=self.engine_var, value="pdftoprinter")
        self.radio_pdf2printer.grid(row=3, column=0, padx=10, pady=5, sticky="w")
        
        # Foxit Path
        ctk.CTkLabel(self.tab_engines, text="Путь к Foxit:").grid(row=4, column=0, padx=10, pady=10, sticky="w")
        self.entry_foxit = ctk.CTkEntry(self.tab_engines, width=300)
        self.entry_foxit.insert(0, self.cfg.get("foxit_path", ""))
        self.entry_foxit.grid(row=4, column=1, padx=10, pady=10)
        
    def save(self):
        try:
            scale = float(self.entry_scale.get().replace(",", "."))
            if not (50.0 <= scale <= 150.0):
                raise ValueError
        except ValueError:
            messagebox.showerror("Настройки", "Масштаб должен быть числом от 50 до 150.", parent=self)
            return

        self.cfg.data["global_scale"] = scale
        self._store_update_settings()
        if not self._store_speeds():
            return
        self.cfg.data["tg_token"] = self.entry_tg_token.get().strip()
        self.cfg.data["tg_chat_id"] = self.entry_tg_chat.get().strip()
        self.cfg.data["print_engine"] = self.engine_var.get()
        self.cfg.data["foxit_path"] = self.entry_foxit.get().strip()
        self.cfg.data["rasterize_dpi"] = int(self.dpi_slider.get())
        self._store_printer_settings()
        try:
            self.cfg.data["heavy_ops_threshold"] = max(int(float(self.entry_heavy.get().replace(",", "."))), 1) * 1000
        except ValueError:
            pass
        self.cfg.save()  # одна запись на диск вместо десяти
        master = self.master
        self._close()
        if hasattr(master, "_update_plan_and_ui"):
            master.after(50, master._update_plan_and_ui)  # пересчитать время печати с новыми скоростями
        if hasattr(master, "check_updates_soon"):
            master.check_updates_soon()  # канал/репозиторий могли смениться — проверить через 5 с
