import win32print
import sys
import os

def test_raw_print(pdf_path, printer_name):
    if not os.path.exists(pdf_path):
        print(f"Error: {pdf_path} not found.")
        return False
        
    print(f"Attempting to send RAW PDF to {printer_name}...")
    try:
        hprinter = win32print.OpenPrinter(printer_name)
    except Exception as e:
        print(f"Failed to open printer: {e}")
        return False
        
    try:
        win32print.StartDocPrinter(hprinter, 1, ("Raw PDF Test", None, "RAW"))
        win32print.StartPagePrinter(hprinter)
        
        with open(pdf_path, "rb") as f:
            data = f.read()
            win32print.WritePrinter(hprinter, data)
            
        win32print.EndPagePrinter(hprinter)
        win32print.EndDocPrinter(hprinter)
        print("Success! PDF sent directly to printer as RAW data.")
        return True
    except Exception as e:
        print(f"Failed to send RAW data: {e}")
        return False
    finally:
        win32print.ClosePrinter(hprinter)

if __name__ == "__main__":
    printers = [p[2] for p in win32print.EnumPrinters(2)]
    print("Available printers:")
    for i, p in enumerate(printers):
        print(f"{i+1}. {p}")
        
    print("\nPlease enter the number of your Canon TM-350 printer:")
    choice = int(input("> ")) - 1
    target_printer = printers[choice]
    
    pdf = input("\nEnter the path to a test PDF file (e.g. C:\\test.pdf): ").strip()
    pdf = pdf.strip('"\'')
    
    test_raw_print(pdf, target_printer)
    input("Press Enter to exit...")
