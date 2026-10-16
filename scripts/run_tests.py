"""Запуск всех тестов так, чтобы оконные тесты не мешали друг другу.

Почему не просто `pytest`: каждый оконный тест создаёт несколько интерпретаторов Tk.
После десятков таких созданий в ОДНОМ процессе Tk на Windows иногда не может
загрузить собственные файлы (`Can't find a usable tk.tcl`) — это сбой окружения,
а не программы. Поэтому:
  1) обычные тесты — одним процессом;
  2) каждый оконный тест (маркер gui) — в отдельном свежем процессе.

Запуск:  python scripts/run_tests.py      (код выхода 0 — всё прошло)
"""
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Консоль Windows (и GitHub Actions) по умолчанию в cp1252/cp866 — русский текст ронял скрипт.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")


def run(args):
    print(">>> pytest " + " ".join(args), flush=True)
    return subprocess.call([sys.executable, "-m", "pytest", *args], cwd=ROOT, env=ENV)


def main():
    failed = []
    if run(["-q", "-m", "not gui"]) != 0:
        failed.append("обычные тесты")

    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", "gui"],
                         cwd=ROOT, env=ENV, capture_output=True, text=True, encoding="utf-8", errors="replace")
    gui_tests = [ln.strip() for ln in out.stdout.splitlines() if "::" in ln]
    print(f"Оконных тестов: {len(gui_tests)} (каждый в отдельном процессе)", flush=True)
    for test_id in gui_tests:
        if run(["-q", test_id]) != 0:
            failed.append(test_id)

    print()
    if failed:
        print("НЕ ПРОШЛИ:")
        for f in failed:
            print("  " + f)
        return 1
    print("ВСЕ ТЕСТЫ ПРОШЛИ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
