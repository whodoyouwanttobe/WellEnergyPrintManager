import os
import unittest
from core.pdf_utils import build_print_plan, compress_pages

class TestPrintPlan(unittest.TestCase):
    def test_compress_pages(self):
        self.assertEqual(compress_pages([1]), "1")
        self.assertEqual(compress_pages([1, 2, 3]), "1-3")
        self.assertEqual(compress_pages([1, 2, 4, 5, 7]), "1-2, 4-5, 7")

    def test_build_print_plan_no_history(self):
        # Format: (page_number, format, width, height, landscape)
        files_analysis = {
            os.path.join("C:", "test", "doc1.pdf"): [
                (1, "A4", 297, 210, True),
                (2, "A4", 297, 210, True),
                (3, "A1", 841, 594, True)
            ]
        }
        
        plan = build_print_plan(files_analysis, printed_pages={}, target_copies=1)
        
        # doc1 should be split: pages 1-2 to laser (A4), page 3 to plotter (A1)
        self.assertEqual(len(plan["laser"]), 1)
        self.assertEqual(len(plan["plotter"]), 1)
        
        laser_job = plan["laser"][0]
        self.assertEqual(laser_job[1], "doc1.pdf")
        self.assertEqual(laser_job[2], [1, 2])
        self.assertEqual(laser_job[3]["format"], "A4")
        self.assertEqual(laser_job[3]["missing"], 1)
        
        plotter_job = plan["plotter"][0]
        self.assertEqual(plotter_job[2], [3])
        self.assertEqual(plotter_job[3]["format"], "A1")
        
        self.assertEqual(plan["stats"]["laser_total"], 2)
        self.assertEqual(plan["stats"]["plotter_total"], 1)

    def test_build_print_plan_with_history(self):
        files_analysis = {
            os.path.join("C:", "test", "doc1.pdf"): [
                (1, "A4", 297, 210, True),
                (2, "A4", 297, 210, True)
            ]
        }
        # Page 1 is printed 1 time.
        printed = {os.path.join("C:", "test", "doc1.pdf"): {1: 1}}
        
        # Target copies = 1, so page 1 is done, page 2 missing 1.
        plan = build_print_plan(files_analysis, printed, target_copies=1)
        laser_job = plan["laser"][0]
        self.assertEqual(laser_job[2], [2])
        self.assertEqual(laser_job[3]["missing"], 1)
        self.assertEqual(plan["stats"]["laser_total"], 1)
        
        # Target copies = 2, page 1 missing 1, page 2 missing 2.
        plan2 = build_print_plan(files_analysis, printed, target_copies=2)
        
        # Should create two separate groups because missing count differs!
        self.assertEqual(len(plan2["laser"]), 2)
        missing_counts = [job[3]["missing"] for job in plan2["laser"]]
        self.assertIn(1, missing_counts)
        self.assertIn(2, missing_counts)

if __name__ == '__main__':
    unittest.main()
