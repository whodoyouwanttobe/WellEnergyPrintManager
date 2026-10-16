"""Тяжёлые страницы: формат не меняется (нет «A4 [V]»), тяжёлые A3 идут на принтер,
сложность считается по числу векторных команд, порог берётся из настроек."""
import os

import fitz
import pytest

from core.pdf_utils import (analyze_pdf, build_print_plan, clean_format, get_complexity_weight,
                            group_pages_by_format, is_heavy_page, is_laser, page_complexity, page_ops)

A4 = (595, 842)
A3 = (842, 1191)


def _pdf(path, pages):
    """pages: [(размер, число линий)]"""
    doc = fitz.open()
    for (w, h), lines in pages:
        p = doc.new_page(width=w, height=h)
        if lines:
            sh = p.new_shape()
            for i in range(lines):
                y = 20 + (i % 700)
                sh.draw_line((20, y), (w - 20, y + 1))
            sh.finish(width=0.2)
            sh.commit()
    doc.save(path)
    doc.close()
    return path


def test_page_complexity_counts_vector_commands(tmp_path):
    fp = _pdf(str(tmp_path / "a.pdf"), [(A4, 0), (A4, 5000)])
    doc = fitz.open(fp)
    light, dense = page_complexity(doc[0]), page_complexity(doc[1])
    doc.close()
    assert light < 50
    assert 9000 <= dense <= 11000          # каждая линия = m + l


def test_counts_commands_inside_form_xobjects(tmp_path):
    src = _pdf(str(tmp_path / "src.pdf"), [(A4, 3000)])
    out = fitz.open()
    page = out.new_page(width=A4[0], height=A4[1])
    s = fitz.open(src)
    page.show_pdf_page(page.rect, s, 0)   # содержимое попадает в Form XObject
    assert page_complexity(page) >= 5000
    s.close()
    out.close()


def test_analyze_keeps_clean_format_and_stores_ops(tmp_path):
    fp = _pdf(str(tmp_path / "b.pdf"), [(A4, 10), (A4, 6000), (A3, 6000)])
    pages = analyze_pdf(fp)
    assert [p[1] for p in pages] == ["A4", "A4", "A3"]      # без « [V]»
    assert all(len(p) == 8 for p in pages)
    assert page_ops(pages[1]) > page_ops(pages[0])
    # одна группа A4, а не «A4» и «A4 [V]»
    assert list(group_pages_by_format(pages)) == ["A4", "A3"]


def test_heavy_threshold_and_heavy_a3_goes_to_laser():
    heavy_a3 = (1, "A3", 297, 420, "1", False, False, 900_000)
    light_a4 = (2, "A4", 210, 297, "2", False, False, 2_000)
    assert is_heavy_page(heavy_a3, 300_000) and not is_heavy_page(light_a4, 300_000)
    assert not is_heavy_page(heavy_a3, 1_000_000)
    plan = build_print_plan({"C:/f.pdf": [heavy_a3, light_a4]}, {}, 1)
    assert {e[3]["format"] for e in plan["laser"]} == {"A3", "A4"} and plan["plotter"] == []


def test_threshold_from_config(monkeypatch):
    from core.config import get_config
    monkeypatch.setitem(get_config().data, "heavy_ops_threshold", 50_000)
    assert is_heavy_page((1, "A1", 594, 841, "1", True, False, 60_000))
    monkeypatch.setitem(get_config().data, "heavy_ops_threshold", 100_000)
    assert not is_heavy_page((1, "A1", 594, 841, "1", True, False, 60_000))


def test_legacy_v_marker_is_handled():
    assert clean_format("A4 [V]") == "A4"
    assert is_laser("A3 [V]")                         # старые данные тоже на принтер
    assert get_complexity_weight("A4 [V]") == get_complexity_weight("A4", heavy=True) == 13
    assert get_complexity_weight("A1") == 8


def test_old_pages_without_ops_are_not_heavy():
    assert not is_heavy_page((1, "A4", 210, 297, "1", False, False))
