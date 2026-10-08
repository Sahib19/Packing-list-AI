import io
import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from packing_app import learning
from packing_app import main
from packing_app.recognition import ExtractedBox, ExtractedItem, ExtractedPage, combine_pages
from packing_app.schema import Box, Item, PackingList


class LearningTests(unittest.TestCase):
    def test_finished_sheet_ignores_blank_template_boxes(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "PRINT "
        sheet["C1"], sheet["D1"] = "BOX No.", 1
        sheet["G1"], sheet["H1"] = "BOX No.", 2
        sheet["C5"], sheet["G5"] = "ITEM CODE", "ITEM CODE"
        sheet["C6"], sheet["D6"] = "PC-244 S", "2 PCS"
        sheet["C8"], sheet["D8"] = "OLYMPIC MEDAL 2.5\" (GOLDEN)", "150 PCS"
        output = io.BytesIO()
        workbook.save(output)
        parsed = learning.parse_finished_workbook(output.getvalue(), "Sharda 08-10-2026.xlsx")
        self.assertEqual([box.number for box in parsed.boxes], [1])
        self.assertEqual(parsed.boxes[0].items[0].code, "PC-244")
        self.assertEqual(parsed.boxes[0].items[0].size, "S")
        self.assertEqual(parsed.boxes[0].items[1].code, 'OLYMPIC MEDAL 2.5" (GOLDEN)')

    def test_only_saved_verified_data_enters_guidance(self):
        original_root = learning.ROOT
        try:
            with tempfile.TemporaryDirectory() as folder:
                learning.ROOT = Path(folder)
                self.assertEqual(learning.verified_guidance(), "")
                data = PackingList(boxes=[Box(number=1, items=[Item(code="W-4721", size="S", quantity=2)])])
                case_id = learning.save_verified_case(data, [b"photo"], "test")
                self.assertIsNone(learning.find_exact_case([b"different photo"]))
                self.assertEqual(learning.find_exact_case([b"photo"])[0], case_id)
                self.assertIn("W-4721", learning.verified_guidance())
                data.boxes[0].items[0].code = "W-4722"
                learning.replace_verified_case(case_id, data)
                guidance = learning.verified_guidance()
                self.assertIn("W-4722", guidance)
                self.assertNotIn("W-4721", guidance)
                self.assertEqual(learning.training_stats()["verified_examples"], 1)
        finally:
            learning.ROOT = original_root

    def test_medal_colour_is_not_a_size(self):
        page = ExtractedPage(boxes=[ExtractedBox(number=2, needs_review=False, items=[ExtractedItem(
            code='OLYMPIC MEDAL 2.5" (GOLDEN)', size='G', quantity=150, note='', source_text='150 PC', needs_review=False
        )])])
        data = combine_pages([page])
        self.assertEqual(data.boxes[0].items[0].size, '')

    def test_final_saves_and_later_edits_replace_same_case(self):
        original_root = learning.ROOT
        job_id = 'learning-test-job'
        try:
            with tempfile.TemporaryDirectory() as folder:
                learning.ROOT = Path(folder)
                data = PackingList(boxes=[Box(number=1, items=[Item(code='PC-404', size='S', quantity=2)])])
                main.jobs[job_id] = {'status': 'done', 'pages': [b'photo'], 'origin': 'manual_import', 'finalized_case_id': None, 'result': data.model_dump()}
                self.assertEqual(learning.training_stats()['verified_examples'], 0)
                first = main.finalize(main.FinalInput(job_id=job_id, data=data))
                self.assertEqual(first['verified_examples'], 1)
                data.boxes[0].items[0].quantity = 3
                second = main.finalize(main.FinalInput(job_id=job_id, data=data))
                self.assertEqual(second['case_id'], first['case_id'])
                self.assertEqual(learning.training_stats()['verified_examples'], 1)
                self.assertEqual(json.loads((Path(folder) / first['case_id'] / 'case.json').read_text())['list']['boxes'][0]['items'][0]['quantity'], 3)
        finally:
            main.jobs.pop(job_id, None)
            learning.ROOT = original_root


if __name__ == "__main__":
    unittest.main()
