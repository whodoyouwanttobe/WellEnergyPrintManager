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
    """Сколько листов будет лежать в лотке каждого принтера после всех заданий в очереди
    (по расчёту, с учётом запланированных пауз). Сам по себе счётчик НЕ обнуляется:
    только когда вы подтвердили, что лоток пуст (ОК в окне «Заберите листы» или
    «Я забрал листы» в меню значка принтера)."""

    def __init__(self):
        self.fill = {}             # принтер -> занято мест

    def current(self, printer):
        return self.fill.get(printer, 0)

    def set(self, printer, value):
        self.fill[printer] = max(0, round(float(value), 2))

    def emptied(self, printer):
        self.fill[printer] = 0
