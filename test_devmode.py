import win32print
import sys

def test_devmode(printer_name):
    print(f"Testing DevMode for printer: {printer_name}")
    try:
        hprinter = win32print.OpenPrinter(printer_name)
        info = win32print.GetPrinter(hprinter, 2)
        devmode = info['pDevMode']
        
        print(f"Original PaperSize ID: {devmode.PaperSize}")
        print(f"Original PaperLength: {devmode.PaperLength}")
        print(f"Original PaperWidth: {devmode.PaperWidth}")
        print(f"Original Orientation: {devmode.Orientation}")
        
        win32print.ClosePrinter(hprinter)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    printers = [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
    for i, p in enumerate(printers):
        print(f"[{i+1}] {p}")
    choice = input("Select printer: ")
    idx = int(choice) - 1
    test_devmode(printers[idx])
