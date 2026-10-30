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


ON_CI = bool(os.environ.get("GITHUB_ACTIONS"))


def _annotate(output):
    """На GitHub — понятные аннотации с именем теста и причиной (их видно без входа в аккаунт)."""
    if not ON_CI:
        return
    lines = output.splitlines()
    failed = [ln.split(" ", 1)[1].split(" - ")[0] for ln in lines if ln.startswith(("FAILED ", "ERROR "))]
    errors = [ln for ln in lines if ln.startswith("E ")]
    reason = " | ".join(e[1:].strip() for e in errors[:4])[:900] or "см. лог шага"
    for f in failed or ["(тест)"]:
        msg = f"{f}: {reason}".replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::error title=Тест не прошёл::{msg}", flush=True)


def run(args):
    print(">>> pytest " + " ".join(args), flush=True)
    proc = subprocess.run([sys.executable, "-m", "pytest", "-rfE", *args], cwd=ROOT, env=ENV,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace")
    print(proc.stdout, flush=True)
    if proc.returncode != 0:
        _annotate(proc.stdout)
    return proc.returncode


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
