import subprocess
import os
import logging
from printing.engine_base import PrintEngineBase

class SumatraEngine(PrintEngineBase):
    def __init__(self, executable_path: str):
        self.executable_path = executable_path
        if not os.path.exists(self.executable_path):
            logging.warning(f"SumatraPDF not found at {self.executable_path}")

    def get_name(self) -> str:
        return "SumatraPDF"

    def print_file(self, filepath: str, printer_name: str, settings: str, copies: int = 1) -> bool:
        if not os.path.exists(self.executable_path):
            import sys
            try:
                base_path = sys._MEIPASS
                alt_path = os.path.join(base_path, "tools", "SumatraPDF.exe")
                if os.path.exists(alt_path):
                    self.executable_path = alt_path
            except AttributeError:
                pass
                
        if not os.path.exists(self.executable_path):
            logging.error(f"SumatraPDF executable missing at {self.executable_path}.")
            return False

        if copies > 1:
            if settings:
                settings = f"{copies}x,{settings}"
            else:
                settings = f"{copies}x"
                
        cmd = [
            self.executable_path,
            "-print-to", printer_name,
            "-silent",
            filepath
        ]
        if settings:
            cmd.insert(3, "-print-settings")
            cmd.insert(4, settings)
            
        try:
            logging.info(f"[{self.get_name()}] Executing (native copies={copies}): {' '.join(cmd)}")
            creationflags = 0
            if os.name == 'nt':
                creationflags = subprocess.CREATE_NO_WINDOW
                
            result = subprocess.run(cmd, capture_output=True, text=True, errors='replace', creationflags=creationflags)
            logging.info(f"[{self.get_name()}] Return code: {result.returncode}")
            if result.stdout and result.stdout.strip():
                logging.info(f"[{self.get_name()}] stdout: {result.stdout.strip()}")
            if result.stderr and result.stderr.strip():
                logging.warning(f"[{self.get_name()}] stderr: {result.stderr.strip()}")
            
            return True
        except Exception as e:
            logging.error(f"Failed to execute SumatraPDF: {e}")
            return False
