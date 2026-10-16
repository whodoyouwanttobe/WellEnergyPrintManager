import os
import sys
import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageTk

try:
    import fitz
except ImportError:
    import pymupdf as fitz

# Ensure the root directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from printing.queue_manager import PrintQueueManager
from core.pdf_utils import analyze_pdf, build_print_plan
from printing.engine_base import PrintEngineBase

class VirtualEngine(PrintEngineBase):
    def __init__(self, engine_name):
        self.engine_name = engine_name
        
    def get_name(self):
        return self.engine_name
    
    
    def print_file(self, filepath, printer_name, settings, copies=1):
        print(f"--- VIRTUAL PRINT ---")
        print(f"File: {filepath}")
        print(f"Printer: {printer_name}")
        print(f"Settings: {settings}")
        print(f"Copies: {copies}")
        
        # Copy to a persistent location so the browser can open it before queue_manager deletes it
        import shutil
        persist_dir = os.path.join(os.path.dirname(__file__), "virtual_out")
        os.makedirs(persist_dir, exist_ok=True)
        out_name = f"virtual_{printer_name}_{os.path.basename(filepath)}"
        # Sanitize name
        out_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in out_name)
        persistent_path = os.path.join(persist_dir, out_name)
        shutil.copy2(filepath, persistent_path)
        
        print(f"Opening generated PDF for {printer_name}...")
        try:
            os.startfile(persistent_path)
        except Exception as e:
            print(f"Error opening PDF: {e}")
            
        return True

def run_virtual_test():
    filepath = r"C:\Users\aleks\Documents\WellEnergyProject\УРГАЛ-25_283У-ФАБ-П-КР Изм.1_2026.06.11.pdf"
    if not os.path.exists(filepath):
        print("Invalid file path.")
        return
        
    print(f"Analyzing {filepath}...")
    pages = analyze_pdf(filepath)
    files_analysis = {filepath: pages}
    
    # We pass empty printed_pages and copies=1
    plan = build_print_plan(files_analysis, {}, 1)
    
    print("Plan Built:")
    for fp, fn, pgs, inf in plan.get("laser", []):
        print(f" Laser: {fn} pages {pgs} format {inf['format']}")
    for fp, fn, pgs, inf in plan.get("plotter", []):
        print(f" Plotter: {fn} pages {pgs} format {inf['format']}")
        
    queue_manager = PrintQueueManager()
    queue_manager.start()
    
    virtual_hp = VirtualEngine("Virtual_HP_LaserJet")
    virtual_canon = VirtualEngine("Virtual_Canon_TM305")
    
    for fp, fn, pgs, inf in plan.get("laser", []):
        queue_manager.add_job(
            filepath=fp,
            printer_name="HP LaserJet MFP M436",
            engine=virtual_hp,
            settings="fit,paper=" + inf['format'],
            copies=1,
            pages=pgs,
            format_name=inf['format']
        )
        
    for fp, fn, pgs, inf in plan.get("plotter", []):
        queue_manager.add_job(
            filepath=fp,
            printer_name="Canon TM-305",
            engine=virtual_canon,
            settings="",
            copies=1,
            pages=pgs,
            format_name=inf['format']
        )
        
    print("Jobs added to queue. Waiting for processing...")
    
    # Wait for queue to finish
    queue_manager.queue.join()
    queue_manager.stop()
    print("Test finished.")

if __name__ == "__main__":
    run_virtual_test()
