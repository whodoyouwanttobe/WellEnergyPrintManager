"""Короткая запись времени для колонки «Время» и подробная — для подсказки."""
import datetime


def _hm(dt):
    return dt.strftime("%H:%M")


def _day(dt):
    return dt.strftime("%d.%m")


def short_time(times, session=None, fully_folded=False, now=None):
    """times   — {"print_first", "print_last", "fold_last"} (datetime) из базы;
    session — {"start": datetime, "end": datetime} печать в текущем сеансе;
    Возвращает, например: '▶ 10:15', '10:15–10:40', '15.10 10:15', '✓ 11:05', '✓ 15.10'."""
    now = now or datetime.datetime.now()
    today = now.date()
    times = times or {}
    session = session or {}

    if fully_folded:
        fold = times.get("fold_last")
        if not fold:
            return "✓"
        return f"✓ {_hm(fold)}" if fold.date() == today else f"✓ {_day(fold)}"

    if session.get("start") and not session.get("end"):
        return f"▶ {_hm(session['start'])}"

    # Завершённая печать уже записана в базу (журнал transactions), поэтому берём её оттуда
    first, last = times.get("print_first"), times.get("print_last")
    if not last:
        return ""
    if first and first.date() == last.date() and _hm(first) != _hm(last):
        rng = f"{_hm(first)}–{_hm(last)}"
    else:
        rng = _hm(last)
    return rng if last.date() == today else f"{_day(last)} {rng}"


def long_time(times, session=None):
    """Подробная подсказка."""
    times = times or {}
    session = session or {}
    lines = []
    first, last = times.get("print_first"), times.get("print_last")
    if session.get("start") and not session.get("end"):
        lines.append(f"Идёт печать с {session['start']:%H:%M}")
    if last:
        if first and first.date() == last.date():
            lines.append(f"Печать: {first:%d.%m.%Y %H:%M} – {last:%H:%M}")
        elif first:
            lines.append(f"Печать: {first:%d.%m.%Y %H:%M} – {last:%d.%m.%Y %H:%M}")
        else:
            lines.append(f"Печать: {last:%d.%m.%Y %H:%M}")
    if times.get("fold_last"):
        lines.append(f"Сшито: {times['fold_last']:%d.%m.%Y %H:%M}")
    by_fmt = times.get("by_format") or {}
    if by_fmt:
        lines.append("По форматам:")
        for fmt in sorted(by_fmt, key=_fmt_order):
            t = by_fmt[fmt]
            parts = []
            pf, pl = t.get("print_first"), t.get("print_last")
            if pl:
                if pf and pf.date() == pl.date() and _hm(pf) != _hm(pl):
                    parts.append(f"печать {pl:%d.%m} {_hm(pf)}–{_hm(pl)}")
                else:
                    parts.append(f"печать {pl:%d.%m %H:%M}")
            if t.get("fold_last"):
                parts.append(f"сшит {t['fold_last']:%d.%m %H:%M}")
            if parts:
                lines.append(f"  {fmt}: " + ", ".join(parts))
    return "\n".join(lines)


def _fmt_order(fmt):
    """A4, A3, A2, A1, A0, затем склейки и прочее."""
    import re
    m = re.match(r"A(\d)(?:X(\d+))?", str(fmt).upper().replace(" ", ""))
    if not m:
        return (9, 0, str(fmt))
    return (int(m.group(2) or 0) > 0, -int(m.group(1)), int(m.group(2) or 0))
