"""Integration tests for the primary custom MILAAN UI and its APIs."""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import store

import demo_workflow
import invoice_import
import milaan_web
import webjs


class PrimaryUITestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="milaan-primary-ui-"))
        self.old_db = os.environ.get("DATABASE_PATH")
        self.old_agent = os.environ.get("AGENT_DB_PATH")
        os.environ["DATABASE_PATH"] = str(self.tmp / "milaan.db")
        os.environ["AGENT_DB_PATH"] = str(self.tmp / "agent.db")
        store.reset_initialized()
        self.app = milaan_web.create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        if self.old_db is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = self.old_db
        if self.old_agent is None:
            os.environ.pop("AGENT_DB_PATH", None)
        else:
            os.environ["AGENT_DB_PATH"] = self.old_agent
        store.reset_initialized()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seed(self) -> dict:
        return demo_workflow.load_demo_workspace()

    def upload(self, path: str, content: bytes, name: str, **fields):
        data = {"file": (io.BytesIO(content), name), **fields}
        return self.client.post(path, data=data, content_type="multipart/form-data")

    def test_home_serves_the_custom_workspace(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        for label in (
            "MILAAN",
            "Dashboard",
            "Bills",
            "GSTR-2B",
            "Reconciliation",
            "Exceptions",
            "Suppliers",
            "Supplier follow-ups",
            "Conversation",
            "Agent trace",
            "Audit &amp; settings",
            "Download accountant workbook",
            "Recovery status",
        ):
            self.assertIn(label, body)
        self.assertNotIn("Streamlit", body)
        self.assertEqual(self.client.get("/_stcore/health").status_code, 404)

    def test_state_uses_real_data_and_masks_phones(self):
        self.seed()
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["summary"]["invoices"], 6)
        self.assertEqual(data["summary"]["gstr2b_batches"], 1)
        self.assertEqual(data["summary"]["latest_run"]["matched"], 1)
        rendered = response.get_data(as_text=True)
        self.assertNotIn("9900000011", rendered)
        self.assertNotIn("9900000022", rendered)
        self.assertIn("••", str(data))

    def test_state_exposes_database_diagnostics_without_credentials(self):
        self.seed()
        data = self.client.get("/api/state").get_json()
        database = data["database"]
        self.assertTrue(database["exists"])
        self.assertFalse(database["demo"])
        self.assertEqual(
            Path(database["path"]).resolve(), (self.tmp / "milaan.db").resolve()
        )
        self.assertEqual(
            Path(database["agent_path"]).resolve(),
            (self.tmp / "agent.db").resolve(),
        )
        self.assertNotIn("test_recipient", database)

    def test_state_rows_cover_every_gstr2b_batch(self):
        case = self.seed()
        again = self.upload(
            "/api/gstr2b/import",
            demo_workflow.demo_gstr2b_content(),
            "again.csv",
            force="true",
        )
        self.assertEqual(again.status_code, 200)
        rows = self.client.get("/api/state").get_json()["gstr2b"]["rows"]
        batches = {row["batch"] for row in rows}
        self.assertIn(case["batch_id"], batches)
        self.assertIn(again.get_json()["import_id"], batches)

    def test_reconciliation_run_detail_and_missing_run(self):
        case = self.seed()
        run_id = self.client.get("/api/state").get_json()["reconciliation"][
            "latest"
        ]["id"]
        response = self.client.get(f"/api/reconciliations/{run_id}")
        self.assertEqual(response.status_code, 200)
        detail = response.get_json()
        self.assertEqual(detail["run"]["id"], run_id)
        self.assertEqual(detail["run"]["batch"], case["batch_id"])
        self.assertEqual(len(detail["results"]), 6)
        self.assertEqual(
            self.client.get("/api/reconciliations/999999").status_code, 404
        )

    def test_followup_eligibility_is_read_only_and_reports_exclusions(self):
        case = self.seed()
        before = self.client.get("/api/state").get_json()["followups"]
        response = self.client.get(
            f"/api/followups/eligibility/{case['batch_id']}"
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["run"]["batch"], case["batch_id"])
        self.assertGreaterEqual(payload["eligible_invoice_count"], 1)
        self.assertTrue(payload["plans"])
        self.assertTrue(all("phone" in plan for plan in payload["plans"]))
        self.assertEqual(
            self.client.get("/api/state").get_json()["followups"], before
        )
        self.assertEqual(
            self.client.get("/api/followups/eligibility/999999").status_code,
            404,
        )

    def test_whatsapp_status_and_demo_send_guards(self):
        config = webjs.WebJsConfig(
            enabled=True,
            base_url="http://127.0.0.1:3000",
            token="x" * 32,
            test_recipient="+919900000011",
        )
        status = {
            "reachable": True,
            "status": "ready",
            "ready": True,
            "qr_available": False,
            "phone_masked": "+91••••••0011",
            "problems": [],
        }
        with (
            patch("milaan_web.webjs.load_webjs_config", return_value=config),
            patch("milaan_web.webjs.service_status", return_value=status),
        ):
            response = self.client.get("/api/whatsapp/status")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"]["status"], "ready")
        self.assertEqual(body["config"]["test_recipient_masked"], "…0011")
        self.assertNotIn("9900000011", response.get_data(as_text=True))

        missing_confirm = self.client.post(
            "/api/whatsapp/demo-send", json={"opt_in_confirmed": True}
        )
        self.assertEqual(missing_confirm.status_code, 400)
        missing_opt_in = self.client.post(
            "/api/whatsapp/demo-send", json={"confirm_send": True}
        )
        self.assertEqual(missing_opt_in.status_code, 400)

        result = {
            "outcome": "ACCEPTED",
            "provider_message_id": "demo-1",
            "error_code": None,
        }
        with (
            patch("milaan_web.webjs.load_webjs_config", return_value=config),
            patch(
                "milaan_web.webjs.demo_test_send_message", return_value=result
            ) as send,
        ):
            sent = self.client.post(
                "/api/whatsapp/demo-send",
                json={"confirm_send": True, "opt_in_confirmed": True},
            )
        self.assertEqual(sent.status_code, 200)
        self.assertEqual(sent.get_json()["outcome"], "ACCEPTED")
        send.assert_called_once()
        self.assertTrue(send.call_args.kwargs["confirmed"])
        self.assertTrue(send.call_args.kwargs["opt_in_confirmed"])

        with (
            patch("milaan_web.webjs.load_webjs_config", return_value=config),
            patch(
                "milaan_web.webjs.demo_test_send_message",
                side_effect=webjs.WebJsBlocked("blocked by test"),
            ),
        ):
            blocked = self.client.post(
                "/api/whatsapp/demo-send",
                json={"confirm_send": True, "opt_in_confirmed": True},
            )
        self.assertEqual(blocked.status_code, 409)

    def test_csv_export_neutralises_formula_cells(self):
        self.assertEqual(milaan_web._csv_cell("=1+1"), "'=1+1")
        self.assertEqual(milaan_web._csv_cell("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(milaan_web._csv_cell("-12.50"), "-12.50")
        self.assertEqual(milaan_web._csv_cell("INV-1"), "INV-1")

    def test_invoice_preview_import_duplicate_and_export(self):
        content = invoice_import.template_csv().encode("utf-8")
        preview = self.upload("/api/invoices/preview", content, "invoices.csv")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.get_json()["accepted"], 1)

        imported = self.upload("/api/invoices/import", content, "invoices.csv")
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.get_json()["imported"], 1)

        duplicate = self.upload("/api/invoices/import", content, "invoices.csv")
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.get_json()["imported"], 0)
        self.assertEqual(duplicate.get_json()["duplicates"], 1)

        export = self.client.get("/api/export/invoices.csv")
        self.assertEqual(export.status_code, 200)
        self.assertTrue(export.data.startswith(b"ID,Invoice,Supplier"))

    def test_gstr2b_duplicate_guard_and_reconciliation(self):
        case = self.seed()
        content = demo_workflow.demo_gstr2b_content()
        preview = self.upload("/api/gstr2b/preview", content, "again.csv")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(len(preview.get_json()["duplicates"]), 1)

        blocked = self.upload("/api/gstr2b/import", content, "again.csv")
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("already imported", blocked.get_json()["error"])

        forced = self.upload(
            "/api/gstr2b/import", content, "again.csv", force="true"
        )
        self.assertEqual(forced.status_code, 200)
        new_batch = forced.get_json()["import_id"]
        self.assertNotEqual(new_batch, case["batch_id"])

        reconciled = self.client.post(
            "/api/reconciliations", json={"batch_id": new_batch, "tolerance": 0}
        )
        self.assertEqual(reconciled.status_code, 200)
        self.assertEqual(reconciled.get_json()["invoice_count"], 6)
        self.assertIn("run_id", reconciled.get_json())

    def test_followup_edit_approve_and_simulation_guards(self):
        case = self.seed()
        generated = self.client.post(
            "/api/followups/generate", json={"batch_id": case["batch_id"]}
        )
        self.assertEqual(generated.status_code, 200)
        self.assertGreaterEqual(generated.get_json()["created_drafts"], 1)

        drafts = self.client.get("/api/state").get_json()["followups"]
        draft_id = drafts[0]["id"]
        detail = self.client.get(f"/api/followups/{draft_id}").get_json()
        self.assertEqual(detail["draft"]["status"], "DRAFT")
        self.assertEqual(detail["approval_problems"], [])
        self.assertEqual(
            detail["recovery"]["status"], "AWAITING_LATER_EVIDENCE"
        )

        edited = self.client.patch(
            f"/api/followups/{draft_id}",
            json={"draft_text": detail["draft"]["draft_text"] + "\nEdited."},
        )
        self.assertEqual(edited.status_code, 200)
        self.assertIn("Edited.", edited.get_json()["draft"]["draft_text"])

        refused = self.client.post(
            f"/api/followups/{draft_id}/approve", json={"confirmed": False}
        )
        self.assertEqual(refused.status_code, 400)

        approved = self.client.post(
            f"/api/followups/{draft_id}/approve", json={"confirmed": True}
        )
        self.assertEqual(approved.status_code, 200)
        detail = approved.get_json()
        self.assertEqual(detail["draft"]["status"], "APPROVED")
        self.assertTrue(detail["delivery_eligibility"]["eligible"])
        self.assertTrue(detail["draft"]["approved_text"])

        no_opt_in = self.client.post(
            f"/api/followups/{draft_id}/send",
            json={"mode": "mock", "confirm_send": True, "opt_in_confirmed": False},
        )
        self.assertEqual(no_opt_in.status_code, 400)

        sent = self.client.post(
            f"/api/followups/{draft_id}/send",
            json={"mode": "mock", "confirm_send": True, "opt_in_confirmed": True},
        )
        self.assertEqual(sent.status_code, 200)
        self.assertEqual(sent.get_json()["delivery"]["status"], "SENT")
        self.assertNotIn("990000", sent.get_data(as_text=True))

    def test_skip_draft_sends_nothing(self):
        case = self.seed()
        self.client.post(
            "/api/followups/generate", json={"batch_id": case["batch_id"]}
        )
        drafts = self.client.get("/api/state").get_json()["followups"]
        draft_id = drafts[0]["id"]
        skipped = self.client.post(f"/api/followups/{draft_id}/skip")
        self.assertEqual(skipped.status_code, 200)
        self.assertEqual(skipped.get_json()["draft"]["status"], "SKIPPED")
        self.assertEqual(self.client.get("/api/state").get_json()["deliveries"], [])

    def test_health_is_display_safe_and_cached(self):
        safe = {
            "checks": [
                {
                    "key": "operational_db",
                    "label": "Operational database",
                    "status": "ok",
                    "detail": "Ready",
                    "action": "",
                }
            ],
            "counts": {"ok": 1, "warn": 0, "error": 0},
            "healthy": True,
        }
        with patch("milaan_web.service_health.collect", return_value=safe):
            response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), safe)

    def test_exports_and_missing_records(self):
        self.seed()
        self.assertEqual(self.client.get("/api/export/audit.csv").status_code, 200)
        self.assertEqual(
            self.client.get("/api/export/followups.csv").status_code, 200
        )
        self.assertEqual(self.client.get("/api/export/nope.csv").status_code, 404)
        self.assertEqual(
            self.client.get("/api/followups/9999").status_code, 404
        )


if __name__ == "__main__":
    unittest.main()
