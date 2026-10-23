"""Integration tests for the merged UI layer, launcher, and safe seed cleanup."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent import store
from agent.config import get_settings
from agent.demo.seed import main as seed_main

import agent_views
import service_health


class AgentViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="milaan-merge-test-"))
        self.old_db = os.environ.get("AGENT_DB_PATH")
        os.environ["AGENT_DB_PATH"] = str(self.tmp / "agent.db")
        store.reset_initialized()

    def tearDown(self):
        if self.old_db is None:
            os.environ.pop("AGENT_DB_PATH", None)
        else:
            os.environ["AGENT_DB_PATH"] = self.old_db
        store.reset_initialized()

    def test_phone_masking(self):
        self.assertEqual(agent_views.mask_phone("+919876543210"), "+91••••••3210")
        self.assertEqual(agent_views.mask_phone(""), "")
        self.assertEqual(agent_views.mask_phone("+91"), "…")

    def test_payload_masking(self):
        masked = agent_views.mask_payload(
            {
                "to": "+919876543210",
                "nested": {"phone": "+919000000001"},
                "body": "hello",
                "amount": 100,
            }
        )
        self.assertNotIn("9876543210", str(masked))
        self.assertNotIn("9000000001", str(masked))
        self.assertEqual(masked["body"], "hello")
        self.assertEqual(masked["amount"], 100)

    def test_empty_state_is_explicit(self):
        missing = agent_views.overview()
        self.assertFalse(missing["available"])
        self.assertIn("not found", missing["reason"].lower())
        with store.conn():
            pass  # initialise the schema, exactly as the agent does
        overview = agent_views.overview()
        self.assertTrue(overview["available"])
        self.assertTrue(overview["empty"])
        self.assertEqual(overview["traders"], [])

    def test_seed_rejects_placeholder_phone(self):
        with self.assertRaises(SystemExit):
            seed_main(
                ["--trader", "+91", "--supplier", "+919000000002", "--quiet"]
            )

    def test_seed_refuses_default_database_reset(self):
        os.environ.pop("AGENT_DB_PATH", None)
        with self.assertRaises(SystemExit):
            seed_main(
                [
                    "--trader",
                    "+919000000001",
                    "--supplier",
                    "+919000000002",
                    "--quiet",
                ]
            )

    def test_overview_uses_real_seeded_rows(self):
        seed_main(
            [
                "--trader",
                "+919000000001",
                "--supplier",
                "+919000000002",
                "--quiet",
            ]
        )
        data = agent_views.overview()
        self.assertFalse(data["empty"])
        self.assertEqual(data["counts"]["bills"], 7)
        self.assertEqual(len(data["traders"]), 1)
        self.assertNotIn("9000000001", str(data["conversation"]))
        self.assertTrue(data["database"]["exists"])


class HealthTests(unittest.TestCase):
    def test_health_payload_is_display_safe(self):
        os.environ["MILAAN_WA_SERVICE_TOKEN"] = "secret-token-value-that-must-not-leak"
        try:
            report = service_health.collect()
        finally:
            os.environ.pop("MILAAN_WA_SERVICE_TOKEN", None)
        rendered = str(report)
        self.assertNotIn("secret-token-value-that-must-not-leak", rendered)
        self.assertIn("checks", report)
        self.assertTrue(all("status" in item for item in report["checks"]))


class PlaceholderCleanupTests(unittest.TestCase):
    def test_cleanup_removes_only_seed_placeholder_rows(self):
        tmp = Path(tempfile.mkdtemp(prefix="milaan-cleanup-test-"))
        db_path = tmp / "agent.db"
        old_db = os.environ.get("AGENT_DB_PATH")
        os.environ["AGENT_DB_PATH"] = str(db_path)
        store.reset_initialized()
        try:
            store.ensure_trader("+91", "hi")
            store.upsert_supplier("+91", "08AAAAA0000A1Z5", "Demo supplier", phone="+919000000002")
            store.insert_invoice(
                "+91",
                **{
                    "supplier_name": "Demo supplier",
                    "supplier_gstin": "08AAAAA0000A1Z5",
                    "invoice_number": "D-1",
                    "invoice_date": "2026-10-01",
                    "tax_period": "2026-10",
                    "taxable_paise": 10000,
                    "total_gst_paise": 1800,
                    "invoice_total_paise": 11800,
                    "provenance": "seed:photo",
                    "state": "MATCHED",
                },
            )
            store.trace("+91", "seed", "DECISION", "diagnose:MATCHED", {"to": "+919000000002"})
        finally:
            if old_db is None:
                os.environ.pop("AGENT_DB_PATH", None)
            else:
                os.environ["AGENT_DB_PATH"] = old_db
            store.reset_initialized()

        import scripts.cleanup_placeholder_seed as cleanup

        exit_code = cleanup.main(
            ["--db", str(db_path), "--backup-dir", str(tmp / "backups"), "--confirm"]
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(list((tmp / "backups").glob("*.db")))
        with sqlite3.connect(db_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM agent_traders").fetchone()[0], 0
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM agent_invoices").fetchone()[0], 0
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM agent_trace").fetchone()[0], 0
            )


class LauncherTests(unittest.TestCase):
    def test_launcher_is_executable_and_names_one_url(self):
        launcher = Path(__file__).resolve().parent / "run_milaan.sh"
        self.assertTrue(launcher.is_file())
        self.assertTrue(os.access(launcher, os.X_OK))
        text = launcher.read_text(encoding="utf-8")
        self.assertIn("http://127.0.0.1:${PORT}", text)
        self.assertIn("--stop", text)


if __name__ == "__main__":
    unittest.main()
