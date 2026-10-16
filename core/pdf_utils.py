# -*- coding: utf-8 -*-
"""
PDF Format Analyzer — анализ форматов страниц PDF файлов.
Определяет формат каждой страницы (А0, А1, А2, А3, А4, А4хN)
и группирует номера страниц по формату для удобной печати.

Запуск: python pdf_format_analyzer.py
Сборка в exe: pyinstaller --onefile --windowed pdf_format_analyzer.py
"""

import os
import sys
import math
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from collections import OrderedDict

try:
    import fitz  # PyMuPDF
except ImportError:
    import pymupdf as fitz


# ─────────────────────────────────────────────────────────
#  Определение формата по размерам страницы (в мм)
# ─────────────────────────────────────────────────────────

# Стандартные размеры A-серии ISO 216: (длинная_сторона, короткая_сторона)
# Кратность означает, что КОРОТКАЯ сторона повторяется N раз в ряд,
# а ДЛИННАЯ сторона остаётся фиксированной.
#
# Пример: A3x3 = 420 x (297*3) = 420 x 891
#          A4x4 = 297 x (210*4) = 297 x 841
#
# Таблица кратностей (ГОСТ):
# Кратн.  A0          A1          A2          A3          A4
#   2     1189x1682    —           —           —           —
#   3     1189x2523   841x1783   594x1261   420x891    297x630
#   4       —         841x2378   594x1682   420x1189   297x841
#   5       —           —        594x2102   420x1486   297x1051
#   6       —           —          —        420x1783   297x1261
#   7       —           —          —        420x2080   297x1471
#   8       —           —          —          —        297x1682
#   9       —           —          —          —        297x1892

A_SERIES = [
    # (имя, длинная_сторона_мм, короткая_сторона_мм)
    ("A0", 1189, 841),
    ("A1", 841, 594),
    ("A2", 594, 420),
    ("A3", 420, 297),
    ("A4", 297, 210),
]

# Допуск при сравнении размеров (в мм)
TOLERANCE = 6


def detect_format(width_mm: float, height_mm: float) -> str:
    """
    Определяет формат листа по его размерам в миллиметрах.

    Логика определения:
    1. Проверяет стандартные размеры A0–A4.
    2. Проверяет кратные форматы: длинная сторона базового формата
       остаётся фиксированной, а короткая умножается на кратность N.
       Например: A3x3 = 420 x (297*3) = 420 x 891.

    Возвращает:
        Стандартное название (A0, A1, A2, A3, A4) или
        кратное название (A3x3, A4x7 и т.д.)
    """
    # Нормализуем: всегда w >= h
    w = max(width_mm, height_mm)
    h = min(width_mm, height_mm)

    # 1. Проверяем стандартные размеры A-серии
    for name, long_side, short_side in A_SERIES:
        if abs(w - long_side) <= TOLERANCE and abs(h - short_side) <= TOLERANCE:
            return name

    # Особое исключение для US Letter (279x216), который часто используется вместо A4
    if abs(w - 279.4) <= TOLERANCE and abs(h - 215.9) <= TOLERANCE:
        return "A4"

    # 2. Проверяем кратные форматы
    # Меньшая сторона страницы = длинная сторона базового формата (фиксирована)
    # Большая сторона страницы = короткая сторона базового формата × N
    for name, long_side, short_side in A_SERIES:
        if abs(h - long_side) <= TOLERANCE:
            multiplicity = w / short_side
            rounded = round(multiplicity)
            # Уменьшаем допуск (0.05 вместо 0.15), чтобы обычные листы с полями не определялись как XN
            if rounded >= 2 and abs(multiplicity - rounded) < 0.05:
                # Особая проверка, если это A4x4, длина должна быть хотя бы близка к 840. 
                # (предотвращает баги, если каким-то чудом обычный A4 попадет сюда из-за полей)
                return f"{name}x{rounded}"

    # 3. Если не удалось определить — выводим размеры в мм
    return f"{int(round(w))}x{int(round(h))}"

def suggest_nearest_larger_format(w_mm: float, h_mm: float) -> str:
    """
    Для нестандартных размеров находит ближайший больший ГОСТ формат.
    Возвращает строку с названием формата, например "A1x3".
    Возвращает None, если формат слишком велик для всех стандартов.
    """
    w = max(w_mm, h_mm)
    h = min(w_mm, h_mm)
    
    candidates = []
    
    # 1. Проверяем базовые форматы
    for name, long_side, short_side in A_SERIES:
        if long_side >= w and short_side >= h:
            candidates.append((name, long_side, short_side))
            
    # 2. Проверяем кратные (до x9)
    for name, long_side, short_side in A_SERIES:
        for n in range(2, 10):
            # Для кратных: фиксируется длинная сторона базы (это высота листа),
            # короткая сторона базы умножается на N (это ширина листа)
            c_w = short_side * n
            c_h = long_side
            
            # Нормализуем кандидата
            c_max = max(c_w, c_h)
            c_min = min(c_w, c_h)
            
            if c_max >= w and c_min >= h:
                candidates.append((f"{name}x{n}", c_max, c_min))
                
    if not candidates:
        return None
        
    # Сортируем по площади, чтобы найти НАИМЕНЬШИЙ из подходящих бОльших форматов
    candidates.sort(key=lambda x: x[1] * x[2])
    return candidates[0][0]


import re

def decode_pdf_label(lbl):
    if not lbl:
        return lbl
    def replacer(match):
        hex_str = match.group(1)
        try:
            return bytes.fromhex(hex_str).decode('utf-16-be')
        except:
            return match.group(0)
    return re.sub(r'<FEFF([0-9A-Fa-f]+)>', replacer, lbl)

# ───────────── тяжёлые страницы ─────────────
# Раньше «тяжёлой» считалась страница с потоком команд > 500 КБ — под это попадала
# половина обычных чертежей. Теперь считаем сами векторные команды (линии, кривые,
# прямоугольники), включая вложенные блоки (Form XObject), и сравниваем с порогом
# из настроек. Число команд хранится в анализе (8-е поле), поэтому смена порога
# не требует повторного анализа.
HEAVY_OPS_DEFAULT = 300_000
_PATH_OPS = re.compile(rb"\s(?:l|c|v|y|re|m)(?=\s)")


def page_complexity(page) -> int:
    """Число векторных команд на странице (вместе с блоками XObject)."""
    doc = page.parent
    seen = set()
    total = 0
    xrefs = list(page.get_contents())
    try:
        xrefs += [x[0] for x in page.get_xobjects()]
    except Exception:
        pass
    for xref in xrefs:
        if xref in seen or not xref:
            continue
        seen.add(xref)
        try:
            data = doc.xref_stream(xref) or b""
        except Exception:
            continue
        total += len(_PATH_OPS.findall(data))
    return total


def clean_format(fmt) -> str:
    """Формат без старой пометки « [V]» (из истории и кэша старых версий)."""
    return str(fmt or "").replace(" [V]", "").replace("[V]", "").strip()


def page_ops(info) -> int:
    v = info[7] if len(info) > 7 else 0
    return int(v) if isinstance(v, (int, float)) else 0


def heavy_threshold() -> int:
    try:
        from core.config import get_config
        return max(int(get_config().get("heavy_ops_threshold", HEAVY_OPS_DEFAULT)), 1000)
    except Exception:
        return HEAVY_OPS_DEFAULT


def is_heavy_page(info, threshold=None) -> bool:
    return page_ops(info) >= (threshold or heavy_threshold())


def analyze_pdf(filepath: str) -> list:
    """
    Анализ PDF файла и возвращение списка форматов страниц.
    
    Returns:
        list of (page_number, format_name, width_mm, height_mm, logical_label)
    """
    doc = fitz.open(filepath)
    pages_info = []
    for i, page in enumerate(doc):
        w_mm = page.rect.width * 25.4 / 72.0
        h_mm = page.rect.height * 25.4 / 72.0
        fmt = detect_format(w_mm, h_mm)
        lbl = page.get_label()
        lbl = decode_pdf_label(lbl) if lbl else str(i + 1)
        is_landscape = (page.rect.width > page.rect.height)
        is_black = False
        
        # Сложность страницы (число векторных команд). Формат при этом НЕ меняется:
        # раньше к нему дописывалось « [V]», и тяжёлые A3 уходили на плоттер, а A4 делился на две группы.
        ops = page_complexity(page)

        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(0.05, 0.05))
            import numpy as np
            samples = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.h, pix.w, pix.n))
            if pix.n >= 3:
                rgb = samples[:, :, :3]
                black_pixels = np.sum((rgb[:, :, 0] < 30) & (rgb[:, :, 1] < 30) & (rgb[:, :, 2] < 30))
                total_pixels = pix.w * pix.h
                if total_pixels > 0 and (black_pixels / total_pixels) > 0.40:
                    is_black = True
        except Exception:
            pass
            
        pages_info.append((i + 1, fmt, w_mm, h_mm, lbl, is_landscape, is_black, ops))
    doc.close()
    return pages_info
    
def pack_pages_side_by_side(input_pdf_path: str, pages_list: list) -> str:
    import tempfile
    doc = fitz.open(input_pdf_path)
    new_doc = fitz.open()
    
    for i in range(0, len(pages_list), 2):
        p1_idx = pages_list[i] - 1
        p2_idx = pages_list[i+1] - 1 if i+1 < len(pages_list) else None
        
        p1 = doc[p1_idx]
        w1 = p1.rect.width
        h1 = p1.rect.height
        
        if p2_idx is not None:
            p2 = doc[p2_idx]
            w2 = p2.rect.width
            h2 = p2.rect.height
            
            new_w = w1 + w2
            new_h = max(h1, h2)
            new_page = new_doc.new_page(width=new_w, height=new_h)
            new_page.show_pdf_page(fitz.Rect(0, 0, w1, h1), doc, p1_idx)
            new_page.show_pdf_page(fitz.Rect(w1, 0, new_w, h2), doc, p2_idx)
        else:
            new_page = new_doc.new_page(width=w1, height=h1)
            new_page.show_pdf_page(fitz.Rect(0, 0, w1, h1), doc, p1_idx)
            
    tdir = tempfile.mkdtemp(prefix="pm_packed_")
    out_path = os.path.join(tdir, "packed_" + os.path.basename(input_pdf_path))
    new_doc.save(out_path)
    new_doc.close()
    doc.close()
    return out_path
def group_pages_by_format(pages_info: list) -> OrderedDict:
    """
    Группирует страницы по формату в порядке первого появления.
    Последовательные номера сжимаются в диапазоны (1-5, 7, 9-12).
    
    Returns:
        OrderedDict: {format_name: page_ranges_string}
    """
    # Собираем номера страниц по форматам
    format_order = []
    format_pages = {}
    for info in pages_info:
        page_num = info[0]
        fmt = info[1]
        lbl = info[4] if len(info) >= 5 else str(page_num)
        
        if fmt not in format_pages:
            format_order.append(fmt)
            format_pages[fmt] = []
        format_pages[fmt].append((page_num, lbl))

    # Сжатие последовательностей и создание словаря
    result = OrderedDict()
    for fmt in format_order:
        pages = sorted(format_pages[fmt], key=lambda x: x[0])
        result[fmt] = compress_pages(pages)
        
    return result


def compress_pages(pages: list) -> str:
    """
    Сжимает список (номер, метка) страниц в строку с диапазонами.
    """
    if not pages:
        return ""

    ranges = []
    start_info = pages[0]
    last_info = pages[0]

    for p in pages[1:]:
        # Если это последовательные страницы (номер p[0] == last_info[0] + 1)
        if p[0] == last_info[0] + 1:
            last_info = p
        else:
            ranges.append((start_info, last_info))
            start_info = p
            last_info = p
    ranges.append((start_info, last_info))

    parts = []
    for s_info, e_info in ranges:
        if s_info[0] == e_info[0]:
            parts.append(str(s_info[1]))
        else:
            parts.append(f"{s_info[1]}-{e_info[1]}")
    return ", ".join(parts)


def _compress_page_numbers(pages: list) -> str:
    """
    Сжимает список номеров страниц в строку с диапазонами.
    [1,2,3,5,7,8,9] -> "1-3, 5, 7-9"
    """
    if not pages:
        return ""

    ranges = []
    start = pages[0]
    end = pages[0]

    for p in pages[1:]:
        if p == end + 1:
            end = p
        else:
            ranges.append((start, end))
            start = p
            end = p
    ranges.append((start, end))

    parts = []
    for s, e in ranges:
        if s == e:
            parts.append(str(s))
        else:
            parts.append(f"{s}-{e}")
    return ", ".join(parts)


# ─────────────────────────────────────────────────────────
#  GUI — главное окно приложения
# ─────────────────────────────────────────────────────────

class PDFFormatAnalyzerApp:
    """Главное окно приложения анализатора форматов PDF."""

    # Цветовая палитра — светлая тема с приглушённым оранжевым
    BG_DARK = "#f5f5f5"
    BG_PANEL = "#ffffff"
    BG_CARD = "#fafafa"
    BG_INPUT = "#ffffff"
    FG_TEXT = "#2d2d2d"
    FG_DIM = "#888888"
    FG_ACCENT = "#d48a4c"
    FG_ACCENT2 = "#b07840"
    FG_SELECT = "#e8a060"
    BORDER_COLOR = "#dcdcdc"
    BTN_BG = "#d48a4c"
    BTN_HOVER = "#c07838"
    COPY_SUCCESS = "#5a9a5a"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PDF Формат-Анализатор")
        self.root.geometry("950x700")
        self.root.minsize(750, 500)
        self.root.configure(bg=self.BG_DARK)

        # Попытка установить светлый заголовок окна (Windows 10/11)
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(ctypes.c_int(0)), ctypes.sizeof(ctypes.c_int)
            )
        except Exception:
            pass

        # Иконка окна (буква P в PDF стиле)
        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        self.current_folder = ""
        self.pdf_files = []
        self.current_analysis = None

        self._setup_styles()
        self._build_ui()

    def _setup_styles(self):
        """Настройка стилей ttk."""
        self.style = ttk.Style()
        self.style.theme_use("clam")

        # Treeview
        self.style.configure(
            "Light.Treeview",
            background=self.BG_CARD,
            foreground=self.FG_TEXT,
            fieldbackground=self.BG_CARD,
            borderwidth=0,
            rowheight=28,
            font=("Segoe UI", 10),
        )
        self.style.map(
            "Light.Treeview",
            background=[("selected", "#f0dcc8")],
            foreground=[("selected", "#2d2d2d")],
        )
        self.style.configure(
            "Light.Treeview.Heading",
            background="#f0f0f0",
            foreground=self.FG_ACCENT2,
            font=("Segoe UI Semibold", 10),
            borderwidth=0,
        )
        self.style.map(
            "Light.Treeview.Heading",
            background=[("active", "#e8e8e8")],
        )

        # Scrollbar
        self.style.configure(
            "Light.Vertical.TScrollbar",
            background="#e0e0e0",
            troughcolor=self.BG_DARK,
            borderwidth=0,
            arrowsize=14,
        )

    def _build_ui(self):
        """Строит интерфейс пользователя."""
        # ── Выбор папки ──
        folder_frame = tk.Frame(self.root, bg=self.BG_PANEL, pady=8, padx=12)
        folder_frame.pack(fill=tk.X, padx=15, pady=(0, 8))

        folder_label = tk.Label(
            folder_frame,
            text="Папка:",
            font=("Segoe UI", 10),
            fg=self.FG_DIM,
            bg=self.BG_PANEL,
        )
        folder_label.pack(side=tk.LEFT, padx=(5, 8))

        self.folder_var = tk.StringVar()
        self.folder_entry = tk.Entry(
            folder_frame,
            textvariable=self.folder_var,
            font=("Segoe UI", 10),
            bg=self.BG_INPUT,
            fg=self.FG_TEXT,
            insertbackground=self.FG_ACCENT,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightcolor=self.FG_ACCENT,
            highlightbackground=self.BORDER_COLOR,
        )
        self.folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5, ipady=4)
        self.folder_entry.bind("<Return>", lambda e: self._load_folder())

        browse_btn = tk.Button(
            folder_frame,
            text="📁 Обзор...",
            font=("Segoe UI", 10),
            bg=self.BTN_BG,
            fg="#ffffff",
            activebackground=self.BTN_HOVER,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            cursor="hand2",
            padx=12,
            pady=3,
            command=self._browse_folder,
        )
        browse_btn.pack(side=tk.LEFT, padx=(8, 5))

        # ── Основная панель — разделена на две части ──
        main_pane = tk.PanedWindow(
            self.root,
            orient=tk.HORIZONTAL,
            bg=self.BG_DARK,
            sashwidth=4,
            sashrelief=tk.FLAT,
        )
        main_pane.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 10))

        # ── Левая панель: список PDF ──
        left_frame = tk.Frame(main_pane, bg=self.BG_PANEL)
        main_pane.add(left_frame, minsize=250, width=340)

        left_header = tk.Label(
            left_frame,
            text="PDF файлы",
            font=("Segoe UI Semibold", 11),
            fg=self.FG_ACCENT2,
            bg=self.BG_PANEL,
            anchor=tk.W,
            padx=10,
            pady=6,
        )
        left_header.pack(fill=tk.X)

        # Treeview для списка файлов
        tree_frame = tk.Frame(left_frame, bg=self.BG_PANEL)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        self.file_tree = ttk.Treeview(
            tree_frame,
            columns=("pages",),
            show="tree headings",
            style="Light.Treeview",
            selectmode="browse",
        )
        self.file_tree.heading("#0", text="Имя файла", anchor=tk.W)
        self.file_tree.heading("pages", text="Стр.", anchor=tk.CENTER)
        self.file_tree.column("#0", width=230, minwidth=150)
        self.file_tree.column("pages", width=50, minwidth=40, anchor=tk.CENTER)

        file_scroll = ttk.Scrollbar(
            tree_frame,
            orient=tk.VERTICAL,
            command=self.file_tree.yview,
            style="Light.Vertical.TScrollbar",
        )
        self.file_tree.configure(yscrollcommand=file_scroll.set)

        self.file_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        file_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.file_tree.bind("<<TreeviewSelect>>", self._on_file_select)

        # ── Правая панель: результат анализа ──
        right_frame = tk.Frame(main_pane, bg=self.BG_PANEL)
        main_pane.add(right_frame, minsize=300)

        right_header_frame = tk.Frame(right_frame, bg=self.BG_PANEL)
        right_header_frame.pack(fill=tk.X)

        self.result_title = tk.Label(
            right_header_frame,
            text="Выберите PDF файл",
            font=("Segoe UI Semibold", 11),
            fg=self.FG_ACCENT2,
            bg=self.BG_PANEL,
            anchor=tk.W,
            padx=10,
            pady=6,
        )
        self.result_title.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # ── Таблица «формат — страницы» (для копирования) ──
        self.format_frame = tk.Frame(right_frame, bg=self.BG_PANEL)
        self.format_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        # Текстовое поле с результатом (для копирования)
        self.result_text = tk.Text(
            self.format_frame,
            font=("Consolas", 12),
            bg=self.BG_CARD,
            fg=self.FG_TEXT,
            insertbackground=self.FG_ACCENT,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightcolor=self.FG_ACCENT,
            highlightbackground=self.BORDER_COLOR,
            padx=12,
            pady=10,
            wrap=tk.WORD,
            state=tk.DISABLED,
        )
        self.result_text.pack(fill=tk.BOTH, expand=True)

        # Тег для форматирования
        self.result_text.tag_configure("format_name", foreground="#b07840", font=("Consolas", 12, "bold"))
        self.result_text.tag_configure("pages", foreground="#2d2d2d", font=("Consolas", 12))
        self.result_text.tag_configure("header", foreground="#b07840", font=("Segoe UI", 11, "bold"))
        self.result_text.tag_configure("separator", foreground=self.BORDER_COLOR)
        self.result_text.tag_configure("dim", foreground=self.FG_DIM, font=("Consolas", 10))

        # ── Кнопки внизу ──
        btn_frame = tk.Frame(right_frame, bg=self.BG_PANEL, pady=6, padx=10)
        btn_frame.pack(fill=tk.X)

        self.copy_btn = tk.Button(
            btn_frame,
            text="📋 Копировать всё",
            font=("Segoe UI", 10),
            bg=self.BTN_BG,
            fg="#ffffff",
            activebackground=self.BTN_HOVER,
            activeforeground="#ffffff",
            relief=tk.FLAT,
            cursor="hand2",
            padx=12,
            pady=4,
            command=self._copy_result,
            state=tk.DISABLED,
        )
        self.copy_btn.pack(side=tk.LEFT, padx=3)

        self.copy_status = tk.Label(
            btn_frame,
            text="",
            font=("Segoe UI", 9),
            fg=self.COPY_SUCCESS,
            bg=self.BG_PANEL,
        )
        self.copy_status.pack(side=tk.LEFT, padx=10)

        # ── Подробная таблица внизу ──
        detail_label = tk.Label(
            right_frame,
            text="Подробно по страницам",
            font=("Segoe UI Semibold", 10),
            fg=self.FG_DIM,
            bg=self.BG_PANEL,
            anchor=tk.W,
            padx=10,
            pady=4,
        )
        detail_label.pack(fill=tk.X)

        detail_tree_frame = tk.Frame(right_frame, bg=self.BG_PANEL)
        detail_tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        self.detail_tree = ttk.Treeview(
            detail_tree_frame,
            columns=("format", "width", "height"),
            show="headings",
            style="Light.Treeview",
            height=8,
        )
        self.detail_tree.heading("format", text="Формат", anchor=tk.CENTER)
        self.detail_tree.heading("width", text="Ш (мм)", anchor=tk.CENTER)
        self.detail_tree.heading("height", text="В (мм)", anchor=tk.CENTER)
        self.detail_tree.column("format", width=100, anchor=tk.CENTER)
        self.detail_tree.column("width", width=80, anchor=tk.CENTER)
        self.detail_tree.column("height", width=80, anchor=tk.CENTER)

        detail_scroll = ttk.Scrollbar(
            detail_tree_frame,
            orient=tk.VERTICAL,
            command=self.detail_tree.yview,
            style="Light.Vertical.TScrollbar",
        )
        self.detail_tree.configure(yscrollcommand=detail_scroll.set)

        self.detail_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # ── Строка состояния ──
        status_frame = tk.Frame(self.root, bg=self.BG_DARK, pady=4)
        status_frame.pack(fill=tk.X)

        self.status_var = tk.StringVar(value="Выберите папку с PDF файлами")
        status_label = tk.Label(
            status_frame,
            textvariable=self.status_var,
            font=("Segoe UI", 9),
            fg=self.FG_DIM,
            bg=self.BG_DARK,
            anchor=tk.W,
            padx=20,
        )
        status_label.pack(fill=tk.X)

    def _browse_folder(self):
        """Открывает диалог выбора папки."""
        folder = filedialog.askdirectory(
            title="Выберите папку с PDF файлами",
            initialdir=self.current_folder or os.path.expanduser("~"),
        )
        if folder:
            self.folder_var.set(folder)
            self._load_folder()

    def _load_folder(self):
        """Загружает список PDF файлов из указанной папки."""
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showwarning(
                "Ошибка", "Указанная папка не существует."
            )
            return

        self.current_folder = folder

        # Очистка
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)
        self._clear_result()

        # Поиск PDF файлов
        self.pdf_files = []
        for fname in sorted(os.listdir(folder)):
            if fname.lower().endswith(".pdf"):
                fpath = os.path.join(folder, fname)
                try:
                    doc = fitz.open(fpath)
                    page_count = doc.page_count
                    doc.close()
                    self.pdf_files.append((fname, fpath, page_count))
                except Exception as e:
                    self.pdf_files.append((fname, fpath, -1))

        if not self.pdf_files:
            self.status_var.set(f"В папке нет PDF файлов")
            messagebox.showinfo("Информация", "В выбранной папке нет PDF файлов.")
            return

        # Заполнение дерева
        for fname, fpath, pcount in self.pdf_files:
            pcount_str = str(pcount) if pcount > 0 else "?"
            self.file_tree.insert("", tk.END, text=f"  {fname}", values=(pcount_str,))

        self.status_var.set(
            f"Папка: {folder} | Найдено PDF: {len(self.pdf_files)}"
        )

    def _on_file_select(self, event=None):
        """Обработка выбора файла — запуск анализа."""
        selection = self.file_tree.selection()
        if not selection:
            return

        idx = self.file_tree.index(selection[0])
        fname, fpath, pcount = self.pdf_files[idx]

        if pcount < 0:
            messagebox.showerror("Ошибка", f"Не удалось открыть файл:\n{fname}")
            return

        self.status_var.set(f"Анализ: {fname}...")
        self.root.update_idletasks()

        try:
            pages_info = analyze_pdf(fpath)
            grouped = group_pages_by_format(pages_info)
            self._display_result(fname, pages_info, grouped)
            self.status_var.set(
                f"{fname} | {len(pages_info)} стр. | {len(grouped)} формат(ов)"
            )
        except Exception as e:
            messagebox.showerror("Ошибка анализа", f"Ошибка при анализе файла:\n{e}")
            self.status_var.set(f"Ошибка: {e}")

    def _display_result(self, fname, pages_info, grouped):
        """Отображает результат анализа."""
        self.result_title.config(text=f"📊 {fname}")

        # Текстовое поле — форматы и страницы
        self.result_text.config(state=tk.NORMAL)
        self.result_text.delete("1.0", tk.END)

        # Записываем сгруппированный результат
        for fmt, page_str in grouped.items():
            self.result_text.insert(tk.END, f"{fmt}", "format_name")
            self.result_text.insert(tk.END, f"  {page_str}\n", "pages")

        self.result_text.config(state=tk.DISABLED)

        # Подробная таблица
        for item in self.detail_tree.get_children():
            self.detail_tree.delete(item)

        for page_num, fmt, w, h in pages_info:
            self.detail_tree.insert(
                "", tk.END,
                text=str(page_num),
                values=(fmt, f"{w:.0f}", f"{h:.0f}"),
            )

        # Добавляем колонку номера страницы в detail_tree
        # (пересоздаём с нужной колонкой — но проще просто использовать #0)
        # Уже используем text для page_num, но show="headings" скрывает #0.
        # Переделаем.
        self.detail_tree.config(show="tree headings")
        self.detail_tree.heading("#0", text="Стр.", anchor=tk.CENTER)
        self.detail_tree.column("#0", width=50, minwidth=40, anchor=tk.CENTER)

        self.copy_btn.config(state=tk.NORMAL)
        self.copy_status.config(text="")

    def _clear_result(self):
        """Очищает результат анализа."""
        self.result_title.config(text="Выберите PDF файл")
        self.result_text.config(state=tk.NORMAL)
        self.result_text.delete("1.0", tk.END)
        self.result_text.config(state=tk.DISABLED)
        for item in self.detail_tree.get_children():
            self.detail_tree.delete(item)
        self.copy_btn.config(state=tk.DISABLED)
        self.copy_status.config(text="")

    def _copy_result(self):
        """Копирует текст из результата в буфер обмена."""
        content = self.result_text.get("1.0", tk.END).strip()
        if content:
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            self.copy_status.config(text="✓ Скопировано!")
            self.root.after(2000, lambda: self.copy_status.config(text=""))

    def run(self):
        """Запуск главного цикла приложения."""
        # Центрирование окна
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"+{x}+{y}")

        self.root.mainloop()


# ─────────────────────────────────────────────────────────
#  Точка входа
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = PDFFormatAnalyzerApp()
    app.run()


def safe_scale_pdf(filepath, scale_percent):
    import fitz
    import os
    import tempfile
    
    scale_factor = scale_percent / 100.0
    if abs(scale_factor - 1.0) < 0.001:
        return filepath
        
    doc = fitz.open(filepath)
    
    for page in doc:
        native_mb = page.mediabox
        nw_mb = native_mb.width / scale_factor
        nh_mb = native_mb.height / scale_factor
        dx_mb = (nw_mb - native_mb.width) / 2
        dy_mb = (nh_mb - native_mb.height) / 2
        
        new_mb = fitz.Rect(native_mb.x0 - dx_mb, native_mb.y0 - dy_mb, native_mb.x1 + dx_mb, native_mb.y1 + dy_mb)
        val = f"[{new_mb.x0} {new_mb.y0} {new_mb.x1} {new_mb.y1}]"
        
        doc.xref_set_key(page.xref, "MediaBox", val)
        doc.xref_set_key(page.xref, "CropBox", val)
        
    fd, temp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    doc.save(temp_path)
    doc.close()

def compress_pages(pages):
    if not pages:
        return ""
        
    # Check if this is a list of tuples (physical, logical)
    has_labels = isinstance(pages[0], tuple)
    
    ranges = []
    s = e = pages[0][0] if has_labels else pages[0]
    s_lbl = e_lbl = pages[0][1] if has_labels else str(pages[0])
    
    for p_item in pages[1:]:
        p = p_item[0] if has_labels else p_item
        lbl = p_item[1] if has_labels else str(p_item)
        
        if p == e + 1:
            e = p
            e_lbl = lbl
        else:
            ranges.append((s, e, s_lbl, e_lbl))
            s = e = p
            s_lbl = e_lbl = lbl
    ranges.append((s, e, s_lbl, e_lbl))
    
    parts = []
    for s, e, s_lbl, e_lbl in ranges:
        phys = str(s) if s == e else f"{s}-{e}"
        
        log_differs = (str(s) != str(s_lbl) or str(e) != str(e_lbl))
        if has_labels and log_differs:
            log_str = str(s_lbl) if s == e else f"{s_lbl}-{e_lbl}"
            parts.append(f"{phys} (лог. {log_str})")
        else:
            parts.append(phys)
            
    return ", ".join(parts)

def is_laser(fmt):
    return clean_format(fmt) in {"A4", "A3"}

def build_print_plan(files_analysis, printed_pages=None, target_copies=1):
    import os
    if printed_pages is None:
        printed_pages = {}
    plan = {"laser": [], "plotter": [], "stats": {}}
    lt, pt = 0, 0
    lf, pf = {}, {}

    for fpath, pages_info in files_analysis.items():
        fname = os.path.basename(fpath)
        done_dict = printed_pages.get(fpath, {})
        fmt_groups = {}
        for info in pages_info:
            pn = info[0]
            fmt = info[1]
            lbl = info[4] if len(info) >= 5 else str(pn)
            land = info[5] if len(info) >= 6 else True
            
            page_data = done_dict.get(pn, {})
            printed = page_data.get("printed", 0) if isinstance(page_data, dict) else page_data
            missing = target_copies - printed
            if missing <= 0:
                continue
            fmt_groups.setdefault((fmt, missing), []).append((pn, lbl, land))

        for (fmt, missing), data in fmt_groups.items():
            pnums = [d[0] for d in data]
            pnums_with_labels = [(d[0], d[1]) for d in data]
            lands = [d[2] for d in data]
            mostly_land = sum(lands) > len(lands) / 2
            
            entry = (fpath, fname, pnums, {
                "format": fmt, "pages_str": compress_pages(pnums_with_labels),
                "count": len(pnums), "landscape": mostly_land,
                "missing": missing
            })
            if is_laser(fmt):
                plan["laser"].append(entry)
                lt += len(pnums) * missing
                lf[fmt] = lf.get(fmt, 0) + len(pnums) * missing
            else:
                plan["plotter"].append(entry)
                pt += len(pnums) * missing
                pf[fmt] = pf.get(fmt, 0) + len(pnums) * missing

    plan["stats"] = {
        "laser_total": lt, "plotter_total": pt,
        "laser_by_fmt": lf, "plotter_by_fmt": pf,
        "grand_total": lt + pt,
    }
    return plan
def get_complexity_weight(fmt, heavy=False):
    """Условная «сложность» листа для прогресса и оценок. heavy — страница тяжёлая
    (см. is_heavy_page). Старая пометка « [V]» в формате тоже считается тяжёлой."""
    f = str(fmt).upper().replace(" ", "")
    if "[V]" in f:
        heavy = True
        f = f.replace("[V]", "")
    base_weight = 1
    if "A4" in f and "X" not in f: base_weight = 1
    elif "A3" in f and "X" not in f: base_weight = 2
    elif "A2" in f and "X" not in f: base_weight = 4
    elif "A1" in f and "X" not in f: base_weight = 8
    elif "A0" in f and "X" not in f: base_weight = 16
    else:
        m = re.search(r'A(\d)X(\d+)', f)
        if m:
            base = int(m.group(1))
            mult = int(m.group(2))
            b_w = {4: 1, 3: 2, 2: 4, 1: 8, 0: 16}.get(base, 1)
            base_weight = b_w * mult
            
    # Добавляем искусственный вес для тяжёлых векторных страниц
    if heavy:
        base_weight += 12
        
    return base_weight
