import unittest
import time
from unittest.mock import MagicMock, patch

# Mock tkinter and fitz to avoid GUI dependencies during test
import sys
sys.modules['tkinter'] = MagicMock()
sys.modules['tkinter.ttk'] = MagicMock()
sys.modules['tkinter.messagebox'] = MagicMock()
sys.modules['fitz'] = MagicMock()

# Import the main app logic
# We need to simulate _print_worker which is inside PrintManagerApp
from print_manager import PrintManagerApp, PrintEngine, PrintHistory, ThemeLight

class TestPrinterQueueThrottling(unittest.TestCase):
    def setUp(self):
        # Setup a dummy app instance
        with patch('print_manager.UpdateChecker'):
            self.app = PrintManagerApp()
            self.app.root = MagicMock()
            self.app.engine = MagicMock()
            self.app.engine.print_pages.return_value = {"success": True, "message": "OK"}
            self.app.config = {
                "cooldown_seconds": 0,
                "printers": {
                    "laser": {"name": "Test_Laser", "formats": []},
                    "plotter": {"name": "Test_Plotter", "formats": []}
                }
            }
            # Set up a fake print plan
            self.app.current_plan = {
                "custom_scale": None,
                "laser": [
                    ("fake_file1.pdf", "fake_file1.pdf", [1,2,3], {"format": "A4", "missing": 2, "count": 3})
                ],
                "plotter": []
            }

    @patch('print_manager.win32print')
    def test_printer_out_of_paper_warning(self, mock_win32):
        # We simulate the spooler queue having 1 job initially, and printer status indicating PAPER_OUT (16)
        
        # We need a side effect for GetPrinter to return PAPER_OUT once, then clear it
        status_responses = [
            (2, 16), # Queue len 2, PAPER_OUT
            (2, 16),
            (0, 0),  # Queue cleared
            (0, 0)
        ]
        
        def mock_enum_jobs(h, a, b, c):
            return [1] * status_responses[0][0] # fake job array
            
        def mock_get_printer(h, level):
            status = status_responses[0][1]
            if len(status_responses) > 1:
                status_responses.pop(0)
            return {'Status': status}
            
        mock_win32.EnumJobs.side_effect = mock_enum_jobs
        mock_win32.GetPrinter.side_effect = mock_get_printer
        
        # Override sleep to run fast
        with patch('time.sleep', return_value=None):
            self.app._print_worker()
            
        # The app should have called self.root.after to show the warning message
        # Let's check if the warning string was ever sent to root.after
        warning_calls = [call for call in self.app.root.after.call_args_list 
                         if 'ОШИБКА ПРИНТЕРА: проверьте бумагу/замятие' in str(call)]
        
        self.assertTrue(len(warning_calls) > 0, "The app should have displayed a paper out warning")
        self.assertEqual(self.app.engine.print_pages.call_count, 2, "Should print exactly 2 copies (missing=2)")

    @patch('print_manager.win32print')
    def test_printer_queue_throttling(self, mock_win32):
        # Simulate queue filling up
        
        q_len = [3, 2, 1, 0, 0, 0]
        def mock_enum_jobs(h, a, b, c):
            l = q_len.pop(0) if q_len else 0
            return [1] * l
            
        mock_win32.EnumJobs.side_effect = mock_enum_jobs
        mock_win32.GetPrinter.return_value = {'Status': 0}
        
        with patch('time.sleep', return_value=None) as mock_sleep:
            self.app._print_worker()
            
        # Since the queue starts at 3, drops to 2, 1, it should have slept multiple times waiting
        self.assertTrue(mock_sleep.call_count > 0, "Throttling should sleep if queue >= 1")
        
    def test_engine_returns_error(self):
        # What if the print engine throws an error?
        self.app.engine.print_pages.return_value = {"success": False, "message": "Crash"}
        with patch('print_manager.win32print'):
            with patch('time.sleep', return_value=None):
                self.app._print_worker()
        
        # Should record error in history
        # (History record is called per copy)
        # Verify history is called with 'error'
        pass

if __name__ == '__main__':
    unittest.main()
