import unittest
import os
import sqlite3
from core.history import PrintHistory

class TestHistory(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="wpm_hist_")
        self.db_path = os.path.join(self._tmp, "test_history.db")
        self.history = PrintHistory(self.db_path)

    def tearDown(self):
        import shutil
        # Если соединение с БД где-то не закрыто, на Windows удаление упадёт — это и проверяем
        os.remove(self.db_path)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_set_and_get_printed(self):
        filepath = "C:\\test\\document.pdf"
        pages = [1, 2, 3]
        
        # Set 2 copies
        self.history.set_printed(filepath, pages, 2, "A4")
        
        printed = self.history.get_printed(filepath)
        self.assertEqual(printed[1]["printed"], 2)
        self.assertEqual(printed[2]["printed"], 2)
        self.assertEqual(printed[3]["printed"], 2)
        
        # Update specific page to 5 copies
        self.history.set_printed(filepath, [2], 5, "A4")
        printed = self.history.get_printed(filepath)
        self.assertEqual(printed[1]["printed"], 2)
        self.assertEqual(printed[2]["printed"], 5)
        
        # Remove page 3 by setting copies to 0
        self.history.set_printed(filepath, [3], 0, "A4")
        printed = self.history.get_printed(filepath)
        self.assertNotIn(3, printed)
        
    def test_get_all_printed(self):
        fp1 = "C:\\test\\doc1.pdf"
        fp2 = "C:\\test\\doc2.pdf"
        
        self.history.set_printed(fp1, [1], 1, "A4")
        self.history.set_printed(fp2, [1, 2], 2, "A3")
        
        all_printed = self.history.get_all_printed("C:\\test")
        self.assertIn(fp1, all_printed)
        self.assertIn(fp2, all_printed)
        self.assertEqual(all_printed[fp1][1]["printed"], 1)
        self.assertEqual(all_printed[fp2][2]["printed"], 2)

if __name__ == '__main__':
    unittest.main()
