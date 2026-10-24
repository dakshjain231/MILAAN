"""Inbound WhatsApp document intake: classification, import, dedupe, workflow.

Every test here runs against a throwaway SQLite database (DATABASE_PATH is
repointed in setUp) and a throwaway storage directory. No test touches the
bridge over HTTP and no test can send a WhatsApp message: the fake bridge
client used below has no send method at all.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import db
import inbound

PILOT = "918888888888"

INVOICE_CSV = (
    "supplier_name,supplier_gstin,invoice_number,invoice_date,tax_period,"
    "taxable_value,gst_rate,cgst,sgst,igst,total_gst,invoice_total,contact_phone\n"
    "(Synthetic) Alpha Traders,27AAACS1234A1Z2,INV-001,2026-03-04,2026-03,"
    "10000.00,18,900.00,900.00,0.00,1800.00,11800.00,918888888888\n"
    "(Synthetic) Beta Supplies,29AAACB5678B1ZP,INV-002,2026-03-07,2026-03,"
    "20000.00,18,1800.00,1800.00,0.00,3600.00,23600.00,918888888888\n"
)

# Reports only INV-001. INV-002 is missing from 2B, so it becomes a follow-up.
GSTR2B_CSV = (
    "supplier_gstin,invoice_number,invoice_date,tax_period,"
    "taxable_value,cgst,sgst,igst,total_gst\n"
    "27AAACS1234A1Z2,INV-001,2026-03-04,2026-03,10000.00,900.00,900.00,0.00,1800.00\n"
)

# Same supplier/invoice as the books but a different taxable value.
GSTR2B_MISMATCH_CSV = (
    "supplier_gstin,invoice_number,invoice_date,tax_period,"
    "taxable_value,cgst,sgst,igst,total_gst\n"
    "27AAACS1234A1Z2,INV-001,2026-03-04,2026-03,9000.00,810.00,810.00,0.00,1620.00\n"
)


def entry(**overrides):
    """A bridge queue entry as `GET /inbound` reports it."""
    base = {
        "id": "false_918888888888@c.us_AAA",
        "fromMasked": "…8888",
        "kind": "document",
        "fileName": "invoices.csv",
        "mimeType": "text/csv",
        "extension": "csv",
        "size": len(INVOICE_CSV),
        "sha256": None,
        "supported": True,
        "reason": None,
        "detail": None,
        "text": None,
        "timestamp": 1_700_000_000,
        "receivedAt": "2026-10-24T10:00:00.000Z",
    }
    base.update(overrides)
    return base


class IsolatedStorageCase(unittest.TestCase):
    """Throwaway database + storage directory for every test."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="milaan-inbound-test-")
        self._saved = {
            key: os.environ.get(key)
            for key in ("DATABASE_PATH", "MILAAN_WA_INBOUND_DIR",
                        "MILAAN_WA_INBOUND_ENABLED", "MILAAN_WA_INBOUND_SENDERS",
                        "MILAAN_WA_INBOUND_MAX_BYTES")
        }
        os.environ["DATABASE_PATH"] = str(Path(self._tmp) / "milaan.db")
        os.environ["MILAAN_WA_INBOUND_DIR"] = str(Path(self._tmp) / "media")
        os.environ["MILAAN_WA_INBOUND_SENDERS"] = f"+{PILOT}"
        db.init_db()
        self.config = inbound.load_inbound_config()

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self._tmp, ignore_errors=True)

    def ingest(self, content: str | bytes | None, **overrides):
        payload = (
            content.encode("utf-8") if isinstance(content, str) else content
        )
        return inbound.ingest_inbound_message(
            entry(**overrides), payload, config=self.config
        )


# --- classification --------------------------------------------------------

class ClassificationTests(unittest.TestCase):
    def test_purchase_register_is_classified_as_invoices(self):
        result = inbound.classify_document(INVOICE_CSV.encode(), "invoices.csv")
        self.assertEqual(result.kind, inbound.KIND_PURCHASE_INVOICE)
        self.assertTrue(result.supported)

    def test_gstr2b_statement_is_classified_as_gstr2b(self):
        result = inbound.classify_document(GSTR2B_CSV.encode(), "2b.csv")
        self.assertEqual(result.kind, inbound.KIND_GSTR2B)
        self.assertTrue(result.supported)

    def test_purchase_register_is_not_mistaken_for_gstr2b(self):
        """A purchase file contains every GSTR-2B column, so order matters."""
        result = inbound.classify_document(INVOICE_CSV.encode(), "data.csv")
        self.assertEqual(result.kind, inbound.KIND_PURCHASE_INVOICE)

    def test_file_name_does_not_override_the_columns(self):
        result = inbound.classify_document(GSTR2B_CSV.encode(), "purchase_invoices.csv")
        self.assertEqual(result.kind, inbound.KIND_GSTR2B)

    def test_half_a_purchase_template_is_ambiguous_not_guessed(self):
        content = (
            "supplier_name,supplier_gstin,invoice_number,invoice_date,tax_period,"
            "taxable_value,cgst,sgst,igst,total_gst\n"
            "Acme,27AAACS1234A1Z2,INV-9,2026-03-04,2026-03,100.00,9.00,9.00,0.00,18.00\n"
        )
        result = inbound.classify_document(content.encode(), "mystery.csv")
        self.assertEqual(result.kind, inbound.KIND_UNSUPPORTED)
        self.assertEqual(result.reason, "AMBIGUOUS_SCHEMA")

    def test_pdf_is_unsupported_and_names_the_blocker(self):
        result = inbound.classify_document(b"%PDF-1.7\n", "bill.pdf")
        self.assertEqual(result.kind, inbound.KIND_UNSUPPORTED)
        self.assertEqual(result.reason, "UNSUPPORTED_PDF")
        self.assertIn("CSV", result.detail)

    def test_legacy_xls_is_unsupported(self):
        result = inbound.classify_document(b"\xd0\xcf\x11\xe0", "old.xls")
        self.assertEqual(result.reason, "UNSUPPORTED_LEGACY_XLS")

    def test_photo_is_unsupported_and_names_the_missing_service(self):
        result = inbound.classify_document(b"\xff\xd8\xff", "bill.jpg")
        self.assertEqual(result.reason, "UNSUPPORTED_IMAGE")
        self.assertIn("OCR", result.detail)

    def test_voice_note_is_unsupported_and_names_the_missing_service(self):
        result = inbound.classify_document(b"OggS", "note.ogg")
        self.assertEqual(result.reason, "UNSUPPORTED_AUDIO")
        self.assertIn("speech-to-text", result.detail)

    def test_an_empty_file_is_rejected(self):
        result = inbound.classify_document(b"", "invoices.csv")
        self.assertEqual(result.reason, "EMPTY_FILE")

    def test_an_unreadable_csv_is_rejected_with_the_parser_reason(self):
        result = inbound.classify_document(b"just,some\nrandom,text\n", "x.csv")
        self.assertEqual(result.kind, inbound.KIND_UNSUPPORTED)
        self.assertEqual(result.reason, "UNRECOGNISED_SCHEMA")

    def test_json_is_routed_to_the_gstr2b_parser(self):
        result = inbound.classify_document(b'{"data": []}', "2b.json")
        self.assertEqual(result.kind, inbound.KIND_GSTR2B)


# --- configuration ---------------------------------------------------------

class ConfigTests(unittest.TestCase):
    def test_senders_fall_back_to_the_send_allowlist(self):
        config = inbound.load_inbound_config({
            "MILAAN_WA_ALLOWED_RECIPIENTS": f"+{PILOT}",
        })
        self.assertEqual(config.senders, (PILOT,))

    def test_senders_fall_back_to_the_test_recipient(self):
        config = inbound.load_inbound_config({
            "MILAAN_WA_TEST_RECIPIENT": f"+{PILOT}",
        })
        self.assertEqual(config.senders, (PILOT,))

    def test_an_unlisted_sender_is_refused(self):
        config = inbound.load_inbound_config({
            "MILAAN_WA_INBOUND_SENDERS": f"+{PILOT}",
        })
        self.assertTrue(config.sender_allowed(PILOT))
        self.assertTrue(config.sender_allowed(f"+{PILOT}"))
        self.assertFalse(config.sender_allowed("919812345678"))
        self.assertFalse(config.sender_allowed(None))

    def test_no_configured_sender_blocks_everything(self):
        config = inbound.load_inbound_config({})
        self.assertEqual(config.senders, ())
        self.assertFalse(config.sender_allowed(PILOT))
        self.assertIn(
            "no inbound sender is configured",
            " ".join(config.problems()),
        )

    def test_intake_can_be_disabled(self):
        config = inbound.load_inbound_config({
            "MILAAN_WA_INBOUND_SENDERS": f"+{PILOT}",
            "MILAAN_WA_INBOUND_ENABLED": "false",
        })
        self.assertFalse(config.enabled)


# --- secure storage --------------------------------------------------------

class StorageTests(IsolatedStorageCase):
    def test_a_document_is_stored_under_a_generated_name(self):
        path = inbound.store_document(INVOICE_CSV.encode(), "invoices.csv", config=self.config)
        self.assertTrue(path.is_file())
        self.assertTrue(path.name.endswith(".csv"))
        self.assertEqual(path.read_text(encoding="utf-8"), INVOICE_CSV)

    def test_a_traversal_file_name_cannot_escape_the_storage_directory(self):
        path = inbound.store_document(b"x,y\n1,2\n", "../../etc/passwd.csv", config=self.config)
        self.assertEqual(path.parent.resolve(), self.config.storage_dir.resolve())
        self.assertNotIn("..", str(path))

    def test_an_absolute_file_name_cannot_escape_the_storage_directory(self):
        path = inbound.store_document(b"x,y\n1,2\n", "/etc/cron.d/evil.csv", config=self.config)
        self.assertEqual(path.parent.resolve(), self.config.storage_dir.resolve())

    def test_a_stored_document_is_not_world_readable(self):
        path = inbound.store_document(INVOICE_CSV.encode(), "invoices.csv", config=self.config)
        self.assertEqual(path.stat().st_mode & 0o077, 0, "stored attachments must be owner-only")

    def test_identical_content_is_stored_once(self):
        first = inbound.store_document(INVOICE_CSV.encode(), "a.csv", config=self.config)
        second = inbound.store_document(INVOICE_CSV.encode(), "b.csv", config=self.config)
        self.assertEqual(first, second)


# --- importing one document ------------------------------------------------

class IngestInvoiceTests(IsolatedStorageCase):
    def test_a_purchase_register_is_imported_into_the_database(self):
        result = self.ingest(INVOICE_CSV)
        self.assertEqual(result["status"], inbound.STATUS_IMPORTED)
        self.assertEqual(result["document_kind"], inbound.KIND_PURCHASE_INVOICE)
        self.assertEqual(result["imported_rows"], 2)
        self.assertEqual(len(db.get_invoices()), 2)

    def test_the_same_whatsapp_message_is_never_imported_twice(self):
        self.ingest(INVOICE_CSV)
        again = self.ingest(INVOICE_CSV)
        self.assertEqual(again["status"], inbound.STATUS_DUPLICATE)
        self.assertEqual(again["reason"], "DUPLICATE_MESSAGE")
        self.assertEqual(len(db.get_invoices()), 2)

    def test_the_same_file_from_a_new_message_imports_no_duplicate_rows(self):
        self.ingest(INVOICE_CSV)
        again = self.ingest(INVOICE_CSV, id="false_918888888888@c.us_BBB")
        self.assertEqual(again["status"], inbound.STATUS_DUPLICATE)
        self.assertEqual(again["reason"], "DUPLICATE_FILE")
        self.assertEqual(len(db.get_invoices()), 2)

    def test_new_invoices_in_a_resent_file_are_still_imported(self):
        self.ingest(INVOICE_CSV)
        extended = INVOICE_CSV + (
            "(Synthetic) Gamma Works,24AAACG9012C1Z8,INV-003,2026-03-09,2026-03,"
            "5000.00,18,450.00,450.00,0.00,900.00,5900.00,918888888888\n"
        )
        result = self.ingest(extended, id="false_918888888888@c.us_CCC")
        self.assertEqual(result["status"], inbound.STATUS_IMPORTED)
        self.assertEqual(result["imported_rows"], 1)
        self.assertEqual(result["duplicate_rows"], 2)
        self.assertEqual(len(db.get_invoices()), 3)

    def test_an_invalid_row_is_reported_and_the_valid_rows_still_import(self):
        content = INVOICE_CSV + (
            "(Synthetic) Bad Row,NOTAGSTIN,INV-BAD,2026-03-09,2026-03,"
            "100.00,18,9.00,9.00,0.00,18.00,118.00,\n"
        )
        result = self.ingest(content)
        self.assertEqual(result["status"], inbound.STATUS_IMPORTED)
        self.assertEqual(result["imported_rows"], 2)
        self.assertEqual(result["rejected_rows"], 1)

    def test_a_file_with_no_valid_row_is_rejected_not_reported_as_imported(self):
        content = (
            "supplier_name,supplier_gstin,invoice_number,invoice_date,tax_period,"
            "taxable_value,gst_rate,cgst,sgst,igst,total_gst,invoice_total\n"
            "Bad,NOTAGSTIN,INV-BAD,2026-03-09,2026-03,100.00,18,9.00,9.00,0.00,18.00,118.00\n"
        )
        result = self.ingest(content)
        self.assertEqual(result["status"], inbound.STATUS_REJECTED)
        self.assertEqual(result["imported_rows"], 0)
        self.assertEqual(len(db.get_invoices()), 0)

    def test_a_malformed_file_is_rejected_with_the_parser_message(self):
        result = self.ingest("supplier_name,supplier_gstin\nAcme,27AAACS1234A1Z2\n")
        self.assertEqual(result["status"], inbound.STATUS_REJECTED)
        self.assertIn("missing required column", result["detail"].lower())
        self.assertEqual(len(db.get_invoices()), 0)


class IngestGstr2bTests(IsolatedStorageCase):
    def test_a_gstr2b_statement_is_imported_as_a_batch(self):
        result = self.ingest(GSTR2B_CSV, fileName="gstr2b.csv")
        self.assertEqual(result["status"], inbound.STATUS_IMPORTED)
        self.assertEqual(result["document_kind"], inbound.KIND_GSTR2B)
        self.assertIsNotNone(result["gstr2b_import_id"])
        self.assertEqual(result["imported_rows"], 1)
        self.assertEqual(len(db.get_gstr2b_imports()), 1)

    def test_the_same_gstr2b_file_is_not_imported_as_a_second_batch(self):
        self.ingest(GSTR2B_CSV, fileName="gstr2b.csv")
        again = self.ingest(
            GSTR2B_CSV, fileName="gstr2b.csv", id="false_918888888888@c.us_BBB"
        )
        self.assertEqual(again["status"], inbound.STATUS_DUPLICATE)
        self.assertEqual(again["reason"], "DUPLICATE_FILE")
        self.assertEqual(len(db.get_gstr2b_imports()), 1)


class IngestUnsupportedTests(IsolatedStorageCase):
    def test_a_pdf_is_recorded_as_unsupported_with_its_blocker(self):
        result = self.ingest(
            None, fileName="bill.pdf", extension="pdf", mimeType="application/pdf",
            supported=False, reason="UNSUPPORTED_PDF", detail="PDF attachments are not parsed.",
        )
        self.assertEqual(result["status"], inbound.STATUS_UNSUPPORTED)
        self.assertEqual(result["reason"], "UNSUPPORTED_PDF")
        self.assertIn("PDF", result["detail"])

    def test_a_photo_is_recorded_as_unsupported(self):
        result = self.ingest(
            None, kind="image", fileName=None, extension=None, mimeType="image/jpeg",
            supported=False, reason="UNSUPPORTED_IMAGE", detail="No OCR service.",
        )
        self.assertEqual(result["status"], inbound.STATUS_UNSUPPORTED)
        self.assertEqual(result["kind"], "image")

    def test_a_voice_note_is_recorded_as_unsupported(self):
        result = self.ingest(
            None, kind="audio", fileName=None, extension=None, mimeType="audio/ogg",
            supported=False, reason="UNSUPPORTED_AUDIO", detail="No transcription service.",
        )
        self.assertEqual(result["status"], inbound.STATUS_UNSUPPORTED)

    def test_a_text_message_is_recorded_but_imports_nothing(self):
        result = self.ingest(
            None, kind="text", fileName=None, extension=None, mimeType=None,
            supported=False, reason="TEXT_MESSAGE", detail="Recorded.", text="sent the file",
        )
        self.assertEqual(result["status"], inbound.STATUS_RECORDED)
        self.assertEqual(len(db.get_invoices()), 0)

    def test_an_unsupported_document_still_appears_in_the_inbound_log(self):
        self.ingest(
            None, fileName="bill.pdf", extension="pdf", supported=False,
            reason="UNSUPPORTED_PDF", detail="PDF attachments are not parsed.",
        )
        log = db.get_inbound_documents()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["status"], inbound.STATUS_UNSUPPORTED)
        self.assertEqual(log[0]["reason"], "UNSUPPORTED_PDF")

    def test_a_document_over_the_size_cap_is_refused(self):
        os.environ["MILAAN_WA_INBOUND_MAX_BYTES"] = "16"
        config = inbound.load_inbound_config()
        result = inbound.ingest_inbound_message(
            entry(), INVOICE_CSV.encode(), config=config
        )
        self.assertEqual(result["status"], inbound.STATUS_REJECTED)
        self.assertEqual(result["reason"], "FILE_TOO_LARGE")
        self.assertEqual(len(db.get_invoices()), 0)


# --- audit trail -----------------------------------------------------------

class InboundAuditTests(IsolatedStorageCase):
    # get_system_audit_events() is a display helper: its keys are the column
    # labels the dashboard renders ("Event", "Subject", "Details").
    def test_an_import_records_an_audit_event(self):
        self.ingest(INVOICE_CSV)
        events = [
            event for event in db.get_system_audit_events()
            if event["Event"] == "whatsapp_inbound_import"
        ]
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["Subject"].startswith("inbound_document:"))
        self.assertEqual(events[0]["Details"]["sent_anything"], False)

    def test_the_audit_event_never_contains_the_raw_sender_number(self):
        self.ingest(INVOICE_CSV)
        events = db.get_system_audit_events()
        self.assertNotIn(PILOT, json.dumps(events))

    def test_the_inbound_log_keeps_the_file_hash_for_evidence(self):
        self.ingest(INVOICE_CSV)
        record = db.get_inbound_documents()[0]
        self.assertEqual(len(record["file_hash"]), 64)
        self.assertEqual(record["from_masked"], "…8888")


# --- automatic workflow ----------------------------------------------------

class AutoWorkflowTests(IsolatedStorageCase):
    def test_invoices_alone_do_not_start_a_reconciliation(self):
        result = self.ingest(INVOICE_CSV)
        self.assertFalse(result["workflow"]["ran"])
        self.assertEqual(result["workflow"]["reason"], "NO_MATCHING_GSTR2B_BATCH")
        self.assertEqual(db.get_reconciliation_runs(), [])

    def test_gstr2b_alone_does_not_start_a_reconciliation(self):
        result = self.ingest(GSTR2B_CSV, fileName="gstr2b.csv")
        self.assertFalse(result["workflow"]["ran"])
        self.assertEqual(result["workflow"]["reason"], "NO_INVOICES_FOR_PERIOD")
        self.assertEqual(db.get_reconciliation_runs(), [])

    def test_both_datasets_for_one_period_reconcile_automatically(self):
        self.ingest(INVOICE_CSV)
        result = self.ingest(
            GSTR2B_CSV, fileName="gstr2b.csv", id="false_918888888888@c.us_BBB"
        )
        workflow = result["workflow"]
        self.assertTrue(workflow["ran"])
        self.assertEqual(workflow["periods"], ["2026-03"])
        self.assertIsNotNone(workflow["run_id"])
        run = db.get_latest_reconciliation_run()
        self.assertEqual(run["matched_count"], 1)
        self.assertEqual(run["missing_count"], 1)

    def test_the_workflow_also_runs_when_the_invoices_arrive_second(self):
        self.ingest(GSTR2B_CSV, fileName="gstr2b.csv")
        result = self.ingest(INVOICE_CSV, id="false_918888888888@c.us_BBB")
        self.assertTrue(result["workflow"]["ran"])
        self.assertEqual(db.get_latest_reconciliation_run()["matched_count"], 1)

    def test_a_missing_invoice_produces_a_supplier_followup_draft(self):
        self.ingest(INVOICE_CSV)
        result = self.ingest(
            GSTR2B_CSV, fileName="gstr2b.csv", id="false_918888888888@c.us_BBB"
        )
        self.assertEqual(result["workflow"]["drafts_created"], 1)
        draft_id = result["workflow"]["draft_ids"][0]
        draft = db.get_followup_draft(draft_id)
        # Only INV-002 is missing from 2B, so only its supplier is chased.
        self.assertEqual(draft["supplier_gstin"], "29AAACB5678B1ZP")

    def test_the_draft_states_the_real_amounts_and_itc_at_risk(self):
        self.ingest(INVOICE_CSV)
        result = self.ingest(
            GSTR2B_CSV, fileName="gstr2b.csv", id="false_918888888888@c.us_BBB"
        )
        draft = db.get_followup_draft(result["workflow"]["draft_ids"][0])
        self.assertEqual(draft["total_gst_paise"], 360000)
        self.assertEqual(draft["total_taxable_value_paise"], 2000000)
        self.assertIn("INV-002", draft["draft_text"])
        self.assertIn("3600.00", draft["draft_text"])

    def test_a_draft_is_never_created_as_approved_or_sent(self):
        self.ingest(INVOICE_CSV)
        result = self.ingest(
            GSTR2B_CSV, fileName="gstr2b.csv", id="false_918888888888@c.us_BBB"
        )
        draft = db.get_followup_draft(result["workflow"]["draft_ids"][0])
        self.assertIn(draft["status"], ("DRAFT", "NEEDS_REVIEW"))
        self.assertIsNone(draft["approved_at"])
        self.assertEqual(db.get_message_deliveries(), [])

    def test_an_amount_mismatch_is_reported_as_a_mismatch(self):
        self.ingest(INVOICE_CSV)
        self.ingest(
            GSTR2B_MISMATCH_CSV, fileName="gstr2b.csv", id="false_918888888888@c.us_BBB"
        )
        run = db.get_latest_reconciliation_run()
        self.assertEqual(run["amount_mismatch_count"], 1)
        self.assertEqual(run["missing_count"], 1)

    def test_a_different_tax_period_does_not_reconcile(self):
        self.ingest(INVOICE_CSV)
        april = GSTR2B_CSV.replace("2026-03", "2026-04")
        result = self.ingest(april, fileName="gstr2b.csv", id="false_918888888888@c.us_BBB")
        self.assertFalse(result["workflow"]["ran"])
        self.assertEqual(result["workflow"]["reason"], "NO_INVOICES_FOR_PERIOD")

    def test_a_second_batch_reuses_existing_drafts_instead_of_duplicating(self):
        """A new batch makes a new run, but one draft per supplier per run."""
        self.ingest(INVOICE_CSV)
        first = self.ingest(
            GSTR2B_CSV, fileName="gstr2b.csv", id="false_918888888888@c.us_BBB"
        )
        self.assertEqual(first["workflow"]["drafts_created"], 1)
        # The same statement plus an unrelated supplier's row.
        extended = GSTR2B_CSV + (
            "24AAACG9012C1Z8,INV-003,2026-03-09,2026-03,5000.00,450.00,450.00,0.00,900.00\n"
        )
        second = self.ingest(
            extended, fileName="gstr2b.csv", id="false_918888888888@c.us_CCC"
        )
        # INV-002 is still missing, so its supplier is chased again — but on
        # the new run, and the first draft is untouched.
        self.assertEqual(second["workflow"]["drafts_created"], 1)
        drafts = db.get_followup_drafts()
        self.assertEqual(len(drafts), 2)
        self.assertEqual({draft["Supplier"] for draft in drafts},
                         {"(Synthetic) Beta Supplies"})

    def test_generating_twice_for_one_run_creates_no_second_draft(self):
        self.ingest(INVOICE_CSV)
        result = self.ingest(
            GSTR2B_CSV, fileName="gstr2b.csv", id="false_918888888888@c.us_BBB"
        )
        batch_id = result["gstr2b_import_id"]
        again = db.generate_followup_drafts(batch_id)
        self.assertEqual(again["created_drafts"], 0)
        self.assertEqual(again["existing_drafts"], 1)
        self.assertEqual(len(db.get_followup_drafts()), 1)

    def test_a_rejected_document_never_starts_a_reconciliation(self):
        self.ingest(INVOICE_CSV)
        result = self.ingest("nothing,useful\n1,2\n", id="false_918888888888@c.us_BBB")
        self.assertEqual(result["status"], inbound.STATUS_REJECTED)
        self.assertFalse(result["workflow"]["ran"])
        self.assertEqual(db.get_reconciliation_runs(), [])


# --- polling the bridge ----------------------------------------------------

class FakeBridge:
    """Stands in for the Node bridge. Deliberately has no send method."""

    def __init__(self, documents, media=None, fail_media=()):
        self._documents = list(documents)
        self._media = media or {}
        self._fail_media = set(fail_media)
        self.acked: list[str] = []

    def list_documents(self):
        return {"documents": list(self._documents), "dropped": 0, "queued": len(self._documents)}

    def fetch_media(self, message_id):
        if message_id in self._fail_media:
            raise inbound.InboundBridgeError("media fetch failed")
        payload = self._media.get(message_id)
        if payload is None:
            return None
        return base64.b64encode(payload).decode("ascii")

    def ack(self, message_id):
        self.acked.append(message_id)
        return True


class PollTests(IsolatedStorageCase):
    def test_polling_imports_a_queued_document_and_acknowledges_it(self):
        message = entry()
        bridge = FakeBridge([message], {message["id"]: INVOICE_CSV.encode()})
        summary = inbound.poll_once(config=self.config, bridge=bridge)
        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["imported"], 1)
        self.assertEqual(bridge.acked, [message["id"]])
        self.assertEqual(len(db.get_invoices()), 2)

    def test_an_unsupported_document_is_recorded_and_acknowledged(self):
        message = entry(
            fileName="bill.pdf", extension="pdf", supported=False,
            reason="UNSUPPORTED_PDF", detail="PDF attachments are not parsed.",
        )
        bridge = FakeBridge([message])
        summary = inbound.poll_once(config=self.config, bridge=bridge)
        self.assertEqual(summary["unsupported"], 1)
        self.assertEqual(bridge.acked, [message["id"]])

    def test_a_document_whose_media_cannot_be_fetched_is_not_acknowledged(self):
        message = entry()
        bridge = FakeBridge([message], fail_media=[message["id"]])
        summary = inbound.poll_once(config=self.config, bridge=bridge)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(bridge.acked, [], "an unfetched document must stay queued")

    def test_polling_with_an_empty_queue_is_a_no_op(self):
        bridge = FakeBridge([])
        summary = inbound.poll_once(config=self.config, bridge=bridge)
        self.assertEqual(summary["processed"], 0)
        self.assertEqual(summary["imported"], 0)

    def test_polling_is_disabled_when_intake_is_off(self):
        os.environ["MILAAN_WA_INBOUND_ENABLED"] = "false"
        config = inbound.load_inbound_config()
        message = entry()
        bridge = FakeBridge([message], {message["id"]: INVOICE_CSV.encode()})
        summary = inbound.poll_once(config=config, bridge=bridge)
        self.assertFalse(summary["enabled"])
        self.assertEqual(summary["processed"], 0)
        self.assertEqual(bridge.acked, [])

    def test_polling_reports_the_bridge_queue_overflow(self):
        bridge = FakeBridge([])
        bridge.list_documents = lambda: {"documents": [], "dropped": 3, "queued": 0}
        summary = inbound.poll_once(config=self.config, bridge=bridge)
        self.assertEqual(summary["dropped"], 3)

    def test_a_full_cycle_imports_both_files_and_reconciles(self):
        invoices = entry(id="M1")
        statement = entry(id="M2", fileName="gstr2b.csv")
        bridge = FakeBridge(
            [invoices, statement],
            {"M1": INVOICE_CSV.encode(), "M2": GSTR2B_CSV.encode()},
        )
        summary = inbound.poll_once(config=self.config, bridge=bridge)
        self.assertEqual(summary["imported"], 2)
        self.assertEqual(summary["reconciliation_runs"], 1)
        self.assertEqual(summary["drafts_created"], 1)
        self.assertEqual(len(bridge.acked), 2)


# --- the inbound path can never send --------------------------------------

class NoAutomaticSendTests(IsolatedStorageCase):
    def test_the_inbound_module_does_not_reference_any_send_function(self):
        source = Path("inbound.py").read_text(encoding="utf-8")
        for forbidden in ("send_followup_live", "demo_test_send", "/send",
                          "deliver_approved_draft", "approve_followup_draft"):
            self.assertNotIn(forbidden, source)

    def test_a_full_inbound_cycle_creates_no_delivery_record(self):
        self.ingest(INVOICE_CSV)
        result = self.ingest(
            GSTR2B_CSV, fileName="gstr2b.csv", id="false_918888888888@c.us_BBB"
        )
        self.assertEqual(db.get_message_deliveries(), [])
        draft = db.get_followup_draft(result["workflow"]["draft_ids"][0])
        self.assertIsNone(draft["approved_at"])
        self.assertIsNone(draft["approved_text"])

    def test_a_draft_from_an_inbound_file_is_not_eligible_for_delivery(self):
        self.ingest(INVOICE_CSV)
        result = self.ingest(
            GSTR2B_CSV, fileName="gstr2b.csv", id="false_918888888888@c.us_BBB"
        )
        draft_id = result["workflow"]["draft_ids"][0]
        eligibility = db.get_delivery_eligibility(draft_id)
        self.assertFalse(eligibility["eligible"])
        self.assertTrue(eligibility["problems"])


if __name__ == "__main__":
    unittest.main()
