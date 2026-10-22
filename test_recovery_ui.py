"""Regression tests for delivery recovery controls and delivery audit events.

All providers are in-process fakes. No network call is made and no real
message is sent.
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import db
import delivery
from test_delivery import (
    GSTIN_A,
    DatabaseTestCase,
    ScriptedProvider,
    gstr2b_row,
)


def _send_attempt_events():
    return [
        event for event in db.get_system_audit_events(limit=100)
        if event["Event"] == "delivery_send_attempt"
    ]


class DeliveryAuditTests(DatabaseTestCase):
    def test_send_attempt_is_audited_without_secrets(self):
        case = self.approved_draft()
        provider = ScriptedProvider([
            delivery.ProviderResponse(
                outcome=delivery.OUTCOME_ACCEPTED,
                provider_message_id="wamid.AUDIT",
            )
        ])
        record = delivery.deliver_approved_draft(
            case["draft_id"], provider
        )
        self.assertEqual(record["status"], "SENT")
        events = _send_attempt_events()
        self.assertEqual(len(events), 1)
        details = events[0]["Details"]
        self.assertEqual(details["status"], "SENT")
        self.assertEqual(details["provider"], "mock")
        self.assertEqual(details["attempt"], 1)
        self.assertNotIn("message_body", details)
        self.assertNotIn("recipient", details)

    def test_unknown_resolution_is_audited(self):
        case = self.approved_draft()
        provider = ScriptedProvider([
            delivery.ProviderResponse(outcome=delivery.OUTCOME_UNKNOWN)
        ])
        record = delivery.deliver_approved_draft(
            case["draft_id"], provider
        )
        self.assertEqual(record["status"], "UNKNOWN")
        delivery.resolve_unknown(
            record["id"],
            outcome="NOT_SENT",
            resolved_by="operator@example.test",
            note="Meta dashboard shows no accepted message",
        )
        resolved = db.get_message_delivery(record["id"])
        self.assertEqual(resolved["status"], "RETRY_PENDING")
        events = [
            event for event in db.get_system_audit_events(limit=100)
            if event["Event"] == "delivery_resolved"
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["Details"]["outcome"], "NOT_SENT")
        self.assertEqual(events[0]["Actor"], "operator@example.test")


class DeliveryRecoveryUITests(DatabaseTestCase):
    def app(self):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(
            str(Path(__file__).parent / "app.py"), default_timeout=30
        )
        at.run()
        at.switch_page("pages/6_followups.py").run()
        return at

    def test_retry_pending_delivery_can_be_retried_manually(self):
        case = self.approved_draft()
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        provider = ScriptedProvider([
            delivery.ProviderResponse(
                outcome=delivery.OUTCOME_RETRYABLE,
                http_status=503,
                error_code="SERVER_ERROR",
                error_message="temporarily unavailable",
            )
        ])
        record = delivery.deliver_approved_draft(
            case["draft_id"], provider, now=past
        )
        self.assertEqual(record["status"], "RETRY_PENDING")

        at = self.app()
        self.assertEqual(at.exception, [])
        delivery_id = record["id"]
        at.checkbox(
            key=f"delivery_retry_confirm_{delivery_id}"
        ).check().run()
        at.button(key=f"delivery_retry_{delivery_id}").click().run()

        self.assertEqual(at.exception, [])
        retried = db.get_message_delivery(delivery_id)
        self.assertEqual(retried["status"], "SENT")
        self.assertEqual(retried["attempt_count"], 2)
        self.assertEqual(len(_send_attempt_events()), 2)

    def test_unknown_delivery_resolution_requires_evidence(self):
        case = self.approved_draft()
        provider = ScriptedProvider([
            delivery.ProviderResponse(outcome=delivery.OUTCOME_UNKNOWN)
        ])
        record = delivery.deliver_approved_draft(
            case["draft_id"], provider
        )
        self.assertEqual(record["status"], "UNKNOWN")
        delivery_id = record["id"]

        at = self.app()
        self.assertEqual(at.exception, [])
        at.text_input(key=f"delivery_resolve_by_{delivery_id}").input(
            "Asha"
        ).run()
        at.text_area(key=f"delivery_resolve_note_{delivery_id}").input(
            "Checked the Meta dashboard"
        ).run()
        at.text_input(
            key=f"delivery_resolve_msgid_{delivery_id}"
        ).input("wamid.CONFIRMED").run()
        at.checkbox(
            key=f"delivery_resolve_confirm_{delivery_id}"
        ).check().run()
        at.button(key=f"delivery_resolve_{delivery_id}").click().run()

        self.assertEqual(at.exception, [])
        resolved = db.get_message_delivery(delivery_id)
        self.assertEqual(resolved["status"], "SENT")
        self.assertEqual(resolved["provider_message_id"], "wamid.CONFIRMED")

    def test_real_send_is_blocked_until_template_name_is_entered(self):
        self.approved_draft()
        configured = {
            "WHATSAPP_ENABLED": "true",
            "WHATSAPP_ACCESS_TOKEN": "test-token-only",
            "WHATSAPP_PHONE_NUMBER_ID": "1234567890",
            "WHATSAPP_API_VERSION": "v99.0",
        }
        with unittest.mock.patch.dict(os.environ, configured):
            from streamlit.testing.v1 import AppTest

            at = AppTest.from_file(
                str(Path(__file__).parent / "app.py"), default_timeout=30
            )
            at.run()
            at.switch_page("pages/6_followups.py").run()
            at.radio(key="delivery_mode").set_value(
                "WhatsApp Cloud API (real)"
            ).run()
            draft_id = db.get_followup_drafts()[0]["Draft"]
            at.checkbox(key=f"delivery_template_{draft_id}").check().run()

        self.assertEqual(at.exception, [])
        send_buttons = [
            item for item in at.button
            if "Send via WhatsApp Cloud API" in item.label
        ]
        self.assertEqual(len(send_buttons), 1)
        self.assertTrue(send_buttons[0].disabled)


class MismatchEvidenceUITests(DatabaseTestCase):
    def test_mismatch_page_renders_field_by_field_evidence(self):
        supplier = self.add_supplier("Alpha Traders", GSTIN_A)
        self.add_invoice(supplier, "INV-A-1", total_gst=225_000)
        batch_id = self.import_rows([
            gstr2b_row(
                GSTIN_A, "INV-A-1", total_gst_paise=200_000
            )
        ])
        db.reconcile_gstr2b_import(batch_id)

        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(
            str(Path(__file__).parent / "app.py"), default_timeout=30
        )
        at.run()
        at.switch_page("pages/5_mismatches.py").run()
        self.assertEqual(at.exception, [])
        texts = " ".join(item.value for item in at.markdown)
        self.assertIn("Field-by-field evidence", texts)
        captions = " ".join(item.value for item in at.caption)
        self.assertIn("component and taxable value differences", captions)


if __name__ == "__main__":
    unittest.main()
