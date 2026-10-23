"""Tests for the labelled synthetic reconciliation evaluation."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import reconciliation_eval  # noqa: E402


class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = reconciliation_eval.run_evaluation()

    def test_no_false_matches_and_full_precision_recall(self):
        metrics = self.report["metrics"]
        self.assertEqual(metrics["false_matches"], 0)
        self.assertEqual(metrics["false_positives"], 0)
        self.assertEqual(metrics["false_negatives"], 0)
        self.assertEqual(metrics["precision"], 1.0)
        self.assertEqual(metrics["recall"], 1.0)
        self.assertEqual(metrics["scenario_pass"], "22/22")

    def test_every_labelled_scenario_passes(self):
        for scenario in self.report["scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                self.assertTrue(scenario["pass"], scenario)

    def test_required_scenario_coverage(self):
        ids = {item["id"] for item in self.report["scenarios"]}
        for required in (
            "exact_match",
            "gstin_mismatch",
            "period_mismatch",
            "invoice_number_variant",
            "duplicate_purchase_invoice",
            "duplicate_2b_candidates",
            "swapped_components_equal_total",
            "taxable_value_mismatch",
            "date_mismatch",
            "cess_mismatch",
            "books_only",
            "twob_only",
            "itc_unavailable",
            "ambiguous_amount_candidates",
            "not_eligible_invoice",
        ):
            self.assertIn(required, ids)

    def test_file_ingestion_scenarios_pass(self):
        file_report = self.report["file_ingestion"]
        self.assertEqual(
            file_report["passed"], file_report["scenario_count"]
        )
        self.assertEqual(
            file_report["scenario_count"], len(file_report["scenarios"])
        )
        outcomes = {item["id"]: item for item in file_report["scenarios"]}
        self.assertEqual(
            outcomes["portal_xlsx"]["parser_kind"], "portal_b2b"
        )
        self.assertEqual(outcomes["portal_xlsx"]["source_row"], 4)
        self.assertIn("ambiguous", outcomes["ambiguous_portal_headers"]["error"])
        self.assertIn("PDF", outcomes["pdf_rejected"]["error"])

    def test_report_is_json_serialisable_and_labelled(self):
        encoded = json.dumps(self.report, sort_keys=True)
        self.assertIn("labelled synthetic data", encoded)
        self.assertIn("no real taxpayer data", encoded)


if __name__ == "__main__":
    unittest.main()
