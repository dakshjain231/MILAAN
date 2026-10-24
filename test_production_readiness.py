"""Regressions from the production-readiness audit.

Covers GSTR-2B portal JSON parsing, purchase-invoice formats, organisation
settings validation and persistence, the protected clear-data operation,
request-origin/host protection, authentication enforcement, and complete
reconciliation evidence. Every test uses a temporary database; nothing is
sent anywhere.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash

from agent import store

import db
import demo_workflow
import gstr2b
import invoice_import
import milaan_web
import security

GSTIN_A = "27AAACS1234A1Z2"  # passes the checksum validator (demo supplier)
GSTIN_B = "29AAACB5678B1ZP"


def portal_json(b2b, **root) -> bytes:
    document = {"data": {"gstin": GSTIN_A, "rtnprd": "032026", **root, "docdata": {"b2b": b2b}}}
    return json.dumps(document).encode("utf-8")


class IsolatedApp(unittest.TestCase):
    ENV = ("DATABASE_PATH", "AGENT_DB_PATH", "APP_ENV", "MILAAN_AUTH_REQUIRED",
           "MILAAN_BASIC_AUTH_USER", "MILAAN_BASIC_AUTH_PASSWORD_HASH",
           "MILAAN_ALLOWED_HOSTS")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="milaan-readiness-"))
        self.saved = {key: os.environ.get(key) for key in self.ENV}
        for key in self.ENV:
            os.environ.pop(key, None)
        os.environ["DATABASE_PATH"] = str(self.tmp / "milaan.db")
        os.environ["AGENT_DB_PATH"] = str(self.tmp / "agent.db")
        store.reset_initialized()
        self.client = milaan_web.create_app().test_client()

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        store.reset_initialized()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def upload(self, path, content, name, **headers):
        return self.client.post(
            path, data={"file": (io.BytesIO(content), name)},
            content_type="multipart/form-data", headers=headers,
        )

    def count(self, table):
        with db.db() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# --- GSTR-2B portal JSON ------------------------------------------------------

class PortalJsonTests(unittest.TestCase):
    def test_docdata_b2b_items_are_summed_exactly_and_period_comes_from_supprd(self):
        content = portal_json([{
            "ctin": GSTIN_A, "trdnm": "Demo Traders", "supprd": "032026",
            "inv": [{
                "inum": "INV-DEMO-001", "dt": "05-03-2026", "val": 14750,
                "itcavl": "Y", "rev": "N",
                "items": [
                    {"num": 1, "rt": 18, "txval": 10000.10, "cgst": 900.01, "sgst": 900.01},
                    {"num": 2, "rt": 18, "txval": 2499.90, "cgst": 224.99, "sgst": 224.99},
                ],
            }],
        }])
        parsed = gstr2b.parse_gstr2b(content, "gstr2b.json")
        self.assertEqual(parsed.source_format, "json")
        row = parsed.rows[0]
        self.assertEqual(row.validation_status, "valid", row.errors)
        self.assertEqual(row.taxable_value, 1_250_000)
        self.assertEqual((row.cgst, row.sgst, row.igst), (112_500, 112_500, 0))
        self.assertEqual(row.total_gst, 225_000)
        self.assertEqual(row.invoice_date, "2026-03-05")
        self.assertEqual(row.tax_period, "2026-03")
        self.assertEqual(row.itc_availability, "Y")

    def test_invoice_value_is_never_used_as_taxable_value(self):
        content = portal_json([{
            "ctin": GSTIN_A, "supprd": "032026",
            "inv": [{"inum": "NO-ITEMS", "dt": "05-03-2026", "val": 14750}],
        }])
        row = gstr2b.parse_gstr2b(content, "gstr2b.json").rows[0]
        self.assertEqual(row.validation_status, "invalid")
        self.assertIsNone(row.taxable_value)
        self.assertIn("taxable_value", {issue.field for issue in row.errors})

    def test_itc_unavailable_and_reverse_charge_are_preserved(self):
        content = portal_json([{
            "ctin": GSTIN_A, "supprd": "032026",
            "inv": [{"inum": "INV-DEMO-001", "dt": "05-03-2026", "itcavl": "N",
                     "rsn": "P", "rev": "Y",
                     "items": [{"txval": 12500, "cgst": 1125, "sgst": 1125}]}],
        }])
        row = gstr2b.parse_gstr2b(content, "gstr2b.json").rows[0]
        self.assertEqual(row.itc_availability, "N")
        self.assertEqual(row.reverse_charge, "Y")

    def test_return_period_is_a_reviewed_fallback(self):
        content = portal_json([{
            "ctin": GSTIN_A,
            "inv": [{"inum": "X-1", "dt": "05-03-2026",
                     "items": [{"txval": 100, "igst": 18}]}],
        }])
        row = gstr2b.parse_gstr2b(content, "gstr2b.json").rows[0]
        self.assertEqual(row.tax_period, "2026-03")
        self.assertIn("TAX_PERIOD_FROM_FILE", {issue.code for issue in row.warnings})

    def test_gstr2a_style_itm_det_amounts_are_supported(self):
        content = json.dumps({"b2b": [{
            "ctin": GSTIN_A, "supprd": "032026",
            "inv": [{"inum": "A-1", "idt": "05-03-2026",
                     "itms": [{"num": 1, "itm_det": {"txval": 1000, "iamt": 180}}]}],
        }]}).encode()
        row = gstr2b.parse_gstr2b(content, "gstr2a.json").rows[0]
        self.assertEqual((row.taxable_value, row.igst, row.total_gst), (100_000, 18_000, 18_000))

    def test_malformed_structures_fail_as_file_errors(self):
        cases = {
            "b2b not a list": portal_json({"ctin": GSTIN_A}),
            "supplier not an object": portal_json(["oops"]),
            "inv not a list": portal_json([{"ctin": GSTIN_A, "inv": {"inum": 1}}]),
            "item not an object": portal_json([{"ctin": GSTIN_A, "inv": [{"inum": "1", "items": [5]}]}]),
            "no invoices": portal_json([{"ctin": GSTIN_A, "inv": []}]),
        }
        for label, content in cases.items():
            with self.subTest(label), self.assertRaises(ValueError) as caught:
                gstr2b.parse_gstr2b(content, "bad.json")
            self.assertIn("Nothing was imported", str(caught.exception))
        with self.assertRaises(ValueError):
            gstr2b.parse_gstr2b(b"{not json", "bad.json")

    def test_non_numeric_amounts_are_rejected_not_coerced(self):
        content = portal_json([{
            "ctin": GSTIN_A, "supprd": "032026",
            "inv": [{"inum": "BAD", "dt": "05-03-2026", "items": [{"txval": "12,5O0", "igst": True}]}],
        }])
        row = gstr2b.parse_gstr2b(content, "gstr2b.json").rows[0]
        self.assertEqual(row.validation_status, "invalid")

    def test_flat_record_arrays_still_parse(self):
        content = json.dumps([{
            "supplier_gstin": GSTIN_A, "invoice_number": "F-1", "invoice_date": "2026-03-05",
            "tax_period": "2026-03", "taxable_value": "100.00", "cgst": "9.00",
            "sgst": "9.00", "igst": "0.00", "total_gst": "18.00",
        }]).encode()
        row = gstr2b.parse_gstr2b(content, "flat.json").rows[0]
        self.assertEqual(row.validation_status, "valid", row.errors)


class PortalJsonImportTests(IsolatedApp):
    def test_malformed_json_upload_is_a_400_and_imports_nothing(self):
        response = self.upload("/api/gstr2b/import", portal_json(["oops"]), "bad.json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.count("gstr2b_imports"), 0)
        self.assertEqual(self.count("gstr2b_rows"), 0)

    def test_itc_unavailable_json_row_is_never_matched(self):
        demo_workflow.load_demo_workspace()
        content = portal_json([{
            "ctin": GSTIN_A, "supprd": "032026",
            "inv": [{"inum": "INV-DEMO-001", "dt": "05-03-2026", "itcavl": "N",
                     "items": [{"txval": 12500, "cgst": 1125, "sgst": 1125}]}],
        }])
        imported = self.upload("/api/gstr2b/import", content, "itc.json").get_json()
        run = self.client.post("/api/reconciliations",
                               json={"batch_id": imported["import_id"], "tolerance": 0}).get_json()
        results = {r["number"]: r for r in self.client.get(
            f"/api/reconciliations/{run['run_id']}").get_json()["results"]}
        self.assertEqual(results["INV-DEMO-001"]["status"], "ITC_UNAVAILABLE")


class JsonFormatMigrationTests(IsolatedApp):
    def downgrade_to_csv_xlsx_only(self):
        """Recreate the pre-fix schema, whose CHECK rejected 'json'."""
        old_ddl = db.GSTR2B_IMPORTS_DDL.replace("'csv', 'xlsx', 'json'", "'csv', 'xlsx'").replace(
            "CREATE TABLE IF NOT EXISTS gstr2b_imports (", "CREATE TABLE gstr2b_imports_old (")
        conn = sqlite3.connect(os.environ["DATABASE_PATH"])
        conn.isolation_level = None
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        conn.execute(old_ddl)
        conn.execute("INSERT INTO gstr2b_imports_old SELECT * FROM gstr2b_imports")
        conn.execute("DROP TABLE gstr2b_imports")
        conn.execute("ALTER TABLE gstr2b_imports_old RENAME TO gstr2b_imports")
        conn.execute("COMMIT")
        conn.close()

    def test_old_schema_is_migrated_without_losing_rows_or_foreign_keys(self):
        demo_workflow.load_demo_workspace()
        self.client.post("/api/followups/generate", json={"batch_id": 1})
        before = {t: self.count(t) for t in (
            "gstr2b_imports", "gstr2b_rows", "reconciliation_runs", "followup_drafts")}
        self.downgrade_to_csv_xlsx_only()
        self.assertTrue(db._needs_gstr2b_json_format_migration(db.get_connection()))

        db.init_db()

        after = {t: self.count(t) for t in before}
        self.assertEqual(after, before)
        with db.db() as conn:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            child_sql = " ".join(r[0] for r in conn.execute(
                "SELECT sql FROM sqlite_master WHERE name IN "
                "('gstr2b_rows','reconciliation_runs','followup_drafts','followup_verifications')"))
        self.assertNotIn("gstr2b_imports_new", child_sql)
        self.assertNotIn("gstr2b_imports_old", child_sql)
        backups = list((self.tmp / "backups").glob("milaan-pre-gstr2b-json-migration-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        self.assertFalse(db._needs_gstr2b_json_format_migration(db.get_connection()))

        content = portal_json([{"ctin": GSTIN_B, "supprd": "032026", "inv": [{
            "inum": "J-1", "dt": "12-03-2026", "items": [{"txval": 100, "igst": 18}]}]}])
        imported = self.upload("/api/gstr2b/import", content, "after.json")
        self.assertEqual(imported.status_code, 200, imported.get_json())
        self.assertEqual(self.count("gstr2b_imports"), before["gstr2b_imports"] + 1)


# --- purchase-invoice formats --------------------------------------------------

class InvoiceFormatTests(unittest.TestCase):
    def test_purchase_invoices_accept_only_csv_and_xlsx(self):
        with self.assertRaisesRegex(ValueError, "CSV or XLSX"):
            invoice_import.parse_invoice_file(b"[]", "invoices.json")
        with self.assertRaisesRegex(ValueError, "CSV or XLSX"):
            invoice_import.parse_invoice_file(b"%PDF-1.4", "invoices.pdf")


# --- reconciliation evidence ---------------------------------------------------

class ReconciliationEvidenceTests(IsolatedApp):
    def test_rows_only_in_2b_are_listed_with_no_invented_register_values(self):
        demo_workflow.load_demo_workspace()
        content = (
            ",".join(demo_workflow.DEMO_HEADER) + "\n"
            f"{GSTIN_A},INV-DEMO-001,2026-03-05,2026-03,12500.00,1125.00,1125.00,0.00,2250.00\n"
            f"{GSTIN_A},ONLY-IN-2B,2026-03-09,2026-03,1000.00,90.00,90.00,0.00,180.00\n"
        ).encode()
        batch = self.upload("/api/gstr2b/import", content, "orphan.csv").get_json()["import_id"]
        self.client.post("/api/reconciliations", json={"batch_id": batch, "tolerance": 0})
        rows = self.client.get("/api/state").get_json()["reconciliation"]["results"]
        orphan = next(row for row in rows if row["status"] == "IN_2B_NOT_IN_BOOKS")
        self.assertEqual(orphan["number"], "ONLY-IN-2B")
        self.assertIsNone(orphan["invoice_id"])
        self.assertIsNone(orphan["invoice_taxable"])
        self.assertEqual(orphan["twob_taxable"], 1000.0)
        self.assertEqual(orphan["twob_gst"], 180.0)

    def test_invalid_latest_batch_error_explains_the_corrective_action(self):
        demo_workflow.load_demo_workspace()
        bad = (",".join(demo_workflow.DEMO_HEADER) + "\n"
               "08ABCDE1234F1Z5,DEMO-101,2026-09-03,2026-09,10000.00,900.00,900.00,0.00,1800.00\n").encode()
        batch = self.upload("/api/gstr2b/import", bad, "demo_gstr2b.csv").get_json()["import_id"]
        response = self.client.post("/api/reconciliations", json={"batch_id": batch, "tolerance": 0})
        self.assertEqual(response.status_code, 400)
        error = response.get_json()["error"]
        self.assertIn("no usable (valid) rows", error)
        self.assertIn("GSTR-2B Import", error)

    def test_state_exposes_2b_supplier_names_and_invoice_upload_times(self):
        demo_workflow.load_demo_workspace()
        state = self.client.get("/api/state").get_json()
        row = next(r for r in state["gstr2b"]["rows"] if r["gstin"] == GSTIN_A)
        self.assertEqual(row["supplier"], "(Synthetic) Demo Traders Pvt Ltd")
        self.assertTrue(all(invoice["created"] for invoice in state["invoices"]))


# --- organisation settings -------------------------------------------------------

class SettingsTests(IsolatedApp):
    def test_unset_settings_are_empty_not_invented(self):
        body = self.client.get("/api/settings/organization").get_json()
        self.assertEqual(body["company_name"], "")
        self.assertEqual(body["gstin"], "")
        self.assertFalse(body["configured"])

    def test_valid_settings_persist_across_a_restart(self):
        payload = {"company_name": "Acme Traders", "gstin": GSTIN_A.lower(),
                   "email": "accounts@acme.example", "phone": "+91 98765 43210"}
        saved = self.client.post("/api/settings/organization", json=payload)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.get_json()["organization"]["gstin"], GSTIN_A)
        restarted = milaan_web.create_app().test_client()
        body = restarted.get("/api/settings/organization").get_json()
        self.assertEqual(body["company_name"], "Acme Traders")
        self.assertEqual(body["gstin"], GSTIN_A)
        self.assertTrue(body["configured"])

    def test_invalid_settings_are_rejected_with_field_messages(self):
        response = self.client.post("/api/settings/organization", json={
            "company_name": "  ", "gstin": "27AAACS1234A1Z9",
            "email": "not-an-email", "phone": "call me",
        })
        self.assertEqual(response.status_code, 400)
        fields = response.get_json()["fields"]
        self.assertEqual(set(fields), {"company_name", "gstin", "email", "phone"})
        self.assertEqual(self.client.get("/api/settings/organization").get_json()["company_name"], "")

    def test_malformed_settings_bodies_are_400_not_500(self):
        for body in ([1, 2], "text", {"company_name": ["x"]}):
            response = self.client.post("/api/settings/organization", json=body)
            self.assertEqual(response.status_code, 400, body)


# --- clear data -------------------------------------------------------------------

class ClearDataTests(IsolatedApp):
    def test_confirmation_must_be_literally_true(self):
        demo_workflow.load_demo_workspace()
        for body in ({}, {"confirm": "yes"}, {"confirm": 1}, [True]):
            response = self.client.post("/api/data/clear", json=body)
            self.assertEqual(response.status_code, 400, body)
        self.assertEqual(self.count("invoices"), 6)

    def test_confirmed_clear_backs_up_first_and_keeps_the_audit_trail(self):
        demo_workflow.load_demo_workspace()
        response = self.client.post("/api/data/clear", json={"confirm": True})
        self.assertEqual(response.status_code, 200)
        backup = Path(response.get_json()["backup"])
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.parent.resolve(), (self.tmp / "backups").resolve())
        with sqlite3.connect(backup) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0], 6)
        self.assertEqual(self.count("invoices"), 0)
        events = [e["event"] for e in self.client.get("/api/state").get_json()["audit"]]
        self.assertIn("data_cleared", events)


# --- request origin and host protection ----------------------------------------------

class RequestProtectionTests(IsolatedApp):
    def test_cross_site_writes_are_refused(self):
        content = invoice_import.template_csv().encode()
        for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            response = self.upload("/api/invoices/import", content, "x.csv", **headers)
            self.assertEqual(response.status_code, 403, headers)
        self.assertEqual(self.count("invoices"), 0)

    def test_same_origin_writes_are_allowed(self):
        content = invoice_import.template_csv().encode()
        response = self.upload("/api/invoices/import", content, "x.csv",
                               Origin="http://localhost", **{"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(response.status_code, 200)

    def test_unknown_host_headers_are_refused(self):
        response = self.client.get("/api/state", headers={"Host": "attacker.example"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/health", headers={"Host": "127.0.0.1:8501"}).status_code, 200)

    def test_allowed_host_suffixes_match_subdomains_only(self):
        os.environ["MILAAN_ALLOWED_HOSTS"] = ".app.github.dev, milaan.example.com"
        ok = {"x-8501.app.github.dev", "milaan.example.com:443", "localhost:8501", "[::1]:8501"}
        bad = {"app.github.dev.evil.example", "evilapp.github.dev", "example.com", "app.github.dev"}
        for host in ok:
            self.assertEqual(self.client.get("/health", headers={"Host": host}).status_code, 200, host)
        for host in bad:
            self.assertEqual(self.client.get("/health", headers={"Host": host}).status_code, 400, host)

    def test_pages_cannot_be_framed(self):
        response = self.client.get("/")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])


# --- authentication ----------------------------------------------------------------

class AuthenticationTests(IsolatedApp):
    def test_production_without_credentials_refuses_to_start(self):
        os.environ["APP_ENV"] = "production"
        with self.assertRaisesRegex(RuntimeError, "MILAAN_BASIC_AUTH_USER"):
            milaan_web.create_app()

    def test_configured_basic_auth_protects_everything_but_health(self):
        os.environ["MILAAN_AUTH_REQUIRED"] = "true"
        os.environ["MILAAN_BASIC_AUTH_USER"] = "ops"
        os.environ["MILAAN_BASIC_AUTH_PASSWORD_HASH"] = generate_password_hash("correct horse")
        client = milaan_web.create_app().test_client()

        def auth(user, password):
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            return {"Authorization": f"Basic {token}"}

        self.assertEqual(client.get("/health").status_code, 200)
        denied = client.get("/api/state")
        self.assertEqual(denied.status_code, 401)
        self.assertIn("Basic", denied.headers["WWW-Authenticate"])
        self.assertEqual(client.get("/", headers=auth("ops", "wrong")).status_code, 401)
        self.assertEqual(client.get("/", headers=auth("other", "correct horse")).status_code, 401)
        self.assertEqual(client.get("/api/state", headers=auth("ops", "correct horse")).status_code, 200)

    def test_security_config_reads_basic_auth_settings(self):
        config = security.load_security_config({
            "APP_ENV": "production", "MILAAN_BASIC_AUTH_USER": "ops",
            "MILAAN_BASIC_AUTH_PASSWORD_HASH": "scrypt:x",
        })
        self.assertTrue(config.auth_required)
        self.assertTrue(config.basic_auth_configured)


if __name__ == "__main__":
    unittest.main()
