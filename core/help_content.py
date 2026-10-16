"""Справка программы: чтение docs/help.md и дата редакции."""
import datetime
import os
import re

from core.config import get_base_path


def help_path():
    return os.path.join(get_base_path(), "docs", "help.md")


def revision_date():
    """Дата редакции: из сборки (дата последнего изменения help.md в git) или дата файла."""
    try:
        from core.version import DOCS_REVISION
        if DOCS_REVISION:
            return DOCS_REVISION
    except ImportError:
        pass
    try:
        return datetime.date.fromtimestamp(os.path.getmtime(help_path())).strftime("%d.%m.%Y")
    except OSError:
        return "неизвестна"


def load_sections(path=None):
    """{'Вопросы и ответы': [(заголовок, текст), ...], 'Документация': [...], ...} в порядке файла."""
    path = path or help_path()
    with open(path, encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    tabs = {}
    current_tab, current_title, buf = None, None, []

    def flush():
        if current_tab is not None and current_title is not None:
            tabs[current_tab].append((current_title, "\n".join(buf).strip()))

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            current_tab, current_title, buf = line[3:].strip(), None, []
            tabs[current_tab] = []
        elif line.startswith("### "):
            flush()
            current_title, buf = line[4:].strip(), []
        elif current_title is not None:
            buf.append(line)
    flush()
    return tabs


def search(sections, query):
    """Поиск по всем вкладкам: [(вкладка, заголовок, текст)], сначала совпадения в заголовке."""
    q = query.strip().lower()
    if not q:
        return []
    words = q.split()
    hits = []
    for tab, items in sections.items():
        for title, body in items:
            hay_t, hay_b = title.lower(), body.lower()
            if all(w in hay_t or w in hay_b for w in words):
                score = sum(w in hay_t for w in words)
                hits.append((-score, tab, title, body))
    hits.sort(key=lambda h: h[0])
    return [(t, ti, b) for _s, t, ti, b in hits]
