import win32print

def set_printer_paper(printer_name, width_mm, height_mm):
    try:
        hprinter = win32print.OpenPrinter(printer_name)
        
        info = win32print.GetPrinter(hprinter, 9)
        devmode = info['pDevMode']
        
        if devmode is None:
            info2 = win32print.GetPrinter(hprinter, 2)
            devmode = info2['pDevMode']
            info['pDevMode'] = devmode
            
        print(f"Old: W={devmode.PaperWidth}, H={devmode.PaperLength}, O={devmode.Orientation}")
        
        devmode.PaperWidth = int(width_mm * 10)
        devmode.PaperLength = int(height_mm * 10)
        devmode.Orientation = 1
        
        devmode.Fields |= 0x0008 # DM_PAPERWIDTH
        devmode.Fields |= 0x0004 # DM_PAPERLENGTH
        devmode.Fields |= 0x0001 # DM_ORIENTATION
        
        win32print.SetPrinter(hprinter, 9, info, 0)
        
        info_new = win32print.GetPrinter(hprinter, 9)
        devmode_new = info_new['pDevMode']
        print(f"New: W={devmode_new.PaperWidth}, H={devmode_new.PaperLength}, O={devmode_new.Orientation}")
        win32print.ClosePrinter(hprinter)
        return True
    except Exception as e:
        print(f"DevMode Error: {e}")
        return False

if __name__ == "__main__":
    set_printer_paper("Microsoft Print to PDF", 594, 420)
