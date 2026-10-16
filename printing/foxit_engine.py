import subprocess
import os
import logging
from printing.engine_base import PrintEngineBase

class FoxitEngine(PrintEngineBase):
    def __init__(self, executable_path: str):
        self.executable_path = executable_path
        if not os.path.exists(self.executable_path):
            logging.warning(f"Foxit Reader/Editor not found at {self.executable_path}")

    def get_name(self) -> str:
        return "Foxit PDF Editor"

    def print_file(self, filepath: str, printer_name: str, settings: str, copies: int = 1) -> bool:
        if not os.path.exists(self.executable_path):
            # Try to dynamically find Foxit if config path is wrong
            paths = [
                r"C:\Program Files (x86)\Foxit Software\Foxit PDF Editor\FoxitPDFEditor.exe",
                r"C:\Program Files\Foxit Software\Foxit PDF Editor\FoxitPDFEditor.exe",
                r"C:\Program Files (x86)\Foxit Software\Foxit PDF Reader\FoxitPDFReader.exe",
                r"C:\Program Files\Foxit Software\Foxit PDF Reader\FoxitPDFReader.exe",
                r"C:\Program Files (x86)\Foxit Software\Foxit Reader\FoxitReader.exe",
                r"C:\Program Files\Foxit Software\Foxit Reader\FoxitReader.exe"
            ]
            found = False
            for p in paths:
                if os.path.exists(p):
                    self.executable_path = p
                    found = True
                    break
            
            if not found:
                logging.error(f"Foxit executable missing. Tried {self.executable_path} and standard paths.")
                return False

        # Foxit uses `/t <filename> <printername>` for silent printing.
        # It does not natively support detailed settings via CLI like SumatraPDF does.
        # It will rely on the default preferences of the printer.
        success = True
        for copy_num in range(copies):
            cmd = [
                self.executable_path,
                "/t",
                filepath,
                printer_name
            ]
                
            try:
                logging.info(f"[{self.get_name()}] Executing (copy {copy_num+1}/{copies}): {' '.join(cmd)}")
                creationflags = 0
                if os.name == 'nt':
                    creationflags = subprocess.CREATE_NO_WINDOW
                    
                # Foxit might launch a background process. Wait for it.
                result = subprocess.run(cmd, capture_output=True, text=True, errors='replace', creationflags=creationflags)
                logging.info(f"[{self.get_name()}] Return code: {result.returncode}")
                if result.stdout and result.stdout.strip():
                    logging.info(f"[{self.get_name()}] stdout: {result.stdout.strip()}")
                if result.stderr and result.stderr.strip():
                    logging.warning(f"[{self.get_name()}] stderr: {result.stderr.strip()}")
                if result.returncode != 0:
                    logging.warning(f"[{self.get_name()}] Process exited with code {result.returncode}. Ignoring because GUI apps often return non-zero on success.")
                    # Do not set success = False here, assume it worked if it didn't throw an Exception.
            except Exception as e:
                logging.error(f"[{self.get_name()}] Exception during print: {e}", exc_info=True)
                success = False
            if copy_num < copies - 1:
                import time
                time.sleep(1)
                
        return success
