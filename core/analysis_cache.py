"""Кэш результатов анализа PDF.

Анализ тяжёлых чертежей занимает секунды на страницу. Если файл не менялся
(тот же размер и время изменения), повторно его не разбираем — берём из кэша.
Кэш лежит в %APPDATA%\\WellEnergyPrintManager\\analysis_cache.db.
"""
import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager

# Увеличьте, если поменялась логика analyze_pdf — старый кэш станет недействительным.
ANALYSIS_VERSION = 3   # 3: формат без « [V]», 8-е поле — число векторных команд

log = logging.getLogger(__name__)


def default_cache_path():
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    folder = os.path.join(appdata, "WellEnergyPrintManager")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "analysis_cache.db")


class AnalysisCache:
    def __init__(self, path=None):
        self.path = path or default_cache_path()
        self._lock = threading.Lock()
        try:
            with self._connect() as conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS cache (
                    path TEXT PRIMARY KEY, size INTEGER, mtime REAL,
                    version INTEGER, data TEXT)""")
        except Exception as e:
            log.warning(f"Кэш анализа недоступен: {e}")
            self.path = None

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            with conn:
                yield conn
        finally:
            conn.close()  # иначе на Windows файл кэша остаётся занятым

    @staticmethod
    def _key(filepath):
        st = os.stat(filepath)
        return st.st_size, round(st.st_mtime, 3)

    def get(self, filepath):
        if not self.path:
            return None
        try:
            size, mtime = self._key(filepath)
            with self._lock, self._connect() as conn:
                row = conn.execute(
                    "SELECT data FROM cache WHERE path=? AND size=? AND mtime=? AND version=?",
                    (os.path.normcase(filepath), size, mtime, ANALYSIS_VERSION)).fetchone()
            if row:
                return [tuple(p) for p in json.loads(row[0])]
        except Exception as e:
            log.debug(f"cache get failed for {filepath}: {e}")
        return None

    def put(self, filepath, pages_info):
        if not self.path or not pages_info:
            return
        try:
            size, mtime = self._key(filepath)
            with self._lock, self._connect() as conn:
                conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)",
                             (os.path.normcase(filepath), size, mtime, ANALYSIS_VERSION,
                              json.dumps(pages_info, ensure_ascii=False)))
        except Exception as e:
            log.debug(f"cache put failed for {filepath}: {e}")
