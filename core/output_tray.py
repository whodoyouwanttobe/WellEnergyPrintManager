"""Учёт заполнения лотка выдачи, чтобы он не переполнялся.

Ёмкость по паспорту:
  • HP LaserJet Pro MFP M435nw — выходной лоток до 250 листов;
  • HP LaserJet MFP M436 — выходной лоток до 250 листов;
  • Canon imagePROGRAF TM-305 — корзина: по паспорту 1 лист в обычном положении и
    20 листов A1/A2 в плоском. На практике (листы в корзине сворачиваются) влезает
    примерно 8 листов A2 — от этого и считаем: лист A2 = 1 место, A1 = 2, A0 = 4, A3 = 0.5.

Что было раньше: паузы ставились только внутри одного задания больше 150 листов.
Два задания по 140 листов подряд давали 280 листов в лотке на 250, а у плоттера
пауз не было совсем. Теперь счётчик лотка общий для всех заданий принтера: пауза
ставится там, где лоток реально заполнится, с учётом того, что уже напечатано.
"""
import re

LASER_CAPACITY = 250          # паспорт M435 и M436
PLOTTER_CAPACITY = 8          # корзина TM-305: ≈8 свёрнутых листов A2 (по опыту)
DEFAULT_FILL_PCT = 80         # лазерный лоток заполняем на 80% — запас на скрученную бумагу
A2_AREA = 420 * 594


_SIZES = {0: (841, 1189), 1: (594, 841), 2: (420, 594), 3: (297, 420), 4: (210, 297)}


def plotter_slots(fmt, w_mm=None, h_mm=None):
    """Сколько места в корзине плоттера занимает лист, в листах A2 (по площади):
    A2 = 1, A1 = 2, A0 = 4, A3 = 0.5, A2x3 = 3. Нестандартный лист — по размерам."""
    if w_mm and h_mm:
        return round(w_mm * h_mm / A2_AREA, 2)
    f = str(fmt).upper().replace(" ", "")
    m = re.match(r"A(\d)(?:X(\d+))?", f)
    if not m:
        return 1.0
    base, n = int(m.group(1)), int(m.group(2) or 1)
    short, long_ = _SIZES.get(base, _SIZES[2])
    return round(short * n * long_ / A2_AREA * 4) / 4      # A0 = 4, A4 = 0.25


def effective_capacity(capacity, fill_pct=DEFAULT_FILL_PCT):
    if not capacity or capacity <= 0:
        return 0                      # 0 — паузы не нужны
    return max(1, int(capacity * max(10, min(100, fill_pct)) / 100))


def _fits(room, per):
    """Сколько раз per помещается в room (с допуском на округление дробных мест)."""
    return int((room + 1e-6) // per) if per > 0 else 0


def plan_job(n_pages, copies, fill, capacity, slot=1):
    """Разбить задание (n_pages страниц × copies копий) так, чтобы лоток не переполнился.

    fill — сколько «мест» в лотке уже занято, capacity — допустимо (с запасом).
    → (шаги, новое_заполнение). Шаг: ("print", start, end, copies) — страницы [start:end]
      указанное число копий; или ("pause", заполнено_перед_паузой).
    """
    steps = []
    if n_pages <= 0 or copies <= 0:
        return steps, fill
    if not capacity:
        return [("print", 0, n_pages, copies)], fill + n_pages * copies * slot
    per_copy = n_pages * slot
    if per_copy <= capacity:
        rem = copies
        while rem > 0:
            fit = _fits(capacity - fill, per_copy)
            if fit <= 0:
                steps.append(("pause", fill))
                fill = 0
                continue
            k = min(rem, fit)
            steps.append(("print", 0, n_pages, k))
            fill += k * per_copy
            rem -= k
        return steps, fill
    # одна копия больше лотка — режем копию по страницам
    for _ in range(copies):
        start = 0
        while start < n_pages:
            room = _fits(capacity - fill, slot)
            if room <= 0:
                steps.append(("pause", fill))
                fill = 0
                continue
            end = min(n_pages, start + room)
            steps.append(("print", start, end, 1))
            fill += (end - start) * slot
            start = end
    return steps, fill


class TrayCounter:
    """Два счётчика на каждый принтер:

    • planned — сколько будет лежать в лотке после всех заданий в очереди (с учётом
      запланированных пауз). По нему ставятся паузы. Новое задание (файл, группа,
      страница) ПРИБАВЛЯЕТСЯ к нему, а не начинает счёт заново.
    • in_tray — сколько лежит сейчас: растёт, когда задание реально допечатано.
      Это число показывается рядом с состоянием принтера.

    Сами по себе счётчики не обнуляются — только когда вы подтвердили, что лоток пуст
    (ОК в окне «Заберите листы» или щелчок по индикатору лотка). Значения сохраняются
    в настройках и переживают перезапуск программы.
    """

    def __init__(self, saved=None, on_change=None):
        saved = saved or {}
        self.fill = {k: float(v.get("planned", 0)) for k, v in saved.items() if isinstance(v, dict)}
        self.now = {k: float(v.get("in_tray", 0)) for k, v in saved.items() if isinstance(v, dict)}
        self.on_change = on_change

    def _changed(self, printer):
        if self.on_change:
            try:
                self.on_change(printer)
            except Exception:
                pass

    def to_dict(self):
        names = set(self.fill) | set(self.now)
        return {n: {"planned": self.fill.get(n, 0), "in_tray": self.now.get(n, 0)} for n in names}

    # — планирование —
    def current(self, printer):
        return self.fill.get(printer, 0)

    def set(self, printer, value):
        self.fill[printer] = max(0, round(float(value), 2))
        self._changed(printer)

    # — что лежит сейчас —
    def in_tray(self, printer):
        return self.now.get(printer, 0)

    def delivered(self, printer, amount):
        """Задание реально допечатано — листы легли в лоток."""
        self.now[printer] = round(self.now.get(printer, 0) + max(0.0, float(amount)), 2)
        self._changed(printer)

    def pause_ok(self, printer):
        """ОК в окне «Заберите листы»: лоток пуст. planned уже учитывает эту паузу."""
        self.now[printer] = 0
        self._changed(printer)

    def set_in_tray(self, printer, amount):
        """Вы указали вручную, сколько лежит (или 0 — забрал всё без окна)."""
        amount = max(0.0, float(amount))
        removed = self.now.get(printer, 0) - amount
        self.now[printer] = round(amount, 2)
        if removed > 0:   # забрали часть листов — в плане их тоже больше нет
            self.fill[printer] = max(0, round(self.fill.get(printer, 0) - removed, 2))
        self._changed(printer)

    def reset(self, printer):
        """Очередь снята (Отмена у паузы) и лоток пуст."""
        self.fill[printer] = 0
        self.now[printer] = 0
        self._changed(printer)

    def sync_idle(self, printer):
        """Принтер ничего не печатает и очередь пуста: в лотке ровно то, что напечатано."""
        if abs(self.fill.get(printer, 0) - self.now.get(printer, 0)) > 0.01:
            self.fill[printer] = self.now.get(printer, 0)
            self._changed(printer)

    def emptied(self, printer, idle=True):
        """Вы забрали листы. Если принтер ничего не печатает — лоток пуст полностью;
        если печатает — из плана убираем только то, что уже лежало."""
        if idle:
            self.reset(printer)
        else:
            self.set_in_tray(printer, 0)
