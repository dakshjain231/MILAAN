"""Webhook tests: signed verification, status updates and inbound replies.

Run: .venv/bin/python -m unittest test_webhook -v

No test opens a socket or contacts Meta. Payloads are synthetic and the
raw-body signature is computed locally with a throwaway secret.
"""

import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import db  # noqa: E402
import delivery  # noqa: E402
import gstr2b  # noqa: E402

_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_CSV_HEADER = (
    "supplier_gstin,invoice_number,invoice_date,tax_period,"
    "taxable_value,cgst,sgst,igst,total_gst"
)
PERIOD = "2026-03"
APP_SECRET = "test-app-secret-never-real"
VERIFY_TOKEN = "test-verify-token-never-real"


def reference_checksum(first_14: str) -> str:
    total = 0
    for position, character in enumerate(first_14):
        value = _CHARSET.index(character.upper())
        product = value * (1 if position % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _CHARSET[(36 - total % 36) % 36]


def make_gstin(first_14: str) -> str:
    return first_14 + reference_checksum(first_14)


GSTIN_A = make_gstin("27AAACS1234A1Z")
GSTIN_B = make_gstin("29AAACB5678B1Z")


def rupees(paise: int) -> str:
    return f"{paise // 100}.{paise % 100:02d}"


def csv_bytes(rows) -> bytes:
    lines = [_CSV_HEADER]
    for values in rows:
        lines.append(",".join(str(value) for value in values))
    return ("\n".join(lines) + "\n").encode("utf-8")


def gstr2b_row(gstin, invoice_number, total_gst_paise=225_000):
    cgst = total_gst_paise // 2
    return [
        gstin,
        invoice_number,
        "2026-03-05",
        PERIOD,
        "12500.00",
        rupees(cgst),
        rupees(total_gst_paise - cgst),
        "0.00",
        rupees(total_gst_paise),
    ]


def status_payload(message_id, status, recipient="919999999999", errors=None):
    status_entry = {
        "id": message_id,
        "status": status,
        "timestamp": "1760000000",
        "recipient_id": recipient,
    }
    if errors is not None:
        status_entry["errors"] = errors
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "919888888888",
                                "phone_number_id": "1234567890",
                            },
                            "statuses": [status_entry],
                        },
                    }
                ],
            }
        ],
    }


def reply_payload(message_id, from_phone="919999999999", text="Thanks"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "919888888888",
                                "phone_number_id": "1234567890",
                            },
                            "messages": [
                                {
                                    "from": from_phone,
                                    "id": message_id,
                                    "timestamp": "1760000001",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


class WebhookTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="milaan-test-")
        self._old_env = os.environ.get("DATABASE_PATH")
        os.environ["DATABASE_PATH"] = str(Path(self._tmp) / "test_milaan.db")
        db.init_db()

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = self._old_env
        shutil.rmtree(self._tmp, ignore_errors=True)

    def add_supplier(self, name, gstin, phone="9999999999"):
        with db.db() as conn:
            return conn.execute(
                """INSERT INTO suppliers (supplier_name, gstin, contact_phone)
                   VALUES (?, ?, ?)""",
                (name, gstin, phone),
            ).lastrowid

    def add_invoice(self, supplier_id, number, total_gst=225_000,
                    status="valid"):
        taxable = 1_250_000
        with db.db() as conn:
            return conn.execute(
                """INSERT INTO invoices
                       (supplier_id, invoice_number, invoice_date, tax_period,
                        taxable_value, gst_rate, cgst, sgst, igst, total_gst,
                        invoice_total, validation_status)
                   VALUES (?, ?, '2026-03-05', ?, ?, 18, ?, 0, 0, ?, ?, ?)""",
                (supplier_id, number, PERIOD, taxable, total_gst,
                 total_gst, taxable + total_gst, status),
            ).lastrowid

    def approved_draft(self, phone="9999999999"):
        supplier_a = self.add_supplier("Alpha Traders", GSTIN_A, phone)
        self.add_invoice(supplier_a, "INV-A-1")
        supplier_b = self.add_supplier("Beta Traders", GSTIN_B, "8888888888")
        self.add_invoice(supplier_b, "INV-B-1", total_gst=90_000)
        parsed = gstr2b.parse_gstr2b(
            csv_bytes([gstr2b_row(GSTIN_B, "INV-B-1", 90_000)]),
            "webhook.csv",
        )
        batch_id = db.save_gstr2b_import(parsed)["import_id"]
        db.reconcile_gstr2b_import(batch_id)
        draft_id = db.generate_followup_drafts(batch_id)["draft_ids"][0]
        db.approve_followup_draft(draft_id, confirmed=True)
        return draft_id

    def sent_delivery(self):
        draft_id = self.approved_draft()
        record = delivery.deliver_approved_draft(
            draft_id, delivery.MockDeliveryProvider(), opt_in_confirmed=True
        )
        self.assertEqual(record["status"], "SENT")
        return record

    def process(self, payload, config=None):
        return delivery.process_webhook_payload(
            payload, config=config or delivery.WhatsAppConfig()
        )


class SignatureTests(WebhookTestCase):
    def test_valid_signature(self):
        body = b'{"object":"whatsapp_business_account"}'
        self.assertTrue(
            delivery.verify_webhook_signature(body, sign(body), APP_SECRET)
        )

    def test_signature_is_over_raw_bytes(self):
        body = json.dumps(
            {"object": "whatsapp_business_account", "name": "café"},
            ensure_ascii=False,
        ).encode("utf-8")
        self.assertTrue(
            delivery.verify_webhook_signature(body, sign(body), APP_SECRET)
        )

    def test_invalid_signature_rejected(self):
        body = b'{"object":"whatsapp_business_account"}'
        self.assertFalse(
            delivery.verify_webhook_signature(body, "sha256=deadbeef", APP_SECRET)
        )

    def test_tampered_body_rejected(self):
        body = b'{"object":"whatsapp_business_account"}'
        signature = sign(body)
        self.assertFalse(
            delivery.verify_webhook_signature(body + b" ", signature, APP_SECRET)
        )

    def test_missing_header_or_secret_rejected(self):
        body = b"{}"
        self.assertFalse(
            delivery.verify_webhook_signature(body, None, APP_SECRET)
        )
        self.assertFalse(
            delivery.verify_webhook_signature(body, sign(body), None)
        )
        self.assertFalse(delivery.verify_webhook_signature(body, "", ""))


class ChallengeTests(WebhookTestCase):
    def params(self, **overrides):
        base = {
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "1158201444",
        }
        base.update(overrides)
        return base

    def test_correct_token_echoes_challenge(self):
        accepted, value = delivery.verify_webhook_challenge(
            self.params(), VERIFY_TOKEN
        )
        self.assertTrue(accepted)
        self.assertEqual(value, "1158201444")

    def test_wrong_token_rejected(self):
        accepted, _ = delivery.verify_webhook_challenge(
            self.params(**{"hub.verify_token": "nope"}), VERIFY_TOKEN
        )
        self.assertFalse(accepted)

    def test_wrong_mode_rejected(self):
        accepted, reason = delivery.verify_webhook_challenge(
            self.params(**{"hub.mode": "unsubscribe"}), VERIFY_TOKEN
        )
        self.assertFalse(accepted)
        self.assertIn("mode", reason)

    def test_unconfigured_verify_token_rejected(self):
        accepted, _ = delivery.verify_webhook_challenge(self.params(), None)
        self.assertFalse(accepted)


class ConfigTests(WebhookTestCase):
    def test_webhook_disabled_by_default(self):
        config = delivery.WhatsAppConfig()
        self.assertFalse(config.webhook_enabled)
        self.assertFalse(config.webhook_ready)
        self.assertIn(
            "WHATSAPP_WEBHOOK_ENABLED is not true", config.webhook_problems()
        )

    def test_webhook_ready_requires_secret_and_token(self):
        config = delivery.WhatsAppConfig(
            webhook_enabled=True,
            verify_token=VERIFY_TOKEN,
            app_secret=APP_SECRET,
        )
        self.assertTrue(config.webhook_ready)
        summary = config.safe_summary()
        self.assertTrue(summary["app_secret_configured"])
        self.assertTrue(summary["verify_token_configured"])
        self.assertNotIn(APP_SECRET, json.dumps(summary))
        self.assertNotIn(VERIFY_TOKEN, json.dumps(summary))

    def test_webhook_support_reports_receiver(self):
        support = delivery.webhook_support()
        self.assertTrue(support["supported"])
        self.assertEqual(support["path"], "/webhook/whatsapp")
        self.assertEqual(support["signature_header"], "x-hub-signature-256")


class StatusTests(WebhookTestCase):
    def test_delivered_updates_sent_delivery(self):
        record = self.sent_delivery()
        message_id = record["provider_message_id"]
        summary = self.process(status_payload(message_id, "delivered"))
        self.assertEqual(summary["statuses_seen"], 1)
        self.assertEqual(summary["statuses_applied"], 1)
        updated = db.get_message_delivery(record["id"])
        self.assertEqual(updated["status"], "DELIVERED")
        self.assertIsNotNone(updated["delivered_at"])
        self.assertIsNotNone(updated["webhook_sync_at"])

    def test_read_after_delivered(self):
        record = self.sent_delivery()
        message_id = record["provider_message_id"]
        self.process(status_payload(message_id, "delivered"))
        self.process(status_payload(message_id, "read"))
        updated = db.get_message_delivery(record["id"])
        self.assertEqual(updated["status"], "READ")
        self.assertIsNotNone(updated["read_at"])

    def test_failed_records_error_detail(self):
        record = self.sent_delivery()
        message_id = record["provider_message_id"]
        errors = [{"code": 131047, "message": "Re-engagement required"}]
        summary = self.process(
            status_payload(message_id, "failed", errors=errors)
        )
        self.assertEqual(summary["statuses_applied"], 1)
        updated = db.get_message_delivery(record["id"])
        self.assertEqual(updated["status"], "FAILED")
        self.assertEqual(updated["last_error_code"], "WEBHOOK_FAILED")
        self.assertIn("Re-engagement", updated["last_error_message"])

    def test_duplicate_status_is_noop(self):
        record = self.sent_delivery()
        message_id = record["provider_message_id"]
        first = self.process(status_payload(message_id, "delivered"))
        second = self.process(status_payload(message_id, "delivered"))
        self.assertEqual(first["statuses_applied"], 1)
        self.assertEqual(second["statuses_applied"], 0)
        events = db.get_message_status_events(delivery_id=record["id"])
        delivered = [e for e in events if e["event_type"] == "delivered"]
        self.assertEqual(len(delivered), 1)

    def test_orphan_status_recorded_without_delivery(self):
        summary = self.process(status_payload("wamid.unknown", "delivered"))
        self.assertEqual(summary["statuses_applied"], 1)
        events = db.get_message_status_events()
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]["delivery_id"])

    def test_invalid_status_is_ignored(self):
        record = self.sent_delivery()
        message_id = record["provider_message_id"]
        summary = self.process(status_payload(message_id, "deleted"))
        self.assertEqual(summary["statuses_applied"], 0)
        self.assertTrue(summary["errors"])
        updated = db.get_message_delivery(record["id"])
        self.assertEqual(updated["status"], "SENT")

    def test_missing_message_id_is_ignored(self):
        summary = self.process(status_payload("", "delivered"))
        self.assertEqual(summary["statuses_applied"], 0)
        self.assertEqual(db.get_message_status_events(), [])


class ReplyTests(WebhookTestCase):
    def test_reply_recorded(self):
        summary = self.process(reply_payload("wamid.reply.1"))
        self.assertEqual(summary["replies_recorded"], 1)
        replies = db.get_incoming_replies()
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["from_phone"], "919999999999")
        self.assertEqual(replies[0]["reply_type"], "text")
        self.assertEqual(replies[0]["reply_text"], "Thanks")

    def test_duplicate_reply_is_noop(self):
        first = self.process(reply_payload("wamid.reply.2"))
        second = self.process(reply_payload("wamid.reply.2"))
        self.assertEqual(first["replies_recorded"], 1)
        self.assertEqual(second["replies_recorded"], 0)
        self.assertEqual(len(db.get_incoming_replies()), 1)

    def test_image_reply_stored_without_text(self):
        payload = reply_payload("wamid.reply.3")
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        message["type"] = "image"
        message.pop("text", None)
        message["image"] = {"id": "media-1"}
        summary = self.process(payload)
        self.assertEqual(summary["replies_recorded"], 1)
        reply = db.get_incoming_replies()[0]
        self.assertEqual(reply["reply_type"], "image")
        self.assertIsNone(reply["reply_text"])

    def test_status_and_reply_in_one_payload(self):
        record = self.sent_delivery()
        payload = status_payload(record["provider_message_id"], "delivered")
        payload["entry"][0]["changes"][0]["value"]["messages"] = (
            reply_payload("wamid.reply.4")["entry"][0]["changes"][0]
            ["value"]["messages"]
        )
        summary = self.process(payload)
        self.assertEqual(summary["statuses_applied"], 1)
        self.assertEqual(summary["replies_recorded"], 1)

    def test_webhook_never_sends_a_message(self):
        with mock.patch.object(
            delivery.MockDeliveryProvider, "send"
        ) as send_mock:
            self.process(reply_payload("wamid.reply.5"))
        send_mock.assert_not_called()


class HandlerTests(WebhookTestCase):
    def setUp(self):
        super().setUp()
        import webhook
        self.webhook = webhook
        self.config = delivery.WhatsAppConfig(
            webhook_enabled=True,
            verify_token=VERIFY_TOKEN,
            app_secret=APP_SECRET,
        )

    def test_verify_handler_echoes_challenge(self):
        status, body, _ = self.webhook.handle_verify(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": VERIFY_TOKEN,
                "hub.challenge": "abc123",
            },
            self.config,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"abc123")

    def test_verify_handler_rejects_bad_token(self):
        status, _, _ = self.webhook.handle_verify(
            {"hub.mode": "subscribe", "hub.verify_token": "bad"},
            self.config,
        )
        self.assertEqual(status, 403)

    def test_notify_handler_accepts_signed_reply(self):
        body = json.dumps(reply_payload("wamid.handler.1")).encode("utf-8")
        status, response, _ = self.webhook.handle_notify(
            body, {"X-Hub-Signature-256": sign(body)}, self.config
        )
        self.assertEqual(status, 200)
        self.assertEqual(response, b"EVENT_RECEIVED")
        self.assertEqual(len(db.get_incoming_replies()), 1)

    def test_notify_handler_rejects_bad_signature(self):
        body = json.dumps(reply_payload("wamid.handler.2")).encode("utf-8")
        status, _, _ = self.webhook.handle_notify(
            body, {"X-Hub-Signature-256": "sha256=bad"}, self.config
        )
        self.assertEqual(status, 403)
        self.assertEqual(db.get_incoming_replies(), [])

    def test_notify_handler_rejects_invalid_json(self):
        body = b"not json"
        status, _, _ = self.webhook.handle_notify(
            body, {"X-Hub-Signature-256": sign(body)}, self.config
        )
        self.assertEqual(status, 400)

    def test_notify_handler_rejects_non_object_json(self):
        body = b"[1, 2, 3]"
        status, _, _ = self.webhook.handle_notify(
            body, {"X-Hub-Signature-256": sign(body)}, self.config
        )
        self.assertEqual(status, 400)

    def test_access_log_redacts_query_token(self):
        import io
        fake = type(
            "FakeHandler", (), {
                "command": "GET",
                "path": "/webhook/whatsapp?hub.verify_token=" + VERIFY_TOKEN,
            },
        )()
        buffer = io.StringIO()
        with mock.patch("sys.stderr", buffer):
            self.webhook.WhatsAppWebhookHandler.log_request(fake, 200)
        output = buffer.getvalue()
        self.assertNotIn(VERIFY_TOKEN, output)
        self.assertIn("/webhook/whatsapp", output)


if __name__ == "__main__":
    unittest.main()
