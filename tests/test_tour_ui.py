"""Окно справки и интерактивное обучение в живом окне программы."""
import os
import sys
import time

import pytest

pytestmark = pytest.mark.gui
needs_display = pytest.mark.skipif(sys.platform != "win32" and not os.environ.get("DISPLAY"),
                                   reason="нет дисплея для окон Tk")


@pytest.fixture
def app(monkeypatch):
    from tkinter import messagebox
    for n in ("showinfo", "showerror", "showwarning"):
        monkeypatch.setattr(messagebox, n, lambda *a, **k: None)
    from core.config import get_config
    get_config().data.update(check_updates=False, last_folder="")
    from ui.app import PrintManagerApp
    a = PrintManagerApp(splash=None)
    a.update()
    yield a
    a.destroy()


def pump(a, sec=0.3):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        a.update()
        time.sleep(0.02)


@needs_display
def test_help_window_opens_tabs_search_and_topic(app):
    dlg = app.open_help()
    pump(app)
    assert dlg is not None and dlg.winfo_exists()
    assert "редакция от" in dlg.revision_label.cget("text")
    assert dlg.tabs == ["Вопросы и ответы", "Документация", "Что нового"]
    dlg._show_tab("Что нового")
    assert dlg._items and dlg._items[0][1] == "4.9.1"
    dlg.search_var.set("сшит")
    dlg._on_search()
    assert dlg._items
    assert dlg._open_topic("Пробная печать")
    assert "Пробная печать" in dlg.text.get("1.0", "3.0")
    # повторное открытие не плодит окна
    assert app.open_help() is dlg
    dlg.destroy()


@needs_display
def test_tour_walks_steps_highlights_and_closes(app):
    tour = app.start_tour()
    pump(app, 0.5)
    assert app.cfg.get("tour_offered") is True
    assert tour.bubble.winfo_viewable()
    assert "из" not in tour.counter_lbl.cget("text")          # приветствие
    tour.next()
    pump(app)
    assert tour.counter_lbl.cget("text") == f"Шаг 1 из {len(tour.steps) - 1}"
    # шаг 1 подсвечивает «Открыть папку»: рамка рядом с кнопкой
    b = app.btn_open
    top = tour._frames[0]
    assert abs(top.winfo_x() - b.winfo_rootx()) < 10 and abs(top.winfo_y() - b.winfo_rooty()) < 10
    assert "Открыть папку" in tour.hint_lbl.cget("text")
    # действие пользователя переводит на следующий шаг само
    app._tour_event("folder_opened")
    pump(app, 0.9)
    assert tour.index == 2
    # рамка ПЕРЕЕХАЛА к новому элементу (раньше на Linux/Tk оставалась на старом месте)
    for idx, name in ((8, "plan_pane"), (12, "btn_help")):
        tour.show(idx)
        pump(app)
        w = getattr(app, name)
        assert abs(top.winfo_rootx() - w.winfo_rootx()) < 10 and abs(top.winfo_rooty() - w.winfo_rooty()) < 10, name
    tour.show(2)
    tour.back()
    assert tour.index == 1
    # у всех шагов с целью виджет существует в окне
    for st in tour.steps:
        if st.target:
            assert getattr(app, st.target, None) is not None, st.target
    tour.btn_close.invoke()     # ✕
    pump(app)
    assert app._tour is None and not tour.bubble.winfo_exists()
    assert not any(f.winfo_exists() for f in tour._frames)


@needs_display
def test_tour_offer_not_now_closes(app):
    tour = app.start_tour(offer=True)
    pump(app)
    assert tour.btn_back.cget("text") == "Не сейчас"
    tour.back()
    pump(app)
    assert app._tour is None


@needs_display
def test_update_dialog_not_shown_over_tour(app, monkeypatch):
    monkeypatch.setattr(app, "_print_busy_jobs", lambda: 0)
    app.start_tour()
    app._on_update_found({"version": "v9.9.9", "changelog": "-", "channel": "stable",
                          "asset": {}, "sha_asset": None}, startup=True)
    pump(app)
    assert app.btn_update.winfo_ismapped()
    assert getattr(app, "_update_dlg", None) is None
    app._tour.close()
    app._update_state = None


@needs_display
def test_result_view_no_checkmark_while_printing():
    import customtkinter as ctk
    from collections import OrderedDict
    from ui.components.result_view import ResultView
    root = ctk.CTk()
    try:
        rv = ResultView(root)
        rv.pack()
        info = [(1, "A4", 210, 297, "1", False, False, 1000), (2, "A4", 210, 297, "2", False, False, 900_000)]
        fmts = OrderedDict([("A4", "1-2")])
        printed = {1: {"printed": 1}, 2: {"printed": 1}}
        rv.display_results("C:/a.pdf", info, fmts, printed, 1, printing_formats={"A4"}, printing_pages={1, 2})
        text = rv.result_text.get("1.0", "end")
        assert "Завершен" not in text and "печатается" in text
        assert "⚡ тяжёлые: стр. 2" in text          # тяжёлая страница внутри группы A4
        rows = [rv.detail_tree.item(i, "values") for i in rv.detail_tree.get_children()]
        assert rows[0][-1] == "🖨 печать" and rows[1][-2].startswith("⚡")
        rv.display_results("C:/a.pdf", info, fmts, printed, 1)
        assert "✔ Завершен" in rv.result_text.get("1.0", "end")
        assert rv.result_text_map[1][5] == [2]
    finally:
        root.destroy()


@needs_display
def test_printer_problem_popup_and_busy_label(app):
    app._current_plotter = "Canon TM-305"
    jam = {"status": 0x8, "attributes": 0, "jobs": [{"Status": 0x10, "pStatus": ""}]}
    app._apply_printer_state("plotter", "Canon TM-305", jam, None, None)
    pump(app)
    assert "Замятие" in app.plotter_status_lbl.cget("text")
    toasts = [t for t in app.notifier.toasts if t.key == "Canon TM-305:jam"]
    assert toasts and toasts[0].winfo_exists()
    assert "Печать стоит" in toasts[0].text_lbl.cget("text")
    # повтор того же состояния — второго окна нет
    app._apply_printer_state("plotter", "Canon TM-305", jam, None, None)
    assert len([t for t in app.notifier.toasts if t.key.endswith(":jam")]) == 1
    # устранено: окно аварии закрылось, короткое «✓ устранено»
    tid = app.print_tracker.add("a.pdf", "A1", 1, 1, "Canon TM-305", "plotter", 300)
    app.print_tracker.start(tid)
    app.print_tracker.spooled(tid)
    app._apply_printer_state("plotter", "Canon TM-305", {"status": 0, "attributes": 0, "jobs": []}, None, None)
    pump(app)
    assert not any(t.key == "Canon TM-305:jam" for t in app.notifier.toasts)
    assert any(t.key == "Canon TM-305:jam:ok" for t in app.notifier.toasts)
    # Windows пишет «Готов», но задание ещё печатается по расчёту — значок «Печатает»
    assert "Печатает A1" in app.plotter_status_lbl.cget("text")
    for t in list(app.notifier.toasts):
        t.close()


@needs_display
def test_offline_idle_printer_no_popup(app):
    app._current_laser = "HP M436"
    app._apply_printer_state("laser", "HP M436", {"status": 0x80, "attributes": 0, "jobs": []}, None, None)
    pump(app)
    assert "Отключен" in app.laser_status_lbl.cget("text")
    assert not app.notifier.toasts


@needs_display
def test_failed_poll_does_not_resolve_problems(app):
    app._current_laser = "HP M436"
    busy_jam = {"status": 0x8, "attributes": 0, "jobs": [{"Status": 0x10}]}
    app._apply_printer_state("laser", "HP M436", busy_jam, None, None)
    app._apply_printer_state("laser", "HP M436", None, None, None)    # очередь не открылась
    assert any(t.key == "HP M436:jam" for t in app.notifier.toasts)
    assert not any(t.key.endswith(":ok") for t in app.notifier.toasts)
    for t in list(app.notifier.toasts):
        t.close()


@needs_display
def test_failed_poll_keeps_label_briefly(app):
    app._current_plotter = "Canon TM-305"
    app._apply_printer_state("plotter", "Canon TM-305", {"status": 0x10, "jobs": []}, None, None)
    app._apply_printer_state("plotter", "Canon TM-305", None, None, None)
    assert "Нет бумаги" in app.plotter_status_lbl.cget("text")
    for _ in range(5):
        app._apply_printer_state("plotter", "Canon TM-305", None, None, None)
    assert "Нет связи" in app.plotter_status_lbl.cget("text")
    for t in list(app.notifier.toasts):
        t.close()


@needs_display
def test_execute_print_inserts_pause_across_jobs(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app.queue_manager, "add_job", lambda *a, **k: calls.append(("job", k["copies"], len(k["pages"]))) or None)
    monkeypatch.setattr(app.queue_manager, "add_pause", lambda *a, **k: calls.append(("pause",)))
    monkeypatch.setattr(app.queue_manager, "start", lambda: None)
    app.laser_printer_var.set("HP M436")
    app.cfg.data.update(laser_tray_capacity=250, tray_fill_pct=80)
    app._execute_print("C:/a.pdf", "A4", list(range(1, 141)), copies=1)
    app._execute_print("C:/b.pdf", "A3", list(range(1, 141)), copies=1)
    assert calls == [("job", 1, 140), ("pause",), ("job", 1, 140)]
    assert "140 из 200" in app._tray_line("laser", "HP M436")


@needs_display
def test_plotter_basket_and_manual_empty(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app.queue_manager, "add_job", lambda *a, **k: calls.append(("job", len(k["pages"]))) or None)
    monkeypatch.setattr(app.queue_manager, "add_pause", lambda *a, **k: calls.append(("pause", a[1])))
    monkeypatch.setattr(app.queue_manager, "start", lambda: None)
    app.plotter_printer_var.set("Canon TM-305")
    app.analyzed_data["C:/p.pdf"] = {"pages": [(i, "A2", 420, 594, str(i), True, False, 0) for i in range(1, 11)]}
    app._execute_print("C:/p.pdf", "A2", list(range(1, 11)), copies=1)
    assert [c[0] for c in calls] == ["job", "pause", "job"] and calls[0][1] == 8
    assert "Корзина плоттера" in calls[1][1] and "только после" in calls[1][1]
    assert app.trays.current("Canon TM-305") == 2
    app.tray_emptied("Canon TM-305")
    assert app.trays.current("Canon TM-305") == 0
