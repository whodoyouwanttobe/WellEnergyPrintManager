import os
import sys
import glob
import time
import subprocess
try:
    import fitz
except ImportError:
    import pymupdf as fitz
import win32print

def draw_ruler(page, w_pt, h_pt, mm_to_pt):
    # Draw top ruler
    for i in range(0, int(w_pt / mm_to_pt), 10):
        x = i * mm_to_pt
        page.draw_line(fitz.Point(x, 0), fitz.Point(x, 20), color=(0,0,0), width=1)
        if i % 50 == 0:
            page.insert_text(fitz.Point(x+2, 18), str(i), fontsize=8, color=(0,0,1))
    
    # Draw bottom ruler
    for i in range(0, int(w_pt / mm_to_pt), 10):
        x = i * mm_to_pt
        page.draw_line(fitz.Point(x, h_pt), fitz.Point(x, h_pt-20), color=(0,0,0), width=1)
        if i % 50 == 0:
            page.insert_text(fitz.Point(x+2, h_pt-5), str(i), fontsize=8, color=(0,0,1))
            
    # Draw left ruler
    for i in range(0, int(h_pt / mm_to_pt), 10):
        y = i * mm_to_pt
        page.draw_line(fitz.Point(0, y), fitz.Point(20, y), color=(0,0,0), width=1)
        if i % 50 == 0:
            page.insert_text(fitz.Point(2, y+10), str(i), fontsize=8, color=(0,0,1))
            
    # Draw right ruler
    for i in range(0, int(h_pt / mm_to_pt), 10):
        y = i * mm_to_pt
        page.draw_line(fitz.Point(w_pt, y), fitz.Point(w_pt-20, y), color=(0,0,0), width=1)
        if i % 50 == 0:
            page.insert_text(fitz.Point(w_pt-18, y+10), str(i), fontsize=8, color=(0,0,1))

def create_test_pdf(filepath, width_mm, height_mm, title):
    mm_to_pt = 72 / 25.4
    w_pt = width_mm * mm_to_pt
    h_pt = height_mm * mm_to_pt
    doc = fitz.open()
    page = doc.new_page(width=w_pt, height=h_pt)
    
    # Border
    page.draw_rect(fitz.Rect(1, 1, w_pt-1, h_pt-1), color=(1,0,0), width=2)
    
    # Center lines
    page.draw_line(fitz.Point(w_pt/2, 0), fitz.Point(w_pt/2, h_pt), color=(0,1,0), width=0.5)
    page.draw_line(fitz.Point(0, h_pt/2), fitz.Point(w_pt, h_pt/2), color=(0,1,0), width=0.5)
    
    # Text
    cx, cy = w_pt/2, h_pt/2
    page.insert_text(fitz.Point(cx - 150, cy - 60), "СТРОГО ВЕРХ!", fontsize=30, color=(1,0,0))
    page.insert_text(fitz.Point(cx - 150, cy), title, fontsize=24, color=(0,0,0))
    page.insert_text(fitz.Point(cx - 150, cy + 40), f"Size: {width_mm}x{height_mm} mm", fontsize=16, color=(0,0,0))
    
    draw_ruler(page, w_pt, h_pt, mm_to_pt)
    
    doc.save(filepath)
    doc.close()
    return filepath

def get_tools_dir():
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
        if not os.path.exists(os.path.join(base_dir, 'tools')):
            base_dir = os.path.dirname(base_dir)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, 'tools')

def print_with_sumatra(pdf_path, printer_name, custom_params):
    sumatra = os.path.join(get_tools_dir(), 'SumatraPDF.exe')
    if not os.path.exists(sumatra):
        print(f"ERROR: Cannot find SumatraPDF.exe at {sumatra}")
        return
    cmd = [sumatra, '-print-to', printer_name, '-print-settings', custom_params, pdf_path]
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd)

def print_with_foxit(pdf_path, printer_name):
    foxit = os.path.join(get_tools_dir(), 'FoxitReader.exe')
    if not os.path.exists(foxit):
        print(f"ERROR: Cannot find FoxitReader.exe at {foxit}")
        return
    cmd = [foxit, '/t', pdf_path, printer_name]
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd)

def get_printers():
    return [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]

def main():
    print("=== Diagnostic Print Suite ===")
    printers = get_printers()
    for i, p in enumerate(printers):
        print(f"{i+1}. {p}")
        
    idx = int(input("\nSelect printer number (Canon TM-350): ")) - 1
    printer_name = printers[idx]
    
    print("\nGenerating Test PDFs...")
    # A4x3 is 297mm x 630mm
    w, h = 297, 630
    
    test_1 = create_test_pdf("test_1_A4x3_Normal.pdf", w, h, "TEST 1: 297x630 (Normal)")
    test_2 = create_test_pdf("test_2_A4x3_Rotated.pdf", h, w, "TEST 2: 630x297 (Rotated)")
    
    print("\n--- TEST 1: SumatraPDF (paper=630x297mm, noscale) ---")
    print("Sends explicitly 630x297mm format to printer")
    print_with_sumatra(test_1, printer_name, "paper=630x297mm,noscale")
    input("Press Enter to continue to next test...")

    print("\n--- TEST 2: SumatraPDF (paper=297x630mm, noscale) ---")
    print("Sends explicitly 297x630mm format to printer")
    print_with_sumatra(test_1, printer_name, "paper=297x630mm,noscale")
    input("Press Enter to continue to next test...")

    print("\n--- TEST 3: SumatraPDF (paper=A4x3, shrink) ---")
    print("Relies on shrink with default paper size")
    print_with_sumatra(test_1, printer_name, "shrink")
    input("Press Enter to continue to next test...")
    
    print("\n--- TEST 4: SumatraPDF (scale=99.94,paper=630x297mm) ---")
    print_with_sumatra(test_1, printer_name, "scale=99.94,paper=630x297mm")
    input("Press Enter to continue to next test...")
    
    print("\n--- TEST 5: FoxitReader (Default Auto-Rotate) ---")
    print("Sends through Foxit Default")
    print_with_foxit(test_1, printer_name)
    input("Press Enter to continue to next test...")
    
    print("\n--- TEST 6: SumatraPDF Rotated PDF (630x297mm, noscale) ---")
    print("Tests if providing pre-rotated PDF works better")
    print_with_sumatra(test_2, printer_name, "paper=630x297mm,noscale")
    
    print("\nDone! Please review the physical papers. The one with perfectly aligned margins and text is the winner.")

if __name__ == "__main__":
    main()
