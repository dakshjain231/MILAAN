"""Regression tests for defects reproduced in this codebase.

Each class names the defect, states the observed wrong behaviour, and pins
the corrected behaviour so it cannot regress. All tests use a throwaway
database and never touch the WhatsApp bridge.
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from agent import store

import db
import milaan_web

INVOICE_CSV = (
    "supplier_name,supplier_gstin,invoice_number,invoice_date,tax_period,"
    "taxable_value,gst_rate,cgst,sgst,igst,total_gst,invoice_total\n"
    "(Synthetic) Alpha,27AAACS1234A1Z2,INV-001,2026-03-04,2026-03,"
    "10000.00,18,900.00,900.00,0.00,1800.00,11800.00\n"
    "(Synthetic) Beta,29AAACB5678B1ZP,INV-002,2026-03-07,2026-03,"
    "20000.00,18,1800.00,1800.00,0.00,3600.00,23600.00\n"
)

GSTR2B_CSV = (
    "supplier_gstin,invoice_number,invoice_date,tax_period,"
    "taxable_value,cgst,sgst,igst,total_gst\n"
    "27AAACS1234A1Z2,INV-001,2026-03-04,2026-03,10000.00,900.00,900.00,0.00,1800.00\n"
)


class WebTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="milaan-defects-"))
        self._saved = {
            key: os.environ.get(key)
            for key in ("DATABASE_PATH", "AGENT_DB_PATH")
        }
        os.environ["DATABASE_PATH"] = str(self.tmp / "milaan.db")
        os.environ["AGENT_DB_PATH"] = str(self.tmp / "agent.db")
        store.reset_initialized()
        self.app = milaan_web.create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        store.reset_initialized()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def upload(self, path: str, content: str, name: str, **fields):
        return self.client.post(
            path,
            data={"file": (io.BytesIO(content.encode("utf-8")), name), **fields},
            content_type="multipart/form-data",
        )

    def audit_events(self, event_type: str) -> list[dict]:
        return [
            event for event in db.get_system_audit_events()
            if event["Event"] == event_type
        ]


class InvoiceImportAuditTests(WebTestCase):
    """Defect: a purchase-invoice upload recorded no audit event at all.

    ``POST /api/invoices/import`` built its audit details from
    ``result["rejected_rows"]``, but ``db.save_invoice_import`` returns that
    count as ``rejected``. The resulting KeyError was swallowed by a bare
    ``except Exception: pass``, so every invoice upload succeeded while
    silently writing nothing to the audit log — the only import path in the
    app that did not. GSTR-2B, reconciliation and follow-up generation all
    logged correctly, which is why it went unnoticed.
    """

    def test_an_invoice_upload_records_an_audit_event(self):
        response = self.upload("/api/invoices/import", INVOICE_CSV, "invoices.csv")
        self.assertEqual(response.status_code, 200)
        events = self.audit_events("invoice_import")
        self.assertEqual(len(events), 1, "the invoice upload must be auditable")

    def test_the_audit_event_records_the_real_counts(self):
        self.upload("/api/invoices/import", INVOICE_CSV, "invoices.csv")
        details = self.audit_events("invoice_import")[0]["Details"]
        self.assertEqual(details["imported"], 2)
        self.assertEqual(details["duplicates"], 0)
        self.assertEqual(details["rejected"], 0)
        self.assertEqual(details["suppliers_created"], 2)

    def test_a_re_upload_is_audited_as_duplicates(self):
        self.upload("/api/invoices/import", INVOICE_CSV, "invoices.csv")
        self.upload("/api/invoices/import", INVOICE_CSV, "invoices.csv")
        events = self.audit_events("invoice_import")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["Details"]["imported"], 0)
        self.assertEqual(events[0]["Details"]["duplicates"], 2)

    def test_a_partially_invalid_upload_audits_the_rejected_count(self):
        content = INVOICE_CSV + (
            "(Synthetic) Bad,NOTAGSTIN,INV-BAD,2026-03-09,2026-03,"
            "100.00,18,9.00,9.00,0.00,18.00,118.00\n"
        )
        self.upload("/api/invoices/import", content, "invoices.csv")
        details = self.audit_events("invoice_import")[0]["Details"]
        self.assertEqual(details["imported"], 2)
        self.assertEqual(details["rejected"], 1)

    def test_the_audit_subject_identifies_the_file(self):
        self.upload("/api/invoices/import", INVOICE_CSV, "march.csv")
        self.assertEqual(
            self.audit_events("invoice_import")[0]["Subject"],
            "invoice_file:march.csv",
        )


class DockerBuildContextTests(unittest.TestCase):
    """Defect: the production image would have been built with secrets in it.

    The Dockerfile does ``COPY . ./`` and there was no ``.dockerignore``, so
    ``docker build`` copied the developer's ``.env`` (service token), the
    live ``milaan.db``, ``backups/``, ``agent_data/`` and — most seriously —
    ``whatsapp_service/.wwebjs_auth/`` (258 MB of linked-device credentials
    for the WhatsApp account) into the image layers. Anyone who pulled that
    image could have resumed the WhatsApp session.
    """

    PATTERNS: tuple[str, ...] = (
        ".env",
        "*.db",
        "backups/",
        "whatsapp_service/.wwebjs_auth/",
        "whatsapp_service/.wwebjs_cache/",
        "whatsapp_service/node_modules/",
        "agent_data/",
        "logs/",
        ".venv/",
        ".git/",
        "inbound_media/",
    )

    def setUp(self):
        self.root = Path(__file__).resolve().parent
        self.path = self.root / ".dockerignore"

    def test_a_dockerignore_exists(self):
        self.assertTrue(
            self.path.is_file(),
            "the Dockerfile uses COPY . ./ so a .dockerignore is mandatory",
        )

    def test_every_sensitive_path_is_excluded_from_the_build_context(self):
        lines = {
            line.strip()
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
        for pattern in self.PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, lines)

    def test_the_whatsapp_session_directory_is_excluded(self):
        """The one that matters most: linked-device credentials."""
        text = self.path.read_text(encoding="utf-8")
        self.assertIn(".wwebjs_auth", text)


class DeclaredDependencyTests(unittest.TestCase):
    """Werkzeug is imported directly, so it must be declared directly.

    ``milaan_web`` does ``from werkzeug.security import check_password_hash``
    for Basic-auth password verification. It only resolved because Flask
    happens to depend on it, which means a future Flask release could drop or
    rename it and break authentication at import time.
    """

    def test_every_directly_imported_package_is_declared(self):
        root = Path(__file__).resolve().parent
        declared = {
            line.split("==")[0].split(">=")[0].strip().lower()
            for line in (root / "requirements.txt").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip() and not line.strip().startswith(("#", "-"))
        }
        for package in ("flask", "openpyxl", "requests", "werkzeug"):
            with self.subTest(package=package):
                self.assertIn(package, declared)


class DotenvPrecedenceTests(unittest.TestCase):
    """Defect: the repository .env re-armed settings an operator had cleared.

    ``agent/config.load_dotenv()`` copies the repo ``.env`` into
    ``os.environ`` with ``setdefault``, and ``agent/store.py`` calls
    ``get_settings()`` to resolve its database path — which happens on every
    ``/api/state`` request. So starting MILAAN with ``MILAAN_WA_LIVE_FOLLOWUPS``
    *unset* did not disable live sending: the first dashboard request quietly
    restored it from ``.env``. Setting it to ``false`` worked, unsetting it
    did not, which is the opposite of what an operator expects.

    The allowlist and the two send confirmations are unaffected, so this was
    never a path to an unintended message — but a delivery switch that
    re-arms itself has to be explicit, not discovered.
    """

    def setUp(self):
        self.root = Path(__file__).resolve().parent
        self._saved = {
            key: os.environ.get(key)
            for key in ("MILAAN_WA_ENABLED", "MILAAN_SKIP_DOTENV")
        }

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_an_explicit_value_always_wins_over_the_dotenv_file(self):
        from agent import config

        os.environ["MILAAN_WA_ENABLED"] = "false"
        config.load_dotenv()
        self.assertEqual(os.environ["MILAAN_WA_ENABLED"], "false")

    def test_skipping_the_dotenv_leaves_the_environment_untouched(self):
        """The documented way to guarantee a clean environment."""
        from agent import config

        os.environ.pop("MILAAN_WA_ENABLED", None)
        os.environ["MILAAN_SKIP_DOTENV"] = "1"
        config.load_dotenv()
        self.assertIsNone(
            os.environ.get("MILAAN_WA_ENABLED"),
            "MILAAN_SKIP_DOTENV=1 must stop .env from populating the environment",
        )

    def test_the_dotenv_still_loads_by_default(self):
        """Without the opt-out, the existing behaviour is unchanged."""
        from agent import config

        env_file = self.root / ".env"
        if not env_file.is_file():
            self.skipTest("no .env on this machine")
        os.environ.pop("MILAAN_SKIP_DOTENV", None)
        os.environ.pop("MILAAN_WA_ENABLED", None)
        config.load_dotenv()
        keys = {
            line.split("=", 1)[0].strip()
            for line in env_file.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.strip().startswith("#")
        }
        if "MILAAN_WA_ENABLED" in keys:
            self.assertIsNotNone(os.environ.get("MILAAN_WA_ENABLED"))


class AuditTrailCompletenessTests(WebTestCase):
    """Every write endpoint must leave an audit record, not just some."""

    def test_the_whole_import_to_followup_chain_is_audited(self):
        self.upload("/api/invoices/import", INVOICE_CSV, "invoices.csv")
        self.upload("/api/gstr2b/import", GSTR2B_CSV, "gstr2b.csv")
        # get_gstr2b_imports() is a display helper; its id column is "Batch".
        batch = db.get_gstr2b_imports()[0]["Batch"]
        self.client.post("/api/reconciliations", json={"batch_id": batch})
        self.client.post("/api/followups/generate", json={"batch_id": batch})

        recorded = {event["Event"] for event in db.get_system_audit_events()}
        for expected in ("invoice_import", "gstr2b_import",
                         "reconciliation_run", "followup_drafts_generated"):
            self.assertIn(expected, recorded, f"{expected} is not audited")


if __name__ == "__main__":
    unittest.main()
