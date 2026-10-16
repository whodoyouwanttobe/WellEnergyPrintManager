"""Самопроверка программы: «ничего не висит, все окна открываются».

Запуск:
    WPM.exe --self-test                 (результат: код выхода 0/1 + файл отчёта)
    python main.py --self-test
    pytest tests/test_ui_smoke.py       (то же самое из тестов)

Что проверяется (ничего не печатается, реальные настройки не трогаются —
используется временная папка вместо %APPDATA%):
  1. главное окно создаётся;
  2. анализ папки с тестовыми PDF завершается и не блокирует интерфейс
     (интерфейс должен откликаться не реже, чем раз в MAX_UI_GAP секунд);
  3. анализ можно отменить, и выбор новой папки сразу работает;
  4. Настройки, Отчёт, Пробная печать, Сообщить об ошибке открываются с содержимым и закрываются;
  5. логика выбора обновлений (без сети).
"""
import os
import sys
import time
import traceback

MAX_UI_GAP = 2.0       # секунд без отклика интерфейса = «зависание»
SCAN_TIMEOUT = 90.0


def prepare_environment(tmp_dir):
    """Вызывать ДО импорта core.config: подменяем APPDATA на временную папку."""
    os.environ["APPDATA"] = tmp_dir
    os.environ["WPM_DATA_DIR"] = os.path.join(tmp_dir, "WellEnergyPrintManager")
    os.environ["WPM_NO_TOUR"] = "1"   # обучение проверяем отдельно, без автопредложения
    os.makedirs(os.path.join(tmp_dir, "WellEnergyPrintManager"), exist_ok=True)


def make_sample_pdfs(folder, many=0):
    import fitz
    os.makedirs(folder, exist_ok=True)
    mm = 72 / 25.4
    sizes = [(210, 297), (420, 297), (841, 594), (297, 841), (594, 420)]  # A4, A3, A1, A4x4, A2
    doc = fitz.open()
    for w, h in sizes:
        page = doc.new_page(width=w * mm, height=h * mm)
        page.insert_text((40, 60), f"WPM self-test {w}x{h}", fontsize=24)
        page.draw_rect(page.rect + (20, 20, -20, -20), color=(0, 0, 0), width=2)
    path = os.path.join(folder, "selftest_mixed.pdf")
    doc.save(path)
    doc.close()
    for i in range(many):
        d = fitz.open()
        d.new_page(width=210 * mm, height=297 * mm).insert_text((40, 60), f"file {i}")
        d.save(os.path.join(folder, f"bulk_{i:03d}.pdf"))
        d.close()
    return path


class _Result:
    def __init__(self):
        self.lines = []
        self.ok = True

    def check(self, cond, name, detail=""):
        self.lines.append(f"[{'OK ' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        if not cond:
            self.ok = False
        return cond

    def text(self):
        return "\n".join(self.lines) + f"\n\nИТОГ: {'УСПЕХ' if self.ok else 'ОШИБКИ'}\n"


def _count_widgets(w):
    n = 0
    for c in w.winfo_children():
        n += 1 + _count_widgets(c)
    return n


def run_self_test(work_dir):
    """Возвращает (ok, текст_отчёта). Должна вызываться после prepare_environment()."""
    import tkinter as tk
    res = _Result()
    folder_a = os.path.join(work_dir, "pdf_a")
    folder_b = os.path.join(work_dir, "pdf_b")
    try:
        make_sample_pdfs(folder_a)
        make_sample_pdfs(folder_b, many=40)
    except Exception as e:
        res.check(False, "создание тестовых PDF", repr(e))
        return res.ok, res.text()

    from core.config import get_config
    cfg = get_config()
    cfg.data["last_folder"] = ""        # анализ запустим сами
    cfg.data["check_updates"] = False   # без сети
    cfg.save()

    from tkinter import messagebox
    shown = []
    # Любое всплывающее окно в самопроверке = ошибка, но не должно блокировать тест
    for name in ("showerror", "showwarning", "showinfo"):
        setattr(messagebox, name, lambda *a, _n=name, **k: shown.append((_n, a)))
    messagebox.askyesno = lambda *a, **k: False
    messagebox.askokcancel = lambda *a, **k: False

    from ui.app import PrintManagerApp

    # 0. Окно видно после заставки (в 4.9.0 оно пряталось в Windows сразу после заставки)
    try:
        # В собранном exe main.py — это __main__ (импортировать его как "main" нельзя)
        make_splash = getattr(sys.modules.get("__main__"), "show_splash", None)
        if make_splash is None:
            def make_splash():
                sp = tk.Tk()
                sp.overrideredirect(True)
                sp.subtitle = tk.Label(sp, text="self-test")
                sp.subtitle.pack()
                sp.update()
                return sp
        splash = make_splash()
        probe = PrintManagerApp(splash=splash)
        seen = {}

        def _probe_check():
            seen["state"] = probe.state()
            seen["viewable"] = probe.winfo_viewable()
            probe.quit()

        probe.after(1500, _probe_check)
        probe.after(10000, probe.quit)  # страховка от зависания
        probe.mainloop()
        res.check(seen.get("state") == "normal" and seen.get("viewable") == 1,
                  "главное окно видно после заставки", str(seen))
        probe._on_close()
    except Exception as e:
        res.check(False, "главное окно видно после заставки", f"{type(e).__name__}: {e}")

    app = PrintManagerApp(splash=None)
    res.check(True, "главное окно создано")

    gaps = {"max": 0.0, "last": time.monotonic()}

    def heartbeat():
        now = time.monotonic()
        gaps["max"] = max(gaps["max"], now - gaps["last"])
        gaps["last"] = now
        app.after(50, heartbeat)

    def pump_until(cond, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.update()
            if cond():
                return True
            time.sleep(0.02)
        return False

    try:
        app.update()
        heartbeat()

        # 3. отмена анализа большой папки и перевыбор
        app.current_folder = folder_b
        app.start_analysis()
        pump_until(lambda: False, 0.5)
        t0 = time.monotonic()
        app.cancel_analysis()
        app.update()
        res.check(app._scan_job is None and not app.scan_frame.winfo_ismapped(),
                  "отмена анализа", f"{time.monotonic() - t0:.2f} с")

        # 2. анализ папки целиком
        gaps["max"] = 0.0
        gaps["last"] = time.monotonic()
        app.current_folder = folder_a
        app.start_analysis()
        done = pump_until(lambda: app._scan_job is None and bool(app.analyzed_data), SCAN_TIMEOUT)
        res.check(done, "анализ папки завершился", f"файлов: {len(app.analyzed_data)}")
        res.check(gaps["max"] < MAX_UI_GAP, "интерфейс не зависал во время анализа",
                  f"макс. пауза {gaps['max']:.2f} с")
        if done:
            pages = sum(len(d["pages"]) for d in app.analyzed_data.values())
            res.check(pages == 5, "все страницы распознаны", f"{pages} из 5")
            res.check(app.current_plan is not None, "план печати построен")

        # повторный анализ — из кэша, должен быть быстрым
        t0 = time.monotonic()
        app.start_analysis()
        done2 = pump_until(lambda: app._scan_job is None and bool(app.analyzed_data), SCAN_TIMEOUT)
        res.check(done2, "повторный анализ (кэш)", f"{time.monotonic() - t0:.2f} с")

        # 4. окна
        dialogs = [
            ("Настройки", app.open_settings, "_settings_dlg"),
            ("Отчёт", app.show_report, "_report_dlg"),
            ("Пробная печать", app.show_trial_print, "_visualizer_dlg"),
            ("Сообщить об ошибке", app._open_bug_report, "_bug_dlg"),
            ("Справка", app.open_help, "_help_dlg"),
        ]
        for title, opener, attr in dialogs:
            gaps["max"] = 0.0
            gaps["last"] = time.monotonic()
            try:
                opener()
                pump_until(lambda: False, 1.5)
                dlg = getattr(app, attr, None)
                alive = dlg is not None and bool(dlg.winfo_exists())
                n = _count_widgets(dlg) if alive else 0
                res.check(alive and n > 5, f"окно «{title}» открылось с содержимым", f"виджетов: {n}")
                res.check(gaps["max"] < MAX_UI_GAP, f"окно «{title}» не подвесило интерфейс",
                          f"макс. пауза {gaps['max']:.2f} с")
                if alive:
                    if hasattr(dlg, "_close"):
                        dlg._close()
                    else:
                        dlg.destroy()
                    app.update()
                    res.check(not dlg.winfo_exists(), f"окно «{title}» закрывается")
            except Exception as e:
                res.check(False, f"окно «{title}»", f"{type(e).__name__}: {e}")

        # 4б. обучение: проходит все шаги и закрывается без следов
        try:
            tour = app.start_tour()
            pump_until(lambda: False, 0.5)
            res.check(tour is not None and tour.bubble.winfo_viewable(), "обучение: пузырь показан")
            for _ in range(len(tour.steps) - 1):
                tour.next()
                app.update()
            res.check(tour.index == len(tour.steps) - 1, "обучение: все шаги пройдены")
            tour.close()
            app.update()
            res.check(app._tour is None and not tour.bubble.winfo_exists(), "обучение закрывается ✕")
        except Exception as e:
            res.check(False, "обучение", f"{type(e).__name__}: {e}")

        leftovers = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_exists()]
        res.check(not leftovers, "не осталось висящих окон", ", ".join(type(w).__name__ for w in leftovers))
        res.check(not shown, "не было сообщений об ошибках", "; ".join(f"{n}: {a}" for n, a in shown)[:300])

        # 5. логика обновлений
        from core import updater
        rels = [{"tag_name": "v9.0.0-dev.3", "assets": []}, {"tag_name": "v9.0.0-beta.2", "assets": []},
                {"tag_name": "v1.0.0", "assets": []}]
        res.check(updater.pick_update(rels, "stable") is None, "обновления: stable не видит бету")
        res.check(updater.pick_update(rels, "beta")["tag_name"] == "v9.0.0-beta.2", "обновления: канал beta")
        res.check(updater.pick_update(rels, "dev")["tag_name"] == "v9.0.0-beta.2", "обновления: beta новее dev той же версии")
    except Exception:
        res.check(False, "самопроверка упала", traceback.format_exc())
    finally:
        try:
            app._on_close()
        except Exception:
            pass
    return res.ok, res.text()


def main_self_test(argv):
    """Точка входа из main.py. Код выхода: 0 — всё хорошо, 1 — есть ошибки."""
    import tempfile
    import logging
    out_path = None
    for a in argv:
        if a.startswith("--self-test="):
            out_path = a.split("=", 1)[1]
    work = tempfile.mkdtemp(prefix="wpm_selftest_")
    prepare_environment(work)
    from core.logger import setup_logger
    setup_logger()
    ok, text = run_self_test(work)
    logging.info("Самопроверка:\n" + text)
    if out_path is None:
        exe_dir = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(sys.argv[0]))
        out_path = os.path.join(exe_dir, "wpm_selftest_result.txt")
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        out_path = os.path.join(work, "wpm_selftest_result.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    if sys.stdout:
        try:
            print(text)
        except Exception:
            pass
    return 0 if ok else 1
