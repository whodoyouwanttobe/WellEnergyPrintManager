import json
import os
import threading

import sys

def get_config_path(filename):
    # Тесты и самопроверка работают ТОЛЬКО во временной папке: раньше, если в текущей папке
    # (папке проекта) лежал print_config.json, тесты читали и меняли настоящие настройки
    # и историю печати.
    forced = os.environ.get("WPM_DATA_DIR")
    if forced:
        os.makedirs(forced, exist_ok=True)
        return os.path.join(forced, filename)
    cwd = os.getcwd()
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    legacy_dir = os.path.join(appdata, "WellEnergyPrintManager")
    
    check_paths = [
        legacy_dir,
        cwd,
        os.path.abspath(os.path.join(cwd, "..")),
        os.path.abspath(os.path.join(cwd, "..", ".."))
    ]
    for p in check_paths:
        path = os.path.join(p, filename)
        if os.path.exists(path):
            return path
            
    # Default to legacy_dir if it doesn't exist anywhere
    os.makedirs(legacy_dir, exist_ok=True)
    return os.path.join(legacy_dir, filename)

def get_base_path():
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    return base_path

class Config:
    _instance = None
    _lock = threading.Lock()
    _CONFIG_FILE = get_config_path("print_config.json")

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(Config, cls).__new__(cls)
                cls._instance._migrate_old_config()
                cls._instance._load()
            return cls._instance

    def _migrate_old_config(self):
        import shutil
        old_config = get_config_path("config.json")
        if not os.path.exists(self._CONFIG_FILE) and os.path.exists(old_config):
            try:
                shutil.copy2(old_config, self._CONFIG_FILE)
            except Exception as e:
                print(f"Error migrating config: {e}")

    def _load(self):
        self.data = {
            "printers": {
                "laser": {"name": "", "formats": ["A4", "A3"], "settings": "simplex,fit,center,bin=auto"},
                "plotter": {"name": "", "formats": ["A2", "A1", "A0", "custom"], "settings": "fit"}
            },
            "print_engine": "sumatra", # "sumatra" or "foxit"
            "foxit_path": r"C:\Program Files (x86)\Foxit Software\Foxit PDF Editor\FoxitPDFEditor.exe",
            "sumatra_path": os.path.join(get_base_path(), "tools", "SumatraPDF.exe"),
            "db_path": get_config_path("print_history.db"),
            "last_folder": "",
            "include_subfolders": False,
            "theme": "dark",
            "last_copies": 1,
            "global_scale": 99.94,
            "check_updates": True,
            "update_repo": "",  # пусто = whodoyouwanttobe/WellEnergyPrintManager
            "update_channel": "stable",  # stable / beta / dev
            "update_token": "",
            "bug_token": "",  # GitHub токен с правом Issues: Read and write
            "beta_channel": False,
            "tg_token": "",
            "tg_chat_id": ""
        }
        if os.path.exists(self._CONFIG_FILE):
            try:
                with open(self._CONFIG_FILE, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    self._update_dict(self.data, loaded)
            except Exception as e:
                print(f"Error loading config: {e}")

    def _update_dict(self, base, update):
        for k, v in update.items():
            if isinstance(v, dict) and k in base and isinstance(base[k], dict):
                self._update_dict(base[k], v)
            else:
                base[k] = v

    def save(self):
        try:
            with open(self._CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving config: {e}")

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()

# Global config instance accessor
def get_config():
    return Config()
