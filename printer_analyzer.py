import subprocess
import json
import os
import sys
import datetime

def run_ps(cmd):
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], 
                                capture_output=True, text=True, encoding='cp866', errors='replace')
        return result.stdout
    except Exception as e:
        return str(e)

def analyze_printers():
    # Определяем папку, откуда запущен exe-файл
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
    else:
        exe_dir = os.path.dirname(os.path.abspath(__file__))
        
    out_file = os.path.join(exe_dir, "printer_report.json")
    
    print(f"Скрипт диагностики запущен. Время: {datetime.datetime.now()}")
    print("Начинаю сбор полного объёма данных...")
    
    report = {
        "timestamp": str(datetime.datetime.now()),
        "wmi_all_printers": [],
        "ps_all_printers": [],
        "canon_details": {}
    }
    
    # 1. WMI Data
    print("-> Сбор через WMI (Win32_Printer)...")
    wmi_data = run_ps("Get-WmiObject -Class Win32_Printer | Select-Object * | ConvertTo-Json -Depth 2")
    try:
        report["wmi_all_printers"] = json.loads(wmi_data)
    except:
        report["wmi_all_printers"] = wmi_data

    # 2. PowerShell PrintManagement Module
    print("-> Сбор через PowerShell (Get-Printer)...")
    ps_data = run_ps("Get-Printer | Select-Object * | ConvertTo-Json -Depth 2")
    try:
        report["ps_all_printers"] = json.loads(ps_data)
        printers = report["ps_all_printers"]
        if isinstance(printers, dict):
            printers = [printers]
    except:
        report["ps_all_printers"] = ps_data
        printers = []

    canon_found = False
    for p in printers:
        if not isinstance(p, dict): continue
        name = p.get("Name", "")
        if "Canon" in name or "TM" in name.upper():
            canon_found = True
            print(f"-> Обнаружен целевой плоттер: {name}")
            
            # PrintConfiguration
            config = run_ps(f'Get-PrintConfiguration -PrinterName "{name}" | Select-Object * | ConvertTo-Json -Depth 3')
            
            # PrinterProperty
            props = run_ps(f'Get-PrinterProperty -PrinterName "{name}" | Select-Object * | ConvertTo-Json -Depth 3')
            
            # WMI PrinterConfiguration
            wmi_config = run_ps(f'Get-WmiObject -Class Win32_PrinterConfiguration -Filter "Name=\'{name}\'" | Select-Object * | ConvertTo-Json -Depth 2')
            
            # Реестр
            reg_path = f"HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Print\\Printers\\{name}"
            reg_info = run_ps(f'Get-ItemProperty -Path "{reg_path}" | ConvertTo-Json -Depth 3')
            
            def safe_load(j_str):
                try:
                    return json.loads(j_str) if j_str.strip() else {}
                except:
                    return j_str

            report["canon_details"][name] = {
                "PrintConfiguration": safe_load(config),
                "PrinterProperties": safe_load(props),
                "WMI_PrinterConfiguration": safe_load(wmi_config),
                "RegistryData": safe_load(reg_info)
            }
            
    if not canon_found:
        print("! ВНИМАНИЕ: Плоттер Canon не найден по ключевым словам 'Canon' или 'TM'.")
            
    # Сохраняем
    print(f"-> Сохранение отчета в {out_file}")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=4)
        
    print("\nАнализ успешно завершён!")
    print(f"Рядом с программой создан файл: printer_report.json")
    print("Нажмите Enter для выхода...")
    input()

if __name__ == "__main__":
    analyze_printers()
