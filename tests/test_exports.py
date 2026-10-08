import io
import unittest

import pymupdf
from openpyxl import load_workbook

from packing_app.exports import make_excel, make_pdf, plan_pages, visible_rows
from packing_app.schema import Box, Item, PackingList


class ExportTests(unittest.TestCase):
    def sample(self):
        return PackingList(
            customer="VINAYAK SPORTS", packing_date="08-10-2026", private_mark="VS/34",
            boxes=[
                Box(number=1, items=[Item(code="PC-404", size="XL", quantity=4), Item(code="PC-405", size="XL", quantity=5), Item(code="FC-25", size="S", quantity=2, note="ONLY BASE")]),
                Box(number=2, items=[Item(code="PC-413", size="S", quantity=5)]),
                Box(number=3, items=[Item(code="PC-405", size=s, quantity=5) for s in ("S", "M", "L")]),
                Box(number=4, items=[Item(code="PC-418", size="L", quantity=6)]),
                Box(number=5, items=[Item(code="PC-422", size="L", quantity=2)]),
            ],
        )

    def test_minimum_five_rows_and_one_worksheet_per_print_page(self):
        data = self.sample()
        self.assertEqual(visible_rows(data.boxes[0]), 5)
        self.assertEqual(len(plan_pages(data.boxes)), 2)
        workbook = load_workbook(io.BytesIO(make_excel(data)))
        self.assertEqual(len(workbook.worksheets), 2)
        first = workbook.worksheets[0]
        self.assertEqual(first["A8"].value, 1)
        self.assertEqual(first["B8"].value, "PC-404 XL")
        self.assertEqual(first["C8"].value, 4)
        self.assertIsNone(first["B12"].value)
        self.assertEqual(first.page_setup.fitToHeight, 1)
        self.assertEqual(first.page_setup.fitToWidth, 1)

    def test_pdf_keeps_box_on_single_page(self):
        data = self.sample()
        document = pymupdf.open(stream=make_pdf(data), filetype="pdf")
        self.assertEqual(len(document), 2)
        page1, page2 = [page.get_text() for page in document]
        self.assertIn("PC-404 XL", page1)
        self.assertIn("BOX No.", page2)
        self.assertIn("5", page2)

    def test_long_box_gets_own_larger_page(self):
        many = Box(number=10, items=[Item(code="PC-404", size="S", quantity=1) for _ in range(19)])
        data = PackingList(boxes=[many])
        pdf = pymupdf.open(stream=make_pdf(data), filetype="pdf")
        self.assertEqual(len(pdf), 1)
        self.assertGreater(pdf[0].rect.width, 1000)


if __name__ == "__main__":
    unittest.main()

