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
        log.info("Пункт «%s» добавлен в меню Проводника (%s)", LABEL, exe)
        ok = True
    except OSError:
        log.exception("Не удалось добавить пункт в меню Проводника")
        ok = False
    # Запасной путь, если реестр заблокирован политикой: «Отправить → WPM — напечатать»
    make_sendto(exe)
    return ok


SENDTO_NAME = "WPM — напечатать.lnk"


def sendto_path():
    import os
    return os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\SendTo", SENDTO_NAME)


def make_sendto(exe=None):
    """Ярлык в «Отправить»: выделенные файлы/папки придут как WPM.exe --print путь1 путь2…"""
    if sys.platform != "win32":
        return False
    exe = exe or sys.executable
    try:
        import win32com.client
        sh = win32com.client.Dispatch("WScript.Shell")
        lnk = sh.CreateShortcut(sendto_path())
        lnk.TargetPath = exe
        lnk.Arguments = "--print"
        lnk.IconLocation = f"{exe},0"
        lnk.Description = "Напечатать в WellEnergy Print Manager"
        lnk.Save()
        return True
    except Exception:
        log.exception("Не удалось создать ярлык в «Отправить»")
        return False


def check():
    """Что сейчас есть в системе — для кнопки «Проверить» в настройках."""
    res = {"registry": {}, "sendto": False, "command": ""}
    if sys.platform != "win32":
        return res
    import os
    try:
        import winreg
        for base, _ph in TARGETS:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{base}\\{KEY}\\command") as k:
                    res["command"] = winreg.QueryValueEx(k, None)[0]
                    res["registry"][base] = True
            except OSError:
                res["registry"][base] = False
    except ImportError:
        pass
    res["sendto"] = os.path.exists(sendto_path())
    return res


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
        import os
        try:
            os.remove(sendto_path())
        except OSError:
            pass
        _delete([b for b, _ph in TARGETS] + OLD_TARGETS)
        return True
    except OSError:
        log.exception("Не удалось убрать пункт из меню Проводника")
        return False


def parse_print_args(argv):
    """Все пути после --print (из «Отправить» их может быть много) → список."""
    out = []
    for i, a in enumerate(argv):
        if a.startswith("--print="):
            out.append(a.split("=", 1)[1])
        elif a == "--print":
            for b in argv[i + 1:]:
                if b.startswith("--"):
                    break
                out.append(b)
    return out


def parse_print_arg(argv):
    """['WPM.exe', '--print', 'C:\\Папка'] или '--print=C:\\...' → путь или None."""
    for i, a in enumerate(argv):
        if a == "--print" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--print="):
            return a.split("=", 1)[1]
    return None
