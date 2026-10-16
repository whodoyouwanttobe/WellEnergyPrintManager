"""Где лежат программы печати. Одна функция для всех мест, где нужен Foxit/SumatraPDF.

Как печатается (выбирать движок не нужно — он определяется форматом):
  • A4 и A3 → лазерный принтер HP: Foxit (тихая печать /t), лоток A4/A3 задаётся в драйвере.
  • A2…A0 и склейки → плоттер Canon: Foxit, размер бумаги задаётся в драйвере (DevMode);
    если не получилось — SumatraPDF с растеризацией.
  • A0, длинные склейки и тяжёлые (⚡) страницы на плоттере перед печатью растеризуются.
"""
import os
import sys

FOXIT_CANDIDATES = [
    r"C:\Program Files (x86)\Foxit Software\Foxit PDF Editor\FoxitPDFEditor.exe",
    r"C:\Program Files\Foxit Software\Foxit PDF Editor\FoxitPDFEditor.exe",
    r"C:\Program Files (x86)\Foxit Software\Foxit PDF Reader\FoxitPDFReader.exe",
    r"C:\Program Files\Foxit Software\Foxit PDF Reader\FoxitPDFReader.exe",
    r"C:\Program Files (x86)\Foxit Software\Foxit Reader\FoxitReader.exe",
    r"C:\Program Files\Foxit Software\Foxit Reader\FoxitReader.exe",
]


def find_foxit(configured=""):
    """Путь из настроек, если файл есть; иначе — стандартные места установки. None — не найден."""
    for p in [configured] + FOXIT_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return None


def find_sumatra(configured=""):
    """SumatraPDF встроен в программу (папка tools внутри exe)."""
    cands = [configured]
    base = getattr(sys, "_MEIPASS", None)
    if base:
        cands.append(os.path.join(base, "tools", "SumatraPDF.exe"))
    cands.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "SumatraPDF.exe"))
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return None
