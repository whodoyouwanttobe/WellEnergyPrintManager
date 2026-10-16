import os
import sys
import logging

# Add current dir to path to import app modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from printing.queue_manager import PrintQueueManager
from printing.sumatra_engine import SumatraEngine
import fitz

logging.basicConfig(level=logging.INFO, format="%(message)s")

def create_test_pdf(filename, width_mm, height_mm):
    w_pts = width_mm * 72 / 25.4
    h_pts = height_mm * 72 / 25.4
    doc = fitz.open()
    page = doc.new_page(width=w_pts, height=h_pts)
    rect = page.rect
    page.draw_rect(rect + (10, 10, -10, -10), color=(0, 1, 0), width=5)
    text = f"Test W={width_mm} L={height_mm}"
    page.insert_text((50, 50), text, fontsize=36, color=(0,0,1))
    doc.save(filename)
    doc.close()
    return os.path.abspath(filename)

qm = PrintQueueManager()
qm.start()

# Mock SumatraEngine
engine = SumatraEngine(r"C:\Program Files\SumatraPDF\SumatraPDF.exe")

import win32print
printers = [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
printer_name = printers[0] if printers else "Unknown Printer"
print(f"Using printer: {printer_name}")

pdf = create_test_pdf("test_a2.pdf", 594, 420)

qm.add_job(
    filepath=pdf,
    printer_name=printer_name,
    engine=engine,
    settings="",
    copies=1,
    pages=[1],
    format_name="A2 Plotter"
)

import time
time.sleep(10)
qm.stop()
print("Done!")
