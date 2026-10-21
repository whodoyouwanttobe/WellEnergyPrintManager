"""Пункт «Напечатать в WPM» в меню Проводника: у папок, у пустого места внутри папки
и у PDF-файлов (одного или нескольких выделенных). У других типов файлов пункта нет,
а из папки берутся только PDF — несовместимые файлы на печать не попадают.

Пишется в HKEY_CURRENT_USER\\Software\\Classes — права администратора не нужны и
другим пользователям ПК ничего не меняется. В Windows 11 пункт находится в
«Показать дополнительные параметры» (или Shift+правый клик).
Программа обновляет путь к себе при каждом запуске (exe мог переехать).
"""
import logging
import sys

log = logging.getLogger(__name__)

KEY = "WPMPrint"
LABEL = "Напечатать в WPM"
TARGETS = [
    (r"Software\Classes\Directory\shell", "%1"),
    (r"Software\Classes\Directory\Background\shell", "%V"),
    (r"Software\Classes\SystemFileAssociations\.pdf\shell", "%1"),
]
OLD_TARGETS = []


def command_for(exe, placeholder):
    return f'"{exe}" --print "{placeholder}"'


def register(exe=None):
    """→ True при успехе. Ничего не делает вне Windows."""
    if sys.platform != "win32":
        return False
    exe = exe or sys.executable
    try:
        import winreg
        for base, ph in TARGETS:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{base}\\{KEY}") as k:
                winreg.SetValueEx(k, None, 0, winreg.REG_SZ, LABEL)
                winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, f'"{exe}",0')
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{base}\\{KEY}\\command") as k:
                winreg.SetValueEx(k, None, 0, winreg.REG_SZ, command_for(exe, ph))
        _delete(OLD_TARGETS)
        return True
    except OSError:
        log.exception("Не удалось добавить пункт в меню Проводника")
        return False


def _delete(bases):
    import winreg
    for base in bases:
        for sub in (f"{base}\\{KEY}\\command", f"{base}\\{KEY}"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except FileNotFoundError:
                pass


def unregister():
    if sys.platform != "win32":
        return False
    try:
        _delete([b for b, _ph in TARGETS] + OLD_TARGETS)
        return True
    except OSError:
        log.exception("Не удалось убрать пункт из меню Проводника")
        return False


def parse_print_arg(argv):
    """['WPM.exe', '--print', 'C:\\Папка'] или '--print=C:\\...' → путь или None."""
    for i, a in enumerate(argv):
        if a == "--print" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--print="):
            return a.split("=", 1)[1]
    return None
