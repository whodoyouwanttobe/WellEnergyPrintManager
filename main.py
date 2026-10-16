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
    
    # Import heavy modules
    from ui.app import PrintManagerApp
    
    # Initialize the app with the splash screen
    app = PrintManagerApp(splash=splash)
    
    # Start app loop. The splash will be destroyed by the app when ready.
    app.mainloop()
