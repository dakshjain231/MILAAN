"""Regressions for defects found while testing the primary UI in a browser.

1. Purchase-invoice upload: a GSTR-2B-shaped file is rejected with one
   actionable error and nothing is imported; the shipped demo sample imports.
2. Reconciliation: displayed GSTR-2B values and differences come from the
   stored 2B row and agree with the engine's status and tolerance.
3. Supplier follow-ups: "Resolved" requires later-statement evidence; a
   skipped draft is not resolved, and the issue type comes from the invoices.

Every test uses a temporary database. Nothing is sent.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent import store

import db
import demo_workflow
import followups
import gstr2b
import invoice_import
import milaan_web
import reconciliation

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "agent" / "web" / "primary.html"
DEMO_INVOICES_CSV = ROOT / "demo" / "demo_purchase_invoices.csv"

# Header of the external "demo_invoice_reference.csv" reference pack, which
# is a GSTR-2B layout rather than a purchase register.
GSTR2B_SHAPED_INVOICE_FILE = (
    "﻿supplier_gstin,invoice_number,invoice_date,tax_period,taxable_value,"
    "cgst,sgst,igst,total_tax,expected_test_case\n"
    "08ABCDE1234F1Z0,DEMO-101,2026-09-03,2026-09,10000.00,900.00,900.00,"
    "0.00,1800.00,Exact match\n"
).encode("utf-8")


class IsolatedAppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="milaan-regressions-"))
        self.old = {key: os.environ.get(key) for key in ("DATABASE_PATH", "AGENT_DB_PATH")}
        os.environ["DATABASE_PATH"] = str(self.tmp / "milaan.db")
        os.environ["AGENT_DB_PATH"] = str(self.tmp / "agent.db")
        store.reset_initialized()
        self.client = milaan_web.create_app().test_client()

    def tearDown(self):
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        store.reset_initialized()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def upload(self, path: str, content: bytes, name: str):
        return self.client.post(
            path,
            data={"file": (io.BytesIO(content), name)},
            content_type="multipart/form-data",
        )

    def invoice_count(self) -> int:
        with db.db() as conn:
            return conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]


# --- Bug 1: purchase-invoice upload ----------------------------------------

class InvoiceUploadTests(IsolatedAppTestCase):
    def test_gstr2b_shaped_file_gets_one_actionable_error_and_imports_nothing(self):
        for path in ("/api/invoices/preview", "/api/invoices/import"):
            response = self.upload(path, GSTR2B_SHAPED_INVOICE_FILE, "demo_invoice_reference.csv")
            self.assertEqual(response.status_code, 400, path)
            error = response.get_json()["error"]
            self.assertIn(
                "missing required column(s): supplier_name, gst_rate, invoice_total", error
            )
            # Actionable: says what the file looks like and where it belongs.
            self.assertIn("GSTR-2B", error)
            self.assertIn("template", error.lower())
        self.assertEqual(self.invoice_count(), 0)

    def test_missing_column_message_without_gstr2b_hint_for_other_files(self):
        content = b"supplier_name,invoice_number\nA,1\n"
        with self.assertRaises(ValueError) as caught:
            invoice_import.parse_invoice_file(content, "x.csv")
        self.assertIn("missing required column(s)", str(caught.exception))
        self.assertNotIn("GSTR-2B Import", str(caught.exception))

    def test_shipped_demo_purchase_file_matches_the_supported_schema(self):
        content = DEMO_INVOICES_CSV.read_bytes()
        parsed = invoice_import.parse_invoice_file(content, DEMO_INVOICES_CSV.name)
        accepted = [row.invoice_number for row in parsed.rows if row.accepted]
        rejected = {row.invoice_number: row for row in parsed.rows if not row.accepted}
        self.assertEqual(accepted, ["DEMO-101", "DEMO-102", "DEMO-103", "DEMO-104", "DEMO-105"])
        # DEMO-106 is deliberately inconsistent (CGST+SGST != total GST).
        self.assertEqual(list(rejected), ["DEMO-106"])

        preview = self.upload("/api/invoices/preview", content, DEMO_INVOICES_CSV.name)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.get_json()["accepted"], 5)
        self.assertEqual(len(preview.get_json()["rejected"]), 1)
        self.assertEqual(self.invoice_count(), 0, "preview must not write")

        imported = self.upload("/api/invoices/import", content, DEMO_INVOICES_CSV.name)
        self.assertEqual(imported.status_code, 200)
        body = imported.get_json()
        self.assertEqual((body["imported"], body["rejected"], body["duplicates"]), (5, 1, 0))
        with db.db() as conn:
            numbers = {r[0] for r in conn.execute("SELECT invoice_number FROM invoices")}
        self.assertNotIn("DEMO-106", numbers)

    def test_file_where_every_row_is_invalid_imports_nothing(self):
        content = invoice_import.template_csv().replace(",180.00,1180.00,", ",999.00,1180.00,")
        response = self.upload("/api/invoices/import", content.encode(), "bad.csv")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["imported"], 0)
        self.assertEqual(response.get_json()["rejected"], 1)
        self.assertEqual(self.invoice_count(), 0)


# --- Bug 2: reconciliation values and status --------------------------------

def _invoice(**overrides):
    base = dict(
        invoice_id=1, supplier_gstin="27AAACS1234A1Z2", invoice_number="INV-DEMO-001",
        invoice_date="2026-03-05", tax_period="2026-03", total_gst=225_000,
        validation_status="valid", taxable_value=1_250_000,
        cgst=112_500, sgst=112_500, igst=0,
    )
    base.update(overrides)
    return reconciliation.InvoiceRecord(**base)


def _row(**overrides):
    base = dict(
        row_id=1, supplier_gstin="27AAACS1234A1Z2", invoice_number="INV-DEMO-001",
        invoice_date="2026-03-05", tax_period="2026-03", total_gst=225_000,
        validation_status="valid", taxable_value=1_250_000,
        cgst=112_500, sgst=112_500, igst=0,
    )
    base.update(overrides)
    return reconciliation.Gstr2bRecord(**base)


class ReconciliationToleranceTests(unittest.TestCase):
    def result(self, invoice, row, tolerance=0):
        config = reconciliation.ReconciliationConfig(amount_tolerance_paise=tolerance)
        return reconciliation.reconcile_invoices([invoice], [row], config=config)[0]

    def test_inv_demo_001_identical_values_match_with_zero_difference(self):
        result = self.result(_invoice(), _row())
        self.assertEqual(result.match_status, "MATCHED")
        self.assertEqual(result.amount_difference_paise, 0)

    def test_taxable_value_difference_is_never_matched(self):
        # 2B taxable ₹11,250 against invoice ₹12,500, identical GST.
        result = self.result(_invoice(), _row(taxable_value=1_125_000))
        self.assertEqual(result.match_status, "AMOUNT_MISMATCH")
        self.assertIn("TAXABLE_VALUE_MISMATCH", result.reason_codes)

    def test_gst_difference_boundaries_respect_tolerance(self):
        def gst(total):
            return dict(total_gst=total, cgst=None, sgst=None, igst=None)

        at_limit = self.result(_invoice(**gst(225_100)), _row(**gst(225_000)), tolerance=100)
        self.assertEqual(at_limit.match_status, "MATCHED")
        self.assertEqual(at_limit.match_method, "exact_tolerance")
        self.assertEqual(at_limit.amount_difference_paise, 100)

        beyond = self.result(_invoice(**gst(225_101)), _row(**gst(225_000)), tolerance=100)
        self.assertEqual(beyond.match_status, "AMOUNT_MISMATCH")

        zero_tolerance = self.result(_invoice(**gst(225_001)), _row(**gst(225_000)), tolerance=0)
        self.assertEqual(zero_tolerance.match_status, "AMOUNT_MISMATCH")

        negative = self.result(_invoice(**gst(224_900)), _row(**gst(225_000)), tolerance=100)
        self.assertEqual(negative.match_status, "MATCHED")
        self.assertEqual(negative.amount_difference_paise, -100)


class ReconciliationReadModelTests(IsolatedAppTestCase):
    def results_by_number(self):
        state = self.client.get("/api/state").get_json()
        return {row["number"]: row for row in state["reconciliation"]["results"]}, state

    def test_values_come_from_the_stored_2b_row(self):
        demo_workflow.load_demo_workspace()
        rows, state = self.results_by_number()

        matched = rows["INV-DEMO-001"]
        self.assertEqual(matched["status"], "MATCHED")
        self.assertEqual(matched["invoice_taxable"], 12500.0)
        self.assertEqual(matched["invoice_total"], 14750.0)
        self.assertEqual(matched["twob_taxable"], 12500.0)
        self.assertEqual(matched["twob_gst"], 2250.0)
        self.assertEqual(matched["taxable_difference"], 0.0)
        self.assertEqual(matched["difference"], 0.0)
        self.assertEqual(matched["date"], "2026-03-05")

        mismatch = rows["INV-DEMO-101"]
        self.assertEqual(mismatch["status"], "AMOUNT_MISMATCH")
        self.assertEqual(mismatch["twob_gst"], 8150.0)
        self.assertEqual(mismatch["difference"], -50.0)
        self.assertEqual(mismatch["taxable_difference"], 0.0)

        missing = rows["INV-DEMO-002"]
        self.assertEqual(missing["status"], "MISSING_FROM_2B")
        self.assertIsNone(missing["twob_taxable"])
        self.assertIsNone(missing["twob_gst"])
        self.assertIsNone(missing["taxable_difference"])
        self.assertIsNone(missing["difference"])

        self.assertEqual(state["reconciliation"]["latest"]["tolerance"], 0)
        run_detail = self.client.get(
            f"/api/reconciliations/{state['reconciliation']['latest']['id']}"
        ).get_json()
        self.assertEqual(
            {row["number"]: row["twob_taxable"] for row in run_detail["results"]},
            {number: row["twob_taxable"] for number, row in rows.items()},
        )


# --- Bug 3: supplier follow-up status ---------------------------------------

class FollowupWorkflowStatusTests(unittest.TestCase):
    def test_status_rules(self):
        status = followups.followup_workflow_status
        self.assertEqual(status("DRAFT", "AWAITING_LATER_EVIDENCE"), "PENDING")
        self.assertEqual(status("NEEDS_REVIEW", "AWAITING_LATER_EVIDENCE"), "NEEDS_REVIEW")
        self.assertEqual(status("APPROVED", "AWAITING_LATER_EVIDENCE"), "IN_PROGRESS")
        self.assertEqual(status("APPROVED", "PARTIAL"), "IN_PROGRESS")
        self.assertEqual(status("APPROVED", "DISPUTED"), "IN_PROGRESS")
        self.assertEqual(status("APPROVED", "NOT_FOUND"), "IN_PROGRESS")
        self.assertEqual(status("APPROVED", "VERIFIED"), "RESOLVED")
        # Skipping closes the follow-up without resolving it ...
        self.assertEqual(status("SKIPPED", "AWAITING_LATER_EVIDENCE"), "SKIPPED")
        # ... but verified later-statement evidence is preserved.
        self.assertEqual(status("SKIPPED", "VERIFIED"), "RESOLVED")
        with self.assertRaises(ValueError):
            status("SENT", "VERIFIED")


class FollowupStatusApiTests(IsolatedAppTestCase):
    def setUp(self):
        super().setUp()
        self.case = demo_workflow.load_demo_workspace()
        generated = self.client.post(
            "/api/followups/generate", json={"batch_id": self.case["batch_id"]}
        )
        self.assertEqual(generated.status_code, 200)

    def drafts(self):
        rows = self.client.get("/api/state").get_json()["followups"]
        return {row["supplier"]: row for row in rows}

    def traders(self):
        return self.drafts()["(Synthetic) Demo Traders Pvt Ltd"]

    def later_batch_with_traders_invoices(self) -> int:
        demo = {(row[1]): row for row in db.DEMO_INVOICES}
        lines = [",".join(demo_workflow.DEMO_HEADER)]
        for number in ("INV-DEMO-002", "INV-DEMO-003"):
            inv = demo[number]
            lines.append(",".join([
                inv[0], inv[1], inv[2], inv[3],
                demo_workflow._rupees(inv[4]), demo_workflow._rupees(inv[6]),
                demo_workflow._rupees(inv[7]), demo_workflow._rupees(inv[8]),
                demo_workflow._rupees(inv[9]),
            ]))
        content = ("\n".join(lines) + "\n").encode()
        saved = db.save_gstr2b_import(gstr2b.parse_gstr2b(content, "later.csv"))
        return saved["import_id"]

    def test_new_drafts_are_pending_with_issue_from_their_invoices(self):
        for draft in self.drafts().values():
            self.assertEqual(draft["status"], "DRAFT")
            self.assertEqual(draft["workflow_status"], "PENDING")
            self.assertEqual(draft["recovery_status"], "AWAITING_LATER_EVIDENCE")
            self.assertEqual(draft["issues"], ["MISSING_FROM_2B"])
            self.assertIsNone(draft["last_followup"])

    def test_skipped_draft_is_not_resolved(self):
        draft = self.traders()
        self.assertEqual(self.client.post(f"/api/followups/{draft['id']}/skip").status_code, 200)
        draft = self.traders()
        self.assertEqual(draft["status"], "SKIPPED")
        self.assertEqual(draft["workflow_status"], "SKIPPED")
        self.assertNotEqual(draft["workflow_status"], "RESOLVED")

    def test_approval_alone_is_in_progress_and_evidence_resolves(self):
        draft = self.traders()
        approved = self.client.post(f"/api/followups/{draft['id']}/approve", json={"confirmed": True})
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(self.traders()["workflow_status"], "IN_PROGRESS")

        later = self.later_batch_with_traders_invoices()
        verified = self.client.post(
            f"/api/followups/{draft['id']}/verify", json={"later_batch_id": later}
        )
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(self.traders()["recovery_status"], "VERIFIED")
        self.assertEqual(self.traders()["workflow_status"], "RESOLVED")

        # A previously resolved follow-up stays resolved if later skipped.
        self.assertEqual(self.client.post(f"/api/followups/{draft['id']}/skip").status_code, 200)
        self.assertEqual(self.traders()["workflow_status"], "RESOLVED")

    def test_drafts_expose_their_invoice_tax_periods(self):
        drafts = self.drafts()
        self.assertEqual(drafts["(Synthetic) Demo Traders Pvt Ltd"]["periods"], ["2026-03", "2026-04"])
        self.assertEqual(drafts["(Synthetic) Demo Bearings Co"]["periods"], ["2026-03"])

    def test_partial_evidence_is_not_resolved(self):
        draft = self.traders()
        self.client.post(f"/api/followups/{draft['id']}/approve", json={"confirmed": True})
        demo = {row[1]: row for row in db.DEMO_INVOICES}
        inv = demo["INV-DEMO-002"]
        content = (
            ",".join(demo_workflow.DEMO_HEADER) + "\n" + ",".join([
                inv[0], inv[1], inv[2], inv[3],
                demo_workflow._rupees(inv[4]), demo_workflow._rupees(inv[6]),
                demo_workflow._rupees(inv[7]), demo_workflow._rupees(inv[8]),
                demo_workflow._rupees(inv[9]),
            ]) + "\n"
        ).encode()
        later = db.save_gstr2b_import(gstr2b.parse_gstr2b(content, "partial.csv"))["import_id"]
        self.client.post(f"/api/followups/{draft['id']}/verify", json={"later_batch_id": later})
        self.assertEqual(self.traders()["recovery_status"], "PARTIAL")
        self.assertEqual(self.traders()["workflow_status"], "IN_PROGRESS")


# --- Frontend mapping (runs the page's own JavaScript in Node) --------------

def _extract_function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    depth = 0
    for index in range(source.index("{", start), len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated function {name}")


class PageScriptTestCase(unittest.TestCase):
    """Runs pure helper functions extracted from primary.html in Node."""

    FUNCTIONS = ("reconRowView", "reconStatusLabel", "followupRowView", "followupStatusLabel",
                 "issueLabel", "periodOf", "periodLabel", "uniqueOptions", "reconFilterOptions",
                 "applyReconFilters", "followupFilterOptions", "applyFollowupFilters",
                 "dashboardModel", "buildLiveMonthlyTrend", "buildLiveTopVendors", "compactInr",
                 "applyInvoiceFilters", "recentlyUploaded", "gstrRowStatus", "applyGstrFilters")
    PRELUDE = ""

    def run_js(self, expression: str):
        html = PAGE.read_text(encoding="utf-8")
        code = self.PRELUDE + "\n".join(_extract_function(html, name) for name in self.FUNCTIONS)
        code += "\nfunction fmtDate(v){return v || '—';}\n"
        code += f"\nprocess.stdout.write(JSON.stringify({expression}));"
        out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)


@unittest.skipIf(shutil.which("node") is None, "Node.js is not installed")
class FrontendMappingTests(PageScriptTestCase):

    def test_no_fabricated_2b_value_multiplier(self):
        html = PAGE.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"twob_gst\s*\*\s*5", html))
        self.assertIsNone(re.search(r"invoice_gst\s*\*\s*5", html))

    def test_matched_row_shows_equal_values_and_zero_difference(self):
        view = self.run_js("""reconRowView({id:7, invoice_id:1, number:'INV-DEMO-001',
            supplier:'S', gstin:'G', date:'2026-03-05', status:'MATCHED',
            invoice_taxable:12500, invoice_gst:2250, twob_taxable:12500, twob_gst:2250,
            taxable_difference:0, difference:0, reasons:[]})""")
        self.assertEqual((view["reg_val"], view["twob_val"], view["diff_val"]), (12500, 12500, 0))
        self.assertEqual((view["reg_gst"], view["twob_gst"], view["diff_gst"]), (2250, 2250, 0))

    def test_missing_row_has_no_invented_2b_values(self):
        view = self.run_js("""reconRowView({id:8, number:'X', status:'MISSING_FROM_2B',
            invoice_taxable:100, invoice_gst:18, twob_taxable:null, twob_gst:null,
            taxable_difference:null, difference:null})""")
        self.assertIsNone(view["twob_val"])
        self.assertIsNone(view["twob_gst"])
        self.assertIsNone(view["diff_val"])
        self.assertIsNone(view["diff_gst"])

    def test_every_engine_status_has_its_own_label(self):
        labels = self.run_js("""['MATCHED','AMOUNT_MISMATCH','MISSING_FROM_2B','NEEDS_REVIEW',
            'NOT_ELIGIBLE','ITC_UNAVAILABLE','IN_2B_NOT_IN_BOOKS'].map(reconStatusLabel)""")
        self.assertEqual(len(set(labels)), 7, labels)
        self.assertEqual(labels[0], "Matched")
        self.assertEqual(labels[1], "Amount mismatch")
        self.assertEqual(labels[2], "Missing from 2B")

    def test_followup_status_and_issue_come_from_the_backend(self):
        rows = self.run_js("""[
            followupRowView({id:1, supplier:'A', gstin:'G', invoices:2, gst:10,
                status:'SKIPPED', workflow_status:'SKIPPED', issues:['MISSING_FROM_2B'],
                reasons:[], last_followup:null, next_followup:null}),
            followupRowView({id:2, supplier:'B', gstin:'G', invoices:1, gst:10,
                status:'APPROVED', workflow_status:'RESOLVED', issues:['MISSING_FROM_2B'],
                reasons:[], last_followup:'2026-10-01 10:00:00', next_followup:null}),
            followupRowView({id:3, supplier:'C', gstin:'G', invoices:1, gst:10,
                status:'DRAFT', workflow_status:'PENDING', issues:[], reasons:[]})
        ]""")
        self.assertEqual([row["status"] for row in rows], ["Skipped", "Resolved", "Pending"])
        self.assertEqual(rows[0]["issue"], "Missing from 2B")
        self.assertNotEqual(rows[2]["issue"], "Missing from 2B")


@unittest.skipIf(shutil.which("node") is None, "Node.js is not installed")
class FrontendFilterTests(PageScriptTestCase):
    RECON = """[
        reconRowView({id:1, number:'A', supplier:'S1', gstin:'G1', date:'2026-03-05', period:'2026-03', status:'MATCHED'}),
        reconRowView({id:2, number:'B', supplier:'S2', gstin:'G2', date:'2026-03-12', period:'2026-03', status:'AMOUNT_MISMATCH'}),
        reconRowView({id:3, number:'C', supplier:'S1', gstin:'G1', date:'2026-04-02', period:'2026-04', status:'MISSING_FROM_2B'}),
        {id:4, number:'D', supplier:'S3', gstin:'G3', date:'2026-07-03', status:'MATCHED'}
    ]"""
    DRAFTS = """[
        followupRowView({id:1, supplier:'S1', gstin:'G1', invoices:2, gst:1, status:'SKIPPED',
            workflow_status:'SKIPPED', issues:['MISSING_FROM_2B'], periods:['2026-03','2026-04']}),
        followupRowView({id:2, supplier:'S2', gstin:'G2', invoices:1, gst:1, status:'DRAFT',
            workflow_status:'PENDING', issues:['MISSING_FROM_2B'], periods:['2026-03']})
    ]"""

    def ids(self, expression):
        return [row["id"] for row in self.run_js(expression)]

    def test_recon_filters_combine_and_empty_filters_keep_everything(self):
        rows = self.RECON
        self.assertEqual(self.ids(f"applyReconFilters({rows}, {{}})"), [1, 2, 3, 4])
        self.assertEqual(self.ids(f"applyReconFilters({rows}, {{period:'2026-03'}})"), [1, 2])
        self.assertEqual(self.ids(f"applyReconFilters({rows}, {{gstin:'G1'}})"), [1, 3])
        self.assertEqual(self.ids(f"applyReconFilters({rows}, {{supplier:'S1', status:'MISSING_FROM_2B'}})"), [3])
        self.assertEqual(self.ids(f"applyReconFilters({rows}, {{status:'MATCHED'}})"), [1, 4])
        # Rows without a stored period fall back to their invoice date.
        self.assertEqual(self.ids(f"applyReconFilters({rows}, {{period:'2026-07'}})"), [4])
        self.assertEqual(self.ids(f"applyReconFilters({rows}, {{period:'2026-03', status:'MISSING_FROM_2B'}})"), [])

    def test_recon_filter_options_come_from_the_data(self):
        options = self.run_js(f"reconFilterOptions({self.RECON})")
        self.assertEqual([o["value"] for o in options["periods"]], ["2026-07", "2026-04", "2026-03"])
        self.assertEqual(options["periods"][2]["label"], "Mar 2026")
        self.assertEqual([o["value"] for o in options["gstins"]], ["G1", "G2", "G3"])
        self.assertEqual([o["value"] for o in options["suppliers"]], ["S1", "S2", "S3"])
        self.assertEqual(
            [(o["value"], o["label"]) for o in options["statuses"]],
            [("MATCHED", "Matched"), ("AMOUNT_MISMATCH", "Amount mismatch"),
             ("MISSING_FROM_2B", "Missing from 2B")],
        )

    def test_followup_filters(self):
        rows = self.DRAFTS
        self.assertEqual(self.ids(f"applyFollowupFilters({rows}, {{}})"), [1, 2])
        self.assertEqual(self.ids(f"applyFollowupFilters({rows}, {{period:'2026-04'}})"), [1])
        self.assertEqual(self.ids(f"applyFollowupFilters({rows}, {{status:'Pending'}})"), [2])
        self.assertEqual(self.ids(f"applyFollowupFilters({rows}, {{status:'Resolved'}})"), [])
        self.assertEqual(self.ids(f"applyFollowupFilters({rows}, {{supplier:'S1', period:'2026-03'}})"), [1])
        options = self.run_js(f"followupFilterOptions({rows})")
        self.assertEqual([o["value"] for o in options["periods"]], ["2026-04", "2026-03"])
        self.assertEqual([o["value"] for o in options["suppliers"]], ["S1", "S2"])

    def test_apply_filters_never_runs_reconciliation(self):
        html = PAGE.read_text(encoding="utf-8")
        body = _extract_function(html, "renderReconciliation")
        apply_button = re.search(r'<button(?:(?!<button).)*?Apply filters', body, re.S)
        self.assertIsNotNone(apply_button)
        self.assertIn("applyFilters('reconciliation')", apply_button.group(0))
        self.assertNotIn("executeReconcile", apply_button.group(0))
        self.assertRegex(body, r'onclick="executeReconcile\(\)"[^>]*>[\s\S]*?Run reconciliation')
        followups_body = _extract_function(html, "renderFollowups")
        self.assertIn("applyFilters('followups')", followups_body)


@unittest.skipIf(shutil.which("node") is None, "Node.js is not installed")
class FrontendLiveDataTests(PageScriptTestCase):
    """Live pages derive every figure from backend rows; nothing is invented."""

    PRELUDE = """
    const S = {dashMetric: 'eligible', filters: {dashboard: {}}, data: {
      invoices: [
        {id:1, number:'A', supplier:'S1', gstin:'G1', date:'2026-03-05', period:'2026-03', gst:2250, total:14750, validation:'valid', created:'2026-10-24 06:00:00'},
        {id:2, number:'B', supplier:'S2', gstin:'G2', date:'2026-03-12', period:'2026-03', gst:8100, total:53100, validation:'valid', created:'2026-10-01 06:00:00'},
        {id:3, number:'C', supplier:'S1', gstin:'G1', date:'2026-04-02', period:'2026-04', gst:1500, total:31500, validation:'valid', created:'2026-10-24 06:00:00'},
        {id:4, number:'D', supplier:'S3', gstin:'G3', date:'2026-05-01', period:'2026-04', gst:330, total:3080, validation:'invalid', created:null}
      ],
      reconciliation: {latest: {id: 2}, results: [
        {id:10, invoice_id:1, supplier:'S1', gstin:'G1', period:'2026-03', status:'MATCHED', invoice_gst:2250},
        {id:11, invoice_id:2, supplier:'S2', gstin:'G2', period:'2026-03', status:'AMOUNT_MISMATCH', invoice_gst:8100},
        {id:12, invoice_id:3, supplier:'S1', gstin:'G1', period:'2026-04', status:'MISSING_FROM_2B', invoice_gst:1500},
        {id:13, invoice_id:4, supplier:'S3', gstin:'G3', period:'2026-04', status:'NOT_ELIGIBLE', invoice_gst:330},
        {id:14, invoice_id:null, supplier:'', gstin:'G9', period:'2026-03', status:'IN_2B_NOT_IN_BOOKS', invoice_gst:null}
      ]}
    }};
    """

    def model(self, filters="{}"):
        return self.run_js(f"(S.filters.dashboard = {filters}, (({{trend, vendors, ...rest}}) => ({{...rest, invsAll: undefined, invs: undefined, results: undefined, resultsAll: undefined, trend, vendors}}))(dashboardModel()))")

    def test_eligible_itc_excludes_invalid_invoices_and_totals_agree(self):
        m = self.model()
        self.assertEqual(m["eligibleGst"], 2250 + 8100 + 1500)
        self.assertEqual(m["eligibleCount"], 3)
        self.assertEqual((m["ineligibleGst"], m["ineligibleCount"]), (330, 1))
        self.assertEqual(m["matchedGst"] + m["mismatchGst"] + m["missingGst"], m["eligibleGst"])
        # Rows only in 2B are not purchase invoices and stay out of the donut.
        self.assertEqual(m["counts"]["total"], 4)
        self.assertEqual(m["counts"]["ineligible"], 1)

    def test_dashboard_filters_change_every_figure(self):
        m = self.model("{period: '2026-03'}")
        self.assertEqual((m["eligibleCount"], m["matchedCount"], m["mismatchCount"], m["missingCount"]), (2, 1, 1, 0))
        m = self.model("{supplier: 'S1', status: 'MISSING_FROM_2B'}")
        self.assertEqual((m["eligibleCount"], m["missingCount"], m["matchedCount"]), (1, 1, 0))
        m = self.model("{gstin: 'nope'}")
        self.assertEqual((m["eligibleGst"], m["counts"]["total"], m["trend"]), (0, 0, []))

    def test_trend_and_vendor_charts_use_only_real_eligible_data(self):
        trend = self.run_js("buildLiveMonthlyTrend(S.data.invoices, 'eligible')")
        self.assertEqual(trend, [{"m": "Mar 2026", "v": 10350}, {"m": "Apr 2026", "v": 1500}, {"m": "May 2026", "v": 0}])
        total = self.run_js("buildLiveMonthlyTrend(S.data.invoices, 'total')")
        self.assertEqual([t["v"] for t in total], [67850, 31500, 3080])
        self.assertEqual(self.run_js("buildLiveMonthlyTrend([], 'eligible')"), [])
        vendors = self.run_js("buildLiveTopVendors(S.data.invoices.filter(i => i.validation === 'valid'))")
        self.assertEqual(vendors, [{"name": "S2", "amount": 8100}, {"name": "S1", "amount": 3750}])
        self.assertEqual(self.run_js("[compactInr(180000), compactInr(45000), compactInr(900), compactInr(25000000)]"),
                         ["₹1.8L", "₹45K", "₹900", "₹2.5Cr"])

    def test_invoice_filters_and_recent_uploads(self):
        ids = self.run_js("applyInvoiceFilters(S.data.invoices, {period:'2026-04', status:'invalid'}).map(i => i.id)")
        self.assertEqual(ids, [4])
        ids = self.run_js("applyInvoiceFilters(S.data.invoices, {supplier:'S1'}).map(i => i.id)")
        self.assertEqual(ids, [1, 3])
        recent = self.run_js("recentlyUploaded(S.data.invoices, Date.parse('2026-10-25T00:00:00Z'))")
        self.assertEqual(recent, 2)

    def test_rejected_gstr2b_rows_are_never_labelled_imported(self):
        labels = self.run_js("['valid','invalid','needs_review'].map(v => gstrRowStatus(v).label)")
        self.assertEqual(labels, ["Imported", "Rejected", "Check required"])
        ids = self.run_js("applyGstrFilters([{id:1,batch:2,period:'2026-09',validation:'invalid'},"
                          "{id:2,batch:1,period:'2026-03',validation:'valid'}], {status:'invalid'}).map(r => r.id)")
        self.assertEqual(ids, [1])
        ids = self.run_js("applyGstrFilters([{id:1,batch:2,period:'2026-09',validation:'invalid'},"
                          "{id:2,batch:1,period:'2026-03',validation:'valid'}], {batch:'1'}).map(r => r.id)")
        self.assertEqual(ids, [2])


class FrontendHonestyTests(unittest.TestCase):
    """Strings that described behaviour the app does not have must not return."""

    def test_no_fake_or_inaccurate_ui_claims(self):
        html = PAGE.read_text(encoding="utf-8")
        for text in ("Filters applied to dashboard", "Only JSON files (max 50 MB)",
                     "(PDF/Excel)", "Reconciliation completed.\", \"ok\"", "Draft approved.\", \"ok\"",
                     "Agarwal Cement\",\n        contact_phone", "July 2026 imported' : 'No GSTR-2B",
                     'id="tax-period-label">July 2026'):
            self.assertNotIn(text, html, text)
        self.assertIn("GST portal JSON or Excel (.xlsx), or CSV · max 25 MB", html)
        self.assertIn("Upload purchase invoices (CSV/Excel).", html)


if __name__ == "__main__":
    unittest.main()
