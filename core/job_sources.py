"""Что выделили в Проводнике → список «источников» для печати.

Выделить можно сразу несколько папок и/или PDF из разных папок:
  • каждая папка — отдельный источник (все PDF в ней);
  • PDF-файлы группируются по своей папке (анализ идёт по папке);
  • если папка выделена целиком, её отдельные PDF в неё и входят;
  • не-PDF файлы пропускаются (их число возвращается отдельно).
"""
import os


def _norm(p):
    return os.path.normcase(os.path.abspath(p))


def group_sources(paths):
    """→ (источники, пропущено_не_pdf). Источник: {"folder", "files" (None — вся папка), "title"}."""
    folders, files_by_folder, order, skipped = {}, {}, [], 0
    for p in paths or []:
        if not p:
            continue
        p = os.path.abspath(p)
        if os.path.isdir(p):
            k = _norm(p)
            if k not in folders:
                folders[k] = p
                if k not in order:
                    order.append(k)
        elif os.path.isfile(p):
            if not p.lower().endswith(".pdf"):
                skipped += 1
                continue
            k = _norm(os.path.dirname(p))
            lst = files_by_folder.setdefault(k, [])
            if p not in lst:
                lst.append(p)
            if k not in order:
                order.append(k)
    out = []
    for k in order:
        if k in folders:
            f = folders[k]
            out.append({"folder": f, "files": None, "title": f"📁 {os.path.basename(f.rstrip(chr(92) + '/')) or f}"})
        else:
            lst = files_by_folder[k]
            folder = os.path.dirname(lst[0])
            title = (f"📄 {os.path.basename(lst[0])}" if len(lst) == 1 else
                     f"📄 {len(lst)} PDF из «{os.path.basename(folder) or folder}»")
            out.append({"folder": folder, "files": list(lst), "title": title})
    return out, skipped


def same_source(a, b):
    return _norm(a["folder"]) == _norm(b["folder"]) and (a["files"] is None) == (b["files"] is None)
