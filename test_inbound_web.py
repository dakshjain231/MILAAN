"""The dashboard's view of inbound WhatsApp documents, and its poll endpoint.

Every test uses a throwaway database. The poll endpoint is always driven with
an injected fake bridge, so no test reaches the real Node service and no test
can send a WhatsApp message.
"""

from __future__ import annotations

import base64
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import store

import db
import inbound
import milaan_web
from test_inbound import GSTR2B_CSV, INVOICE_CSV, entry

PILOT = "918888888888"


class FakeBridge:
    """Mirrors inbound.BridgeClient's read surface. Has no send method."""

    def __init__(self, documents=(), media=None, dropped=0):
        self._documents = list(documents)
        self._media = media or {}
        self._dropped = dropped
        self.acked: list[str] = []

    def list_documents(self):
        return {
            "documents": list(self._documents),
            "dropped": self._dropped,
            "queued": len(self._documents),
        }

    def fetch_media(self, message_id):
        payload = self._media.get(message_id)
        return base64.b64encode(payload).decode("ascii") if payload else None

    def ack(self, message_id):
        self.acked.append(message_id)
        return True


class InboundWebTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="milaan-inbound-web-"))
        self._saved = {
            key: os.environ.get(key)
            for key in ("DATABASE_PATH", "AGENT_DB_PATH", "MILAAN_WA_INBOUND_DIR",
                        "MILAAN_WA_INBOUND_SENDERS", "MILAAN_WA_INBOUND_ENABLED")
        }
        os.environ["DATABASE_PATH"] = str(self.tmp / "milaan.db")
        os.environ["AGENT_DB_PATH"] = str(self.tmp / "agent.db")
        os.environ["MILAAN_WA_INBOUND_DIR"] = str(self.tmp / "media")
        os.environ["MILAAN_WA_INBOUND_SENDERS"] = f"+{PILOT}"
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

    def poll(self, bridge):
        with patch.object(inbound, "BridgeClient", return_value=bridge):
            return self.client.post("/api/whatsapp/inbound/poll")

    def ingest(self, content, **overrides):
        return inbound.ingest_inbound_message(
            entry(**overrides), content.encode("utf-8"),
            config=inbound.load_inbound_config(),
        )


# --- /api/state ------------------------------------------------------------

class InboundStateTests(InboundWebTestCase):
    def test_state_reports_an_empty_inbound_section(self):
        payload = self.client.get("/api/state").get_json()
        self.assertIn("inbound", payload)
        self.assertEqual(payload["inbound"]["documents"], [])
        self.assertIn("enabled", payload["inbound"]["config"])

    def test_an_imported_document_appears_in_state(self):
        self.ingest(INVOICE_CSV)
        payload = self.client.get("/api/state").get_json()
        documents = payload["inbound"]["documents"]
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["status"], "imported")
        self.assertEqual(documents[0]["file"], "invoices.csv")
        self.assertEqual(documents[0]["rows"], 2)

    def test_state_never_exposes_a_raw_sender_number(self):
        self.ingest(INVOICE_CSV)
        body = self.client.get("/api/state").get_data(as_text=True)
        self.assertNotIn(PILOT, body)

    def test_state_never_exposes_the_stored_file_path(self):
        self.ingest(INVOICE_CSV)
        body = self.client.get("/api/state").get_data(as_text=True)
        self.assertNotIn(str(self.tmp / "media"), body)

    def test_an_unsupported_document_shows_its_blocker_in_state(self):
        inbound.ingest_inbound_message(
            entry(fileName="bill.pdf", extension="pdf", supported=False,
                  reason="UNSUPPORTED_PDF", detail="x"),
            None, config=inbound.load_inbound_config(),
        )
        documents = self.client.get("/api/state").get_json()["inbound"]["documents"]
        self.assertEqual(documents[0]["status"], "unsupported")
        self.assertEqual(documents[0]["reason"], "UNSUPPORTED_PDF")
        self.assertIn("OCR", documents[0]["detail"])

    def test_the_summary_counts_inbound_documents(self):
        self.ingest(INVOICE_CSV)
        summary = self.client.get("/api/state").get_json()["summary"]
        self.assertEqual(summary["inbound_documents"], 1)


# --- POST /api/whatsapp/inbound/poll --------------------------------------

class InboundPollEndpointTests(InboundWebTestCase):
    def test_polling_an_empty_queue_succeeds(self):
        response = self.poll(FakeBridge())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["processed"], 0)

    def test_polling_imports_a_queued_purchase_register(self):
        message = entry()
        bridge = FakeBridge([message], {message["id"]: INVOICE_CSV.encode()})
        payload = self.poll(bridge).get_json()
        self.assertEqual(payload["imported"], 1)
        self.assertEqual(len(db.get_invoices()), 2)
        self.assertEqual(bridge.acked, [message["id"]])

    def test_polling_both_files_reconciles_and_drafts(self):
        bridge = FakeBridge(
            [entry(id="M1"), entry(id="M2", fileName="gstr2b.csv")],
            {"M1": INVOICE_CSV.encode(), "M2": GSTR2B_CSV.encode()},
        )
        payload = self.poll(bridge).get_json()
        self.assertEqual(payload["imported"], 2)
        self.assertEqual(payload["reconciliation_runs"], 1)
        self.assertEqual(payload["drafts_created"], 1)
        self.assertIsNotNone(db.get_latest_reconciliation_run())

    def test_the_poll_response_lists_what_each_document_did(self):
        message = entry()
        bridge = FakeBridge([message], {message["id"]: INVOICE_CSV.encode()})
        payload = self.poll(bridge).get_json()
        self.assertEqual(len(payload["documents"]), 1)
        self.assertEqual(payload["documents"][0]["status"], "imported")

    def test_polling_never_creates_a_delivery(self):
        bridge = FakeBridge(
            [entry(id="M1"), entry(id="M2", fileName="gstr2b.csv")],
            {"M1": INVOICE_CSV.encode(), "M2": GSTR2B_CSV.encode()},
        )
        self.poll(bridge)
        self.assertEqual(db.get_message_deliveries(), [])

    def test_an_unreachable_bridge_is_reported_not_crashed(self):
        class Unreachable:
            def list_documents(self):
                raise inbound.InboundBridgeError("bridge is not running")

        response = self.poll(Unreachable())
        self.assertEqual(response.status_code, 503)
        self.assertIn("bridge", response.get_json()["error"].lower())

    def test_polling_is_refused_when_intake_is_disabled(self):
        os.environ["MILAAN_WA_INBOUND_ENABLED"] = "false"
        message = entry()
        bridge = FakeBridge([message], {message["id"]: INVOICE_CSV.encode()})
        payload = self.poll(bridge).get_json()
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["processed"], 0)
        self.assertEqual(bridge.acked, [])

    def test_the_poll_endpoint_rejects_a_cross_site_request(self):
        response = self.client.post(
            "/api/whatsapp/inbound/poll",
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(response.status_code, 403)

    def test_the_poll_endpoint_is_not_reachable_by_get(self):
        self.assertEqual(self.client.get("/api/whatsapp/inbound/poll").status_code, 405)


# --- the UI carries the inbound panel -------------------------------------

class InboundPageTests(InboundWebTestCase):
    def test_the_page_still_has_exactly_the_six_reference_pages(self):
        body = self.client.get("/").get_data(as_text=True)
        for label in ("Dashboard", "Purchase Invoices", "GSTR-2B Import",
                      "Reconciliation", "Supplier Follow-ups", "Settings"):
            self.assertIn(label, body)

    def test_the_page_renders_the_inbound_panel(self):
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("renderInboundPanel", body)
        self.assertIn("/api/whatsapp/inbound/poll", body)

    def test_the_page_explains_the_unsupported_formats(self):
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn("CSV", body)
        self.assertIn("XLSX", body)


if __name__ == "__main__":
    unittest.main()
