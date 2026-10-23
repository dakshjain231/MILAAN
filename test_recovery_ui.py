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






if __name__ == "__main__":
    unittest.main()
