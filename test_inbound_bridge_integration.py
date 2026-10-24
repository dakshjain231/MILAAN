"""Python <-> Node wire contract for inbound documents, over real HTTP.

The unit tests on each side use fakes, which proves each half behaves but
not that they agree. This spawns the actual Node HTTP server (with a seeded
queue, no Chromium and no WhatsApp session) and drives it with the real
``inbound.BridgeClient``, so a change to either side's JSON shape fails here.

No WhatsApp session is involved and the harness configures no send adapter,
so nothing in this file can send a message.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import db
import inbound
import webjs
from test_inbound import GSTR2B_CSV, INVOICE_CSV

ROOT = Path(__file__).resolve().parent
HARNESS = ROOT / "whatsapp_service" / "tools" / "inboundHarness.js"
TOKEN = "integration-service-token-123456"
PILOT = "918888888888"


def _node_available() -> bool:
    return shutil.which("node") is not None


def _entry(message_id: str, file_name: str, content: bytes, **overrides) -> dict:
    entry = {
        "id": message_id,
        "fromMasked": "…8888",
        "kind": "document",
        "fileName": file_name,
        "mimeType": "text/csv",
        "extension": file_name.rsplit(".", 1)[-1],
        "size": len(content),
        "sha256": inbound.sha256_hex(content),
        "supported": True,
        "reason": None,
        "detail": None,
        "text": None,
        "timestamp": 1_700_000_000,
        "receivedAt": "2026-10-24T10:00:00.000Z",
        "data": base64.b64encode(content).decode("ascii"),
    }
    entry.update(overrides)
    return entry


@unittest.skipUnless(_node_available(), "node is not installed")
class InboundBridgeIntegrationTests(unittest.TestCase):
    # A fresh Node process per test: polling drains the queue and
    # acknowledgement is remembered, so a shared harness would make these
    # tests depend on their execution order.

    def _start_harness(self) -> int:
        seed = [
            _entry("WAMID-INVOICES", "invoices.csv", INVOICE_CSV.encode()),
            _entry("WAMID-2B", "gstr2b.csv", GSTR2B_CSV.encode()),
            _entry(
                "WAMID-PDF", "bill.pdf", b"",
                mimeType="application/pdf", extension="pdf", supported=False,
                reason="UNSUPPORTED_PDF", detail="PDF attachments are not parsed.",
                data=None, size=4096, sha256=None,
            ),
        ]
        self.process = subprocess.Popen(
            ["node", str(HARNESS), TOKEN, json.dumps(seed)],
            cwd=str(ROOT / "whatsapp_service"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if line.startswith("PORT="):
                return int(line.strip().split("=", 1)[1])
            if self.process.poll() is not None:
                raise RuntimeError(
                    "harness exited: " + (self.process.stderr.read() or "")
                )
        raise RuntimeError("the Node harness did not report a port")

    def _stop_harness(self):
        process = getattr(self, "process", None)
        if process and process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        for stream in (getattr(process, "stdout", None),
                       getattr(process, "stderr", None)):
            if stream:
                stream.close()

    def setUp(self):
        self.port = self._start_harness()
        self.addCleanup(self._stop_harness)
        self.tmp = Path(tempfile.mkdtemp(prefix="milaan-bridge-int-"))
        self._saved = {
            key: os.environ.get(key)
            for key in ("DATABASE_PATH", "MILAAN_WA_INBOUND_DIR",
                        "MILAAN_WA_INBOUND_SENDERS")
        }
        os.environ["DATABASE_PATH"] = str(self.tmp / "milaan.db")
        os.environ["MILAAN_WA_INBOUND_DIR"] = str(self.tmp / "media")
        os.environ["MILAAN_WA_INBOUND_SENDERS"] = f"+{PILOT}"
        db.init_db()
        self.webjs_config = webjs.load_webjs_config({
            "MILAAN_WA_ENABLED": "true",
            "MILAAN_WA_SERVICE_URL": f"http://127.0.0.1:{self.port}",
            "MILAAN_WA_SERVICE_TOKEN": TOKEN,
            "MILAAN_WA_TEST_RECIPIENT": PILOT,
        })
        self.client = inbound.BridgeClient(self.webjs_config)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- the wire contract ------------------------------------------------

    def test_the_client_reads_the_queue_the_server_serves(self):
        listing = self.client.list_documents()
        self.assertEqual(len(listing["documents"]), 3)
        self.assertEqual(listing["queued"], 3)
        ids = {document["id"] for document in listing["documents"]}
        self.assertEqual(ids, {"WAMID-INVOICES", "WAMID-2B", "WAMID-PDF"})

    def test_the_listing_carries_no_file_bytes(self):
        for document in self.client.list_documents()["documents"]:
            self.assertNotIn("data", document)

    def test_the_client_fetches_real_bytes_for_one_entry(self):
        encoded = self.client.fetch_media("WAMID-INVOICES")
        self.assertIsNotNone(encoded)
        self.assertEqual(base64.b64decode(encoded).decode("utf-8"), INVOICE_CSV)

    def test_an_unsupported_entry_has_no_bytes_to_fetch(self):
        self.assertIsNone(self.client.fetch_media("WAMID-PDF"))

    def test_an_unknown_id_is_reported_as_missing_not_as_an_error(self):
        self.assertIsNone(self.client.fetch_media("WAMID-DOES-NOT-EXIST"))

    def test_a_wrong_token_is_refused_by_the_real_server(self):
        bad = inbound.BridgeClient(webjs.load_webjs_config({
            "MILAAN_WA_ENABLED": "true",
            "MILAAN_WA_SERVICE_URL": f"http://127.0.0.1:{self.port}",
            "MILAAN_WA_SERVICE_TOKEN": "wrong-token-0123456789",
            "MILAAN_WA_TEST_RECIPIENT": PILOT,
        }))
        with self.assertRaises(inbound.InboundBridgeError):
            bad.list_documents()

    # --- the whole cycle over real HTTP -----------------------------------

    def test_a_real_poll_imports_reconciles_and_acknowledges(self):
        summary = inbound.poll_once(
            config=inbound.load_inbound_config(), bridge=self.client
        )
        self.assertEqual(summary["processed"], 3)
        self.assertEqual(summary["imported"], 2)
        self.assertEqual(summary["unsupported"], 1)
        # Both halves of 2026-03 arrived, so the workflow ran by itself.
        self.assertEqual(summary["reconciliation_runs"], 1)
        self.assertEqual(summary["drafts_created"], 1)

        self.assertEqual(len(db.get_invoices()), 2)
        run = db.get_latest_reconciliation_run()
        self.assertEqual(run["matched_count"], 1)
        self.assertEqual(run["missing_count"], 1)
        # Nothing was sent anywhere.
        self.assertEqual(db.get_message_deliveries(), [])

    def test_acknowledged_entries_are_gone_from_the_real_queue(self):
        inbound.poll_once(config=inbound.load_inbound_config(), bridge=self.client)
        self.assertEqual(self.client.list_documents()["queued"], 0)

    def test_a_second_poll_after_acknowledgement_is_a_no_op(self):
        inbound.poll_once(config=inbound.load_inbound_config(), bridge=self.client)
        again = inbound.poll_once(
            config=inbound.load_inbound_config(), bridge=self.client
        )
        self.assertEqual(again["processed"], 0)
        self.assertEqual(len(db.get_invoices()), 2)


if __name__ == "__main__":
    unittest.main()
