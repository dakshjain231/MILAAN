"""Tests that recovery requires later GSTR-2B evidence, not a sent message."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import db  # noqa: E402
import delivery  # noqa: E402
from test_delivery import (  # noqa: E402
    GSTIN_A,
    DatabaseTestCase,
    ScriptedProvider,
    gstr2b_row,
)


class RecoveryStatusTests(DatabaseTestCase):
    def later_batch(self, total_gst_paise: int = 225_000) -> int:
        return self.import_rows([
            gstr2b_row(
                GSTIN_A, "INV-A-1", total_gst_paise=total_gst_paise
            )
        ])

    def test_no_later_evidence_is_awaiting(self):
        case = self.approved_draft()
        providers = ScriptedProvider([
            delivery.ProviderResponse(
                outcome=delivery.OUTCOME_ACCEPTED,
                provider_message_id="wamid.SYNTHETIC",
            )
        ])
        record = delivery.deliver_approved_draft(
            case["draft_id"], providers
        )
        self.assertEqual(record["status"], "SENT")
        recovery = db.get_followup_recovery_status(case["draft_id"])
        self.assertEqual(recovery["status"], "AWAITING_LATER_EVIDENCE")
        self.assertEqual(recovery["verified_count"], 0)

    def test_unique_later_present_row_marks_verified(self):
        case = self.approved_draft()
        self.assertEqual(
            db.get_followup_recovery_status(case["draft_id"])["status"],
            "AWAITING_LATER_EVIDENCE",
        )
        later_id = self.later_batch()
        outcomes = db.verify_followup_with_later_import(
            case["draft_id"], later_id
        )
        self.assertEqual(outcomes[0]["outcome"], "PRESENT")
        recovery = db.get_followup_recovery_status(case["draft_id"])
        self.assertEqual(recovery["status"], "VERIFIED")
        self.assertEqual(recovery["verified_count"], 1)
        self.assertEqual(recovery["invoice_count"], 1)
        self.assertEqual(recovery["later_import_id"], later_id)

    def test_amount_mismatch_is_disputed_not_verified(self):
        case = self.approved_draft()
        later_id = self.later_batch(total_gst_paise=200_000)
        outcomes = db.verify_followup_with_later_import(
            case["draft_id"], later_id
        )
        self.assertEqual(outcomes[0]["outcome"], "AMOUNT_MISMATCH")
        recovery = db.get_followup_recovery_status(case["draft_id"])
        self.assertEqual(recovery["status"], "DISPUTED")
        self.assertEqual(recovery["verified_count"], 0)


if __name__ == "__main__":
    unittest.main()
