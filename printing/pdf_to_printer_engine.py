import subprocess
import os
import logging
from printing.engine_base import PrintEngineBase

class PDFtoPrinterEngine(PrintEngineBase):
    def __init__(self, executable_path: str):
        self.executable_path = executable_path
        if not os.path.exists(self.executable_path):
            logging.warning(f"PDFtoPrinter not found at {self.executable_path}")

    def get_name(self) -> str:
        return "PDFtoPrinter"

    def print_file(self, filepath: str, printer_name: str, settings: str, copies: int = 1) -> bool:
        if not os.path.exists(self.executable_path):
            logging.error("PDFtoPrinter executable missing.")
            return False

        success = True
        for copy_num in range(copies):
            # PDFtoPrinter syntax: PDFtoPrinter.exe "file.pdf" "Printer Name"
            cmd = [
                self.executable_path,
                filepath,
                printer_name
            ]
            
            # settings can include things like "copies=2" natively but we handle loop manually
            if settings and "noscale" in settings.lower():
                # PDFtoPrinter always prints at original size natively if possible
                pass
                
            try:
                logging.info(f"[{self.get_name()}] Executing (copy {copy_num+1}/{copies}): {' '.join(cmd)}")
                creationflags = 0
                if os.name == 'nt':
                    creationflags = subprocess.CREATE_NO_WINDOW
                    
                result = subprocess.run(cmd, capture_output=True, text=True, creationflags=creationflags)
                logging.info(f"[{self.get_name()}] Return code: {result.returncode}")
                if result.stdout and result.stdout.strip():
                    logging.info(f"[{self.get_name()}] stdout: {result.stdout.strip()}")
                if result.stderr and result.stderr.strip():
                    logging.warning(f"[{self.get_name()}] stderr: {result.stderr.strip()}")
                if result.returncode != 0:
                    logging.error(f"[{self.get_name()}] Print failed with code {result.returncode}: {result.stderr}")
                    success = False
            except Exception as e:
                logging.error(f"[{self.get_name()}] Exception during print: {e}", exc_info=True)
                success = False
                
        return success
