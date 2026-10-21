"""Лоток выдачи не переполняется: счётчик общий для всех заданий принтера."""
import threading
import time

from core.output_tray import (TrayCounter, effective_capacity, plan_job, plotter_slots,
                              LASER_CAPACITY, PLOTTER_CAPACITY)


def sheets(steps):
    return sum((b - a) * k for kind, *rest in steps if kind == "print" for a, b, k in [rest])


def test_capacities():
    assert LASER_CAPACITY == 250 and PLOTTER_CAPACITY == 8      # 8 свёрнутых A2 в корзине
    assert effective_capacity(250, 80) == 200 and effective_capacity(0, 80) == 0


def test_two_jobs_share_one_tray():
    """Раньше: 140 + 140 листов = 280 в лотке на 250 (паузы были только внутри задания > 150)."""
    steps1, fill = plan_job(140, 1, 0, 200)
    assert steps1 == [("print", 0, 140, 1)] and fill == 140
    steps2, fill = plan_job(140, 1, fill, 200)
    assert steps2[0] == ("pause", 140) and steps2[1] == ("print", 0, 140, 1) and fill == 140


def test_copies_are_grouped_between_pauses():
    steps, fill = plan_job(30, 10, 0, 200)        # 300 листов, по 6 копий (180 л.) за раз
    assert steps == [("print", 0, 30, 6), ("pause", 180), ("print", 0, 30, 4)]
    assert sheets(steps) == 300 and fill == 120


def test_copy_bigger_than_tray_is_split_by_pages():
    steps, fill = plan_job(450, 1, 50, 200)
    prints = [s for s in steps if s[0] == "print"]
    assert prints[0] == ("print", 0, 150, 1)      # сначала добивает лоток до 200
    assert sheets(steps) == 450 and all(b - a <= 200 for _, a, b, _k in prints)
    assert [s[0] for s in steps].count("pause") == 2


def test_no_capacity_means_no_pauses():
    steps, _ = plan_job(500, 3, 0, 0)
    assert steps == [("print", 0, 500, 3)]


def test_plotter_slots_in_a2_units():
    assert plotter_slots("A2") == 1 and plotter_slots("A1") == 2 and plotter_slots("A0") == 4
    assert plotter_slots("A3") == 0.5 and plotter_slots("A2x3") == 3
    assert plotter_slots("610x900", 610, 900) == 2.2


def test_basket_8_a2():
    steps, fill = plan_job(10, 1, 0, 8, slot=1)          # 10 листов A2 в корзину на 8
    assert steps == [("print", 0, 8, 1), ("pause", 8), ("print", 8, 10, 1)] and fill == 2
    steps, fill = plan_job(3, 2, 0, 8, slot=2)           # A1 × 3 листа × 2 копии: по 4 A1
    assert [s[0] for s in steps].count("pause") == 1 and sheets(steps) == 6
    steps, _ = plan_job(4, 1, 0, 8, slot=0.5)            # A3 по 0.5 — 4 листа без пауз
    assert steps == [("print", 0, 4, 1)]


def test_counter_never_resets_by_itself():
    c = TrayCounter()
    c.set("HP", 150)
    assert c.current("HP") == 150
    c.emptied("HP")                                      # только по подтверждению человека
    assert c.current("HP") == 0


def test_pause_waits_until_really_printed():
    from printing.queue_manager import PrintQueueManager
    qm = PrintQueueManager()
    printing = {"busy": True}
    shown = []

    def on_pause(job):
        shown.append(time.monotonic())
        job.event.set()
    qm.on_pause_request = on_pause
    t0 = time.monotonic()
    qm.add_pause("HP", "Заберите листы", wait_until=lambda: not printing["busy"])
    threading.Timer(0.8, lambda: printing.update(busy=False)).start()
    qm.start()
    for _ in range(60):
        if shown:
            break
        time.sleep(0.05)
    qm.stop()
    assert shown and shown[0] - t0 >= 0.7       # окно — только после окончания печати


def test_new_jobs_add_to_counter_and_runtime_fill():
    c = TrayCounter()
    steps, fill = plan_job(50, 1, c.current("HP"), 200)
    c.set("HP", fill)
    steps, fill = plan_job(30, 1, c.current("HP"), 200)       # следующая страница/группа
    c.set("HP", fill)
    assert c.current("HP") == 80 and c.in_tray("HP") == 0     # в плане 80, напечатано пока 0
    c.delivered("HP", 50)
    assert c.in_tray("HP") == 50
    c.set_in_tray("HP", 0)                                     # забрал 50 л. во время печати
    assert c.in_tray("HP") == 0 and c.current("HP") == 30      # в плане остались ещё 30
    c.delivered("HP", 30)
    c.pause_ok("HP")
    assert c.in_tray("HP") == 0


def test_counter_persists():
    saved = {}
    c = TrayCounter(on_change=lambda p: saved.update(c.to_dict()))
    c.set("Canon", 5)
    c.delivered("Canon", 3)
    c2 = TrayCounter(saved)
    assert c2.current("Canon") == 5 and c2.in_tray("Canon") == 3


def test_sync_idle_fixes_drift():
    c = TrayCounter()
    c.set("HP", 120)
    c.delivered("HP", 100)            # 20 л. не допечатались (ошибка задания)
    c.sync_idle("HP")
    assert c.current("HP") == 100
