import sys
import logging
import traceback

logging.basicConfig(level=logging.DEBUG)
print("Starting trace...")

try:
    print("Importing app...")
    from ui.app import PrintManagerApp
    print("Instantiating app...")
    app = PrintManagerApp()
    print("Done instantiating.")
except Exception as e:
    print("Exception!")
    traceback.print_exc()
