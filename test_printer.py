import win32print
import sys
import time

def test_printers():
    print("Enumerating printers...")
    try:
        printers = win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)
        if not printers:
            print("No printers found.")
            return

        for p in printers:
            name = p[2]
            print(f"\n--- Printer: {name} ---")
            try:
                hprinter = win32print.OpenPrinter(name)
                info = win32print.GetPrinter(hprinter, 2)
                
                print(f"Driver Name: {info.get('pDriverName', 'Unknown')}")
                print(f"Port Name: {info.get('pPortName', 'Unknown')}")
                print(f"Status: {info.get('Status', 0)}")
                print(f"Job Count: {info.get('cJobs', 0)}")

                jobs = win32print.EnumJobs(hprinter, 0, -1, 2)
                if not jobs:
                    print("  Queue is empty.")
                else:
                    for j in jobs:
                        print(f"  Job {j['JobId']}: {j.get('pDocument', 'Unknown')}")
                        print(f"    Status: {j.get('Status', 0)}")
                        print(f"    Pages Printed: {j.get('PagesPrinted', 0)} / {j.get('TotalPages', 0)}")
                        print(f"    Size: {j.get('Size', 0)} bytes")

                win32print.ClosePrinter(hprinter)
            except Exception as e:
                print(f"Error accessing {name}: {e}")

    except Exception as e:
        print(f"Fatal error enumerating printers: {e}")

if __name__ == "__main__":
    test_printers()
    input("\nPress Enter to exit...")
