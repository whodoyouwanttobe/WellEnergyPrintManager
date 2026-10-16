import unittest
from core.pdf_utils import detect_format, is_laser

class TestFormatDetection(unittest.TestCase):
    def test_standard_formats(self):
        self.assertEqual(detect_format(1189, 841), "A0")
        self.assertEqual(detect_format(841, 594), "A1")
        self.assertEqual(detect_format(594, 420), "A2")
        self.assertEqual(detect_format(420, 297), "A3")
        self.assertEqual(detect_format(297, 210), "A4")

    def test_orientation_independence(self):
        # Landscape and portrait should yield the same
        self.assertEqual(detect_format(210, 297), "A4")
        self.assertEqual(detect_format(841, 1189), "A0")

    def test_tolerances(self):
        self.assertEqual(detect_format(299, 212), "A4")
        self.assertEqual(detect_format(293, 208), "A4")

    def test_elongated_formats(self):
        # A4x3 = 297 x (210*3) = 297 x 630
        self.assertEqual(detect_format(630, 297), "A4x3")
        # A4x4 = 297 x (210*4) = 297 x 840
        self.assertEqual(detect_format(840, 297), "A4x4")
        self.assertEqual(detect_format(297, 840), "A4x4")
        # A3x3 = 420 x (297*3) = 420 x 891
        self.assertEqual(detect_format(891, 420), "A3x3")

    def test_custom_formats(self):
        # Non-standard dimensions
        self.assertEqual(detect_format(500, 500), "500x500")
        self.assertEqual(detect_format(297, 500), "500x297")

    def test_is_laser(self):
        self.assertTrue(is_laser("A4"))
        self.assertTrue(is_laser("A3"))
        # A4xN печатается на плоттере (2 листа на рулоне) — это не лазерный формат
        self.assertFalse(is_laser("A4x4"))
        self.assertFalse(is_laser("A2"))
        self.assertFalse(is_laser("A1"))
        self.assertFalse(is_laser("A0"))

if __name__ == '__main__':
    unittest.main()
