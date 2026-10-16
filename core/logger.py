import logging
import os
import sys

def setup_logger():
    # Base dir for logs
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    log_dir = os.path.join(appdata, "WellEnergyPrintManager")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "print_manager.log")

    handlers = [logging.FileHandler(log_file, encoding='utf-8')]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
        
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )
    logging.info(f"Logger initialized. Saving to {log_file}")
