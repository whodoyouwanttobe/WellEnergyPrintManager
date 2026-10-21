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
    assert dlg._items and dlg._items[0][1] == "5.0"
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
    tour = app.start_tour(mode="full")
    pump(app, 0.5)
    from ui.components.tour import TOUR_VERSION
    assert app.cfg.get("tour_seen_version") == TOUR_VERSION
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
    for name in ("plan_pane", "btn_help"):
        idx = next(i for i, st in enumerate(tour.steps) if st.target == name)
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
            w = st.target(app) if callable(st.target) else getattr(app, st.target, None)
            assert w is not None, st.title
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
    assert "ещё ≈140 в очереди" in app._tray_line("laser", "HP M436")


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


@needs_display
def test_tour_topmost_on_restart_and_covers_new_topics(app):
    t1 = app.start_tour()
    pump(app)
    t2 = app.start_tour(mode="full")             # повторный запуск — старый закрыт, новый поверх окна
    pump(app, 0.5)
    assert not t1.active and t2.active
    assert t2._top is True                # пузырь и рамка — поверх окна программы (на X11 без WM атрибут не читается)
    text = " ".join(st.title + " " + st.text for st in t2.steps)
    for topic in ("Печать по форматам", "Меню файла", "миниатюра", "справка", "имитация", "перетащите"):
        assert topic in text, topic
    t2.close()


@needs_display
def test_tour_simulates_printing(app, monkeypatch):
    sent = []
    monkeypatch.setattr(app.queue_manager, "add_job", lambda *a, **k: sent.append(a))
    tour = app.start_tour()
    idx = next(i for i, st in enumerate(tour.steps) if st.wait == "print_simulated")
    tour.show(idx)
    app._execute_print("C:/a.pdf", "A4", [1, 2, 3], copies=2)
    pump(app, 0.9)
    assert sent == []                                   # на принтер ничего не ушло
    assert any(t.key == "tour:sim" for t in app.notifier.toasts)
    assert app._sim_sheets == 6 and tour.index == idx + 1
    tour.close()
    for t in list(app.notifier.toasts):
        t.close()


@needs_display
def test_two_laser_printers_choice(app):
    app._system_printers = ["HP LaserJet Pro MFP M435 PCL 6", "HP MFP M436 PCL6", "Canon TM-305", "PDF"]
    app.cfg.data["laser_printers"] = ["HP LaserJet Pro MFP M435 PCL 6", "HP MFP M436 PCL6"]
    app.cfg.data.setdefault("printers", {}).setdefault("plotter", {})["name"] = "Canon TM-305"
    app.apply_printer_setup()
    assert list(app.laser_combo.cget("values")) == ["HP LaserJet Pro MFP M435 PCL 6", "HP MFP M436 PCL6"]
    assert app._current_laser == "HP LaserJet Pro MFP M435 PCL 6"
    assert app.plotter_printer_var.get() == "Canon TM-305"
    app._on_laser_changed("HP MFP M436 PCL6")
    assert app._current_laser == "HP MFP M436 PCL6" and "Опрос" in app.laser_status_lbl.cget("text")


@needs_display
def test_settings_printers_and_engines_tabs(app):
    app._system_printers = ["HP A", "HP B", "Canon TM-305"]
    app.open_settings()
    pump(app)
    dlg = app._settings_dlg
    rows = dlg._device_rows
    rows[0][1].set("HP A")
    rows[1][1].set("HP B")
    rows[2][1].set("Canon TM-305")
    rows[1][2].insert(0, "10.0.0.7")
    assert "Foxit" in dlg.foxit_status.cget("text")
    assert not hasattr(dlg, "engine_var")              # выбора движка больше нет
    dlg.save()
    pump(app)
    assert app.cfg.get("laser_printers") == ["HP A", "HP B"]
    assert app.cfg.get("printer_hosts")["HP B"] == "10.0.0.7"
    assert list(app.laser_combo.cget("values")) == ["HP A", "HP B"]


@needs_display
def test_paper_out_keeps_tray_counter_and_blocks_pause(app):
    app._current_laser = "HP M436"
    app.trays.set("HP M436", 150)
    tid = app.print_tracker.add("a.pdf", "A4", 50, 1, "HP M436", "laser", 1)
    app.print_tracker.start(tid)
    app.print_tracker.spooled(tid)
    app._apply_printer_state("laser", "HP M436", {"status": 0x10, "jobs": [{"Status": 0x40}]}, None, None)
    import time
    time.sleep(1.2)                                # по расчёту задание уже «напечатано»…
    app.print_tracker.tick()
    assert not app._printer_finished("HP M436")    # …но принтер стоит без бумаги — окно не показываем
    assert app.trays.current("HP M436") == 150     # счётчик лотка не сбился и не обнулился
    app._apply_printer_state("laser", "HP M436", {"status": 0, "jobs": []}, None, None)
    assert app.trays.current("HP M436") == 150
    for t in list(app.notifier.toasts):
        t.close()


@needs_display
def test_tray_indicator_and_click_menu(app):
    app._current_laser = "HP M436"
    app.cfg.data.update(laser_tray_capacity=250, tray_fill_pct=80)
    app.trays.set("HP M436", 150)
    app.trays.delivered("HP M436", 120)
    app._refresh_tray_labels()
    assert app.laser_tray_lbl.cget("text") == "📥 120/200 (+30)"
    assert app.cfg.get("tray_fill")["HP M436"]["in_tray"] == 120      # сохранено в настройках
    app.tray_emptied("HP M436")                                       # принтер простаивает — пусто полностью
    assert app.laser_tray_lbl.cget("text") == "📥 0/200"


@needs_display
def test_done_job_lands_in_tray(app):
    app._current_plotter = "Canon TM-305"
    tid = app.print_tracker.add("a.pdf", "A1", 2, 1, "Canon TM-305", "plotter", 1, slots=4)
    app.print_tracker.start(tid)
    app.print_tracker.spooled(tid)
    app.trays.set("Canon TM-305", 4)
    import time
    time.sleep(5.2)
    app._print_progress_loop()
    assert app.trays.in_tray("Canon TM-305") == 4
    assert app.plotter_tray_lbl.cget("text").startswith("🧺 4/8")


@needs_display
def test_m436_generic_error_not_spammed(app):
    app._current_laser = "HP MFP M436 PCL6"
    st = {"status": 0x2, "attributes": 0, "jobs": []}          # только общий флаг «ошибка» из Windows
    app._apply_printer_state("laser", "HP MFP M436 PCL6", st, None, None)
    app._apply_printer_state("laser", "HP MFP M436 PCL6", st, None, None)
    assert not app.notifier.toasts                               # меньше 20 с — не всплывает
    app._on_printer_alert("HP MFP M436 PCL6", "Возможная ошибка принтера")
    pump(app)
    assert not app.notifier.toasts
    # по сети принтер говорит, что всё в порядке — флаги Windows про бумагу не считаются
    st2 = {"status": 0x10, "attributes": 0, "jobs": []}
    app._apply_printer_state("laser", "HP MFP M436 PCL6", st2, {"printer_status": 3, "errors": b"\x00\x00"}, "10.0.0.5")
    assert "Готов" in app.laser_status_lbl.cget("text")


def _wait(app, cond, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        if cond():
            return True
        time.sleep(0.03)
    return False


@needs_display
def test_choice_dialog_buttons_named_by_action(app):
    from ui.components import choice_dialog
    seen = {}

    def press():
        dlg = [w for w in app.winfo_children() if isinstance(w, choice_dialog.ChoiceDialog)][0]
        seen["labels"] = [b.cget("text") for b in dlg._buttons.values()]
        dlg._buttons["missing"].invoke()
    app.after(300, press)
    r = choice_dialog.ask_choice(app, "Печать", "Уже напечатано 3 из 6",
                                 [("missing", "Допечатать недостающее (3 л.)", "main"),
                                  ("all", "Напечатать всё заново (6 л.)", "danger"), (None, "Отмена", "cancel")])
    assert r == "missing" and seen["labels"][0].startswith("Допечатать")


@needs_display
def test_drop_folder_opens_it(app, tmp_path):
    from ui.selftest import make_sample_pdfs
    make_sample_pdfs(str(tmp_path))
    app._on_drop([str(tmp_path)])
    assert app.current_folder == str(tmp_path)
    assert _wait(app, lambda: app.analyzed_data)


@needs_display
def test_interrupted_print_is_offered_to_undo(app, monkeypatch):
    from ui.components import choice_dialog
    job = type("J", (), {"filepath": "C:/x.pdf", "tracker_id": 7, "printer_name": "HP"})()
    app._inflight_add(job, [1, 2], 1, "A4")
    assert len(app.cfg.get("inflight_jobs")) == 1
    app._inflight_done([7])
    assert app.cfg.get("inflight_jobs") == []
    app.history.add_printed("C:/x.pdf", [1, 2], 1, "A4")
    app._stale_inflight = [{"id": "old:1", "file": "C:/x.pdf", "pages": [1, 2], "copies": 1, "fmt": "A4"}]
    monkeypatch.setattr(choice_dialog, "ask_choice", lambda *a, **k: "undo")
    app._ask_stale_inflight()
    assert app.history.get_printed("C:/x.pdf")[1]["printed"] == 0


@needs_display
def test_big_print_button_and_ctrl_p_confirm(app, monkeypatch, tmp_path):
    from ui.selftest import make_sample_pdfs
    from ui.components import choice_dialog
    make_sample_pdfs(str(tmp_path))
    app._on_drop([str(tmp_path)])
    assert _wait(app, lambda: app.analyzed_data and app._scan_job is None)
    pump(app)
    assert app.btn_print.cget("text").startswith("🖨 Напечатать 5 л.")
    called = []
    monkeypatch.setattr(app, "print_all", lambda: called.append(1))
    monkeypatch.setattr(choice_dialog, "ask_choice", lambda *a, **k: None)
    app._print_all_confirm()
    assert called == []                                          # без подтверждения не печатает
    monkeypatch.setattr(choice_dialog, "ask_choice", lambda *a, **k: "go")
    app._print_all_confirm()
    assert called == [1]


@needs_display
def test_help_menu_has_bug_report_and_tours(app):
    import tkinter as tk
    menus = []
    orig = tk.Menu.tk_popup
    tk.Menu.tk_popup = lambda self, x, y, entry="": menus.append(self)
    try:
        app._help_menu()
    finally:
        tk.Menu.tk_popup = orig
    labels = [menus[0].entrycget(i, "label") for i in range(menus[0].index("end") + 1)
              if menus[0].type(i) == "command"]
    assert any("Сообщить об ошибке" in l for l in labels)
    assert any("Быстрое обучение" in l for l in labels) and any("Подробное обучение" in l for l in labels)




@needs_display
def test_explorer_print_flow(app, monkeypatch, tmp_path):
    """Проводник → окно: форматы и копии → печать → главное окно с прогрессом → один вопрос о закрытии."""
    from ui.selftest import make_sample_pdfs
    from ui.components import choice_dialog
    pdf = make_sample_pdfs(str(tmp_path))
    sent = []
    monkeypatch.setattr(app, "_execute_print", lambda fp, fmt, pgs, copies=1: sent.append((fmt, len(pgs), copies)))
    asked = []
    monkeypatch.setattr(choice_dialog, "ask_choice", lambda *a, **k: asked.append(a[2]) or "stay")
    iconified = []
    monkeypatch.setattr(app, "iconify", lambda: iconified.append(1))
    app.begin_quick_print(str(tmp_path), fresh=True)
    dlg = app._quick_dialog
    assert dlg is not None and iconified                         # сначала только окно выбора
    assert _wait(app, lambda: dlg.ready)
    assert set(dlg.format_vars) == {"A4", "A3", "A2", "A1", "A4x4"}
    dlg.format_vars["A1"].set(False)
    dlg.copies_var.set("2")
    dlg.refresh()
    assert "8 л." in dlg.total.cget("text")
    dlg._print()
    pump(app)
    assert app.state() == "normal"
    assert sorted(f for f, _n, _c in sent) == ["A2", "A3", "A4", "A4x4"] and all(c == 2 for *_x, c in sent)
    assert app.target_copies.get() == 2 and app.current_folder == str(tmp_path)
    tid = app.print_tracker.add(pdf, "A4", 1, 1, "HP", "laser", 1)
    app.print_tracker.start(tid)
    app._check_quick_session_done()
    assert app._quick_session["started"] is True and not asked
    app.print_tracker.spooled(tid)
    time.sleep(5.1)
    app.print_tracker.tick()
    app._check_quick_session_done()
    app._check_quick_session_done()
    assert len(asked) == 1 and "Закрыть программу" in asked[0]


def _make_pdf(path, w, h):
    import fitz
    d = fitz.open()
    d.new_page(width=w, height=h)
    d.save(str(path))
    d.close()
    return str(path)


@needs_display
def test_explorer_several_files_from_different_folders_queue(app, monkeypatch, tmp_path):
    """Выделили PDF из двух папок + потом ещё один — всё в одном окне, по строке на источник."""
    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    a1 = _make_pdf(tmp_path / "A" / "a1.pdf", 595, 842)
    a2 = _make_pdf(tmp_path / "A" / "a2.pdf", 1684, 2384)
    b1 = _make_pdf(tmp_path / "B" / "b1.pdf", 842, 1191)
    junk = tmp_path / "A" / "смета.docx"
    junk.write_bytes(b"x")
    app.begin_quick_print([a1, str(junk), b1])
    dlg = app._quick_dialog
    assert _wait(app, lambda: dlg.ready)
    assert len(dlg.sources) == 2 and sorted(dlg.files()) == sorted([a1, b1])
    assert "не PDF: 1" in dlg.note_lbl.cget("text")
    from core.print_request import write_request
    write_request(a2)                                    # дописали ещё один файл из папки A
    app._poll_print_request()
    assert _wait(app, lambda: dlg.ready and a2 in dlg.files())
    assert len(dlg.sources) == 2                          # дописан в строку папки A, а не новой строкой
    assert set(dlg.format_vars) == {"A4", "A3", "A1"}
    sent = []
    monkeypatch.setattr(app, "_execute_print", lambda fp, fmt, pgs, copies=1: sent.append(os.path.basename(fp)))
    dlg._print()
    pump(app)
    assert sorted(sent) == ["a1.pdf", "a2.pdf", "b1.pdf"]


@needs_display
def test_new_task_goes_to_end_of_queue_and_shown(app, monkeypatch, tmp_path):
    """Идёт печать, добавили ещё задание → оно в конце очереди и видно в «Очереди заданий»."""
    added = []
    monkeypatch.setattr(app.queue_manager, "add_job",
                        lambda *a, **k: (added.append(os.path.basename(a[0])), type("J", (), {})())[1])
    monkeypatch.setattr(app.queue_manager, "start", lambda: None)
    app.laser_printer_var.set("HP")
    app._job_data["C:/x/first.pdf"] = {"pages": [(1, "A4", 210, 297)]}
    app._job_data["C:/y/second.pdf"] = {"pages": [(1, "A4", 210, 297)]}
    with app._task("📄 first.pdf"):
        app._execute_print("C:/x/first.pdf", "A4", [1], copies=1)
    first_ids = list(app.tasks[0]["ids"])
    app.print_tracker.start(first_ids[0])                  # первое уже печатается
    a1 = _make_pdf(tmp_path / "second.pdf", 595, 842)
    app.begin_quick_print([a1])
    dlg = app._quick_dialog
    assert "встанет в конец очереди" in dlg.busy_lbl.cget("text")
    assert _wait(app, lambda: dlg.ready)
    dlg._print()
    pump(app)
    assert added == ["first.pdf", "second.pdf"]            # порядок очереди сохранён
    app._render_tasks()
    texts = [w.cget("text") for w in app.tasks_frame.winfo_children()]
    assert any("first.pdf" in t and "печатается" in t for t in texts)
    assert any("second.pdf" in t and "в очереди (1)" in t for t in texts)


@needs_display
def test_quick_cancel_keeps_running_print(app, monkeypatch, tmp_path):
    a1 = _make_pdf(tmp_path / "x.pdf", 595, 842)
    closed = []
    monkeypatch.setattr(app, "_on_close", lambda: closed.append(1))
    app.begin_quick_print([a1], fresh=True)
    dlg = app._quick_dialog
    assert _wait(app, lambda: dlg.ready)
    dlg._cancel()
    assert closed == [1]                                 # открыли только ради этой печати — закрываемся
