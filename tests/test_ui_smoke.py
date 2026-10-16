"""Главный тест «ничего не висит»: окно, анализ, отмена, все диалоги."""
import os
import sys

import pytest

pytestmark = pytest.mark.gui


def _has_display():
    if sys.platform == "win32":
        return True
    return bool(os.environ.get("DISPLAY"))


@pytest.mark.skipif(not _has_display(), reason="нет дисплея для окон Tk")
def test_self_test_passes(tmp_path):
    from ui.selftest import run_self_test
    ok, report = run_self_test(str(tmp_path))
    print(report)
    assert ok, report


@pytest.mark.skipif(not _has_display(), reason="нет дисплея для окон Tk")
def test_file_tree_columns_and_format_tooltip():
    import customtkinter as ctk
    from ui.components.file_tree import FileTreeView
    root = ctk.CTk()
    try:
        tree = FileTreeView(root)
        tree.pack(fill="both", expand=True)
        tree.set_base_dir("C:/proj")
        tree.add_file("C:/proj/doc.pdf", "⏳")
        tree.update_status("C:/proj/doc.pdf", "В очереди", pages=12,
                           formats=["A4: 10 стр.", "A1: 2 стр."], folded="0/12", time_str="",
                           printed="10/12", printed_tip=["✓ A4: 10/10 л.", "✗ A1: 0/2 л."])
        item = tree._items["C:/proj/doc.pdf"]
        values = tree.tree.item(item, "values")
        assert values[0] == "doc.pdf"            # имя без расшифровки
        assert values[1] == "12" and values[2] == "10/12" and values[3] == "0/12"
        assert tree._meta["C:/proj/doc.pdf"]["printed_tip"][1].startswith("✗ A1")
        # статус без данных о страницах не должен их стирать
        tree.update_status("C:/proj/doc.pdf", "Печать...")
        assert tree.tree.item(item, "values")[1] == "12"
        tree._show_tip("\n".join(tree._meta["C:/proj/doc.pdf"]["formats"]), 10, 10)
        assert "A1: 2 стр." in tree._tip_label.cget("text")
        tree._hide_tip()
    finally:
        root.destroy()
