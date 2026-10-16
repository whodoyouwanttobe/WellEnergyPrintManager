import win32print

def test_devmode_fix(printer_name):
    hprinter = win32print.OpenPrinter(printer_name)
    info = win32print.GetPrinter(hprinter, 9)
    if info['pDevMode'] is None:
        info['pDevMode'] = win32print.GetPrinter(hprinter, 2)['pDevMode']
        
    devmode = info['pDevMode']
    print(f"Old PaperSize: {devmode.PaperSize}")
    
    devmode.PaperSize = 256 # DMPAPER_USER
    devmode.PaperWidth = 5940
    devmode.PaperLength = 4200
    devmode.Orientation = 1
    
    devmode.Fields |= (0x0008 | 0x0004 | 0x0001 | 0x0002) # WIDTH | LENGTH | ORIENTATION | PAPERSIZE
    
    win32print.SetPrinter(hprinter, 9, info, 0)
    
    info2 = win32print.GetPrinter(hprinter, 9)
    print(f"New PaperSize: {info2['pDevMode'].PaperSize}")
    win32print.ClosePrinter(hprinter)

if __name__ == "__main__":
    test_devmode_fix("Microsoft Print to PDF")
