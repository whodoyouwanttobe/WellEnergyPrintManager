"""Перетаскивание папки/PDF из Проводника в окно программы (только Windows).

В Tk нет встроенного drag & drop. Здесь окно регистрируется на приём файлов
(DragAcceptFiles), а сообщение WM_DROPFILES перехватывается подменой оконной процедуры.
Если что-то пошло не так — функция просто возвращает False, программа работает как раньше.
"""
import logging
import sys

log = logging.getLogger(__name__)

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4


def enable_drop(widget, callback):
    """callback(list_of_paths) вызывается в главном потоке Tk. → True, если включено."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        set_long.restype = ctypes.c_void_p
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        get_long.restype = ctypes.c_void_p
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                           wintypes.WPARAM, wintypes.LPARAM]
        user32.CallWindowProcW.restype = LRESULT
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_wchar_p, wintypes.UINT]
        shell32.DragQueryFileW.restype = wintypes.UINT
        shell32.DragFinish.argtypes = [ctypes.c_void_p]
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]

        widget.update_idletasks()
        hwnd = user32.GetParent(widget.winfo_id()) or widget.winfo_id()
        old_proc = get_long(hwnd, GWLP_WNDPROC)

        def proc(h, msg, wp, lp):
            if msg == WM_DROPFILES:
                try:
                    hdrop = ctypes.c_void_p(wp)
                    n = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                    files = []
                    for i in range(n):
                        ln = shell32.DragQueryFileW(hdrop, i, None, 0) + 1
                        buf = ctypes.create_unicode_buffer(ln)
                        shell32.DragQueryFileW(hdrop, i, buf, ln)
                        files.append(buf.value)
                    shell32.DragFinish(hdrop)
                    widget.after(10, lambda: callback(files))
                except Exception:
                    log.exception("Перетаскивание: не удалось прочитать файлы")
                return 0
            return user32.CallWindowProcW(old_proc, h, msg, wp, lp)

        widget._wpm_drop_proc = WNDPROC(proc)      # держим ссылку, иначе сборщик мусора уберёт
        set_long(hwnd, GWLP_WNDPROC, ctypes.cast(widget._wpm_drop_proc, ctypes.c_void_p))
        shell32.DragAcceptFiles(hwnd, True)
        log.info("Перетаскивание папок в окно включено")
        return True
    except Exception:
        log.exception("Перетаскивание недоступно")
        return False
