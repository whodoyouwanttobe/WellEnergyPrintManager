"""Передача «Напечатать в WPM» из Проводника уже запущенной программе.

Если выделить несколько PDF, Проводник запускает WPM.exe отдельно для каждого файла.
Второй экземпляр не запускается (см. main.py): каждый кладёт свой путь отдельным
файлом-запросом в папку print_requests, а работающая программа раз в секунду
забирает ВСЕ запросы и показывает одно окно печати для всех выделенных файлов.
"""
import json
import os
import time
import uuid

from core.config import get_config_path


def request_dir():
    d = os.path.join(os.path.dirname(get_config_path("print_config.json")), "print_requests")
    os.makedirs(d, exist_ok=True)
    return d


def write_request(path):
    name = f"{time.time():.6f}_{uuid.uuid4().hex[:6]}.json"
    tmp = os.path.join(request_dir(), name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"path": os.path.abspath(path), "time": time.time()}, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(request_dir(), name))


def take_requests(max_age=600):
    """→ список путей из всех запросов (по порядку) и удаляет их. Старые (>10 мин) пропускаются."""
    d = request_dir()
    out = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        p = os.path.join(d, name)
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        try:
            os.remove(p)
        except OSError:
            pass
        if time.time() - float(data.get("time", 0)) <= max_age and data.get("path"):
            if data["path"] not in out:
                out.append(data["path"])
    return out


def take_request(max_age=600):
    """Совместимость: первый путь или None."""
    r = take_requests(max_age)
    return r[0] if r else None
