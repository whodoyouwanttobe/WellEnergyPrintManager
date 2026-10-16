"""Какая программа открывает файлы в Windows по умолчанию (то, что выбрано в
«Параметры → Приложения по умолчанию» / «Открыть с помощью → Всегда»)."""
import ctypes
import os
import sys

ASSOCF_NONE = 0
ASSOCF_NOTRUNCATE = 0x20
ASSOCSTR_EXECUTABLE = 2
ASSOCSTR_FRIENDLYAPPNAME = 4


def _assoc_query(ext, what):
    buf_len = ctypes.c_ulong(1024)
    buf = ctypes.create_unicode_buffer(buf_len.value)
    res = ctypes.windll.shlwapi.AssocQueryStringW(ASSOCF_NOTRUNCATE, what, ext, "open",
                                                  buf, ctypes.byref(buf_len))
    return buf.value if res == 0 and buf.value else None


def default_app(ext=".pdf"):
    """Возвращает (название, путь к exe) или (None, None), если не удалось узнать."""
    if sys.platform != "win32":
        return None, None
    try:
        exe = _assoc_query(ext, ASSOCSTR_EXECUTABLE)
        name = _assoc_query(ext, ASSOCSTR_FRIENDLYAPPNAME)
        if not name and exe:
            name = os.path.splitext(os.path.basename(exe))[0]
        return name, exe
    except Exception:
        return None, None


def open_default_label(ext=".pdf"):
    """Текст пункта меню: «Открыть в Foxit PDF Editor (по умолчанию)»."""
    name, _ = default_app(ext)
    if name:
        if len(name) > 45:
            name = name[:42] + "…"
        return f"Открыть в {name} (по умолчанию)"
    return "Открыть файл (программа по умолчанию)"
