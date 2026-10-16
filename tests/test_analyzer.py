"""Анализ папки: результат, отмена, кэш, отсутствие зависаний."""
import os
import threading
import time

from core.analyzer import ScanJob, find_pdfs
from core.analysis_cache import AnalysisCache


def _run(folder, **kw):
    done = threading.Event()
    out = {}

    def on_done(results, errors, total):
        out.update(results=results, errors=errors, total=total)
        done.set()

    job = ScanJob(folder, False, lambda *a: None, on_done, on_error=lambda m: done.set(), **kw).start()
    assert done.wait(90), "анализ не завершился за 90 с"
    return out


def test_find_pdfs_ignores_other_files(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "B.PDF").write_bytes(b"%PDF-1.4")
    (tmp_path / "~$tmp.pdf").write_bytes(b"x")
    (tmp_path / "note.txt").write_text("x")
    names = sorted(os.path.basename(p) for p in find_pdfs(str(tmp_path), False))
    assert names == ["B.PDF", "a.pdf"]


def test_scan_in_processes(sample_folder):
    out = _run(sample_folder, use_processes=True)
    assert out["total"] == 1
    pages = next(iter(out["results"].values()))["pages"]
    fmts = [p[1] for p in pages]
    assert fmts[0].startswith("A4") and fmts[1].startswith("A3") and fmts[2].startswith("A1")


def test_scan_broken_pdf_reports_error(tmp_path):
    (tmp_path / "broken.pdf").write_bytes(b"this is not a pdf")
    out = _run(str(tmp_path), use_processes=False)
    assert out["results"] == {}
    assert len(out["errors"]) == 1


def test_cancel_is_fast(tmp_path):
    from ui.selftest import make_sample_pdfs
    make_sample_pdfs(str(tmp_path), many=60)
    finished = threading.Event()
    job = ScanJob(str(tmp_path), False, lambda *a: None, lambda *a: finished.set()).start()
    time.sleep(0.5)
    t0 = time.monotonic()
    job.cancel()
    job.join(10)
    assert not job._thread.is_alive(), "фоновый поток анализа не остановился за 10 с"
    assert time.monotonic() - t0 < 10
    assert not finished.is_set() or job.cancelled


def test_cache_hit(sample_folder, tmp_path_factory):
    cache = AnalysisCache(str(tmp_path_factory.mktemp("c") / "cache.db"))
    first = _run(sample_folder, cache=cache, use_processes=False)
    pdf = next(iter(first["results"]))
    assert cache.get(pdf) is not None
    second = _run(sample_folder, cache=cache, use_processes=False)
    assert second["results"][pdf]["pages"] == first["results"][pdf]["pages"]


def test_paths_match_old_glob_format(tmp_path):
    """История печати хранится по полному пути файла. Новый поиск обязан давать пути
    ровно в том же виде, что и glob в версиях 4.4–4.8, иначе старая история «потеряется»."""
    import glob
    sub = tmp_path / "Раздел 1"
    sub.mkdir()
    for p in (tmp_path / "a.pdf", tmp_path / "Лист Б.pdf", sub / "c.pdf"):
        p.write_bytes(b"%PDF-1.4")
    # askdirectory в Windows возвращает путь с прямыми слэшами — имитируем это
    folder = str(tmp_path).replace("\\", "/")
    old_flat = glob.glob(os.path.join(folder, "*.pdf"))
    old_rec = glob.glob(os.path.join(folder, "**", "*.pdf"), recursive=True)
    assert sorted(find_pdfs(folder, False)) == sorted(old_flat)
    assert sorted(find_pdfs(folder, True)) == sorted(old_rec)
