"""Интерактивное обучение «🎓 Обучение».

Подсвечивает элемент окна жёлтой рамкой и показывает рядом «пузырь» с пояснением,
кнопками «‹ Назад», «Далее ›», «✕» и счётчиком шагов. Интерактивные шаги переходят
дальше сами, когда пользователь сделал действие (открыл папку, выбрал файл…):
приложение сообщает о действиях через Tour.event(name).

Рамка — 4 узких окна без заголовка (так работает на любом Windows без прозрачности).
Пузырь и рамка следуют за окном программы (опрос раз в 250 мс).
"""
import tkinter as tk

import customtkinter as ctk

HIGHLIGHT = "#f1c40f"
BORDER = 3


def _move(win, geom):
    """Переместить окно без заголовка. deiconify()/lift() вызываем только если окно скрыто:
    на уже показанном окне они сбивают новое положение (окно оставалось на старом месте)."""
    if win.winfo_ismapped():
        win.geometry(geom)
    else:
        win.geometry(geom)
        win.deiconify()
        win.lift()


def _app_in_front():
    """Активно ли окно НАШЕЙ программы (на Windows — по процессу активного окна).
    Если пользователь переключился в другую программу, пузырь её не закрывает."""
    import sys
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        import os
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == os.getpid()
    except Exception:
        return True


class Step:
    def __init__(self, title, text, target=None, wait=None, hint=None):
        self.title = title
        self.text = text
        self.target = target      # имя атрибута приложения (str) или функция app -> widget
        self.wait = wait          # имя события, после которого шаг переходит дальше сам
        self.hint = hint          # подсказка «что сделать», показывается у интерактивных шагов


TOUR_VERSION = 3   # увеличить, когда в обучении появятся новые шаги — его предложат ещё раз


def _rv(name):
    return lambda app: getattr(getattr(app, "result_view", None), name, None)


STEPS = [
    Step("Добро пожаловать!",
         "Обучение покажет, как напечатать комплект чертежей: от выбора папки до печати.\n\n"
         "Всё можно нажимать по-настоящему — открыть папку, выбрать файл, открыть меню. "
         "Только печать во время обучения НЕ отправляется на принтеры: программа покажет, "
         "что ушло бы на печать (имитация).\n\n"
         "Закрыть обучение — ✕, пройти снова — кнопка «🎓 Обучение»."),
    Step("Откройте папку комплекта",
         "Нажмите «📂 Открыть папку» (Ctrl+O) и выберите папку с PDF — или просто перетащите папку "
         "мышью в окно программы. Подпапки тоже учитываются (это настраивается).",
         target="btn_open", wait="folder_opened", hint="Нажмите «Открыть папку»"),
    Step("Анализ",
         "Программа определяет формат каждой страницы. Это идёт в фоне — окно не зависает. "
         "Если выбрали не ту папку, нажмите «✖ Отменить анализ» или просто откройте другую.\n"
         "Повторный анализ той же папки берётся из кэша и занимает секунды.",
         target="status_label", wait="scan_done", hint="Дождитесь окончания анализа"),
    Step("Выберите файл",
         "В списке — все PDF. Щёлкните по любому файлу, чтобы увидеть его страницы.",
         target="file_tree", wait="file_selected", hint="Щёлкните по файлу в списке"),
    Step("Колонки списка",
         "Стр. — страниц в файле; Напеч. — сколько листов уже напечатано; Сшито; Статус; Время.\n"
         "Наведите мышь на число или время — подсказка по форматам "
         "(✓ напечатан, ◐ частично, ✗ нет, 🖨 печатается).",
         target="file_tree"),
    Step("Меню файла (правый клик)",
         "Правой кнопкой по файлу:\n"
         "• открыть в программе по умолчанию или в Foxit, открыть папку;\n"
         "• «Печать этого файла» — если часть уже напечатана, предложит допечатать только недостающее;\n"
         "• «Предпросмотр» — как лягут листы;\n"
         "• отметить напечатанным / сшитым, снять отметки.",
         target="file_tree", wait="file_menu", hint="Щёлкните правой кнопкой по файлу"),
    Step("Форматы файла",
         "Сверху — строка на каждый формат: принтер или плоттер, страницы и сколько напечатано. "
         "⚡ — тяжёлые страницы (много линий): на плоттере они перед печатью превращаются в картинку.",
         target=_rv("result_text")),
    Step("Печать по форматам",
         "Правой кнопкой по строке формата:\n"
         "• «Печать группы» — только этот формат (если часть напечатана — спросит, допечатать ли недостающее);\n"
         "• «Печать только ⚡ тяжёлых» / «без тяжёлых»;\n"
         "• скопировать номера страниц, отметить формат напечатанным.",
         target=_rv("result_text"), wait="format_menu", hint="Щёлкните правой кнопкой по строке формата"),
    Step("Страницы и предпросмотр",
         "Внизу — каждая страница: формат, размер, ориентация, куда печатается, сложность.\n"
         "Зажмите левую кнопку мыши на странице — появится её миниатюра (отпустите — закроется).\n"
         "Правой кнопкой: напечатать одну страницу, поменять формат вручную, задать число напечатанных.",
         target=_rv("detail_tree"), wait="page_preview", hint="Зажмите левую кнопку на странице"),
    Step("Количество копий",
         "Сколько экземпляров комплекта нужно. План и время пересчитываются сами.",
         target="copies_entry", wait="copies_changed", hint="Измените число копий (или «Далее»)"),
    Step("Принтер и плоттер",
         "A4 и A3 печатаются на лазерный принтер HP, большие форматы — на плоттер Canon. "
         "Если принтеров два — выберите нужный в списке, состояние показывается для выбранного.\n"
         "Рядом — состояние: 🟢 готов, 🔵 печатает, 🟡 нет бумаги, 🔴 замятие / нет чернил, ⚪ отключен. "
         "Наведите мышь — подробности и уровни чернил по цветам. О проблемах программа сообщит окошком "
         "справа внизу.",
         target="laser_combo"),
    Step("План печати",
         "Слева — что пойдёт на принтер, справа — на плоттер: файл, страницы, формат, "
         "сколько копий осталось и примерное время. Уже напечатанное сюда не попадает.",
         target="plan_pane"),
    Step("Время и ход печати",
         "Здесь общее примерное время плана. Во время печати появится полоса на каждый принтер. "
         "Когда лоток или корзина плоттера заполнятся, печать встанет на паузу — "
         "заберите листы и нажмите ОК.",
         target="bottom_bar"),
    Step("Пробная печать",
         "Покажет, как листы лягут в лотки и на рулон плоттера. На принтер ничего не отправляет.",
         target="btn_trial", wait="trial_opened", hint="Нажмите «Пробная печать» (или «Далее»)"),
    Step("Печать",
         "Большая зелёная кнопка «🖨 Напечатать N л.» отправляет только недостающие листы "
         "(то же — Ctrl+P).\n"
         "Сейчас идёт обучение — можно смело нажать: на принтеры ничего не уйдёт, программа "
         "только покажет, что было бы отправлено.",
         target="btn_print", wait="print_simulated", hint="Нажмите зелёную кнопку печати (имитация)"),
    Step("Настройки",
         "Общие — масштаб, растеризация, порог тяжёлой страницы. Движки — чем и где печатается. "
         "Принтеры — ваши принтеры и плоттер, адреса, ёмкость лотков. Скорость — для расчёта времени. "
         "Система — версия и обновления.",
         target="btn_settings"),
    Step("Справка",
         "«?» — меню: справка с поиском (F1), быстрое и подробное обучение, горячие клавиши, "
         "«Сообщить об ошибке», проверка обновлений.\n"
         "Папку можно перетащить прямо в окно, а в Проводнике — правый клик по папке или PDF → «Напечатать в WPM».\n\n"
         "Готово! Удачной печати.",
         target="btn_help"),
]


def _by_title(*titles):
    m = {st.title: st for st in STEPS}
    return [m[t] for t in titles]


# Быстрое обучение: самое нужное — папка, файл, предпросмотр, печать по форматам,
# правая кнопка мыши, копии и печать. Подробное — в меню «?».
QUICK_STEPS = [
    Step("Быстрое обучение",
         "8 шагов: откроем папку, посмотрим страницы и предпросмотр, научимся печатать по форматам "
         "правой кнопкой мыши и отправим комплект на печать.\n\n"
         "Печать во время обучения НЕ отправляется на принтеры — только имитация. "
         "Подробное обучение и справка — в меню «?»."),
] + _by_title("Откройте папку комплекта", "Выберите файл", "Страницы и предпросмотр", "Печать по форматам",
              "Меню файла (правый клик)", "Количество копий", "Печать") + [
    Step("Готово!",
         "Главное запомнили: правая кнопка мыши — печать формата или файла, зажатая левая на странице — "
         "предпросмотр, большая зелёная кнопка — печать всего, что ещё не напечатано.\n\n"
         "Ещё быстрее: перетащите папку в окно или в Проводнике щёлкните правой кнопкой по папке или PDF → "
         "«Напечатать в WPM». Подробности — «?» → «Подробное обучение» или F1.",
         target="btn_help"),
]


class Tour:
    def __init__(self, app, steps=None, offer=False):
        self.app = app
        self.steps = steps or STEPS
        self.index = 0
        self.offer = offer
        self.active = True
        self.done_events = set()
        self._frames = []
        self._job = None
        self._last_geom = None
        self.bubble = None
        self._make_frames()
        self._make_bubble()
        self.show(0)
        self._set_topmost(True)
        self._follow()

    # ───────────── окна ─────────────
    def _make_frames(self):
        for _ in range(4):
            w = tk.Toplevel(self.app)
            w.overrideredirect(True)
            w.configure(bg=HIGHLIGHT)
            w.withdraw()
            self._frames.append(w)

    def _make_bubble(self):
        b = tk.Toplevel(self.app)
        b.overrideredirect(True)
        b.configure(bg="#1f2a36")
        try:
            b.transient(self.app)
        except tk.TclError:
            pass
        self._top = None
        box = ctk.CTkFrame(b, fg_color="#1f2a36", border_color=HIGHLIGHT, border_width=2, corner_radius=6)
        box.pack(fill="both", expand=True)
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(10, 0))
        self.title_lbl = ctk.CTkLabel(head, text="", font=("Segoe UI", 15, "bold"),
                                      text_color=HIGHLIGHT, anchor="w")
        self.title_lbl.pack(side="left", fill="x", expand=True)
        self.btn_close = ctk.CTkButton(head, text="✕", width=28, height=24, fg_color="#7f8c8d",
                                       hover_color="#95a5a6", command=self.close)
        self.btn_close.pack(side="right")
        self.text_lbl = ctk.CTkLabel(box, text="", justify="left", anchor="w", wraplength=360,
                                     text_color="#ecf0f1", font=("Segoe UI", 12))
        self.text_lbl.pack(fill="x", padx=12, pady=(6, 2))
        self.hint_lbl = ctk.CTkLabel(box, text="", anchor="w", text_color="#5dade2",
                                     font=("Segoe UI", 12, "bold"))
        self.hint_lbl.pack(fill="x", padx=12)
        nav = ctk.CTkFrame(box, fg_color="transparent")
        nav.pack(fill="x", padx=12, pady=(6, 10))
        self.counter_lbl = ctk.CTkLabel(nav, text="", text_color="gray")
        self.counter_lbl.pack(side="left")
        self.btn_next = ctk.CTkButton(nav, text="Далее ›", width=90, command=self.next)
        self.btn_next.pack(side="right")
        self.btn_back = ctk.CTkButton(nav, text="‹ Назад", width=80, fg_color="#5d6d7e",
                                      hover_color="#85929e", command=self.back)
        self.btn_back.pack(side="right", padx=6)
        b.bind("<Escape>", lambda e: self.close())
        self.bubble = b

    # ───────────── навигация ─────────────
    def show(self, index):
        self.index = max(0, min(index, len(self.steps) - 1))
        st = self.steps[self.index]
        n = len(self.steps) - 1  # приветствие не считаем шагом
        self.title_lbl.configure(text=st.title)
        self.text_lbl.configure(text=st.text)
        hint = ""
        if st.wait and st.wait not in self.done_events and st.hint:
            hint = "👉 " + st.hint
        self.hint_lbl.configure(text=hint)
        if self.index == 0:
            self.counter_lbl.configure(text=f"{n} шагов")
            self.btn_next.configure(text="Начать ›")
            self.btn_back.configure(text="Не сейчас" if self.offer else "‹ Назад",
                                    state="normal" if self.offer else "disabled")
        else:
            self.counter_lbl.configure(text=f"Шаг {self.index} из {n}")
            self.btn_back.configure(text="‹ Назад", state="normal")
            self.btn_next.configure(text="Готово ✓" if self.index == len(self.steps) - 1 else "Далее ›")
        self._last_geom = None
        self._place()

    def next(self):
        if self.index >= len(self.steps) - 1:
            self.close()
        else:
            self.show(self.index + 1)

    def back(self):
        if self.index == 0:
            self.close()  # «Не сейчас»
        else:
            self.show(self.index - 1)

    def event(self, name):
        """Приложение сообщает о действии пользователя."""
        if not self.active:
            return
        self.done_events.add(name)
        st = self.steps[self.index]
        if st.wait == name:
            # Небольшая пауза, чтобы пользователь увидел результат своего действия
            self.app.after(600, lambda i=self.index: self.index == i and self.active and self.next())

    def close(self):
        if not self.active:
            return
        self.active = False
        if self._job:
            try:
                self.app.after_cancel(self._job)
            except Exception:
                pass
        for w in self._frames + [self.bubble]:
            try:
                w.destroy()
            except Exception:
                pass
        if getattr(self.app, "_tour", None) is self:
            self.app._tour = None

    # ───────────── позиционирование ─────────────
    def target_widget(self, step=None):
        st = step or self.steps[self.index]
        t = st.target
        if t is None:
            return None
        w = t(self.app) if callable(t) else getattr(self.app, t, None)
        try:
            if w is not None and w.winfo_exists() and w.winfo_ismapped():
                return w
        except tk.TclError:
            pass
        return None

    def _place(self):
        if not self.active:
            return
        app = self.app
        try:
            if app.state() in ("iconic", "withdrawn"):
                for w in self._frames + [self.bubble]:
                    w.withdraw()
                self._last_geom = None
                return
        except tk.TclError:
            return
        target = self.target_widget()
        b = self.bubble
        b.update_idletasks()
        bw, bh = b.winfo_reqwidth(), b.winfo_reqheight()
        sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
        if target is not None:
            x, y = target.winfo_rootx(), target.winfo_rooty()
            w, h = target.winfo_width(), target.winfo_height()
            geom = (x, y, w, h, bw, bh)
            if geom == self._last_geom:
                return
            self._last_geom = geom
            p = BORDER + 1
            rects = [(x - p, y - p, w + 2 * p, BORDER), (x - p, y + h + 1, w + 2 * p, BORDER),
                     (x - p, y - p, BORDER, h + 2 * p), (x + w + 1, y - p, BORDER, h + 2 * p)]
            for fr, (rx, ry, rw, rh) in zip(self._frames, rects):
                _move(fr, f"{max(rw, 1)}x{max(rh, 1)}+{rx}+{ry}")
            # пузырь — под элементом, если не помещается — над ним, иначе справа/внутри
            bx = min(max(x, 4), sw - bw - 4)
            if y + h + p + 8 + bh < sh - 40:
                by = y + h + p + 8
            elif y - p - 8 - bh > 0:
                by = y - p - 8 - bh
            else:
                by = min(max(y + 20, 4), sh - bh - 40)
                bx = min(max(x + w + p + 8, 4), sw - bw - 4)
        else:
            ax, ay = app.winfo_rootx(), app.winfo_rooty()
            aw, ah = app.winfo_width(), app.winfo_height()
            geom = (ax, ay, aw, ah, bw, bh, "center")
            if geom == self._last_geom:
                return
            self._last_geom = geom
            for fr in self._frames:
                fr.withdraw()
            bx = ax + (aw - bw) // 2
            by = ay + (ah - bh) // 3
        _move(b, f"+{int(bx)}+{int(by)}")

    def _set_topmost(self, flag):
        """Поверх окна программы (иначе при повторном запуске пузырь уходил ПОД главное окно),
        но не поверх модальных окон (Настройки и т.п.) и чужих программ."""
        if flag == self._top:
            return
        self._top = flag
        for w in self._frames + [self.bubble]:
            try:
                w.attributes("-topmost", flag)
                if flag:
                    w.lift()
            except tk.TclError:
                pass

    def _follow(self):
        if not self.active:
            return
        try:
            self._set_topmost(_app_in_front() and self.app.grab_current() is None
                              and self.app.state() not in ("iconic", "withdrawn"))
        except (tk.TclError, KeyError):
            pass
        try:
            self._place()
            # держим пузырь над окном программы, но не над чужими окнами и диалогами
            if self.app.focus_displayof() is not None and self.app.grab_current() is None:
                self.bubble.lift()
        except tk.TclError:
            pass
        except Exception:
            pass
        self._job = self.app.after(250, self._follow)
