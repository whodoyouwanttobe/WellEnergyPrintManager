"""Расчёт для окна «Напечатать в WPM» (из Проводника): что и сколько печатать по форматам."""
import re
from collections import OrderedDict

PLOTTER_MAX_WIDTH_MM = 914     # Canon TM-305: рулон до 36" (914 мм)
_STANDARD = re.compile(r"^A[0-4](x\d+)?$", re.I)

from core.pdf_utils import build_print_plan, is_laser


def _fmt_key(f):
    order = {"A4": 0, "A3": 1, "A2": 2, "A1": 3, "A0": 4}
    return (order.get(f, 5), f)


def format_summary(analyzed, printed_pages, files, copies, reprint=False):
    """→ OrderedDict формат -> {"pages": страниц, "sheets": листов к печати, "laser": bool, "files": файлов}."""
    out = {}
    for fp in files:
        pages = (analyzed.get(fp) or {}).get("pages") or []
        done = printed_pages.get(fp, {}) if not reprint else {}
        for info in pages:
            f = info[1]
            d = out.setdefault(f, {"pages": 0, "sheets": 0, "laser": is_laser(f), "files": set(),
                                   "standard": bool(_STANDARD.match(str(f))), "fits": True})
            d["pages"] += 1
            if len(info) >= 4 and not d["laser"]:
                try:
                    if min(float(info[2]), float(info[3])) > PLOTTER_MAX_WIDTH_MM + 3:
                        d["fits"] = False      # шире рулона плоттера — напечатать нельзя
                except (TypeError, ValueError):
                    pass
            pd = done.get(info[0], 0)
            got = pd.get("printed", 0) if isinstance(pd, dict) else (pd or 0)
            miss = max(0, copies - got)
            d["sheets"] += miss
            d["files"].add(fp)
    res = OrderedDict()
    for f in sorted(out, key=_fmt_key):
        d = out[f]
        d["files"] = len(d["files"])
        res[f] = d
    return res


def entries_for(analyzed, printed_pages, files, copies, formats, reprint=False):
    """→ [(файл, формат, страницы, копий)] — сначала принтер (A4/A3), потом плоттер."""
    formats = set(formats)
    laser, plotter = [], []
    for fp in files:
        pages = (analyzed.get(fp) or {}).get("pages") or []
        if reprint:
            groups = OrderedDict()
            for info in pages:
                groups.setdefault(info[1], []).append(info[0])
            items = [(fp, f, pgs, copies) for f, pgs in groups.items()]
        else:
            plan = build_print_plan({fp: pages}, printed_pages, copies)
            items = [(fp, inf["format"], pgs, inf["missing"]) for _fp, _fn, pgs, inf in plan["laser"] + plan["plotter"]]
        bad = {f for f, d in format_summary(analyzed, printed_pages, [fp], 1).items() if not d["fits"]}
        for it in items:
            if it[1] in formats and it[1] not in bad and it[2] and it[3] > 0:
                (laser if is_laser(it[1]) else plotter).append(it)
    return laser + plotter
