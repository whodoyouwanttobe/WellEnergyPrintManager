"""Анализ папки с PDF без зависания интерфейса.

Почему раньше всё висло: PyMuPDF во время разбора страницы не отпускает GIL Python,
поэтому 4 потока анализа почти полностью забирали интерпретатор у окна Tk —
окно не перерисовывалось и не реагировало на клики. Теперь сам разбор PDF идёт
в ОТДЕЛЬНЫХ ПРОЦЕССАХ (multiprocessing), а главный процесс только собирает результаты.

Анализ можно отменить в любой момент (ScanJob.cancel()): процессы-работники
завершаются сразу, даже если они застряли на огромном чертеже.
"""
import logging
import multiprocessing
import os
import threading
import time

from core.pdf_utils import analyze_pdf, group_pages_by_format

log = logging.getLogger(__name__)


def _analyze_one(filepath):
    """Выполняется в процессе-работнике. Должна быть функцией верхнего уровня (pickle)."""
    try:
        return filepath, analyze_pdf(filepath), None
    except Exception as e:  # битый PDF, нет доступа и т.п.
        return filepath, None, f"{type(e).__name__}: {e}"


def find_pdfs(folder, include_subfolders, cancel_event=None, on_found=None):
    """Поиск PDF с возможностью отмены (на сетевых дисках os.walk бывает долгим)."""
    result = []
    if include_subfolders:
        for root, dirs, files in os.walk(folder):
            if cancel_event is not None and cancel_event.is_set():
                return result
            dirs.sort()
            for name in sorted(files):
                if name.lower().endswith(".pdf") and not name.startswith("~$"):
                    result.append(os.path.join(root, name))
            if on_found:
                on_found(len(result))
    else:
        with os.scandir(folder) as it:
            for entry in sorted(it, key=lambda e: e.name):
                if entry.is_file() and entry.name.lower().endswith(".pdf") and not entry.name.startswith("~$"):
                    result.append(entry.path)
    return result


class ScanJob:
    """Один запуск анализа папки. Колбэки вызываются из ФОНОВОГО потока.

    on_progress(phase, done, total, filepath)  phase: 'search' | 'found' | 'analyze'
    on_done(results, errors, total)
    on_error(message)
    """

    def __init__(self, folder, include_subfolders, on_progress, on_done, on_error=None,
                 cache=None, workers=None, use_processes=True):
        self.folder = folder
        self.include_subfolders = include_subfolders
        self.on_progress = on_progress
        self.on_done = on_done
        self.on_error = on_error
        self.cache = cache
        self.workers = workers or max(1, min(4, (os.cpu_count() or 2) - 1))
        self.use_processes = use_processes
        self._cancel = threading.Event()
        self._pool = None
        self._thread = None

    @property
    def cancelled(self):
        return self._cancel.is_set()

    def start(self):
        self._thread = threading.Thread(target=self._run, name="ScanJob", daemon=True)
        self._thread.start()
        return self

    def cancel(self):
        """Не блокирует вызывающий (UI) поток: фоновый поток сам заметит флаг
        в течение ~0.3 с и завершит процессы-работники."""
        self._cancel.set()

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    # ───────────────────────── внутреннее ─────────────────────────
    def _run(self):
        t0 = time.time()
        try:
            files = find_pdfs(self.folder, self.include_subfolders, self._cancel,
                              on_found=lambda n: self.on_progress("search", n, 0, ""))
            if self.cancelled:
                return
            total = len(files)
            self.on_progress("found", total, total, "")
            results, errors = {}, []
            done = 0

            todo = []
            for fp in files:
                cached = self.cache.get(fp) if self.cache else None
                if cached:
                    self._store(results, fp, cached)
                    done += 1
                    self.on_progress("analyze", done, total, fp)
                else:
                    todo.append(fp)
                if self.cancelled:
                    return

            if todo:
                for fp, pages, err in self._iter_analysis(todo):
                    if self.cancelled:
                        return
                    if err:
                        errors.append((fp, err))
                        log.warning(f"Анализ {fp}: {err}")
                    elif pages:
                        self._store(results, fp, pages)
                        if self.cache:
                            self.cache.put(fp, pages)
                    done += 1
                    self.on_progress("analyze", done, total, fp)

            if not self.cancelled:
                log.info(f"Анализ {self.folder}: {total} файлов ({len(todo)} новых) за {time.time() - t0:.1f} с")
                # Сохраняем порядок файлов как на диске
                ordered = {fp: results[fp] for fp in files if fp in results}
                self.on_done(ordered, errors, total)
        except Exception as e:
            log.exception("Ошибка анализа папки")
            if not self.cancelled and self.on_error:
                self.on_error(f"{type(e).__name__}: {e}")
        finally:
            self._pool = None

    @staticmethod
    def _store(results, fp, pages):
        results[fp] = {
            "pages": pages,
            "formats": group_pages_by_format(pages),
            "total_pages": len(pages),
        }

    def _iter_analysis(self, files):
        if self.use_processes and len(files) > 0:
            try:
                ctx = multiprocessing.get_context("spawn")
                pool = ctx.Pool(processes=min(self.workers, len(files)))
            except Exception as e:
                log.warning(f"Не удалось запустить процессы анализа ({e}); анализ в текущем процессе")
                pool = None
            if pool is not None:
                self._pool = pool
                if self.cancelled:  # отмена могла прийти до создания пула
                    pool.terminate()
                    return
                try:
                    it = pool.imap_unordered(_analyze_one, files, chunksize=1)
                    received = 0
                    while received < len(files):
                        if self.cancelled:
                            log.info("Анализ отменён пользователем")
                            return
                        try:
                            item = it.next(timeout=0.3)
                        except multiprocessing.TimeoutError:
                            continue
                        received += 1
                        yield item
                finally:
                    pool.terminate()  # убивает работников, даже застрявших на огромном чертеже
                    pool.join()
                return
        for fp in files:
            if self.cancelled:
                return
            yield _analyze_one(fp)


class PDFAnalyzer:
    """Обёртка для обратной совместимости (тесты, старый код)."""

    def __init__(self, include_subfolders=False):
        self.include_subfolders = include_subfolders

    def scan_directory(self, folder_path, progress_callback, result_callback):
        done = threading.Event()

        def on_progress(phase, d, t, fp):
            if phase == "analyze":
                progress_callback(d, t, fp)

        def on_done(results, errors, total):
            result_callback(results, errors, total)
            done.set()

        job = ScanJob(folder_path, self.include_subfolders, on_progress, on_done,
                      on_error=lambda m: done.set(), use_processes=False)
        job._run()
