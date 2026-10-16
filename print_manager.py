# -*- coding: utf-8 -*-
"""
PDF Print Manager v2.0 — ВеллЭнерджи / WellEnergy
Автоматизированная система печати чертежей и документации.

Маршрутизация:
  A4 / A3         → HP LaserJet Pro MFP M435 PCL 6
  A2 / A1 / A0 +  → Canon TM-350

Функции: анализ форматов, тихая печать (SumatraPDF CLI),
         мониторинг принтеров (win32print), история (SQLite),
         тестовый режим, система обновлений, обратная связь.

Запуск:          python print_manager.py
Тестовый режим:  python print_manager.py --dev
"""

# ═══════════════════════════════════════════════════════════════
#  ВЕРСИЯ
# ═══════════════════════════════════════════════════════════════

APP_NAME = "PDF Print Manager"
APP_COMPANY = "ВеллЭнерджи"
APP_COMPANY_EN = "WellEnergy"
APP_VERSION = "2.7.6"
APP_DATE = "2026-08-20"
APP_FULL = f"{APP_COMPANY} — {APP_NAME} v{APP_VERSION}"

import os
import sys
import json
import math
import time
import random
import sqlite3
import hashlib
import logging
import tempfile
import subprocess
import threading
import urllib.request
import urllib.parse
import urllib.error
import io
import struct
import re
import csv
from datetime import datetime
from collections import OrderedDict
from pathlib import Path

try:
    import win32print
    HAS_WIN32PRINT = True
except ImportError:
    HAS_WIN32PRINT = False

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# ── PyMuPDF ──
try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "PyMuPDF"])
            import pymupdf as fitz
        except Exception:
            fitz = None
HAS_FITZ = fitz is not None

# ── win32 (опционально) ──
try:
    import win32print
    import win32con
    import win32gui
    import win32ui
    import win32api
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

# ── tkinterdnd2 (опционально, для Drag & Drop) ──
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

# ── Логирование ──
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "print_manager.log")
log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("PrintManager")
log.setLevel(logging.DEBUG)

# Файл
_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(log_formatter)
log.addHandler(_fh)

# Консоль
_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(log_formatter)
log.addHandler(_ch)

# ── Режим разработки (--dev) ──
DEV_MODE = "--dev" in sys.argv


# ═══════════════════════════════════════════════════════════════
#  1. АНАЛИЗ ФОРМАТОВ
# ═══════════════════════════════════════════════════════════════

A_SERIES = [
    ("A0", 1189, 841), ("A1", 841, 594), ("A2", 594, 420),
    ("A3", 420, 297),  ("A4", 297, 210),
]
TOLERANCE = 6
LASER_FORMATS = {"A4", "A3"}


def detect_format(w_mm, h_mm):
    w, h = max(w_mm, h_mm), min(w_mm, h_mm)
    for name, l, s in A_SERIES:
        if abs(w - l) <= TOLERANCE and abs(h - s) <= TOLERANCE:
            return name
    for name, l, s in A_SERIES:
        if abs(h - l) <= TOLERANCE:
            m = round(w / s)
            if m >= 2 and abs(w / s - m) < 0.15:
                return f"{name}x{m}"
    return f"{int(round(w))}x{int(round(h))}"



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
    return temp_path

def analyze_pdf(filepath):
    doc = fitz.open(filepath)
    result = []
    fmt_rx = re.compile(r"(?i)формат\s*(А[0-4])")
    
    reader = None # Fallback reader
    
    for i, page in enumerate(doc):
        rect = page.rect
        w = rect.width * 25.4 / 72.0
        h = rect.height * 25.4 / 72.0
        phys_fmt = detect_format(w, h)
        
        # Анализ штампа
        clip_rect = fitz.Rect(max(0, rect.width - 250), max(0, rect.height - 250), rect.width, rect.height)
        text = ""
        ocr_fmt = None
        
        try:
            text = page.get_text("text", clip=clip_rect)
        except RuntimeError as e:
            if "bomb" in str(e).lower() or "code=7" in str(e).lower():
                try:
                    from pypdf import PdfReader
                    if reader is None:
                        reader = PdfReader(filepath)
                    text = reader.pages[i].extract_text()
                except Exception:
                    pass
        except Exception:
            pass
            
        if text:
            match = fmt_rx.search(text)
            if match:
                ocr_fmt = match.group(1).upper().replace("А", "A") # Нормализация к латинице
                ocr_fmt = re.sub(r"\s*[ХX×]\s*", "x", ocr_fmt)
            
        result.append((i + 1, phys_fmt, w, h, w > h, ocr_fmt))
    doc.close()
    return result


def group_pages_by_format(pages_info):
    order, groups = [], {}
    for pn, fmt, *_ in pages_info:
        if fmt not in groups:
            order.append(fmt)
            groups[fmt] = []
        groups[fmt].append(pn)
    result = OrderedDict()
    for fmt in order:
        pages = sorted(groups[fmt])
        result[fmt] = (compress_pages(pages), pages)
    return result


def compress_pages(pages):
    if not pages:
        return ""
    ranges, s, e = [], pages[0], pages[0]
    for p in pages[1:]:
        if p == e + 1:
            e = p
        else:
            ranges.append((s, e)); s = e = p
    ranges.append((s, e))
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in ranges)


def is_laser(fmt):
    return fmt in LASER_FORMATS


_FILE_HASH_CACHE = {}

def file_hash(path):
    try:
        st = os.stat(path)
        cache_key = f"{path}_{st.st_size}_{st.st_mtime}"
        if cache_key in _FILE_HASH_CACHE:
            return _FILE_HASH_CACHE[cache_key]
            
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        res = h.hexdigest()[:16]
        _FILE_HASH_CACHE[cache_key] = res
        return res
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════
#  2. МАРШРУТИЗАЦИЯ
# ═══════════════════════════════════════════════════════════════

def build_print_plan(files_analysis, printed_pages=None, target_copies=1):
    if printed_pages is None:
        printed_pages = {}
    plan = {"laser": [], "plotter": [], "stats": {}}
    lt, pt = 0, 0
    lf, pf = {}, {}

    for fpath, pages_info in files_analysis.items():
        fname = os.path.basename(fpath)
        done_dict = printed_pages.get(fpath, {})
        fmt_groups = {}
        for pn, fmt, w, h, land, *rest in pages_info:
            printed = done_dict.get(pn, 0)
            missing = target_copies - printed
            if missing <= 0:
                continue
            fmt_groups.setdefault((fmt, missing), []).append((pn, land))

        for (fmt, missing), data in fmt_groups.items():
            pnums = [d[0] for d in data]
            lands = [d[1] for d in data]
            mostly_land = sum(lands) > len(lands) / 2
            
            entry = (fpath, fname, pnums, {
                "format": fmt, "pages_str": compress_pages(pnums),
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


# ═══════════════════════════════════════════════════════════════
#  3. ДВИЖОК ПЕЧАТИ
# ═══════════════════════════════════════════════════════════════

class PrintEngine:
    def __init__(self, config, dev_mode=False):
        self.config = config
        self.dev_mode = dev_mode
        self.sumatra_path = config.get("sumatra_path", "")
        self._locate_sumatra()

    def _locate_sumatra(self):
        if self.sumatra_path and os.path.isfile(self.sumatra_path):
            return
        candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "SumatraPDF.exe"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "SumatraPDF.exe"),
            r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
            r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
        ]
        for p in os.environ.get("PATH", "").split(";"):
            c = os.path.join(p.strip(), "SumatraPDF.exe")
            if os.path.isfile(c):
                candidates.insert(0, c)
        for path in candidates:
            if os.path.isfile(path):
                self.sumatra_path = path
                self.config["sumatra_path"] = path
                log.info(f"SumatraPDF: {path}")
                return

    @property
    def is_available(self):
        return self.dev_mode or (bool(self.sumatra_path) and os.path.isfile(self.sumatra_path))

    def laser_settings(self, fmt="A4"):
        parts = ["simplex", "fit", "center", "bin=auto"]
        if fmt in ("A4", "A3"):
            parts.append(f"paper={fmt}")
        return ",".join(parts)

    def plotter_settings(self, fmt="A1", landscape=True):
        parts = ["fit"]
        
        # Альбомная/книжная ориентация (поворот на 90 градусов) нужна только для формата А2 
        # (чтобы оптимально укладываться на рулон плоттера). Остальные форматы плоттер 
        # на рулоне располагает сам на основе размера страницы.
        if fmt == "A2":
            parts.append("landscape" if landscape else "portrait")
            
        # Для плоттеров всегда явно передаем имя формата бумаги, 
        # чтобы драйвер принтера применил пользовательские форматы (например, "A4x4")
        parts.append(f"paper={fmt}")
        return ",".join(parts)

    def print_pages(self, pdf_path, printer_name, pages, settings, callback=None, copies=1):
        parts = []
        if pages is not None:
            base_str = compress_pages(sorted(pages))
            if copies > 1:
                pages_str = ",".join([base_str] * copies)
            else:
                pages_str = base_str
            parts.append(pages_str)
        if settings:
            parts.append(settings)
            
        full_settings = ",".join(parts)

        # ── Тестовый режим ──
        if self.dev_mode:
            return self._simulate_print(pdf_path, printer_name, pages, full_settings, callback, copies)

        cmd = [
            self.sumatra_path, "-print-to", printer_name,
            "-print-settings", full_settings, "-silent", pdf_path,
        ]
        cmd_str = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        log.info(f"CMD: {cmd_str}")

        if callback:
            callback("printing", f"Отправка на {printer_name} (Настройки: {full_settings})")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if result.returncode == 0:
                copies_txt = f" x{copies}" if copies > 1 else ""
                msg = f"Отправлено: {os.path.basename(pdf_path)} → {printer_name} ({len(pages)} стр.{copies_txt}) [Настройки: {full_settings}]"
                log.info(msg)
                if callback:
                    callback("success", msg)
                return {"success": True, "message": msg, "command": cmd_str}
            else:
                msg = f"Ошибка SumatraPDF (код {result.returncode}): {result.stderr}"
                log.error(msg)
                if callback:
                    callback("error", msg)
                return {"success": False, "message": msg, "command": cmd_str}
        except subprocess.TimeoutExpired:
            msg = "Таймаут SumatraPDF (600 сек)"
            log.error(msg)
            return {"success": False, "message": msg, "command": cmd_str}
        except Exception as e:
            msg = f"Ошибка: {e}"
            log.error(msg)
            return {"success": False, "message": msg, "command": cmd_str}

    def _simulate_print(self, pdf_path, printer_name, pages, settings, callback, copies=1):
        """Имитация печати в тестовом режиме."""
        fname = os.path.basename(pdf_path)
        copies_txt = f" x{copies} копий" if copies > 1 else ""
        log.info(f"[DEV] Имитация: {fname} → {printer_name}, стр. {compress_pages(pages)}{copies_txt}, настройки: {settings}")

        if callback:
            callback("printing", f"[DEV] Имитация: {fname} → {printer_name}{copies_txt}")

        # Имитация задержки
        delay = random.uniform(0.5, 2.0)
        time.sleep(delay)

        # 10% шанс симуляции ошибки
        if random.random() < 0.1:
            msg = f"[DEV] Симуляция ошибки: принтер {printer_name} не ответил"
            log.warning(msg)
            if callback:
                callback("error", msg)
            return {"success": False, "message": msg, "command": f"[DEV-SIM] {fname}"}

        msg = f"[DEV] Успешно: {fname} → {printer_name} ({len(pages)} стр.{copies_txt}, {delay:.1f}с) [Настройки: {settings}]"
        log.info(msg)
        if callback:
            callback("success", msg)
        return {"success": True, "message": msg, "command": f"[DEV-SIM] {fname}"}


# ═══════════════════════════════════════════════════════════════
#  4. МОНИТОРИНГ ПРИНТЕРОВ
# ═══════════════════════════════════════════════════════════════

PRINTER_STATUS_NAMES = {
    0x00000002: "Ошибка", 0x00000008: "Замятие бумаги",
    0x00000010: "Нет бумаги", 0x00000040: "Проблема с бумагой",
    0x00000080: "Оффлайн", 0x00000200: "Занят",
    0x00000400: "Печатает", 0x00002000: "Ожидание",
    0x00010000: "Прогрев", 0x00020000: "Мало тонера",
    0x00040000: "Нет тонера", 0x00400000: "Дверца открыта",
    0x01000000: "Энергосбережение",
}
CRITICAL_BITS = {0x00000002, 0x00000008, 0x00000010, 0x00000040,
                 0x00000080, 0x00040000, 0x00400000}


class PrintMonitor:
    def __init__(self, on_status=None, on_error=None, interval=3.0):
        self.on_status = on_status
        self.on_error = on_error
        self.interval = interval
        self._running = False
        self._thread = None
        self._printers = set()

    def add(self, name):
        self._printers.add(name)

    def start(self):
        if not HAS_WIN32:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def get_status(self, name):
        if not HAS_WIN32:
            return {"code": 0, "text": "win32 недоступен", "ok": True, "critical": False}
        try:
            h = win32print.OpenPrinter(name)
            try:
                info = win32print.GetPrinter(h, 2)
                code = info.get("Status", 0) if isinstance(info, dict) else info[13]
            finally:
                win32print.ClosePrinter(h)
            texts, crit = [], False
            for bit, txt in PRINTER_STATUS_NAMES.items():
                if code & bit:
                    texts.append(txt)
                    if bit in CRITICAL_BITS:
                        crit = True
            return {"code": code, "text": ", ".join(texts) or "Готов",
                    "ok": not crit, "critical": crit}
        except Exception as e:
            return {"code": -1, "text": str(e), "ok": False, "critical": False}

    def _loop(self):
        while self._running:
            for name in list(self._printers):
                s = self.get_status(name)
                if self.on_status:
                    self.on_status(name, s)
                if s.get("critical") and self.on_error:
                    self.on_error(name, s)
            time.sleep(self.interval)


# ═══════════════════════════════════════════════════════════════
#  5. ИСТОРИЯ ПЕЧАТИ (SQLite)
# ═══════════════════════════════════════════════════════════════

class PrintHistory:
    def __init__(self, db_path):
        self.db_path = db_path
        self._init()

    def _init(self):
        with sqlite3.connect(self.db_path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS print_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, filepath TEXT, filename TEXT,
                file_hash TEXT, printer TEXT, pages TEXT,
                page_count INTEGER, format TEXT, status TEXT DEFAULT 'success')""")
            c.execute("""CREATE TABLE IF NOT EXISTS printed_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath TEXT, file_hash TEXT, page_number INTEGER,
                format TEXT, printer TEXT, timestamp TEXT, copies INTEGER DEFAULT 1,
                UNIQUE(filepath, page_number, file_hash))""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_pp_fp ON printed_pages(filepath)")
            try:
                c.execute("ALTER TABLE printed_pages ADD COLUMN copies INTEGER DEFAULT 1")
            except sqlite3.OperationalError:
                pass

    def record(self, filepath, printer, pages, fmt="", status="success", fhash="", copies=1):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fname = os.path.basename(filepath)
        with sqlite3.connect(self.db_path) as c:
            c.execute("INSERT INTO print_jobs (timestamp,filepath,filename,file_hash,printer,pages,page_count,format,status) VALUES (?,?,?,?,?,?,?,?,?)",
                      (now, filepath, fname, fhash, printer, compress_pages(sorted(pages)), len(pages), fmt, status))
            if status == "success":
                for p in pages:
                    c.execute("""
                        INSERT INTO printed_pages (filepath, file_hash, page_number, format, printer, timestamp, copies)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(filepath, page_number, file_hash) DO UPDATE SET
                        copies = copies + excluded.copies,
                        timestamp = excluded.timestamp
                    """, (filepath, fhash, p, fmt, printer, now, copies))

    def get_printed(self, filepath):
        with sqlite3.connect(self.db_path) as c:
            return {r[0]: r[1] for r in c.execute("SELECT page_number, copies FROM printed_pages WHERE filepath=?", (filepath,))}

    def remove_printed(self, filepath):
        with sqlite3.connect(self.db_path) as c:
            c.execute("DELETE FROM printed_pages WHERE filepath=?", (filepath,))

    def set_printed(self, filepath, pages, copies, fmt="", fhash=""):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as c:
            for p in pages:
                if copies <= 0:
                    c.execute("DELETE FROM printed_pages WHERE filepath=? AND page_number=?", (filepath, p))
                else:
                    c.execute("""
                        INSERT INTO printed_pages (filepath, file_hash, page_number, format, printer, timestamp, copies)
                        VALUES (?, ?, ?, ?, 'Manual', ?, ?)
                        ON CONFLICT(filepath, page_number, file_hash) DO UPDATE SET
                        copies = excluded.copies,
                        timestamp = excluded.timestamp
                    """, (filepath, fhash, p, fmt, now, copies))

    def get_all_printed(self, folder):
        result = {}
        with sqlite3.connect(self.db_path) as c:
            for fp, pn, copies in c.execute("SELECT filepath, page_number, copies FROM printed_pages WHERE filepath LIKE ?", (folder+"%",)):
                result.setdefault(fp, {})[pn] = copies
        return result

    def get_history(self, limit=500):
        with sqlite3.connect(self.db_path) as c:
            return c.execute("SELECT timestamp,filename,printer,pages,page_count,format,status,filepath FROM print_jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def clear(self):
        with sqlite3.connect(self.db_path) as c:
            c.execute("DELETE FROM print_jobs")
            c.execute("DELETE FROM printed_pages")

    def reset_file(self, filepath):
        with sqlite3.connect(self.db_path) as c:
            c.execute("DELETE FROM printed_pages WHERE filepath=?", (filepath,))


# ═══════════════════════════════════════════════════════════════
#  6. СИСТЕМА ОБНОВЛЕНИЙ (GitHub Releases)
# ═══════════════════════════════════════════════════════════════

class UpdateChecker:
    """
    Проверка обновлений через GitHub API.
    Формат тегов: v1.0.0, v1.0.1-beta, v1.1.0
    """

    def __init__(self, config):
        self.config = config
        self.repo_url = config.get("update_repo", "")
        self.current = APP_VERSION

    def check(self):
        """
        Возвращает dict или None:
        {
            "__version__ = "2.7.3",
            "tag": "v2.1.0",
            "type": "stable" | "beta" | "bugfix",
            "body": "Release notes...",
            "date": "2026-08-20",
            "download_url": "...",
            "html_url": "..."
        }
        """
        if not self.repo_url:
            return None

        # Извлекаем owner/repo из URL
        repo = self.repo_url.strip().rstrip("/")
        if "github.com" in repo:
            parts = repo.split("github.com/")[-1].split("/")
            if len(parts) >= 2:
                repo = f"{parts[0]}/{parts[1]}"
        elif "/" not in repo:
            return None

        api_url = f"https://api.github.com/repos/{repo}/releases"
        log.info(f"Проверка обновлений: {api_url}")

        try:
            req = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github.v3+json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                releases = json.loads(resp.read().decode())
        except Exception as e:
            log.warning(f"Ошибка проверки обновлений: {e}")
            return None

        if not releases:
            return None

        latest = releases[0]
        tag = latest.get("tag_name", "")
        ver = tag.lstrip("v").split("-")[0]  # "v2.1.0-beta" → "2.1.0"
        body = latest.get("body", "")
        date = latest.get("published_at", "")[:10]

        # Сравниваем версии
        if not self._is_newer(ver, self.current):
            return None

        # Определяем тип
        if "-beta" in tag or "-alpha" in tag or "-rc" in tag:
            update_type = "beta"
        elif self._is_bugfix(ver, self.current):
            update_type = "bugfix"
        else:
            update_type = "stable"

        # URL для скачивания
        download_url = ""
        for asset in latest.get("assets", []):
            if asset["name"].endswith(".py") or asset["name"].endswith(".zip"):
                download_url = asset["browser_download_url"]
                break

        return {
            "version": ver, "tag": tag, "type": update_type,
            "body": body, "date": date,
            "download_url": download_url,
            "html_url": latest.get("html_url", ""),
        }

    def _is_newer(self, remote, local):
        try:
            r = [int(x) for x in remote.split(".")]
            l = [int(x) for x in local.split(".")]
            return r > l
        except Exception:
            return False

    def _is_bugfix(self, remote, local):
        try:
            r = [int(x) for x in remote.split(".")]
            l = [int(x) for x in local.split(".")]
            return r[0] == l[0] and r[1] == l[1] and r[2] > l[2]
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════
#  7. ОТПРАВКА ОТЧЁТОВ (Telegram + VK)
# ═══════════════════════════════════════════════════════════════

def take_screenshot_bmp():
    """Скриншот экрана через win32api → bytes (BMP)."""
    if not HAS_WIN32:
        return None
    try:
        hdesktop = win32gui.GetDesktopWindow()
        width = win32api.GetSystemMetrics(0)
        height = win32api.GetSystemMetrics(1)

        hwindc = win32gui.GetWindowDC(hdesktop)
        srcdc = win32ui.CreateDCFromHandle(hwindc)
        memdc = srcdc.CreateCompatibleDC()

        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(srcdc, width, height)
        memdc.SelectObject(bmp)
        memdc.BitBlt((0, 0), (width, height), srcdc, (0, 0), 0x00CC0020)  # SRCCOPY

        # Сохраняем BMP в байты
        bmp_info = bmp.GetInfo()
        bmp_bits = bmp.GetBitmapBits(True)

        # Формируем BMP файл
        bmp_header = struct.pack('<2sIHHI', b'BM',
                                 14 + 40 + len(bmp_bits), 0, 0, 14 + 40)
        dib_header = struct.pack('<IiiHHIIiiII', 40,
                                 bmp_info['bmWidth'], -bmp_info['bmHeight'],
                                 1, bmp_info['bmBitsPixel'], 0, len(bmp_bits),
                                 0, 0, 0, 0)
        bmp_data = bmp_header + dib_header + bmp_bits

        memdc.DeleteDC()
        srcdc.DeleteDC()
        win32gui.ReleaseDC(hdesktop, hwindc)

        return bmp_data
    except Exception as e:
        log.error(f"Ошибка скриншота: {e}")
        return None


def send_telegram(token, chat_id, text, screenshot_bytes=None, log_text=None):
    """Отправка сообщения в Telegram."""
    if not token or not chat_id:
        return False, "Telegram: не указаны token или chat_id"

    try:
        # Текст
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
        urllib.request.urlopen(url, data, timeout=15)

        # Скриншот
        if screenshot_bytes:
            url = f"https://api.telegram.org/bot{token}/sendDocument"
            boundary = "----WellEnergyBoundary"
            body = _build_multipart(boundary, chat_id, "screenshot.bmp", screenshot_bytes)
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            urllib.request.urlopen(req, timeout=30)

        # Лог
        if log_text:
            url = f"https://api.telegram.org/bot{token}/sendDocument"
            boundary = "----WellEnergyLogBound"
            body = _build_multipart(boundary, chat_id, "print_manager.log", log_text.encode("utf-8"))
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            urllib.request.urlopen(req, timeout=30)

        return True, "Telegram: отправлено"
    except Exception as e:
        return False, f"Telegram: {e}"


def send_vk(token, peer_id, text, screenshot_bytes=None):
    """Отправка сообщения в VK."""
    if not token or not peer_id:
        return False, "VK: не указаны token или peer_id"

    try:
        url = "https://api.vk.com/method/messages.send"
        data = urllib.parse.urlencode({
            "peer_id": peer_id, "message": text,
            "random_id": random.randint(1, 2**31),
            "access_token": token, "v": "5.199",
        }).encode()
        urllib.request.urlopen(url, data, timeout=15)
        return True, "VK: отправлено"
    except Exception as e:
        return False, f"VK: {e}"


def _build_multipart(boundary, chat_id, filename, file_bytes):
    """Формирует multipart/form-data для Telegram API."""
    lines = []
    lines.append(f"--{boundary}".encode())
    lines.append(b'Content-Disposition: form-data; name="chat_id"\r\n')
    lines.append(str(chat_id).encode())
    lines.append(f"--{boundary}".encode())
    lines.append(f'Content-Disposition: form-data; name="document"; filename="{filename}"'.encode())
    lines.append(b"Content-Type: application/octet-stream\r\n")
    lines.append(file_bytes)
    lines.append(f"--{boundary}--".encode())
    return b"\r\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  8. КОНФИГУРАЦИЯ
# ═══════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "printers": {
        "laser": {"name": "HP LaserJet Pro MFP M435 PCL 6", "formats": ["A4", "A3"],
                  "settings": "simplex,fit,center,bin=auto"},
        "plotter": {"name": "Canon TM-350", "formats": ["A2", "A1", "A0", "custom"],
                    "settings": "fit"},
    },
    "sumatra_path": "",
    "db_path": "",
    "last_folder": "",
    "include_subfolders": False,
    "dev_mode": False,
    "check_updates": True,
    "update_repo": "",
    "beta_channel": False,
    "tg_bot_token": "",
    "tg_chat_id": "",
    "vk_token": "",
    "vk_peer_id": "",
    "log_level": "INFO",
    "theme": "dark",
}


def load_config(path):
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
                elif isinstance(v, dict):
                    for k2, v2 in v.items():
                        cfg[k].setdefault(k2, v2)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg, path):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"Ошибка сохранения конфига: {e}")


# ═══════════════════════════════════════════════════════════════
#  9. GUI — КОРПОРАТИВНЫЙ ИНТЕРФЕЙС
# ═══════════════════════════════════════════════════════════════

class Theme:
    """Тёмная корпоративная тема с улучшенным контрастом и выдающимися цветами."""
    BG       = "#1e2126"
    BG2      = "#272a30"
    BG3      = "#32363e"
    FG       = "#f8fafc"
    FG2      = "#94a3b8"
    FG3      = "#64748b"
    ACCENT   = "#f97316"
    ACCENT_D = "#c2410c"
    ACCENT_L = "#fdba74"
    OK       = "#22c55e"
    ERR      = "#ef4444"
    WARN     = "#f59e0b"
    BORDER   = "#3f3f46"
    INPUT_BG = "#181a1f"
    SEL      = "#431407"
    HP_CLR   = "#3b82f6"
    CN_CLR   = "#f43f5e"
    PRINTED  = "#14532d"
    PARTIAL  = "#422006"


class ThemeLight:
    """Светлая тема с плавными переходами и приятными цветами для глаз."""
    BG       = "#f1f5f9"
    BG2      = "#ffffff"
    BG3      = "#e2e8f0"
    FG       = "#0f172a"
    FG2      = "#475569"
    FG3      = "#94a3b8"
    ACCENT   = "#ea580c"
    ACCENT_D = "#9a3412"
    ACCENT_L = "#fb923c"
    OK       = "#16a34a"
    ERR      = "#dc2626"
    WARN     = "#d97706"
    BORDER   = "#cbd5e1"
    INPUT_BG = "#ffffff"
    SEL      = "#ffedd5"
    HP_CLR   = "#2563eb"
    CN_CLR   = "#e11d48"
    PRINTED  = "#dcfce7"
    PARTIAL  = "#fef3c7"


def get_theme(name):
    """Возвращает класс темы по имени."""
    return ThemeLight if name == "light" else Theme


T = Theme  # переключается через get_theme()



class PrintPreviewDialog:
    def __init__(self, parent, pdf_path, filename, theme, initial_copies=1):
        self.parent = parent
        self.pdf_path = pdf_path
        self.filename = filename
        T = theme
        import fitz
        from PIL import Image, ImageTk
        self.doc = fitz.open(pdf_path)
        self.total_pages = len(self.doc)
        self.current_page = 0
        
        self.result = None
        
        self.top = tk.Toplevel(parent)
        self.top.title(f"Предпросмотр печати: {filename}")
        self.top.geometry("1000x750")
        self.top.configure(bg=T.BG)
        self.top.transient(parent)
        self.top.grab_set()
        
        self.top.rowconfigure(0, weight=1)
        self.top.columnconfigure(0, weight=1)
        
        main_paned = tk.PanedWindow(self.top, orient=tk.HORIZONTAL, bg=T.BG, bd=0)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        left_frame = tk.Frame(main_paned, bg=T.BG2)
        main_paned.add(left_frame, minsize=500, stretch="always")
        
        self.canvas = tk.Canvas(left_frame, bg=T.BG2, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        nav_f = tk.Frame(left_frame, bg=T.BG2)
        nav_f.pack(fill=tk.X, pady=5)
        
        self.btn_prev = self._btn(nav_f, "◀ Пред.", self._prev_page)
        self.btn_prev.pack(side=tk.LEFT, padx=10)
        
        self.lbl_page = tk.Label(nav_f, text="", font=("Segoe UI", 12), bg=T.BG2, fg=T.FG)
        self.lbl_page.pack(side=tk.LEFT, expand=True)
        
        self.btn_next = self._btn(nav_f, "След. ▶", self._next_page)
        self.btn_next.pack(side=tk.RIGHT, padx=10)
        
        right_frame = tk.Frame(main_paned, bg=T.BG3, padx=15, pady=15)
        main_paned.add(right_frame, minsize=300)
        
        tk.Label(right_frame, text="Настройки печати", font=("Segoe UI Semibold", 16), 
                 bg=T.BG3, fg=T.FG).pack(anchor=tk.W, pady=(0, 20))
                 
        tk.Label(right_frame, text="Масштаб (%):", font=("Segoe UI", 12), bg=T.BG3, fg=T.FG).pack(anchor=tk.W)
        self.scale_var = tk.StringVar(value="99.94")
        tk.Entry(right_frame, textvariable=self.scale_var, font=("Segoe UI", 12), 
                 bg=T.INPUT_BG, fg=T.FG, insertbackground=T.FG).pack(fill=tk.X, pady=(0, 15))
                 
        tk.Label(right_frame, text="Копии:", font=("Segoe UI", 12), bg=T.BG3, fg=T.FG).pack(anchor=tk.W)
        self.copies_var = tk.IntVar(value=initial_copies)
        tk.Spinbox(right_frame, from_=1, to=999, textvariable=self.copies_var, font=("Segoe UI", 12),
                   bg=T.INPUT_BG, fg=T.FG, buttonbackground=T.BG3).pack(fill=tk.X, pady=(0, 30))
        
        self.lbl_info = tk.Label(right_frame, text="", font=("Segoe UI", 10), bg=T.BG3, fg=T.FG2, justify=tk.LEFT)
        self.lbl_info.pack(fill=tk.X, pady=(0, 20), anchor=tk.W)

        btn_f = tk.Frame(right_frame, bg=T.BG3)
        btn_f.pack(side=tk.BOTTOM, fill=tk.X)
        
        self._btn(btn_f, "ОК (Печать)", self._on_ok, bg=T.OK).pack(fill=tk.X, pady=5)
        self._btn(btn_f, "Отмена", self.top.destroy).pack(fill=tk.X)
        
        self.top.bind("<Left>", lambda e: self._prev_page())
        self.top.bind("<Right>", lambda e: self._next_page())
        self.canvas.bind("<Configure>", lambda e: self._draw_page())
        
        self._draw_page()
        
    def _btn(self, p, txt, cmd, bg=None, fg=None):
        if not bg: bg = T.BG3
        if not fg: fg = T.FG
        b = tk.Button(p, text=txt, font=("Segoe UI", 11), bg=bg, fg=fg,
                      activebackground=T.ACCENT, activeforeground="#ffffff",
                      relief=tk.FLAT, padx=12, pady=6, cursor="hand2", command=cmd)
        return b
        
    def _prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            self._draw_page()
            
    def _next_page(self):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self._draw_page()
            
    def _draw_page(self):
        if not hasattr(self, 'canvas'): return
        
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            self.top.after(50, self._draw_page)
            return
            
        page = self.doc.load_page(self.current_page)
        
        rect = page.rect
        w_mm = rect.width * 25.4 / 72
        h_mm = rect.height * 25.4 / 72
        self.lbl_info.config(text=f"Стр. {self.current_page+1}: {w_mm:.0f} x {h_mm:.0f} мм")
        
        zoom = min(cw / rect.width, ch / rect.height) * 0.95
        import fitz
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        
        from PIL import Image, ImageTk
        if pix.alpha:
            img = Image.frombytes("RGBA", [pix.width, pix.height], pix.samples)
        else:
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            
        self.tkimg = ImageTk.PhotoImage(image=img)
        self.canvas.delete("all")
        self.canvas.create_image(cw//2, ch//2, image=self.tkimg, anchor=tk.CENTER)
        
        self.lbl_page.config(text=f"Страница {self.current_page + 1} из {self.total_pages}")
        
        self.btn_prev.config(state=tk.NORMAL if self.current_page > 0 else tk.DISABLED)
        self.btn_next.config(state=tk.NORMAL if self.current_page < self.total_pages - 1 else tk.DISABLED)
        
    def _on_ok(self):
        try:
            scale = float(self.scale_var.get().replace(",", "."))
        except:
            scale = 99.94
        copies = self.copies_var.get()
        
        self.result = {
            "scale": scale,
            "copies": copies
        }
        self.doc.close()
        self.top.destroy()

class PrintManagerApp:
    def _on_printer_status(self, printer_name, status_dict):
        pass

    def _on_printer_error(self, printer_name, err_msg):
        pass

    def _spooler_monitor_loop(self):
        pass

    def __init__(self):
        if HAS_DND:
            self.root = TkinterDnD.Tk()
        else:
            self.root = tk.Tk()
        self.root.withdraw()  # скрываем пока строим

        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        self.app_dir = os.path.join(appdata, "WellEnergyPrintManager")
        os.makedirs(self.app_dir, exist_ok=True)
        
        self.config_path = os.path.join(self.app_dir, "print_config.json")
        self.config = load_config(self.config_path)
        self.scan_cache_path = os.path.join(self.app_dir, "scan_cache.json")
        self.scan_cache = {}
        self._load_scan_cache()

        # Тема
        global T
        T = get_theme(self.config.get("theme", "dark"))

        # Dev mode
        self.dev_mode = DEV_MODE or self.config.get("dev_mode", False)

        # Заголовок
        title = APP_FULL
        if self.dev_mode:
            title += "  [DEV MODE]"
        self.root.title(title)
        self.root.geometry("1120x800")
        self.root.minsize(920, 620)
        self.root.configure(bg=T.BG)

        # Titlebar (тёмный)
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int))
        except Exception:
            pass

        # Модули
        # База данных
        db_path = self.config.get("db_path")
        if not db_path or db_path == os.path.join(os.path.dirname(os.path.abspath(__file__)), "print_history.db"):
            db_path = os.path.join(self.app_dir, "print_history.db")
            
        self.config["db_path"] = db_path
        self.history = PrintHistory(db_path)
        self.engine = PrintEngine(self.config, dev_mode=self.dev_mode)
        self.monitor = PrintMonitor(on_status=self._on_printer_status, on_error=self._on_printer_error)
        self.updater = UpdateChecker(self.config)

        # Состояние
        self.current_folder = self.config.get("last_folder", "")
        self.pdf_files = []
        self.files_analysis = {}
        self.printed_pages = {}
        self.current_plan = None
        self._print_running = False
        self._print_cancel = False
        self._log_lines = []
        self._preview_cache = {}
        self._preview_current = None

        # Мониторинг
        for key in ("laser", "plotter"):
            self.monitor.add(self.config["printers"][key]["name"])

        if HAS_WIN32PRINT:
            self._spooler_running = True
            threading.Thread(target=self._spooler_monitor_loop, daemon=True).start()

        self._setup_styles()
        self._build_ui()

        # Загрузка
        if self.current_folder and os.path.isdir(self.current_folder):
            self.folder_var.set(self.current_folder)
            self.root.after(300, self._load_folder)

        # Показываем окно
        self.root.deiconify()
        self._center_window()

        # Обновления
        if self.config.get("check_updates", True):
            self.root.after(2000, self._check_updates_async)

        log.info(f"Запуск {APP_FULL} (dev={self.dev_mode})")

    # ─── Стили ────────────────────────────────────────────────

    def _setup_styles(self):
        s = ttk.Style()
        s.theme_use("clam")

        s.configure(".", background=T.BG, foreground=T.FG, borderwidth=0)

        # Treeview
        s.configure("W.Treeview", background=T.BG3, foreground=T.FG,
                     fieldbackground=T.BG3, rowheight=26, font=("Segoe UI", 10))
        s.map("W.Treeview", background=[("selected", T.SEL)], foreground=[("selected", T.ACCENT_L)])
        s.configure("W.Treeview.Heading", background=T.BG2, foreground=T.ACCENT,
                     font=("Segoe UI Semibold", 10))
        s.map("W.Treeview.Heading", background=[("active", T.BG3)])

        # Notebook
        s.configure("W.TNotebook", background=T.BG, borderwidth=0)
        s.configure("W.TNotebook.Tab", background=T.BG2, foreground=T.FG2,
                     font=("Segoe UI Semibold", 10), padding=[18, 7])
        s.map("W.TNotebook.Tab", background=[("selected", T.BG3)],
              foreground=[("selected", T.ACCENT)])

        # Progress
        s.configure("W.Horizontal.TProgressbar", troughcolor=T.BG2,
                     background=T.ACCENT, thickness=18)

        # Scrollbar
        s.configure("W.Vertical.TScrollbar", background=T.BG2,
                     troughcolor=T.BG, arrowsize=13)

    # ─── Кнопка (утилита) ─────────────────────────────────────

    def _btn(self, parent, text, cmd, bg=None, fg=None, font_size=10, **kw):
        bg = bg or T.ACCENT
        fg = fg or "#ffffff"
        hover_bg = T.ACCENT_L if bg == T.ACCENT else T.BG3
        b = tk.Button(parent, text=text, command=cmd, font=("Segoe UI", font_size, "bold"),
                      bg=bg, fg=fg, activebackground=T.ACCENT_D, activeforeground="#ffffff",
                      relief=tk.FLAT, cursor="hand2", padx=16, pady=6, **kw)
        
        # Плавные эффекты наведения
        def on_enter(e):
            b.config(bg=hover_bg)
        def on_leave(e):
            b.config(bg=bg)
            
        b.bind("<Enter>", on_enter)
        b.bind("<Leave>", on_leave)
        return b

    # ─── Построение UI ────────────────────────────────────────

    def _build_ui(self):
        # ── Шапка ──
        header = tk.Frame(self.root, bg=T.ACCENT_D, height=42)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        # ── Заголовок ──
        # Пытаемся загрузить пользовательскую иконку
        icon_path = os.path.join(self.app_dir, "icon.png")
        if os.path.exists(icon_path):
            try:
                self.icon_img = tk.PhotoImage(file=icon_path)
                # Устанавливаем иконку приложения (в трее и шапке окна)
                self.root.iconphoto(True, self.icon_img)
                tk.Label(header, image=self.icon_img, bg=T.ACCENT_D, padx=5).pack(side=tk.LEFT, padx=(10, 0))
            except Exception:
                tk.Label(header, text="WE", font=("Segoe UI Black", 16), fg="#ffffff", bg=T.ACCENT_D, padx=12).pack(side=tk.LEFT)
        else:
            tk.Label(header, text="WE", font=("Segoe UI Black", 16), fg="#ffffff", bg=T.ACCENT_D, padx=12).pack(side=tk.LEFT)
            
        tk.Label(header, text=f"{APP_COMPANY}  |  {APP_NAME}",
                 font=("Segoe UI Semibold", 11), fg="#ffffff", bg=T.ACCENT_D).pack(side=tk.LEFT, padx=5)

        if self.dev_mode:
            tk.Label(header, text="DEV MODE (ТЕСТОВЫЙ РЕЖИМ)", font=("Consolas", 10, "bold"),
                     fg="#ffff00", bg=T.ERR, padx=8).pack(side=tk.LEFT)

        ver_label = tk.Label(header, text=f"v{APP_VERSION} ({APP_DATE})",
                             font=("Segoe UI", 9), fg="#e0e0e0", bg=T.ACCENT_D)
        ver_label.pack(side=tk.RIGHT, padx=12)

        # ── Выбор папки ──
        folder_bar = tk.Frame(self.root, bg=T.BG2, pady=7, padx=10)
        folder_bar.pack(fill=tk.X, padx=8, pady=(6, 0))

        tk.Label(folder_bar, text="Папка:", font=("Segoe UI", 10),
                 fg=T.FG2, bg=T.BG2).pack(side=tk.LEFT, padx=(4, 6))

        self.folder_var = tk.StringVar()
        e = tk.Entry(folder_bar, textvariable=self.folder_var, font=("Segoe UI", 10),
                     bg=T.INPUT_BG, fg=T.FG, insertbackground=T.ACCENT,
                     relief=tk.FLAT, highlightthickness=1,
                     highlightcolor=T.ACCENT, highlightbackground=T.BORDER)
        e.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, ipady=3)
        e.bind("<Return>", lambda _: self._load_folder())

        self.subfolder_var = tk.BooleanVar(value=self.config.get("include_subfolders", False))
        tk.Checkbutton(folder_bar, text="Подпапки", variable=self.subfolder_var,
                       font=("Segoe UI", 9), bg=T.BG2, fg=T.FG2,
                       activebackground=T.BG2, selectcolor=T.INPUT_BG,
                       command=lambda: self.current_folder and self._load_folder()
                       ).pack(side=tk.LEFT, padx=6)

        self._btn(folder_bar, "Обзор...", self._browse_folder).pack(side=tk.LEFT, padx=4)

        # ── Вкладки ──
        self.nb = ttk.Notebook(self.root, style="W.TNotebook")
        self.nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        self.tab_ana = tk.Frame(self.nb, bg=T.BG3)
        self.tab_prt = tk.Frame(self.nb, bg=T.BG3)
        self.tab_his = tk.Frame(self.nb, bg=T.BG3)
        self.tab_set = tk.Frame(self.nb, bg=T.BG3)
        self.tab_log = tk.Frame(self.nb, bg=T.BG3)

        self.nb.add(self.tab_ana, text="  Анализ  ")
        self.nb.add(self.tab_prt, text="  Печать  ")
        self.nb.add(self.tab_his, text="  История  ")
        self.nb.add(self.tab_set, text="  Настройки  ")
        if self.dev_mode:
            self.nb.add(self.tab_log, text="  Логи [DEV]  ")

        self._build_tab_analysis()
        self._build_tab_print()
        self._build_tab_history()
        self._build_tab_settings()
        if self.dev_mode:
            self._build_tab_logs()

        # ── Статус-бар ──
        sbar = tk.Frame(self.root, bg=T.BG2, height=30)
        sbar.pack(fill=tk.X, padx=8, pady=(0, 6))
        sbar.pack_propagate(False)

        self.status_var = tk.StringVar(value="Выберите папку с PDF")
        tk.Label(sbar, textvariable=self.status_var, font=("Segoe UI", 9),
                 fg=T.FG2, bg=T.BG2, anchor=tk.W, padx=8).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Кнопка «Сообщить о проблеме»
        self._btn(sbar, "Сообщить о проблеме", self._report_problem_dialog,
                  bg=T.BG3, font_size=9).pack(side=tk.RIGHT, padx=4, pady=2)

        # Статусы принтеров
        self.hp_st = tk.StringVar(value="")
        self.cn_st = tk.StringVar(value="")
        self.spooler_st = tk.StringVar(value="")
        
        tk.Label(sbar, textvariable=self.hp_st, font=("Segoe UI", 9),
                 fg=T.OK, bg=T.BG2).pack(side=tk.RIGHT, padx=6)
        tk.Label(sbar, textvariable=self.cn_st, font=("Segoe UI", 9),
                 fg=T.OK, bg=T.BG2).pack(side=tk.RIGHT, padx=6)
        tk.Label(sbar, textvariable=self.spooler_st, font=("Segoe UI", 9, "bold"),
                 fg="#38bdf8", bg=T.BG2).pack(side=tk.RIGHT, padx=6)

        # Горячие клавиши + Drag & Drop
        self._setup_hotkeys()
        self._setup_dnd()

    # ─── Вкладка «Анализ» ────────────────────────────────────

    def _build_tab_analysis(self):
        tab = self.tab_ana
        pane = tk.PanedWindow(tab, orient=tk.HORIZONTAL, bg=T.BG3,
                              sashwidth=3, sashrelief=tk.FLAT)
        pane.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Левая — список файлов
        left = tk.Frame(pane, bg=T.BG3)
        pane.add(left, minsize=260, width=350)

        tk.Label(left, text="PDF файлы", font=("Segoe UI Semibold", 11),
                 fg=T.ACCENT, bg=T.BG3, anchor=tk.W, padx=8, pady=5).pack(fill=tk.X)

        tf = tk.Frame(left, bg=T.BG3)
        tf.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        self.file_tree = ttk.Treeview(tf, columns=("pages", "status"),
                                       show="tree headings", style="W.Treeview", selectmode="browse")
        self.file_tree.heading("#0", text="Имя файла", anchor=tk.W)
        self.file_tree.heading("pages", text="Стр.", anchor=tk.CENTER)
        self.file_tree.heading("status", text="", anchor=tk.CENTER)
        self.file_tree.column("#0", width=210, minwidth=140)
        self.file_tree.column("pages", width=42, minwidth=30, anchor=tk.CENTER)
        self.file_tree.column("status", width=65, minwidth=40, anchor=tk.CENTER)

        self.file_tree.tag_configure("printed", background=T.PRINTED)
        self.file_tree.tag_configure("partial", background=T.PARTIAL)
        self.file_tree.tag_configure("none", background=T.BG3)

        scr = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.file_tree.yview, style="W.Vertical.TScrollbar")
        self.file_tree.configure(yscrollcommand=scr.set)
        self.file_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scr.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_tree.bind("<<TreeviewSelect>>", self._on_file_select)
        self.file_tree.bind("<Button-3>", self._show_file_menu)

        self.summary_var = tk.StringVar()
        tk.Label(left, textvariable=self.summary_var, font=("Segoe UI", 9),
                 fg=T.FG2, bg=T.BG3, anchor=tk.W, padx=8, pady=3,
                 wraplength=330, justify=tk.LEFT).pack(fill=tk.X)

        # Правая — результат
        right = tk.Frame(pane, bg=T.BG3)
        pane.add(right, minsize=300)

        self.result_title = tk.Label(right, text="Выберите файл",
                                      font=("Segoe UI Semibold", 11), fg=T.ACCENT,
                                      bg=T.BG3, anchor=tk.W, padx=8, pady=5)
        self.result_title.pack(fill=tk.X)

        self.result_text = tk.Text(right, font=("Consolas", 11), bg=T.BG, fg=T.FG,
                                    insertbackground=T.ACCENT, relief=tk.FLAT,
                                    highlightthickness=1, highlightcolor=T.ACCENT,
                                    highlightbackground=T.BORDER, padx=10, pady=8,
                                    wrap=tk.WORD, state=tk.DISABLED, height=7)
        self.result_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        self.result_text.tag_configure("fmt", foreground=T.ACCENT, font=("Consolas", 11, "bold"))
        self.result_text.tag_configure("pg", foreground=T.FG)
        self.result_text.tag_configure("hp", foreground=T.HP_CLR, font=("Consolas", 10))
        self.result_text.tag_configure("cn", foreground=T.CN_CLR, font=("Consolas", 10))
        self.result_text.tag_configure("ok", foreground=T.OK, font=("Consolas", 11, "bold"))
        self.result_text.tag_configure("dim", foreground=T.FG3, font=("Consolas", 10))

        self.result_text.bind("<Button-3>", self._show_result_menu)

        # Кнопки
        bf = tk.Frame(right, bg=T.BG3, pady=4, padx=6)
        bf.pack(fill=tk.X)

        self.copy_btn = self._btn(bf, "Копировать", self._copy_result, bg=T.BG2, font_size=9)
        self.copy_btn.pack(side=tk.LEFT, padx=2)
        self.copy_btn.config(state=tk.DISABLED)
        
        tk.Label(bf, text="Копии:", font=("Segoe UI", 9), fg=T.FG, bg=T.BG3).pack(side=tk.LEFT, padx=(10, 2))
        self.copies_var = tk.IntVar(value=self.config.get("last_copies", 1))
        self.copies_var.trace_add("write", self._on_copies_changed)
        self.copies_spin = tk.Spinbox(bf, from_=1, to=99, textvariable=self.copies_var, width=3,
                                      font=("Consolas", 10, "bold"), bg=T.INPUT_BG, fg=T.FG,
                                      buttonbackground=T.BG2, highlightthickness=1, highlightbackground=T.BORDER)
        self.copies_spin.pack(side=tk.LEFT, padx=2)

        self.print_one_btn = self._btn(bf, "Печатать этот файл", self._print_selected_file, bg=T.OK, font_size=9)
        self.print_one_btn.pack(side=tk.LEFT, padx=6)
        self.print_one_btn.config(state=tk.DISABLED)

        self.copy_lbl = tk.Label(bf, text="", font=("Segoe UI", 9), fg=T.OK, bg=T.BG3)
        self.copy_lbl.pack(side=tk.LEFT, padx=8)

        self.export_btn = self._btn(bf, "Экспорт", self._export_analysis, bg=T.BG2, font_size=9)
        self.export_btn.pack(side=tk.RIGHT, padx=2)
        self.export_btn.config(state=tk.DISABLED)

        self.export_all_btn = self._btn(bf, "Экспорт всех", self._export_all_analysis, bg=T.BG2, font_size=9)
        self.export_all_btn.pack(side=tk.RIGHT, padx=2)

        # Подробная таблица
        tk.Label(right, text="Постраничная информация", font=("Segoe UI Semibold", 10),
                 fg=T.FG3, bg=T.BG3, anchor=tk.W, padx=8, pady=3).pack(fill=tk.X)

        df = tk.Frame(right, bg=T.BG3)
        df.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        self.detail_tree = ttk.Treeview(df, columns=("format", "w", "h", "orient", "dest", "copies"),
                                         show="tree headings", style="W.Treeview", height=5)
        for cid, txt, w in [("#0","Стр.",40),("format","Формат",65),("w","Ш",55),
                             ("h","В",55),("orient","Ор.",55),("dest","Куда",70),("copies","Копии",60)]:
            self.detail_tree.heading(cid, text=txt, anchor=tk.CENTER)
            self.detail_tree.column(cid, width=w, anchor=tk.CENTER, minwidth=30)

        self.detail_tree.tag_configure("hp", background="#2a3040")
        self.detail_tree.tag_configure("cn", background="#402a28")
        self.detail_tree.tag_configure("done", foreground=T.FG3)
        self.detail_tree.tag_configure("warn", background=T.WARN, foreground="#000000")

        dscr = ttk.Scrollbar(df, orient=tk.VERTICAL, command=self.detail_tree.yview, style="W.Vertical.TScrollbar")
        self.detail_tree.configure(yscrollcommand=dscr.set)
        self.detail_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        dscr.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.detail_tree.bind("<Button-3>", self._show_format_menu)

        self.detail_tree.bind("<<TreeviewSelect>>", self._on_detail_page_select)

        # Предпросмотр
        pv_frame = tk.Frame(right, bg=T.BG3, height=200)
        pv_frame.pack(fill=tk.X, padx=4, pady=(0, 4))
        pv_frame.pack_propagate(False)

        pv_header = tk.Frame(pv_frame, bg=T.BG3)
        pv_header.pack(fill=tk.X)
        tk.Label(pv_header, text="Предпросмотр", font=("Segoe UI Semibold", 10),
                 fg=T.FG3, bg=T.BG3, anchor=tk.W, padx=8, pady=2).pack(side=tk.LEFT)
        self.preview_btn = self._btn(pv_header, "Увеличить", self._show_fullscreen_preview,
                                      bg=T.BG2, font_size=9)
        self.preview_btn.pack(side=tk.RIGHT, padx=4)
        self.preview_btn.config(state=tk.DISABLED)

        self.preview_info = tk.Label(pv_header, text="", font=("Segoe UI", 9),
                                     fg=T.FG2, bg=T.BG3)
        self.preview_info.pack(side=tk.RIGHT, padx=4)

        self.preview_label = tk.Label(pv_frame, bg=T.BG, text="Нажмите на страницу",
                                      font=("Segoe UI", 9), fg=T.FG3, relief=tk.FLAT,
                                      highlightthickness=1, highlightbackground=T.BORDER)
        self.preview_label.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

    # ─── Вкладка «Печать» ────────────────────────────────────

    def _build_tab_print(self):
        tab = self.tab_prt
        main = tk.Frame(tab, bg=T.BG3)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        tk.Label(main, text="ПЛАН ПЕЧАТИ", font=("Segoe UI Semibold", 13),
                 fg=T.ACCENT, bg=T.BG3, anchor=tk.W, padx=4, pady=6).pack(fill=tk.X)

        blocks = tk.Frame(main, bg=T.BG3)
        blocks.pack(fill=tk.BOTH, expand=True)

        # HP блок
        self.hp_lf = tk.LabelFrame(blocks, text=f"  {self.config['printers']['laser']['name']}  ",
                               font=("Segoe UI Semibold", 10), fg=T.HP_CLR,
                               bg=T.BG, labelanchor="nw", padx=6, pady=6)
        self.hp_lf.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        self.hp_plan = tk.Text(self.hp_lf, font=("Consolas", 10), bg=T.BG, fg=T.FG,
                               relief=tk.FLAT, height=5, wrap=tk.WORD, state=tk.DISABLED,
                               highlightthickness=0)
        self.hp_plan.pack(fill=tk.BOTH, expand=True)

        # Canon блок
        self.cn_lf = tk.LabelFrame(blocks, text=f"  {self.config['printers']['plotter']['name']}  ",
                               font=("Segoe UI Semibold", 10), fg=T.CN_CLR,
                               bg=T.BG, labelanchor="nw", padx=6, pady=6)
        self.cn_lf.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        self.cn_plan = tk.Text(self.cn_lf, font=("Consolas", 10), bg=T.BG, fg=T.FG,
                               relief=tk.FLAT, height=5, wrap=tk.WORD, state=tk.DISABLED,
                               highlightthickness=0)
        self.cn_plan.pack(fill=tk.BOTH, expand=True)

        # Кнопки
        cf = tk.Frame(main, bg=T.BG3, pady=6)
        cf.pack(fill=tk.X)

        self.test_btn = self._btn(cf, "Пробная (1 стр.)", self._test_print, bg=T.HP_CLR, font_size=10)
        self.test_btn.pack(side=tk.LEFT, padx=4)
        self.test_btn.config(state=tk.DISABLED)

        self.go_btn = self._btn(cf, "Начать печать", self._start_print, bg=T.OK, font_size=11)
        self.go_btn.pack(side=tk.LEFT, padx=8)
        self.go_btn.config(state=tk.DISABLED)

        self.stop_btn = self._btn(cf, "Отмена", self._cancel_print, bg=T.ERR, font_size=10)
        self.stop_btn.pack(side=tk.LEFT, padx=4)
        self.stop_btn.config(state=tk.DISABLED)

        self._btn(cf, "Сменить принтер...", self._change_printer_dialog, bg=T.BG2, font_size=9).pack(side=tk.RIGHT, padx=4)

        # Прогресс
        pf = tk.Frame(main, bg=T.BG3, pady=4)
        pf.pack(fill=tk.X)

        self.prog_var = tk.DoubleVar(value=0)
        ttk.Progressbar(pf, variable=self.prog_var, style="W.Horizontal.TProgressbar",
                        maximum=100).pack(fill=tk.X, padx=4)

        self.prog_lbl = tk.Label(pf, text="", font=("Segoe UI", 9),
                                  fg=T.FG2, bg=T.BG3, anchor=tk.W, padx=4, pady=2)
        self.prog_lbl.pack(fill=tk.X)

        # Статусы живые
        sf = tk.Frame(main, bg=T.BG3, pady=3)
        sf.pack(fill=tk.X)
        self.hp_live = tk.Label(sf, text="HP: ...", font=("Segoe UI", 10),
                                 fg=T.FG2, bg=T.BG3, padx=8)
        self.hp_live.pack(side=tk.LEFT)
        self.cn_live = tk.Label(sf, text="Canon: ...", font=("Segoe UI", 10),
                                 fg=T.FG2, bg=T.BG3, padx=8)
        self.cn_live.pack(side=tk.LEFT)

    # ─── Вкладка «История» ───────────────────────────────────

    def _build_tab_history(self):
        tab = self.tab_his
        hd = tk.Frame(tab, bg=T.BG3, pady=5)
        hd.pack(fill=tk.X, padx=6)

        tk.Label(hd, text="История печати", font=("Segoe UI Semibold", 12),
                 fg=T.ACCENT, bg=T.BG3, padx=4).pack(side=tk.LEFT)

        self._btn(hd, "Обновить", self._refresh_history, bg=T.BG2, font_size=9).pack(side=tk.RIGHT, padx=4)
        self._btn(hd, "Экспорт CSV", self._export_history, bg=T.BG2, font_size=9).pack(side=tk.RIGHT, padx=4)
        self._btn(hd, "Очистить", self._clear_history, bg=T.ERR, font_size=9).pack(side=tk.RIGHT, padx=4)

        tf = tk.Frame(tab, bg=T.BG3)
        tf.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        self.hist_tree = ttk.Treeview(tf, columns=("date","file","printer","pages","cnt","fmt","st"),
                                       show="headings", style="W.Treeview")
        for cid, txt, w in [("date","Дата",135),("file","Файл",230),("printer","Принтер",170),
                             ("pages","Страницы",110),("cnt","Кол.",50),("fmt","Формат",65),("st","",45)]:
            self.hist_tree.heading(cid, text=txt, anchor=tk.W)
            self.hist_tree.column(cid, width=w, anchor=tk.W)

        self.hist_tree.tag_configure("ok", foreground=T.OK)
        self.hist_tree.tag_configure("err", foreground=T.ERR)

        hs = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.hist_tree.yview, style="W.Vertical.TScrollbar")
        self.hist_tree.configure(yscrollcommand=hs.set)
        self.hist_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        hs.pack(side=tk.RIGHT, fill=tk.Y)

    # ─── Вкладка «Настройки» ─────────────────────────────────

    def _build_tab_settings(self):
        tab = self.tab_set

        canvas = tk.Canvas(tab, bg=T.BG3, highlightthickness=0)
        sb = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=canvas.yview, style="W.Vertical.TScrollbar")
        inner = tk.Frame(canvas, bg=T.BG3)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6, pady=6)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        def _on_mousewheel(event):
            try:
                if self.nb.index(self.nb.select()) == 3: # 3 is the settings tab
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except: pass

        self.root.bind("<MouseWheel>", _on_mousewheel, add="+")

        row = [0]  # mutable counter

        def section(title):
            tk.Label(inner, text=title, font=("Segoe UI Semibold", 12),
                     fg=T.ACCENT, bg=T.BG3, anchor=tk.W).grid(
                row=row[0], column=0, columnspan=3, sticky=tk.W, padx=8, pady=(12, 4))
            row[0] += 1

        def field(label, var, width=38):
            tk.Label(inner, text=label, font=("Segoe UI", 10), fg=T.FG,
                     bg=T.BG3).grid(row=row[0], column=0, sticky=tk.W, padx=8, pady=3)
            tk.Entry(inner, textvariable=var, font=("Segoe UI", 10), bg=T.INPUT_BG,
                     fg=T.FG, insertbackground=T.ACCENT, width=width, relief=tk.FLAT,
                     highlightthickness=1, highlightbackground=T.BORDER).grid(
                row=row[0], column=1, sticky=tk.W, padx=4, pady=3)
            row[0] += 1

        def field_btn(label, var, cmd, btn_text="...", width=38):
            field(label, var, width)
            row[0] -= 1
            self._btn(inner, btn_text, cmd, bg=T.BG2, font_size=9).grid(
                row=row[0], column=2, padx=4, pady=3)
            row[0] += 1

        def checkbox(label, var):
            tk.Checkbutton(inner, text=label, variable=var, font=("Segoe UI", 10),
                           bg=T.BG3, fg=T.FG, activebackground=T.BG3,
                           selectcolor=T.INPUT_BG).grid(
                row=row[0], column=0, columnspan=2, sticky=tk.W, padx=8, pady=3)
            row[0] += 1

        # ── Принтеры ──
        section("Принтеры")
        self.hp_var = tk.StringVar(value=self.config["printers"]["laser"]["name"])
        field_btn("Лазерный (A4/A3):", self.hp_var, lambda: self._pick_printer("laser"), "Выбрать")
        self.cn_var = tk.StringVar(value=self.config["printers"]["plotter"]["name"])
        field_btn("Плоттер (A2+):", self.cn_var, lambda: self._pick_printer("plotter"), "Выбрать")

        # ── SumatraPDF ──
        section("SumatraPDF")
        self.sum_var = tk.StringVar(value=self.config.get("sumatra_path", ""))
        field_btn("Путь:", self.sum_var, self._browse_sumatra, "Обзор")
        self.sum_st = tk.StringVar()
        self._update_sumatra_st()
        tk.Label(inner, textvariable=self.sum_st, font=("Segoe UI", 9),
                 fg=T.FG3, bg=T.BG3).grid(row=row[0], column=1, sticky=tk.W, padx=4)
        row[0] += 1

        # ── Печать ──
        section("Печать")
        self.global_scale_var = tk.StringVar(value=str(self.config.get("global_scale", "99.94")))
        field("Масштаб по умолчанию (%):", self.global_scale_var)

        # ── Обновления ──
        section("Обновления")
        self.repo_var = tk.StringVar(value=self.config.get("update_repo", ""))
        field("GitHub репозиторий:", self.repo_var)
        self.upd_check_var = tk.BooleanVar(value=self.config.get("check_updates", True))
        checkbox("Проверять обновления при запуске", self.upd_check_var)
        self.beta_var = tk.BooleanVar(value=self.config.get("beta_channel", False))
        checkbox("Участвовать в бета-тестировании", self.beta_var)

        # ── Обратная связь ──
        section("Обратная связь (Telegram)")
        self.tg_token_var = tk.StringVar(value=self.config.get("tg_bot_token", ""))
        field("Bot Token:", self.tg_token_var)
        self.tg_chat_var = tk.StringVar(value=self.config.get("tg_chat_id", ""))
        field("Chat ID:", self.tg_chat_var)

        section("Обратная связь (VK)")
        self.vk_token_var = tk.StringVar(value=self.config.get("vk_token", ""))
        field("Access Token:", self.vk_token_var)
        self.vk_peer_var = tk.StringVar(value=self.config.get("vk_peer_id", ""))
        field("Peer ID:", self.vk_peer_var)

        # ── Интерфейс ──
        section("Интерфейс")
        self.theme_var = tk.StringVar(
            value="Светлая" if self.config.get("theme", "dark") == "light" else "Тёмная")
        tk.Label(inner, text="Тема:", font=("Segoe UI", 10), fg=T.FG,
                 bg=T.BG3).grid(row=row[0], column=0, sticky=tk.W, padx=8, pady=3)
        theme_menu = tk.OptionMenu(inner, self.theme_var, "Тёмная", "Светлая")
        theme_menu.config(font=("Segoe UI", 10), bg=T.INPUT_BG, fg=T.FG,
                         activebackground=T.BG2, highlightthickness=0)
        theme_menu.grid(row=row[0], column=1, sticky=tk.W, padx=4, pady=3)
        row[0] += 1
        tk.Label(inner, text="Перезапустите приложение после смены темы", font=("Segoe UI", 8),
                 fg=T.FG3, bg=T.BG3).grid(row=row[0], column=0, columnspan=3, sticky=tk.W, padx=8)
        row[0] += 1

        # ── Разработка ──
        section("Разработка и тестирование")
        self.dev_var = tk.BooleanVar(value=self.config.get("dev_mode", False))
        
        # Делаем режим разработчика более заметным
        dev_frame = tk.Frame(inner, bg=T.BG3, bd=2, relief=tk.GROOVE)
        dev_frame.grid(row=row[0], column=0, columnspan=2, sticky=tk.EW, padx=8, pady=5)
        
        dev_cb = tk.Checkbutton(dev_frame, text="✅ ТЕСТОВЫЙ РЕЖИМ (Имитация печати без бумаги)",
                                variable=self.dev_var, font=("Segoe UI Semibold", 11),
                                bg=T.BG3, fg=T.ACCENT_L, selectcolor=T.BG2,
                                activebackground=T.BG3, activeforeground=T.ACCENT_L)
        dev_cb.pack(anchor=tk.W, padx=10, pady=10)
        row[0] += 1

        # ── БД ──
        section("База данных")
        self.db_var = tk.StringVar(value=self.config.get("db_path", ""))
        tk.Label(inner, text=f"Путь: {self.config.get('db_path','')}", font=("Segoe UI", 9),
                 fg=T.FG3, bg=T.BG3).grid(row=row[0], column=0, columnspan=3, sticky=tk.W, padx=8, pady=3)
        row[0] += 1

        # ── Системные принтеры ──
        section("Принтеры в системе")
        self.sys_pr = tk.Text(inner, font=("Consolas", 10), bg=T.BG, fg=T.FG,
                               height=5, width=50, relief=tk.FLAT, highlightthickness=1,
                               highlightbackground=T.BORDER, state=tk.DISABLED)
        self.sys_pr.grid(row=row[0], column=0, columnspan=3, padx=8, pady=4, sticky=tk.W)
        row[0] += 1
        self._btn(inner, "Обновить список", self._refresh_sys_printers, bg=T.BG2, font_size=9).grid(
            row=row[0], column=0, sticky=tk.W, padx=8, pady=4)
        row[0] += 1

        # Сохранить
        self._btn(inner, "Сохранить настройки", self._save_settings, bg=T.OK, font_size=11).grid(
            row=row[0], column=0, columnspan=3, sticky=tk.W, padx=8, pady=12)

        self.root.after(500, self._refresh_sys_printers)

    # ─── Вкладка «Логи» (DEV) ────────────────────────────────

    def _build_tab_logs(self):
        tab = self.tab_log

        hd = tk.Frame(tab, bg=T.BG3, pady=4)
        hd.pack(fill=tk.X, padx=6)
        tk.Label(hd, text="Журнал событий (DEV)", font=("Segoe UI Semibold", 12),
                 fg=T.ACCENT, bg=T.BG3).pack(side=tk.LEFT, padx=4)
        self._btn(hd, "Очистить", self._clear_dev_log, bg=T.ERR, font_size=9).pack(side=tk.RIGHT, padx=4)
        self._btn(hd, "Обновить", self._refresh_dev_log, bg=T.BG2, font_size=9).pack(side=tk.RIGHT, padx=4)

        self.log_text = scrolledtext.ScrolledText(tab, font=("Consolas", 9),
                                                   bg=T.BG, fg="#aaddaa", relief=tk.FLAT,
                                                   highlightthickness=0, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

    # ═══════════════════════════════════════════════════════════
    #  ЛОГИКА
    # ═══════════════════════════════════════════════════════════

    def _browse_folder(self):
        f = filedialog.askdirectory(title="Выберите папку с PDF",
                                    initialdir=self.current_folder or os.path.expanduser("~"))
        if f:
            self.folder_var.set(f)
            self._load_folder()

    def _load_scan_cache(self):
        self.scan_cache = {}
        if os.path.exists(self.scan_cache_path):
            try:
                import json
                with open(self.scan_cache_path, "r", encoding="utf-8") as f:
                    self.scan_cache = json.load(f)
            except: pass

    def _save_scan_cache(self):
        try:
            import json
            with open(self.scan_cache_path, "w", encoding="utf-8") as f:
                json.dump(self.scan_cache, f, ensure_ascii=False)
        except Exception as e:
            log.error(f"Error saving scan cache: {e}")

    def _load_folder(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            from tkinter import messagebox
            messagebox.showwarning("Ошибка", "Папка не найдена.")
            return

        self.current_folder = folder
        self.config["last_folder"] = folder
        self.config["include_subfolders"] = self.subfolder_var.get()
        save_config(self.config, self.config_path)

        for i in self.file_tree.get_children():
            self.file_tree.delete(i)
        self._clear_result()

        self.pdf_files, self.files_analysis = [], {}
        self.status_var.set("Поиск PDF...")
        self.root.update_idletasks()

        if self.subfolder_var.get():
            for rd, _, fs in os.walk(folder):
                for fn in sorted(fs):
                    if fn.lower().endswith(".pdf"):
                        self._add_pdf(fn, os.path.join(rd, fn))
        else:
            for fn in sorted(os.listdir(folder)):
                if fn.lower().endswith(".pdf"):
                    self._add_pdf(fn, os.path.join(folder, fn))

        if not self.pdf_files:
            self.status_var.set("Нет PDF файлов")
            return

        self.printed_pages = self.history.get_all_printed(folder)

        import tkinter as tk
        for fn, fp, pc in self.pdf_files:
            pr = self.printed_pages.get(fp, {})
            pcs = str(pc) if pc > 0 else "?"
            if pc > 0 and len(pr) >= pc:
                st, tag = "✓", "printed"
            elif pr:
                st, tag = f"{len(pr)}/{pc}", "partial"
            else:
                st, tag = "—", "none"
            self.file_tree.insert("", tk.END, text=f"  {fn}", values=(pcs, st), tags=(tag,))

        self.status_var.set("Анализ форматов...")
        self.root.update_idletasks()
        
        self.current_plan = build_print_plan(self.files_analysis, self.printed_pages)
        self._update_print_tab()

        import threading
        threading.Thread(target=self._scan_folder_worker, args=(self.pdf_files, folder), daemon=True).start()

    def _scan_folder_worker(self, pdfs, folder):
        total = len(pdfs)
        scanned = 0
        changed = False
        
        for idx, (fn, fp, pc) in enumerate(pdfs):
            if pc <= 0: continue
            
            try:
                mtime = os.path.getmtime(fp)
                size = os.path.getsize(fp)
                cache_key = f"{mtime}_{size}"
                
                info = None
                if fp in self.scan_cache and self.scan_cache[fp].get("key") == cache_key:
                    info = self.scan_cache[fp].get("info")
                    
                if not info:
                    info = analyze_pdf(fp)
                    self.scan_cache[fp] = {"key": cache_key, "info": info}
                    changed = True
                    
                self.files_analysis[fp] = info
                
            except Exception as e:
                log.error(f"Ошибка анализа {fn}: {e}")
                
            scanned += 1
            if scanned % 3 == 0 or scanned == total:
                self.root.after(0, lambda s=scanned, t=total: self.status_var.set(f"Анализ... {s}/{t}"))

        if changed:
            self._save_scan_cache()
            
        self.root.after(0, lambda: self._on_scan_complete(folder))

    def _on_scan_complete(self, folder):
        if self.current_folder != folder: return
        self.status_var.set("Готово")
        self.current_plan = build_print_plan(self.files_analysis, self.printed_pages)
        if hasattr(self, 'copies_var'):
            try: tc = self.copies_var.get()
            except: tc = 1
            self.current_plan = build_print_plan(self.files_analysis, self.printed_pages, tc)
        self._update_print_tab()
        self._update_folder_stats()

    def _update_folder_stats(self):
        s = self.current_plan["stats"]
        parts = []
        if s["laser_total"]:
            parts.append(f"HP: {s['laser_total']}")
        if s["plotter_total"]:
            parts.append(f"Canon: {s['plotter_total']}")

        try:
            target_copies = self.copies_var.get()
            if target_copies < 1: target_copies = 1
        except:
            target_copies = 1

        printed_files_cnt = 0
        printed_pages_cnt = 0
        total_pages = 0
        for fn, fp, pc in self.pdf_files:
            if pc > 0:
                total_pages += pc
            done_dict = self.printed_pages.get(fp, {})
            # Sum up all printed copies for this file, capped at target_copies per page
            file_printed_copies = 0
            for pn in range(1, pc + 1):
                c = done_dict.get(pn, 0)
                file_printed_copies += min(c, target_copies)
            
            printed_pages_cnt += file_printed_copies
            if pc > 0 and file_printed_copies >= pc * target_copies:
                printed_files_cnt += 1

        total_target_pages = total_pages * target_copies
        left_files = len(self.pdf_files) - printed_files_cnt
        left_pages = total_target_pages - printed_pages_cnt

        self.summary_var.set(
            f"Файлов: {len(self.pdf_files)} (готово: {printed_files_cnt}, осталось: {left_files})\n"
            f"Страниц (цель {target_copies}x): {total_target_pages} (готово: {printed_pages_cnt}, осталось: {left_pages})\n" +
            ("В очередь: " + "  ".join(parts) if parts else "Всё напечатано")
        )
        folder = self.current_folder or "Нет папки"
        self.status_var.set(f"{folder}  |  Осталось: {left_files} файлов ({left_pages} стр.)")

    def _on_copies_changed(self, *args):
        try:
            val = self.copies_var.get()
            self.config["last_copies"] = val
            self._save_settings()
            if val < 1: return
        except Exception:
            return
        if not hasattr(self, 'files_analysis'): return
        if not hasattr(self, 'hp_plan'): return
        
        # Перестраиваем план с новым кол-вом копий
        self.current_plan = build_print_plan(self.files_analysis, self.printed_pages, val)
        self._update_print_tab()
        self._update_folder_stats()
        
        # Обновляем детали выбранного файла
        sel = self.file_tree.selection()
        if sel:
            self._on_file_select()

    def _add_pdf(self, fn, fp):
        try:
            doc = fitz.open(fp)
            pc = doc.page_count
            doc.close()
            self.pdf_files.append((fn, fp, pc))
        except Exception:
            self.pdf_files.append((fn, fp, -1))

    def _on_file_select(self, _=None):
        sel = self.file_tree.selection()
        if not sel:
            return
        idx = self.file_tree.index(sel[0])
        if idx >= len(self.pdf_files):
            return
        fn, fp, pc = self.pdf_files[idx]
        if pc < 0:
            return

        info = self.files_analysis.get(fp)
        if not info:
            try:
                import os
                mtime = os.path.getmtime(fp)
                size = os.path.getsize(fp)
                cache_key = f"{mtime}_{size}"
                
                if hasattr(self, 'scan_cache') and fp in self.scan_cache and self.scan_cache[fp].get("key") == cache_key:
                    info = self.scan_cache[fp].get("info")
                
                if not info:
                    info = analyze_pdf(fp)
                    if hasattr(self, 'scan_cache'):
                        self.scan_cache[fp] = {"key": cache_key, "info": info}
                        self._save_scan_cache()
                        
                self.files_analysis[fp] = info
            except Exception as e:
                self.result_title.config(text=fn)
                self.result_text.config(state=tk.NORMAL)
                self.result_text.delete("1.0", tk.END)
                self.result_text.insert(tk.END, "Ошибка анализа файла", "warn")
                self.result_text.config(state=tk.DISABLED)
                for i in self.detail_tree.get_children():
                    self.detail_tree.delete(i)
                return

        gr = group_pages_by_format(info)
        pr = self.printed_pages.get(fp, {})
        self._display_result(fn, fp, info, gr, pr)

    def _display_result(self, fn, fp, info, grouped, printed):
        self._current_selected_fp = fp
        self._current_selected_fn = fn
        self.result_title.config(text=fn)
        self.result_text.config(state=tk.NORMAL)
        self.result_text.delete("1.0", tk.END)
        self.result_text_map = {}

        line_idx = 1
        for fmt, (ps, pl) in grouped.items():
            self.result_text_map[line_idx] = (fmt, ps, pl)
            tag = "hp" if is_laser(fmt) else "cn"
            label = "HP" if is_laser(fmt) else "Canon"
            self.result_text.insert(tk.END, f"[{label}] ", tag)
            self.result_text.insert(tk.END, f"{fmt}", "fmt")
            self.result_text.insert(tk.END, f"  {ps}", "pg")

            done = [p for p in pl if p in printed]
            if len(done) == len(pl) and done:
                self.result_text.insert(tk.END, "  done", "ok")
            elif done:
                self.result_text.insert(tk.END, f"  {len(done)}/{len(pl)}", "dim")
            self.result_text.insert(tk.END, "\n")
            line_idx += 1

        self.result_text.config(state=tk.DISABLED)

        for i in self.detail_tree.get_children():
            self.detail_tree.delete(i)

        try:
            target_copies = self.copies_var.get()
        except:
            target_copies = 1

        for info_tuple in info:
            if len(info_tuple) == 6:
                pn, fmt, w, h, land, ocr_fmt = info_tuple
            else:
                pn, fmt, w, h, land = info_tuple
                ocr_fmt = None
                
            o = "Альб." if land else "Книж."
            dest = "HP" if is_laser(fmt) else "Canon"
            
            disp_fmt = fmt
            tag = "hp" if is_laser(fmt) else "cn"
            
            if ocr_fmt and ocr_fmt != fmt:
                disp_fmt = f"{fmt} (в штампе: {ocr_fmt})"
                tag = "warn"
                
            printed_copies = printed.get(pn, 0)
            if printed_copies >= target_copies:
                tag = "done"
                
            self.detail_tree.insert("", tk.END, text=str(pn),
                                     values=(disp_fmt, f"{w:.0f}", f"{h:.0f}", o, dest, f"{printed_copies}/{target_copies}"), tags=(tag,))

        self.copy_btn.config(state=tk.NORMAL)
        self.print_one_btn.config(state=tk.NORMAL)
        self.export_btn.config(state=tk.NORMAL)
        self.preview_btn.config(state=tk.DISABLED)
        self._preview_cache = {}
        self._preview_current = None
        self.preview_label.config(image="", text="Нажмите на страницу")
        self.preview_info.config(text="")

    def _clear_result(self):
        self.result_title.config(text="Выберите файл")
        self.result_text.config(state=tk.NORMAL)
        self.result_text.delete("1.0", tk.END)
        self.result_text.config(state=tk.DISABLED)
        for i in self.detail_tree.get_children():
            self.detail_tree.delete(i)
        self.copy_btn.config(state=tk.DISABLED)
        self.print_one_btn.config(state=tk.DISABLED)
        self.export_btn.config(state=tk.DISABLED)
        self.preview_btn.config(state=tk.DISABLED)
        self._preview_current = None
        self.preview_label.config(image="", text="Нажмите на страницу")
        self.preview_info.config(text="")

    def _show_format_menu(self, event):
        item = self.detail_tree.identify_row(event.y)
        if not item:
            return
        self.detail_tree.selection_set(item)
        pn = int(self.detail_tree.item(item, "text"))
        
        sel = self.file_tree.selection()
        if not sel: return
        fp = self.pdf_files[self.file_tree.index(sel[0])][1]
        
        menu = tk.Menu(self.root, tearoff=0, bg=T.BG3, fg=T.FG, activebackground=T.BG2)
        
        menu.add_command(label="Печать этой страницы", 
                         command=lambda f=fp, p=pn: self._print_single_page(f, p))
        menu.add_command(label="Указать кол-во напечатанных копий...", 
                         command=lambda f=fp, p=pn: self._set_printed_copies(f, p))
        menu.add_command(label="Снять отметку (0 копий)", 
                         command=lambda f=fp, p=pn: self._clear_printed_copies(f, p))
        menu.add_separator()
        
        for fmt in ["A4", "A3", "A2", "A1", "A0"]:
            menu.add_command(label=f"Установить формат {fmt}", 
                             command=lambda f=fmt, p=pn: self._change_page_format(p, f))
        menu.post(event.x_root, event.y_root)

    def _print_single_page(self, fp, pn):
        if not self.engine.is_available:
            messagebox.showerror("Ошибка", "SumatraPDF не найден.")
            return
            
        info = self.files_analysis.get(fp)
        if not info: return
        
        page_info = next((i for i in info if i[0] == pn), None)
        if not page_info: return
        
        fmt = page_info[1]
        land = page_info[4] if len(page_info) >= 5 else True
        
        try:
            target = self.copies_var.get()
        except:
            target = 1
            
        # Запрашиваем количество копий для печати
        from tkinter import simpledialog
        copies = simpledialog.askinteger("Печать страницы", f"Сколько копий страницы {pn} ({fmt}) напечатать?", 
                                         initialvalue=target, minvalue=1, parent=self.root)
        if not copies: return
        
        # Определяем принтер и настройки
        if is_laser(fmt):
            pr = self.config["printers"]["laser"]["name"]
            st = self.engine.laser_settings(fmt)
        else:
            pr = self.config["printers"]["plotter"]["name"]
            st = self.engine.plotter_settings(fmt, land)
            
        confirm = messagebox.askyesno("Подтверждение", 
            f"Отправить страницу {pn} на принтер?\n\n"
            f"Принтер: {pr}\n"
            f"Формат: {fmt}\n"
            f"Копии: {copies}\n"
            f"Настройки:\n{st}")
            
        if not confirm: return
        
        r = self.engine.print_pages(fp, pr, [pn], st, copies=copies)
        if r["success"]:
            try: fh = file_hash(fp)
            except: fh = ""
            self.history.record(fp, pr, [pn], fmt, "success", fh, copies=copies)
            self.printed_pages = self.history.get_all_printed(self.current_folder or "")
            self._on_copies_changed() # Rebuild plan and update UI
            messagebox.showinfo("Готово", f"Страница {pn} успешно отправлена на печать.")
        else:
            messagebox.showerror("Ошибка", r["message"])

    def _set_printed_copies(self, fp, pn):
        from tkinter import simpledialog
        done_dict = self.printed_pages.get(fp, {})
        current = done_dict.get(pn, 0)
        copies = simpledialog.askinteger("Копии", f"Сколько копий страницы {pn} уже напечатано?", 
                                         initialvalue=current, minvalue=0, parent=self.root)
        if copies is not None:
            self._update_copies_in_db(fp, pn, copies)
            
    def _clear_printed_copies(self, fp, pn):
        self._update_copies_in_db(fp, pn, 0)
        
    def _update_copies_in_db(self, fp, pn, copies):
        try: fh = file_hash(fp)
        except: fh = ""
        info = self.files_analysis.get(fp)
        fmt = ""
        if info:
            page_info = next((i for i in info if i[0] == pn), None)
            if page_info: fmt = page_info[1]
            
        self.history.set_printed(fp, [pn], copies, fmt, fh)
        self.printed_pages = self.history.get_all_printed(self.current_folder or "")
        self._on_copies_changed()
        
    def _change_page_format(self, pn, new_fmt):
        sel = self.file_tree.selection()
        if not sel: return
        fp = self.pdf_files[self.file_tree.index(sel[0])][1]
        
        info = self.files_analysis.get(fp)
        if not info: return
        
        for i, info_tuple in enumerate(info):
            if info_tuple[0] == pn:
                # Меняем формат, сохраняя остальные данные
                if len(info_tuple) == 6:
                    _, old_fmt, w, h, land, ocr_fmt = info_tuple
                    info[i] = (pn, new_fmt, w, h, land, ocr_fmt)
                else:
                    _, old_fmt, w, h, land = info_tuple
                    info[i] = (pn, new_fmt, w, h, land)
                break
                
        self.files_analysis[fp] = info
        self.log(f"Изменён формат страницы {pn} с {old_fmt} на {new_fmt}.")
        self._on_file_select()

    def _show_file_menu(self, event):
        item = self.file_tree.identify_row(event.y)
        if not item:
            return
        self.file_tree.selection_set(item)
        
        idx = self.file_tree.index(item)
        fn, fp, pc = self.pdf_files[idx]
        
        menu = tk.Menu(self.root, tearoff=0, bg=T.BG3, fg=T.FG, activebackground=T.BG2)
        menu.add_command(label="Открыть файл...", command=lambda: os.startfile(fp))
        menu.add_separator()
        menu.add_command(label="Отметить как напечатанный", command=lambda: self._mark_file_printed(item, True))
        menu.add_command(label="Снять отметку (не напечатан)", command=lambda: self._mark_file_printed(item, False))
        menu.post(event.x_root, event.y_root)

    def _mark_file_printed(self, item, printed):
        idx = self.file_tree.index(item)
        fn, fp, pc = self.pdf_files[idx]
        
        if printed:
            # Отмечаем все страницы как напечатанные
            info = self.files_analysis.get(fp)
            if info:
                pages = [info_tuple[0] for info_tuple in info]
                try:
                    target_copies = self.copies_var.get()
                except Exception:
                    target_copies = 1
                # Записываем в историю с пометкой ручного ввода
                self.history.record(fp, "Ручная отметка", pages, "Неизвестно", "success", "", copies=target_copies)
        else:
            # Удаляем все записи о печати этого файла
            self.history.remove_printed(fp)
            
        # Обновляем состояние
        self.printed_pages[fp] = self.history.get_printed(fp)
        
        # Перестраиваем план печати, чтобы очередь сразу обновилась
        self.current_plan = build_print_plan(self.files_analysis, self.printed_pages)
        self._update_print_tab()
        self._update_folder_stats()
        
        # Обновляем отображение в дереве
        done = self.printed_pages[fp]
        if len(done) == 0:
            self.file_tree.item(item, values=(pc, ""), tags=("none",))
        elif len(done) >= pc:
            self.file_tree.item(item, values=(pc, "✓"), tags=("printed",))
        else:
            self.file_tree.item(item, values=(f"{len(done)}/{pc}", "○"), tags=("partial",))
            
        # Обновляем детали, если файл сейчас выбран
        sel = self.file_tree.selection()
        if sel and sel[0] == item:
            self._on_file_select()

    def _copy_result(self):
        c = self.result_text.get("1.0", tk.END).strip()
        if c:
            self.root.clipboard_clear()
            self.root.clipboard_append(c)
            self.copy_lbl.config(text="Скопировано")
            self.root.after(2000, lambda: self.copy_lbl.config(text=""))

    def _show_result_menu(self, event):
        if not hasattr(self, 'result_text_map') or not self.result_text_map:
            return

        index = self.result_text.index(f"@{event.x},{event.y}")
        line_idx = int(index.split('.')[0])

        if line_idx not in self.result_text_map:
            return

        fmt, ps, pl = self.result_text_map[line_idx]

        menu = tk.Menu(self.root, tearoff=0, bg=T.BG2, fg=T.FG, activebackground=T.ACCENT)
        menu.add_command(label=f"Напечатать {fmt} ({len(pl)} стр.)",
                         command=lambda: self._print_result_group(fmt, pl))
        try: target_copies = self.copies_var.get()
        except: target_copies = 1
        menu.add_command(label=f"Отметить {fmt} как напечатанный (x{target_copies})",
                         command=lambda: self._mark_result_group_printed(pl, target_copies))
        menu.add_command(label=f"Сбросить статус (Ненапечатанный)",
                         command=lambda: self._clear_result_group_printed(pl))
        menu.post(event.x_root, event.y_root)

    def _mark_result_group_printed(self, pages_list, copies=1):
        fp = self._current_selected_fp
        info = self.files_analysis.get(fp)
        if not info: return
        for p in pages_list:
            fmt_for_p = next((x[1] for x in info if x[0] == p), "Неизвестно")
            self.history.record(fp, "Ручная отметка", [p], fmt_for_p, "success", "", copies=copies)
        self.printed_pages[fp] = self.history.get_printed(fp)
        self.current_plan = build_print_plan(self.files_analysis, self.printed_pages)
        self._update_print_tab()
        self._update_folder_stats()
        self._on_file_select()

    def _clear_result_group_printed(self, pages_list):
        fp = self._current_selected_fp
        for pn in pages_list:
            self._clear_printed_copies(fp, pn)
        self.printed_pages[fp] = self.history.get_printed(fp)
        self.current_plan = build_print_plan(self.files_analysis, self.printed_pages)
        self._update_print_tab()
        self._update_folder_stats()
        self._on_file_select()

    def _print_result_group(self, fmt, pages_list):
        from tkinter.simpledialog import askinteger
        copies = askinteger("Печать", f"Количество копий для формата {fmt}:", 
                            initialvalue=1, minvalue=1, maxvalue=50, parent=self.root)
        if copies is None:
            return

        fp = self._current_selected_fp
        fn = self._current_selected_fn
        info = self.files_analysis.get(fp)
        if not info: return

        plan = {"laser": [], "plotter": [], "stats": {}}
        plan["custom_scale"] = self.config.get("global_scale", 99.94)
        lands = [p[4] for p in info if p[0] in pages_list]
        mostly_land = sum(lands) > len(lands) / 2 if lands else True
        
        entry = (fp, fn, pages_list, {
            "format": fmt, "pages_str": compress_pages(pages_list),
            "count": len(pages_list), "landscape": mostly_land,
            "missing": copies
        })
        if is_laser(fmt):
            plan["laser"].append(entry)
        else:
            plan["plotter"].append(entry)
                
        plan["stats"] = {
            "laser_total": len(pages_list) * copies if is_laser(fmt) else 0,
            "plotter_total": len(pages_list) * copies if not is_laser(fmt) else 0,
            "grand_total": len(pages_list) * copies
        }
        
        if is_laser(fmt):
            plan["stats"]["laser_by_fmt"] = {fmt: len(pages_list) * copies}
            plan["stats"]["plotter_by_fmt"] = {}
        else:
            plan["stats"]["plotter_by_fmt"] = {fmt: len(pages_list) * copies}
            plan["stats"]["laser_by_fmt"] = {}

        self.current_plan = plan
        if self._confirm_dialog():
            # self.monitor.start() # Disabled to prevent win32print GIL block lag
            self._print_running, self._print_cancel = True, False
            self.go_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.NORMAL)
            self.test_btn.config(state=tk.DISABLED)
            threading.Thread(target=self._print_worker, daemon=True).start()

    # ─── Печать ───────────────────────────────────────────────

    def _update_print_tab(self):
        if not self.current_plan:
            return
        plan = self.current_plan

        for widget, items, total_key in [
            (self.hp_plan, plan["laser"], "laser_total"),
            (self.cn_plan, plan["plotter"], "plotter_total"),
        ]:
            widget.config(state=tk.NORMAL)
            widget.delete("1.0", tk.END)
            if items:
                for fp, fn, pgs, inf in items:
                    o = "альб." if inf.get("landscape") else "книж."
                    missing_str = f" x{inf['missing']}" if inf.get("missing", 1) > 1 else ""
                    widget.insert(tk.END, f"  {fn}: стр. {inf['pages_str']} ({inf['format']}, {o}) — {inf['count']} л.{missing_str}\n")
                widget.insert(tk.END, f"\n  Итого: {plan['stats'][total_key]} лист(ов)\n")
            else:
                widget.insert(tk.END, "  Нет заданий\n")
            widget.config(state=tk.DISABLED)

        has = plan["stats"]["grand_total"] > 0
        self.go_btn.config(state=tk.NORMAL if has else tk.DISABLED)
        self.test_btn.config(state=tk.NORMAL if has else tk.DISABLED)

    def _print_selected_file(self):
        sel = self.file_tree.selection()
        if not sel:
            return
        idx = self.file_tree.index(sel[0])
        fn, fp, pc = self.pdf_files[idx]
        if fp not in self.files_analysis:
            return
            
        copies = self.copies_var.get() if hasattr(self, 'copies_var') else 1
        d = PrintPreviewDialog(self.root, fp, fn, T, copies)
        self.root.wait_window(d.top)
        
        if d.result:
            self.current_plan = build_print_plan({fp: self.files_analysis[fp]},
                                                  {fp: self.printed_pages.get(fp, {})},
                                                  target_copies=d.result["copies"])
            self.current_plan["custom_scale"] = d.result["scale"]
            self._update_print_tab()
            self._start_print(skip_confirm=True)

    def _confirm_dialog(self):
        if not self.current_plan:
            return False
        s = self.current_plan["stats"]
        copies = self.copies_var.get() if hasattr(self, 'copies_var') else 1
        copies_txt = f"\n\nКопий: {copies}" if copies > 1 else ""
        msg = f"Отправить на печать?\n\n"
        if s["laser_total"]:
            lf = ", ".join(f"{f}:{c}" for f, c in s["laser_by_fmt"].items())
            msg += f"HP: {s['laser_total']} л. ({lf})\n"
        if s["plotter_total"]:
            pf = ", ".join(f"{f}:{c}" for f, c in s["plotter_by_fmt"].items())
            msg += f"Canon: {s['plotter_total']} л. ({pf})\n"
        msg += f"\nВсего: {s['grand_total']} лист(ов){copies_txt}"
        if self.dev_mode:
            msg += "\n\n[DEV MODE — печать будет имитирована]"
        return messagebox.askokcancel("Подтверждение", msg)

    def _set_file_printed_copies(self, fp):
        from tkinter import simpledialog
        try: target = self.copies_var.get()
        except: target = 1
        done_dict = self.printed_pages.get(fp, {})
        current = max(done_dict.values()) if done_dict else 0
        copies = simpledialog.askinteger("Указать копии", f"Сколько копий файла уже напечатано?", 
                                         initialvalue=current, minvalue=0, parent=self.root)
        if copies is not None:
            if fp in self.files_analysis:
                info = self.files_analysis[fp]
                pages = [t[0] for t in info]
                try: fh = file_hash(fp)
                except: fh = ""
                self.history.set_printed(fp, pages, copies, "Manual", fh)
                self.printed_pages[fp] = self.history.get_printed(fp)
                try: tc = self.copies_var.get()
                except: tc = 1
                self.current_plan = build_print_plan(self.files_analysis, self.printed_pages, tc)
                self._update_print_tab()
                self._update_folder_stats()
                
                # Update UI tree
                sel = self.file_tree.selection()
                if sel:
                    item = sel[0]
                    done = self.printed_pages[fp]
                    pc = len(info)
                    if len(done) == 0:
                        self.file_tree.item(item, values=(pc, ""), tags=("none",))
                    elif len(done) >= pc:
                        self.file_tree.item(item, values=(pc, "✓"), tags=("printed",))
                    else:
                        self.file_tree.item(item, values=(f"{len(done)}/{pc}", "✓"), tags=("partial",))

    def _test_print(self):
        if not self.current_plan:
            return
        if not self.engine.is_available:
            messagebox.showerror("Ошибка", "SumatraPDF не найден. Проверьте настройки.")
            return

        items = []
        if self.current_plan["laser"]:
            items.append(("laser", self.current_plan["laser"][0]))
        if self.current_plan["plotter"]:
            items.append(("plotter", self.current_plan["plotter"][0]))
        if not items:
            return

        sel = 0
        if len(items) > 1:
            a = messagebox.askquestion("Пробная печать",
                f"1) {self.config['printers']['laser']['name']}\n"
                f"2) {self.config['printers']['plotter']['name']}\n\n"
                f"«Да» = первый, «Нет» = второй")
            sel = 0 if a == "yes" else 1

        pt, (fp, fn, pgs, inf) = items[sel]
        pr = self.config["printers"][pt]["name"]
        st = self.engine.laser_settings(inf["format"]) if pt == "laser" else \
             self.engine.plotter_settings(inf["format"], inf.get("landscape", True))

        r = self.engine.print_pages(fp, pr, [pgs[0]], st)
        if r["success"]:
            messagebox.showinfo("Пробная печать", f"Отправлено на {pr}\nСтраница {pgs[0]} ({inf['format']})")
        else:
            messagebox.showerror("Ошибка", r["message"])

    def _start_print(self, skip_confirm=False):
        if not self.current_plan or self._print_running:
            return
        if not self.engine.is_available:
            messagebox.showerror("Ошибка", "SumatraPDF не найден.")
            return
        if not skip_confirm and not self._confirm_dialog():
            return

        # self.monitor.start() # Disabled to prevent win32print GIL block lag
        self._print_running, self._print_cancel = True, False
        self.go_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.test_btn.config(state=tk.DISABLED)
        threading.Thread(target=self._print_worker, daemon=True).start()

    def _print_worker(self):
        plan = self.current_plan
        custom_scale = plan.get("custom_scale", None)
        tasks = []
        for fp, fn, pgs, inf in plan["laser"]:
            st = self.engine.laser_settings(inf["format"])
            tasks.append((fp, fn, pgs, inf, st, self.config["printers"]["laser"]["name"]))
        for fp, fn, pgs, inf in plan["plotter"]:
            st = self.engine.plotter_settings(inf["format"], inf.get("landscape", True))
            tasks.append((fp, fn, pgs, inf, st, self.config["printers"]["plotter"]["name"]))

        import time
        import win32print

        def get_printer_status(printer_name):
            try:
                h = win32print.OpenPrinter(printer_name)
                jobs = win32print.EnumJobs(h, 0, 99, 1)
                info = win32print.GetPrinter(h, 2)
                win32print.ClosePrinter(h)
                return len(jobs), info['Status']
            except Exception as e:
                import logging
                logging.error(f"Printer status error: {e}")
                return 0, 0

        total, done = len(tasks), 0
        for fp, fn, pgs, inf, st, pr in tasks:
            if self._print_cancel:
                break
                
            missing = inf.get("missing", 1)
            
            for copy_idx in range(missing):
                if self._print_cancel:
                    break
                    
                copies_txt = f" (Копия {copy_idx+1}/{missing})" if missing > 1 else ""
                msg = f"{fn}   {pr} ({inf['format']}, {inf['count']} л.){copies_txt}"
                self.root.after(0, lambda m=msg, d=done, t=total: self._upd_prog(d, t, m))

                if self._print_cancel:
                    break

                print_fp = fp
                if custom_scale is not None:
                    st = st.replace("shrink", "noscale").replace("fit", "noscale")
                    print_fp = safe_scale_pdf(fp, custom_scale)
                
                r = self.engine.print_pages(print_fp, pr, pgs, st, copies=1)
                
                status = "success" if r["success"] else "error"
                try: fh = file_hash(fp)
                except Exception: fh = ""
                self.history.record(fp, pr, pgs, inf["format"], status, fh, copies=1)
                
                time.sleep(1) # Cooldown

            if r["success"]:
                def _refresh(f=fp):
                    try: tc = self.copies_var.get()
                    except: tc = 1
                    self.printed_pages[f] = self.history.get_printed(f)
                    self._update_print_tab()
                self.root.after(0, _refresh)
            done += 1
            
        self.root.after(0, lambda: self._upd_prog(total, total, "Печать завершена."))
        self.root.after(1000, self._finish_print)


    def _center_window(self):
        self.root.geometry('1000x700')
        
    def _change_printer_dialog(self):
        import tkinter as tk
        from tkinter import ttk
        import win32print
        
        printers = [p[2] for p in win32print.EnumPrinters(2)]
        
        top = tk.Toplevel(self.root)
        top.title("Выбор принтеров")
        top.geometry("400x350")
        
        tk.Label(top, text="Выберите лазерный принтер (A4/A3):").pack(pady=(10,0))
        lb_laser = tk.Listbox(top, height=5)
        lb_laser.pack(fill=tk.X, padx=10, pady=5)
        
        tk.Label(top, text="Выберите плоттер (A2+):").pack(pady=(10,0))
        lb_plotter = tk.Listbox(top, height=5)
        lb_plotter.pack(fill=tk.X, padx=10, pady=5)
        
        for pr in printers:
            lb_laser.insert(tk.END, pr)
            lb_plotter.insert(tk.END, pr)
            
        try:
            curr_laser = self.config["printers"]["laser"]["name"]
            curr_plotter = self.config["printers"]["plotter"]["name"]
            if curr_laser in printers: lb_laser.selection_set(printers.index(curr_laser))
            if curr_plotter in printers: lb_plotter.selection_set(printers.index(curr_plotter))
        except: pass

        def on_save():
            sl = lb_laser.curselection()
            sp = lb_plotter.curselection()
            if sl:
                pr = lb_laser.get(sl[0])
                self.config["printers"]["laser"]["name"] = pr
                if hasattr(self, 'hp_var'): self.hp_var.set(pr)
                if hasattr(self, 'hp_lf'): self.hp_lf.config(text=f"  {pr}  ")
            if sp:
                pr = lb_plotter.get(sp[0])
                self.config["printers"]["plotter"]["name"] = pr
                if hasattr(self, 'cn_var'): self.cn_var.set(pr)
                if hasattr(self, 'cn_lf'): self.cn_lf.config(text=f"  {pr}  ")

            self._save_settings()
            try:
                val = self.copies_var.get()
            except:
                val = 1
            self.current_plan = build_print_plan(self.files_analysis, getattr(self, 'printed_pages', {}), val)
            if hasattr(self, '_update_print_tab'): self._update_print_tab()
            top.destroy()
            
        ttk.Button(top, text="Сохранить", command=on_save).pack(pady=15)

    def _save_settings(self):
        import json
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)


    def _upd_prog(self, done, total, msg):
        if not hasattr(self, 'prog_var'): return
        p = 0 if total == 0 else (done / total) * 100
        self.prog_var.set(p)
        if hasattr(self, 'prog_lbl'):
            self.prog_lbl.config(text=msg)

    def _finish_print(self):
        self._print_active = False
        if hasattr(self, 'go_btn'): self.go_btn.config(state="normal")
        if hasattr(self, 'stop_btn'): self.stop_btn.config(state="disabled")
        if hasattr(self, 'print_prog_frame'): self.print_prog_frame.pack_forget()

    def _export_analysis(self):
        import csv, tkinter.filedialog as fd
        if not hasattr(self, 'scan_results') or not self.scan_results: return
        p = fd.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if p:
            with open(p, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["File", "Format", "Pages", "Printer"])
                for fp, info in self.scan_results.items():
                    w.writerow([info["filename"], str(info["formats"]), info["page_count"], info["printer"]])

    def _export_all_analysis(self):
        self._export_analysis()

    def _set_file_printed_copies(self, fp):
        from tkinter import simpledialog
        try: target = self.copies_var.get()
        except: target = 1
        done_dict = self.printed_pages.get(fp, {})
        current = max(done_dict.values()) if done_dict else 0
        copies = simpledialog.askinteger("Указать копии", f"Сколько копий файла уже напечатано?", 
                                         initialvalue=current, minvalue=0, parent=self.root)
        if copies is not None:
            if fp in self.files_analysis:
                info = self.files_analysis[fp]
                pages = [t[0] for t in info]
                try: fh = file_hash(fp)
                except: fh = ""
                self.history.set_printed(fp, pages, copies, "Manual", fh)
                self.printed_pages[fp] = self.history.get_printed(fp)
                try: tc = self.copies_var.get()
                except: tc = 1
                self.current_plan = build_print_plan(self.files_analysis, self.printed_pages, tc)
                self._update_print_tab()
                self._update_folder_stats()
                sel = self.file_tree.selection()
                if sel:
                    item = sel[0]
                    done = self.printed_pages[fp]
                    pc = len(info)
                    if len(done) == 0:
                        self.file_tree.item(item, values=(pc, ""), tags=("none",))
                    elif len(done) >= pc:
                        self.file_tree.item(item, values=(pc, "✓"), tags=("printed",))
                    else:
                        self.file_tree.item(item, values=(f"{len(done)}/{pc}", "✓"), tags=("partial",))

    def _on_detail_page_select(self, *args, **kwargs): pass
    def _show_fullscreen_preview(self, *args, **kwargs): pass
    def _refresh_history(self, *args, **kwargs): pass
    def _export_history(self, *args, **kwargs): pass
    def _clear_history(self, *args, **kwargs): pass
    def _refresh_dev_log(self, *args, **kwargs): pass
    def _clear_dev_log(self, *args, **kwargs): pass
    def _report_problem_dialog(self, *args, **kwargs): pass
    def _pick_printer(self, ptype):
        import tkinter as tk
        import win32print
        printers = [p[2] for p in win32print.EnumPrinters(2)]
        top = tk.Toplevel(self.root)
        top.title("Выбор принтера")
        top.geometry("400x300")
        tk.Label(top, text=f"Выберите {'лазерный принтер' if ptype == 'laser' else 'плоттер'}:").pack(pady=10)
        lb = tk.Listbox(top, height=10)
        lb.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        for pr in printers:
            lb.insert(tk.END, pr)
        try:
            curr = self.config["printers"][ptype]["name"]
            if curr in printers:
                lb.selection_set(printers.index(curr))
        except:
            pass
        def on_save():
            s = lb.curselection()
            if s:
                pr = lb.get(s[0])
                self.config["printers"][ptype]["name"] = pr
                if ptype == "laser":
                    if hasattr(self, 'hp_var'): self.hp_var.set(pr)
                    if hasattr(self, 'hp_lf'): self.hp_lf.config(text=f"  {pr}  ")
                else:
                    if hasattr(self, 'cn_var'): self.cn_var.set(pr)
                    if hasattr(self, 'cn_lf'): self.cn_lf.config(text=f"  {pr}  ")
                self._save_settings()
                try:
                    val = self.copies_var.get()
                except:
                    val = 1
                self.current_plan = build_print_plan(self.files_analysis, getattr(self, 'printed_pages', {}), val)
                if hasattr(self, '_update_print_tab'): self._update_print_tab()
            top.destroy()
        self._btn(top, "Сохранить", on_save, bg="#264b3a").pack(pady=10)
    def _update_sumatra_st(self, *args, **kwargs): pass
    def _refresh_sys_printers(self, *args, **kwargs): pass
    def _setup_hotkeys(self, *args, **kwargs): pass
    def _setup_dnd(self, *args, **kwargs): pass
    def _browse_sumatra(self, *args, **kwargs): pass
    def _check_updates_async(self, *args, **kwargs): pass


    def _cancel_print(self):
        self._print_cancel = True
        if hasattr(self, 'stop_btn'): self.stop_btn.config(state="disabled")

    def run(self):
        self.root.mainloop()

if __name__ == '__main__':
    app = PrintManagerApp()
    app.run()
