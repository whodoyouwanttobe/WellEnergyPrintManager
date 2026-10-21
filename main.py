import os
import sys
import ctypes
import time
import threading

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# Ensure the current directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

def show_splash():
    import tkinter as tk
    from tkinter import ttk
    splash = tk.Tk()
    splash.overrideredirect(True)
    
    width = 400
    height = 200
    screen_width = splash.winfo_screenwidth()
    screen_height = splash.winfo_screenheight()
    x = (screen_width / 2) - (width / 2)
    y = (screen_height / 2) - (height / 2)
    splash.geometry(f'{width}x{height}+{int(x)}+{int(y)}')
    
    splash.configure(bg='#2b2b2b')
    
    title = tk.Label(splash, text="WellEnergy Print Manager", font=("Arial", 16, "bold"), bg="#2b2b2b", fg="white")
    title.pack(pady=(40, 10))
    
    subtitle = tk.Label(splash, text="Загрузка компонентов...", font=("Arial", 10), bg="#2b2b2b", fg="#aaaaaa")
    subtitle.pack(pady=5)
    
    style = ttk.Style()
    style.theme_use('default')
    style.configure("Horizontal.TProgressbar", background='#2ecc71', troughcolor='#444444', bordercolor='#2b2b2b', lightcolor='#2ecc71', darkcolor='#2ecc71')
    
    progress = ttk.Progressbar(splash, style="Horizontal.TProgressbar", orient="horizontal", length=300, mode="determinate")
    progress.pack(pady=15)
    
    splash.subtitle = subtitle
    splash.progress = progress
    
    splash.update()
    return splash

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()  # обязательно первой строкой: анализ PDF идёт в дочерних процессах

    if any(arg.startswith("--self-test") for arg in sys.argv[1:]):
        from ui.selftest import main_self_test
        sys.exit(main_self_test(sys.argv[1:]))
    
    # «Напечатать в WPM» из меню Проводника: WPM.exe --print "C:\Папка" (или PDF)
    from core.shell_integration import parse_print_arg
    print_request = parse_print_arg(sys.argv[1:])

    # Только один экземпляр: два одновременно запущенных WPM дважды анализируют папку
    # и могут дважды отправить одно и то же на печать.
    _instance_mutex = None
    if sys.platform == "win32":
        try:
            _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            _instance_mutex = _k32.CreateMutexW(None, False, "Local\\WellEnergyPrintManager")
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                if print_request:
                    # Программа уже открыта — передаём ей путь, она сама покажет окно печати
                    from core.print_request import write_request
                    write_request(print_request)
                    sys.exit(0)
                import tkinter as _tk
                from tkinter import messagebox as _mb
                _r = _tk.Tk(); _r.withdraw()
                _mb.showwarning("WellEnergy Print Manager",
                                "Программа уже запущена.\n\nЕсли её окна не видно — закройте WPM.exe "
                                "в Диспетчере задач (Ctrl+Shift+Esc) и запустите снова.", parent=_r)
                _r.destroy()
                sys.exit(0)
        except SystemExit:
            raise
        except Exception:
            pass

    # Show splash before heavy imports
    splash = show_splash()
    
    from core.logger import setup_logger
    setup_logger()

    # В exe (--windowed) нет консоли: без этого любая ошибка исчезает бесследно.
    import logging, traceback
    def _log_unhandled(exc_type, exc, tb):
        logging.critical("Необработанное исключение:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
    sys.excepthook = _log_unhandled
    threading.excepthook = lambda a: _log_unhandled(a.exc_type, a.exc_value, a.exc_traceback)

    # Падения на уровне C (Tk, PyMuPDF, драйвер принтера) не проходят через excepthook —
    # faulthandler пишет для них трассировку в отдельный файл.
    import faulthandler
    _log_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "WellEnergyPrintManager")
    try:
        _crash_file = open(os.path.join(_log_dir, "crash_native.log"), "a", encoding="utf-8")
        _crash_file.write(f"\n--- запуск {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        _crash_file.flush()
        faulthandler.enable(_crash_file, all_threads=True)
    except OSError:
        pass

    try:
        from core.version import FULL_VERSION
        logging.info(f"Запуск WPM {FULL_VERSION}, exe: {sys.executable}")
        from ui.app import PrintManagerApp
        if print_request:
            logging.info(f"Запуск из Проводника: печать {print_request}")
        app = PrintManagerApp(splash=splash, print_request=print_request)
        logging.info("Главное окно создано")
        app.mainloop()
        logging.info("Программа закрыта")
    except Exception:
        text = traceback.format_exc()
        logging.critical("Программа не запустилась:\n%s", text)
        try:
            from tkinter import messagebox
            messagebox.showerror("WPM — ошибка запуска",
                                 "Программа не смогла запуститься.\n\n" + text[-1500:] +
                                 f"\n\nЖурнал: {os.path.join(_log_dir, 'print_manager.log')}")
        except Exception:
            pass
        sys.exit(1)
