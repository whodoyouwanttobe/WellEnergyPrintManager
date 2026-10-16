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
    return "\n".join(lines)
