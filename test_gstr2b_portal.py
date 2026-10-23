"""Tests for portal GSTR-2B header and file-layout handling."""

from __future__ import annotations

import csv
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import gstr2b  # noqa: E402
from test_gstr2b import GSTIN_INTRA, csv_bytes, row  # noqa: E402

PORTAL_HEADERS = [
    "GSTIN of supplier",
    "Invoice Number",
    "Invoice Date",
    "Taxable Value",
    "Integrated Tax",
    "Central Tax",
    "State/UT Tax",
    "Cess",
    "GSTR-1/IFF/GSTR-5 Period",
    "ITC Availability",
    "Reason",
    "Supply Attract Reverse Charge",
    "Invoice Type",
    "IRN",
]

PORTAL_ROW = [
    GSTIN_INTRA, "INV-PORTAL-1", "2026-03-15", "12,500.00", "0.00",
    "1,125.00", "1,125.00", "0.00", "2026-03", "Y", "", "N", "R",
    "SYNTHETIC-IRN",
]


def portal_workbook(headers=PORTAL_HEADERS, rows=(PORTAL_ROW,),
                    sheet_name="B2B", title_rows=2) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    for index in range(title_rows):
        sheet.append([f"Synthetic GSTR-2B title row {index + 1}"])
    sheet.append(headers)
    for values in rows:
        sheet.append(values)
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def portal_csv(headers=PORTAL_HEADERS, values=PORTAL_ROW,
               title_rows=2) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for index in range(title_rows):
        writer.writerow([f"Synthetic GSTR-2B title row {index + 1}"])
    writer.writerow(headers)
    writer.writerow(values)
    return buffer.getvalue().encode("utf-8")


class PortalWorkbookTests(unittest.TestCase):
    def test_title_rows_are_skipped_and_source_row_is_preserved(self):
        parsed = gstr2b.parse_gstr2b(
            portal_workbook(), "portal-gstr2b.xlsx"
        )
        self.assertEqual(parsed.parser_kind, "portal_b2b")
        self.assertEqual(parsed.accepted_rows, 1)
        self.assertEqual(parsed.sheets_used, ("B2B",))
        row = parsed.rows[0]
        self.assertEqual(row.source_sheet, "B2B")
        self.assertEqual(row.source_row, 4)
        self.assertEqual(row.invoice_number, "INV-PORTAL-1")
        self.assertEqual(row.taxable_value, 1_250_000)
        self.assertEqual(row.cgst, 112_500)
        self.assertEqual(row.sgst, 112_500)
        self.assertEqual(row.total_gst, 225_000)
        self.assertEqual(row.itc_availability, "Y")
        self.assertEqual(row.reverse_charge, "N")
        self.assertEqual(row.document_type, "R")

    def test_portal_csv_fallback_uses_actual_source_row(self):
        parsed = gstr2b.parse_gstr2b(portal_csv(), "portal-gstr2b.csv")
        self.assertEqual(parsed.parser_kind, "portal_b2b")
        self.assertEqual(parsed.accepted_rows, 1)
        self.assertEqual(parsed.rows[0].source_sheet, "CSV")
        self.assertEqual(parsed.rows[0].source_row, 4)

    def test_two_igst_columns_are_rejected_as_ambiguous(self):
        headers = [
            "GSTIN of supplier", "Invoice Number", "Invoice Date",
            "Taxable Value", "Integrated Tax", "IGST",
            "Central Tax", "State/UT Tax",
        ]
        values = [
            GSTIN_INTRA, "INV-AMB", "2026-03-15", "125.00",
            "0.00", "0.00", "11.25", "11.25",
        ]
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            gstr2b.parse_gstr2b(
                portal_workbook(headers, [values]), "ambiguous.xlsx"
            )


class CanonicalHeaderLayoutTests(unittest.TestCase):
    def test_leading_title_rows_in_canonical_csv(self):
        content = (
            "Synthetic GSTR-2B export\n"
            "Generated for testing only\n"
            "supplier_gstin,invoice_number,invoice_date,tax_period,"
            "taxable_value,cgst,sgst,igst,total_gst\n"
            + ",".join(row()) + "\n"
        ).encode("utf-8")
        parsed = gstr2b.parse_gstr2b(content, "titles.csv")
        self.assertEqual(parsed.parser_kind, "canonical")
        self.assertEqual(parsed.accepted_rows, 1)
        self.assertEqual(parsed.rows[0].row_number, 1)

    def test_two_complete_header_rows_are_ambiguous(self):
        header = (
            "supplier_gstin,invoice_number,invoice_date,tax_period,"
            "taxable_value,cgst,sgst,igst,total_gst"
        )
        content = (
            header + "\n" + header + "\n" + ",".join(row()) + "\n"
        ).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            gstr2b.parse_gstr2b(content, "double-header.csv")

    def test_incomplete_canonical_template_stays_fatal(self):
        header = (
            "supplier_gstin,invoice_number,invoice_date,tax_period,"
            "taxable_value,cgst,sgst,igst"
        )
        with self.assertRaisesRegex(ValueError, "missing required column"):
            gstr2b.parse_gstr2b(
                csv_bytes([row()[:-1]], header=header), "incomplete.csv"
            )


if __name__ == "__main__":
    unittest.main()
