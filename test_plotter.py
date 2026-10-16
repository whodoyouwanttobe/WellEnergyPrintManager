import sys
import os
import subprocess
import win32print
import time
import traceback
import fitz

def create_test_pdf(filename, width_mm, height_mm, rotate=False):
    # Create PDF with specific dimensions
    w_pts = width_mm * 72 / 25.4
    h_pts = height_mm * 72 / 25.4
    
    doc = fitz.open()
    page = doc.new_page(width=w_pts, height=h_pts)
    
    # Draw border
    rect = page.rect
    page.draw_rect(rect, color=(1, 0, 0), width=5)
    
    # Draw inner border to see cropping
    inner_rect = rect + (10, 10, -10, -10)
    page.draw_rect(inner_rect, color=(0, 1, 0), width=5)
    
    # Draw text
    text = f"Format: {width_mm}x{height_mm}mm"
    if rotate:
        text += " (Rotated)"
    
    page.insert_text((50, 50), text, fontsize=36, color=(0,0,1))
    
    # Add orientation arrows
    page.insert_text((w_pts/2, 50), "^ TOP ^", fontsize=24)
    page.insert_text((w_pts - 100, h_pts/2), "RIGHT >", fontsize=24)
    page.insert_text((50, h_pts/2), "< LEFT", fontsize=24)
    page.insert_text((w_pts/2, h_pts - 50), "v BOTTOM v", fontsize=24)
    
    if rotate:
        page.set_rotation(90)
        
    doc.save(filename)
    doc.close()
    return os.path.abspath(filename)

def get_foxit_path():
    paths = [
        r"C:\Program Files (x86)\Foxit Software\Foxit PDF Editor\FoxitPDFEditor.exe",
        r"C:\Program Files\Foxit Software\Foxit PDF Editor\FoxitPDFEditor.exe",
        r"C:\Program Files (x86)\Foxit Software\Foxit PDF Reader\FoxitPDFReader.exe",
        r"C:\Program Files\Foxit Software\Foxit PDF Reader\FoxitPDFReader.exe",
        r"C:\Program Files (x86)\Foxit Software\Foxit Reader\FoxitReader.exe",
        r"C:\Program Files\Foxit Software\Foxit Reader\FoxitReader.exe"
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def main():
    print("=== Canon TM-350 A2 Orientation Fix Test (Foxit DevMode) ===")
    
    # 1. Get printers
    try:
        printers = [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
    except Exception as e:
        print(f"Error enumerating printers: {e}")
        input("Press Enter to exit...")
        return
        
    print("\nAvailable Printers:")
    for i, p in enumerate(printers):
        print(f"[{i+1}] {p}")
        
    try:
        choice = input("\nSelect Plotter (number): ")
        printer_name = printers[int(choice)-1]
    except:
        print("Invalid choice.")
        return
        
    print(f"\nSelected: {printer_name}")
    
    # Create test PDF A2 (594x420)
    pdf_path = create_test_pdf("test_A2_594x420.pdf", 594, 420)
    print(f"Created {pdf_path}")
    
    foxit = get_foxit_path()
    if not foxit:
        print("Error: Foxit not found! Checked all standard paths.")
        input("Press Enter to exit...")
        return
        
    print(f"Found Foxit: {foxit}")
    print("\n--- Strategy: Foxit DevMode Hack ---")
    print("This will temporarily change your printer's default paper size to 594x420, print using Foxit, and restore it.")
    input("Press Enter to print...")
    
    try:
        hprinter = win32print.OpenPrinter(printer_name)
        
        # Get Level 9 (User Default)
        info_orig = win32print.GetPrinter(hprinter, 9)
        devmode_orig = info_orig['pDevMode']
        
        if devmode_orig is None:
            print("Fetching Level 2 defaults...")
            info2 = win32print.GetPrinter(hprinter, 2)
            devmode_orig = info2['pDevMode']
            info_orig['pDevMode'] = devmode_orig
            
        # Create a new info object for modification to keep orig safe
        info_new = win32print.GetPrinter(hprinter, 9)
        if info_new['pDevMode'] is None:
            info_new['pDevMode'] = win32print.GetPrinter(hprinter, 2)['pDevMode']
            
        # Modify for A2
        devmode_new = info_new['pDevMode']
        devmode_new.PaperSize = 256 # DMPAPER_USER
        devmode_new.PaperWidth = 5940
        devmode_new.PaperLength = 4200
        devmode_new.Orientation = 1 # Portrait
        
        devmode_new.Fields |= 0x0008 # DM_PAPERWIDTH
        devmode_new.Fields |= 0x0004 # DM_PAPERLENGTH
        devmode_new.Fields |= 0x0001 # DM_ORIENTATION
        devmode_new.Fields |= 0x0002 # DM_PAPERSIZE
        
        # Set
        print(f"Applying 594x420 DevMode...")
        win32print.SetPrinter(hprinter, 9, info_new, 0)
        
        # Double check it stuck
        info_check = win32print.GetPrinter(hprinter, 9)
        dc = info_check['pDevMode']
        print(f"Verified DevMode: W={dc.PaperWidth}, H={dc.PaperLength}, O={dc.Orientation}, Size={dc.PaperSize}")
        
        # Print
        print("Printing via Foxit...")
        cmd = [foxit, "/t", pdf_path, printer_name]
        print(f"Running: {' '.join(cmd)}")
        subprocess.run(cmd, timeout=30)
        print("Sent to spooler!")
        
        # Wait a bit for spooler
        time.sleep(5)
        
        # Restore
        print("Restoring original DevMode...")
        win32print.SetPrinter(hprinter, 9, info_orig, 0)
        print("Done! Check the plotter output.")
        
    except Exception as e:
        print(f"Error during print: {e}")
        traceback.print_exc()
        
    finally:
        try:
            # Force restore just in case
            win32print.SetPrinter(hprinter, 9, info_orig, 0)
            win32print.ClosePrinter(hprinter)
        except:
            pass

    input("\nPress Enter to exit...")

if __name__ == "__main__":
    main()
