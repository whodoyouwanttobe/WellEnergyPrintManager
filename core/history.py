import sqlite3
from contextlib import contextmanager
import os
import datetime
import logging

log = logging.getLogger(__name__)


@contextmanager
def db_connect(path):
    """Соединение с БД, которое ГАРАНТИРОВАННО закрывается.
    `with sqlite3.connect(...)` только фиксирует транзакцию, но не закрывает файл —
    на Windows база оставалась занятой (из-за этого падали тесты и мог «залипать» файл)."""
    conn = sqlite3.connect(path, timeout=10)
    try:
        with conn:
            yield conn
    finally:
        conn.close()

class PrintHistory:
    def __init__(self, db_path="print_history.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        try:
            with db_connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("""CREATE TABLE IF NOT EXISTS print_jobs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp TEXT, filepath TEXT, filename TEXT,
                  file_hash TEXT, printer TEXT, pages TEXT,
                  page_count INTEGER, format TEXT, status TEXT DEFAULT 'success')""")
                
                c.execute("""CREATE TABLE IF NOT EXISTS printed_pages (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  filepath TEXT, file_hash TEXT, page_number INTEGER,
                  format TEXT, printer TEXT, timestamp TEXT, copies INTEGER DEFAULT 1,
                  folded_copies INTEGER DEFAULT 0,
                  UNIQUE(filepath, page_number, file_hash))""")
                c.execute("CREATE INDEX IF NOT EXISTS idx_pp_fp ON printed_pages(filepath)")
                
                # Migration for v3.1: Add folded_copies column if not exists
                try:
                    c.execute("ALTER TABLE printed_pages ADD COLUMN folded_copies INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass # Column already exists
                    
                # Transactions for detailed reporting
                c.execute("""CREATE TABLE IF NOT EXISTS transactions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  timestamp TEXT, filepath TEXT, page_number INTEGER,
                  action TEXT, copies INTEGER, format TEXT)""")
                  
                conn.commit()
        except Exception as e:
            log.error(f"Error initializing DB: {e}")


    def _log_transaction(self, filepath, page_number, action, copies, fmt=""):
        try:
            with db_connect(self.db_path) as conn:
                c = conn.cursor()
                ts = datetime.datetime.now().isoformat()
                c.execute('''INSERT INTO transactions 
                             (timestamp, filepath, page_number, action, copies, format)
                             VALUES (?, ?, ?, ?, ?, ?)''',
                          (ts, filepath, page_number, action, copies, fmt))
                conn.commit()
        except Exception as e:
            log.error(f"DB _log_transaction error: {e}")

    def record(self, filepath, printer, pages_list, fmt, status="success", file_hash="", copies=1):
        try:
            with db_connect(self.db_path) as conn:
                c = conn.cursor()
                ts = datetime.datetime.now().isoformat()
                filename = os.path.basename(filepath)
                # compress pages_list for logs
                pages_str = ",".join(map(str, pages_list)) if pages_list else ""
                
                c.execute("""INSERT INTO print_jobs 
                             (timestamp, filepath, filename, file_hash, printer, pages, page_count, format, status)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                          (ts, filepath, filename, file_hash, printer, pages_str, len(pages_list)*copies, fmt, status))
                
                if status == "success":
                    for p in pages_list:
                        c.execute("""INSERT INTO printed_pages 
                                     (filepath, file_hash, page_number, format, printer, timestamp, copies)
                                     VALUES (?, ?, ?, ?, ?, ?, ?)
                                     ON CONFLICT(filepath, page_number, file_hash) 
                                     DO UPDATE SET copies = copies + ?, timestamp = ?, printer = ?, format = ?""",
                                  (filepath, file_hash, p, fmt, printer, ts, copies,
                                   copies, ts, printer, fmt))
                conn.commit()
                if status == "success":
                    for p in pages_list:
                        self._log_transaction(filepath, p, "print", copies, fmt)
        except Exception as e:
            log.error(f"DB record error: {e}")

    
    def add_printed(self, filepath, pages_list, copies, fmt, file_hash=""):
        try:
            with db_connect(self.db_path) as conn:
                c = conn.cursor()
                ts = datetime.datetime.now().isoformat()
                if copies > 0:
                    for p in pages_list:
                        c.execute('''INSERT INTO printed_pages 
                                     (filepath, file_hash, page_number, format, printer, timestamp, copies)
                                     VALUES (?, ?, ?, ?, 'Manual', ?, ?)
                                     ON CONFLICT(filepath, page_number, file_hash) 
                                     DO UPDATE SET copies = copies + ?, timestamp = ?, format = ?''',
                                  (filepath, file_hash, p, fmt, ts, copies,
                                   copies, ts, fmt))
                conn.commit()
                if copies > 0:
                    for p in pages_list:
                        self._log_transaction(filepath, p, "print", copies, fmt)
        except Exception as e:
            log.error(f"DB add_printed error: {e}")

    def unprint_pages(self, filepath, pages_list, copies, fmt=""):
        """Снять отметки (печать была прервана, листы не вышли): copies -= N, не ниже 0."""
        try:
            with db_connect(self.db_path) as conn:
                c = conn.cursor()
                for p in pages_list:
                    c.execute("UPDATE printed_pages SET copies = MAX(0, copies - ?) "
                              "WHERE filepath = ? AND page_number = ?", (int(copies), filepath, p))
                conn.commit()
            for p in pages_list:
                self._log_transaction(filepath, p, "unprint", int(copies), fmt)
        except Exception as e:
            log.error(f"DB remove_printed error: {e}")

    def get_all_printed(self, folder):
        """Returns dict: {filepath: {page_number: {"printed": copies, "folded": folded}}} for all files in a folder"""
        res = {}
        try:
            with db_connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("SELECT filepath, page_number, copies, folded_copies FROM printed_pages WHERE filepath LIKE ?", (f"{folder}%",))
                for fp, pn, cp, fcp in c.fetchall():
                    if fp not in res:
                        res[fp] = {}
                    if pn not in res[fp]:
                        res[fp][pn] = {"printed": cp, "folded": fcp or 0}
                    else:
                        res[fp][pn]["printed"] = res[fp][pn].get("printed", 0) + cp
                        res[fp][pn]["folded"] = res[fp][pn].get("folded", 0) + (fcp or 0)
        except Exception as e:
            log.error(f"DB get_all_printed error: {e}")
        return res

    def get_printed(self, filepath):
        """Returns dict: {page_number: {"printed": copies, "folded": folded}} for a specific file"""
        res = {}
        try:
            with db_connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("SELECT page_number, copies, folded_copies FROM printed_pages WHERE filepath = ?", (filepath,))
                for pn, cp, fcp in c.fetchall():
                    res[pn] = {"printed": cp, "folded": fcp or 0}
        except Exception as e:
            log.error(f"DB get_printed error: {e}")
        return res

    def set_printed(self, filepath, pages_list, copies, fmt, file_hash=""):
        try:
            with db_connect(self.db_path) as conn:
                c = conn.cursor()
                ts = datetime.datetime.now().isoformat()
                if copies <= 0:
                    for p in pages_list:
                        c.execute("DELETE FROM printed_pages WHERE filepath=? AND page_number=?", (filepath, p))
                else:
                    for p in pages_list:
                        c.execute("""INSERT INTO printed_pages 
                                     (filepath, file_hash, page_number, format, printer, timestamp, copies)
                                     VALUES (?, ?, ?, ?, 'Manual', ?, ?)
                                     ON CONFLICT(filepath, page_number, file_hash) 
                                     DO UPDATE SET copies = ?, timestamp = ?, format = ?""",
                                  (filepath, file_hash, p, fmt, ts, copies,
                                   copies, ts, fmt))
                conn.commit()
                if copies > 0:
                    for p in pages_list:
                        self._log_transaction(filepath, p, "print", copies, fmt)
        except Exception as e:
            log.error(f"DB set_printed error: {e}")

    def remove_printed(self, filepath):
        try:
            with db_connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("DELETE FROM printed_pages WHERE filepath=?", (filepath,))
                conn.commit()
        except Exception as e:
            log.error(f"DB remove_printed error: {e}")

    def set_folded_copies(self, filepath, page_number, copies):
        try:
            with db_connect(self.db_path) as conn:
                c = conn.cursor()
                ts = datetime.datetime.now().isoformat()
                c.execute("""INSERT INTO printed_pages 
                             (filepath, file_hash, page_number, format, printer, timestamp, copies, folded_copies)
                             VALUES (?, '', ?, '', 'Manual', ?, 0, ?)
                             ON CONFLICT(filepath, page_number, file_hash) 
                             DO UPDATE SET folded_copies = ?""",
                          (filepath, page_number, ts, copies, copies))
                conn.commit()
            # Раньше сшивка не записывалась в журнал: «Отчёт» не мог посчитать скорость,
            # а колонка «Время» — показать, когда файл сшит.
            if copies > 0:
                fmt = ""
                try:
                    with db_connect(self.db_path) as conn:
                        row = conn.execute("SELECT format FROM printed_pages WHERE filepath=? AND page_number=? "
                                           "AND format != '' LIMIT 1", (filepath, page_number)).fetchone()
                        fmt = row[0] if row else ""
                except Exception:
                    pass
                self._log_transaction(filepath, page_number, "fold", copies, fmt)
        except Exception as e:
            log.error(f"Error setting folded copies: {e}")

    # ───────────── время печати/сшивки (колонка «Время») ─────────────
    def _query_times(self, where, arg):
        res = {}

        def parse(ts):
            try:
                return datetime.datetime.fromisoformat(ts) if ts else None
            except (TypeError, ValueError):
                return None

        try:
            with db_connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute(f"SELECT filepath, action, MIN(timestamp), MAX(timestamp) FROM transactions "
                          f"WHERE filepath {where} ? GROUP BY filepath, action", (arg,))
                for fp, action, t_min, t_max in c.fetchall():
                    d = res.setdefault(fp, {})
                    if action == "print":
                        d["print_first"], d["print_last"] = parse(t_min), parse(t_max)
                    elif action == "fold":
                        d["fold_last"] = parse(t_max)
                # По форматам — для подсказки «когда какой формат печатали»
                c.execute(f"SELECT filepath, format, action, MIN(timestamp), MAX(timestamp) FROM transactions "
                          f"WHERE filepath {where} ? GROUP BY filepath, format, action", (arg,))
                for fp, fmt, action, t_min, t_max in c.fetchall():
                    if not fmt:
                        continue
                    fmt = str(fmt).replace(" [V]", "")   # старые записи: «A4 [V]» = A4
                    f = res.setdefault(fp, {}).setdefault("by_format", {}).setdefault(fmt, {})
                    if action == "print":
                        a, b = parse(t_min), parse(t_max)
                        f["print_first"] = min(filter(None, [f.get("print_first"), a]), default=None)
                        f["print_last"] = max(filter(None, [f.get("print_last"), b]), default=None)
                    elif action == "fold":
                        f["fold_last"] = max(filter(None, [f.get("fold_last"), parse(t_max)]), default=None)
                # Запасной вариант для старых записей без журнала: время из printed_pages
                c.execute(f"SELECT filepath, MIN(timestamp), MAX(timestamp) FROM printed_pages "
                          f"WHERE copies > 0 AND filepath {where} ? GROUP BY filepath", (arg,))
                for fp, t_min, t_max in c.fetchall():
                    d = res.setdefault(fp, {})
                    if "print_last" not in d:
                        d["print_first"], d["print_last"] = parse(t_min), parse(t_max)
        except Exception as e:
            log.error(f"DB times error: {e}")
        return res

    def get_times(self, folder):
        """{filepath: {"print_first", "print_last", "fold_last"}} для всех файлов папки."""
        return self._query_times("LIKE", f"{folder}%")

    def get_times_for(self, filepath):
        return self._query_times("=", filepath).get(filepath, {})
