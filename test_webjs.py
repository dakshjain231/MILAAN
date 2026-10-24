"""Tests for the local whatsapp-web.js demo bridge.

Run: .venv/bin/python -m unittest test_webjs -v

No test starts Node, loads Chromium, contacts WhatsApp, or sends a message.
HTTP is replaced by a fake transport and every payload is synthetic.
"""

import base64
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

sys.path.insert(0, str(Path(__file__).parent))

import db  # noqa: E402
import delivery  # noqa: E402
import gstr2b  # noqa: E402
import webjs  # noqa: E402

_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_CSV_HEADER = (
    "supplier_gstin,invoice_number,invoice_date,tax_period,"
    "taxable_value,cgst,sgst,igst,total_gst"
)
PERIOD = "2026-03"
TOKEN = "test-service-token-123456"
TEST_RECIPIENT = "918888888888"


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


def body(payload) -> bytes:
    return json.dumps(payload).encode("utf-8")


def ready_config(**overrides):
    values = {
        "enabled": True,
        "base_url": "http://127.0.0.1:8801",
        "token": TOKEN,
        "test_recipient": TEST_RECIPIENT,
        "timeout_seconds": 5.0,
    }
    values.update(overrides)
    return webjs.WebJsConfig(**values)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, payload, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "payload": payload,
                "timeout": timeout,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected transport call")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class ConfigTests(unittest.TestCase):
    def test_disabled_by_default(self):
        config = webjs.load_webjs_config({})
        self.assertFalse(config.enabled)
        self.assertFalse(config.ready)
        self.assertIn("MILAAN_WA_ENABLED is not true", config.problems())

    def test_enabled_requires_token_and_test_recipient(self):
        config = webjs.load_webjs_config(
            {"MILAAN_WA_ENABLED": "true", "MILAAN_WA_SERVICE_URL": "http://127.0.0.1:8801"}
        )
        problems = " ".join(config.problems())
        self.assertIn("MILAAN_WA_SERVICE_TOKEN", problems)
        self.assertIn("MILAAN_WA_TEST_RECIPIENT", problems)

    def test_ready_config(self):
        config = ready_config()
        self.assertTrue(config.ready, config.problems())

    def test_non_loopback_rejected(self):
        config = ready_config(base_url="http://example.com:8801")
        self.assertIn(
            "loopback host", " ".join(config.problems())
        )

    def test_https_and_credentials_rejected(self):
        https = ready_config(base_url="https://127.0.0.1:8801")
        self.assertIn("http URL", " ".join(https.problems()))
        with_creds = ready_config(base_url="http://user:pass@127.0.0.1:8801")
        self.assertIn("credentials", " ".join(with_creds.problems()))

    def test_short_token_rejected(self):
        config = ready_config(token="short")
        self.assertIn("MILAAN_WA_SERVICE_TOKEN", " ".join(config.problems()))

    def test_safe_summary_never_contains_token(self):
        summary = ready_config().safe_summary()
        self.assertNotIn(TOKEN, json.dumps(summary))
        self.assertEqual(summary["test_recipient_masked"], "\u20268888")

    def test_load_reads_environment(self):
        config = webjs.load_webjs_config(
            {
                "MILAAN_WA_ENABLED": "yes",
                "MILAAN_WA_SERVICE_URL": "http://127.0.0.1:9000",
                "MILAAN_WA_SERVICE_TOKEN": TOKEN,
                "MILAAN_WA_TEST_RECIPIENT": TEST_RECIPIENT,
                "MILAAN_WA_TIMEOUT_SECONDS": "4.5",
            }
        )
        self.assertTrue(config.ready, config.problems())
        self.assertEqual(config.timeout_seconds, 4.5)


class StatusTests(unittest.TestCase):
    def test_disabled_makes_no_request(self):
        fake = FakeTransport([])
        with mock.patch.object(webjs, "_default_transport", fake):
            result = webjs.service_status(webjs.load_webjs_config({}))
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(fake.calls, [])

    def test_unreachable_is_reported_without_raising(self):
        fake = FakeTransport([requests.ConnectionError("refused")])
        with mock.patch.object(webjs, "_default_transport", fake):
            result = webjs.service_status(ready_config())
        self.assertFalse(result["reachable"])
        self.assertEqual(result["status"], "unreachable")

    def test_ready_status(self):
        fake = FakeTransport([
            (200, body({
                "ok": True,
                "status": "ready",
                "ready": True,
                "qrAvailable": False,
                "phoneMasked": "\u20268888",
                "since": "2026-10-22T00:00:00.000Z",
            }))
        ])
        with mock.patch.object(webjs, "_default_transport", fake):
            result = webjs.service_status(ready_config())
        self.assertTrue(result["reachable"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["status"], "ready")
        authorization = fake.calls[0]["headers"]["Authorization"]
        self.assertEqual(authorization, f"Bearer {TOKEN}")
        self.assertNotIn(TOKEN, json.dumps(result))

    def test_unauthorized_status(self):
        fake = FakeTransport([(401, body({"ok": False, "error": "unauthorized"}))])
        with mock.patch.object(webjs, "_default_transport", fake):
            result = webjs.service_status(ready_config())
        self.assertTrue(result["reachable"])
        self.assertEqual(result["status"], "unauthorized")
        self.assertFalse(result["ready"])

    def test_qr_data_url_and_png(self):
        png = b"\x89PNG\r\n\x1a\n" + b"test-bytes"
        data_url = "data:image/png;base64," + base64.b64encode(png).decode()
        fake = FakeTransport([(200, body({"ok": True, "status": "qr", "qr": data_url}))])
        with mock.patch.object(webjs, "_default_transport", fake):
            self.assertEqual(webjs.fetch_qr_data_url(ready_config()), data_url)
        fake = FakeTransport([(200, body({"ok": True, "status": "qr", "qr": data_url}))])
        with mock.patch.object(webjs, "_default_transport", fake):
            self.assertEqual(webjs.fetch_qr_png(ready_config()), png)

    def test_qr_rejects_non_png_payload(self):
        fake = FakeTransport([(200, body({"ok": True, "status": "qr", "qr": "javascript:alert(1)"}))])
        with mock.patch.object(webjs, "_default_transport", fake):
            self.assertIsNone(webjs.fetch_qr_data_url(ready_config()))
            self.assertIsNone(webjs.fetch_qr_png(ready_config()))


class ProviderTests(unittest.TestCase):
    def send(self, response):
        fake = FakeTransport([response])
        with mock.patch.object(webjs, "_default_transport", fake):
            result = webjs.WhatsAppWebJsProvider(ready_config()).send(
                "9999999999", "Hello"
            )
        return result, fake

    def test_accepted(self):
        result, fake = self.send((200, body({"ok": True, "messageId": "wamid.1"})))
        self.assertEqual(result.outcome, delivery.OUTCOME_ACCEPTED)
        self.assertEqual(result.provider_message_id, "wamid.1")
        self.assertEqual(fake.calls[0]["url"], "http://127.0.0.1:8801/send")
        self.assertEqual(fake.calls[0]["payload"]["to"], "9999999999")

    def test_missing_message_id_is_unknown(self):
        result, _ = self.send((200, body({"ok": True})))
        self.assertEqual(result.outcome, delivery.OUTCOME_UNKNOWN)
        self.assertEqual(result.error_code, "MISSING_PROVIDER_MESSAGE_ID")

    def test_auth_error(self):
        result, _ = self.send((401, body({"ok": False, "error": "unauthorized"})))
        self.assertEqual(result.outcome, delivery.OUTCOME_AUTH_ERROR)

    def test_not_ready_is_config_error(self):
        result, _ = self.send((409, body({"ok": False, "error": "not_ready"})))
        self.assertEqual(result.outcome, delivery.OUTCOME_CONFIG_ERROR)

    def test_bad_request_is_invalid(self):
        result, _ = self.send((400, body({"ok": False, "error": "invalid_recipient"})))
        self.assertEqual(result.outcome, delivery.OUTCOME_INVALID_REQUEST)

    def test_ambiguous_send_failures_are_unknown_not_retryable(self):
        for code in ("send_outcome_unknown", "missing_message_id", "send_failed"):
            result, _ = self.send((502, body({"ok": False, "error": code})))
            self.assertEqual(result.outcome, delivery.OUTCOME_UNKNOWN, code)
            self.assertIn("may or may not", result.error_message)

    def test_transient_failures_are_retryable(self):
        for status in (429, 500, 503):
            result, _ = self.send((status, body({"ok": False, "error": "busy"})))
            self.assertEqual(result.outcome, delivery.OUTCOME_RETRYABLE)

    def test_invalid_recipient_is_rejected_locally(self):
        fake = FakeTransport([])
        with mock.patch.object(webjs, "_default_transport", fake):
            result = webjs.WhatsAppWebJsProvider(ready_config()).send("1", "Hello")
        self.assertEqual(result.outcome, delivery.OUTCOME_INVALID_REQUEST)
        self.assertEqual(fake.calls, [])

    def test_template_is_unsupported(self):
        result, fake = self.send((200, body({"ok": True, "messageId": "x"})))
        self.assertEqual(result.outcome, delivery.OUTCOME_ACCEPTED)
        fake = FakeTransport([])
        with mock.patch.object(webjs, "_default_transport", fake):
            blocked = webjs.WhatsAppWebJsProvider(ready_config()).send(
                "9999999999", "Hello", template_name="t", template_language="en"
            )
        self.assertEqual(blocked.outcome, delivery.OUTCOME_CONFIG_ERROR)
        self.assertEqual(fake.calls, [])

    def test_timeout_is_unknown(self):
        result, _ = self.send(requests.Timeout("timed out"))
        self.assertEqual(result.outcome, delivery.OUTCOME_UNKNOWN)
        self.assertEqual(result.error_code, "SERVICE_TIMEOUT")

    def test_connection_error_is_unknown_and_redacts_token(self):
        result, _ = self.send(requests.ConnectionError(f"refused {TOKEN}"))
        self.assertEqual(result.outcome, delivery.OUTCOME_UNKNOWN)
        self.assertNotIn(TOKEN, str(result.error_message))

    def test_errors_never_contain_token(self):
        result, _ = self.send((500, body({"ok": False, "error": f"bad {TOKEN}"})))
        self.assertNotIn(TOKEN, json.dumps(result.__dict__))


class DatabaseTestCase(unittest.TestCase):
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

    def add_invoice(self, supplier_id, number, total_gst=225_000):
        taxable = 1_250_000
        with db.db() as conn:
            return conn.execute(
                """INSERT INTO invoices
                       (supplier_id, invoice_number, invoice_date, tax_period,
                        taxable_value, gst_rate, cgst, sgst, igst, total_gst,
                        invoice_total, validation_status)
                   VALUES (?, ?, '2026-03-05', ?, ?, 18, ?, 0, 0, ?, ?, 'valid')""",
                (supplier_id, number, PERIOD, taxable, total_gst,
                 total_gst, taxable + total_gst),
            ).lastrowid

    def approved_draft(self, approve=True):
        supplier_a = self.add_supplier("Alpha Traders", GSTIN_A, "9999999999")
        self.add_invoice(supplier_a, "INV-A-1")
        supplier_b = self.add_supplier("Beta Traders", GSTIN_B, "8888888888")
        self.add_invoice(supplier_b, "INV-B-1", total_gst=90_000)
        parsed = gstr2b.parse_gstr2b(
            csv_bytes([gstr2b_row(GSTIN_B, "INV-B-1", 90_000)]),
            "webjs.csv",
        )
        batch_id = db.save_gstr2b_import(parsed)["import_id"]
        db.reconcile_gstr2b_import(batch_id)
        draft_id = db.generate_followup_drafts(batch_id)["draft_ids"][0]
        if approve:
            db.approve_followup_draft(draft_id, confirmed=True)
        return draft_id

    def delivery_count(self):
        with db.db() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM message_deliveries"
            ).fetchone()[0]


class DemoSendTests(DatabaseTestCase):
    def test_requires_both_confirmations(self):
        draft_id = self.approved_draft()
        with self.assertRaises(webjs.WebJsBlocked):
            webjs.demo_test_send(
                draft_id, config=ready_config(), confirmed=True,
                opt_in_confirmed=False,
            )
        with self.assertRaises(webjs.WebJsBlocked):
            webjs.demo_test_send(
                draft_id, config=ready_config(), confirmed=False,
                opt_in_confirmed=True,
            )

    def test_requires_consented_test_recipient(self):
        draft_id = self.approved_draft()
        config = ready_config(test_recipient=None)
        with self.assertRaises(webjs.WebJsConfigurationError):
            webjs.demo_test_send(
                draft_id, config=config, confirmed=True, opt_in_confirmed=True
            )

    def test_rejects_unapproved_draft(self):
        draft_id = self.approved_draft(approve=False)
        with self.assertRaises(webjs.WebJsBlocked):
            webjs.demo_test_send(
                draft_id, config=ready_config(), confirmed=True,
                opt_in_confirmed=True,
            )

    def test_sends_only_to_test_recipient_and_audits(self):
        draft_id = self.approved_draft()
        fake = FakeTransport([(200, body({"ok": True, "messageId": "wamid.demo.1"}))])
        with mock.patch.object(webjs, "_default_transport", fake):
            result = webjs.demo_test_send(
                draft_id, config=ready_config(), actor="local-demo",
                confirmed=True, opt_in_confirmed=True,
            )
        self.assertEqual(fake.calls[0]["payload"]["to"], TEST_RECIPIENT)
        self.assertNotEqual(fake.calls[0]["payload"]["to"], "9999999999")
        self.assertEqual(result["outcome"], delivery.OUTCOME_ACCEPTED)
        self.assertEqual(result["provider_message_id"], "wamid.demo.1")
        self.assertFalse(result["recorded_as_delivery"])
        self.assertTrue(result["audit_recorded"])
        self.assertEqual(self.delivery_count(), 0)
        events = db.get_system_audit_events()
        self.assertTrue(any(e["Event"] == "webjs_demo_send" for e in events))
        serialized = json.dumps(events)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn("9999999999", serialized)

    def test_failed_send_is_audited_without_delivery(self):
        draft_id = self.approved_draft()
        fake = FakeTransport([(409, body({"ok": False, "error": "not_ready"}))])
        with mock.patch.object(webjs, "_default_transport", fake):
            result = webjs.demo_test_send(
                draft_id, config=ready_config(), confirmed=True,
                opt_in_confirmed=True,
            )
        self.assertEqual(result["outcome"], delivery.OUTCOME_CONFIG_ERROR)
        self.assertEqual(self.delivery_count(), 0)
        self.assertTrue(result["audit_recorded"])

    def test_audit_failure_does_not_hide_result(self):
        draft_id = self.approved_draft()
        fake = FakeTransport([(200, body({"ok": True, "messageId": "wamid.demo.2"}))])
        with mock.patch.object(webjs, "_default_transport", fake), \
                mock.patch.object(db, "record_system_audit_event", side_effect=RuntimeError("boom")):
            result = webjs.demo_test_send(
                draft_id, config=ready_config(), confirmed=True,
                opt_in_confirmed=True,
            )
        self.assertEqual(result["outcome"], delivery.OUTCOME_ACCEPTED)
        self.assertFalse(result["audit_recorded"])



class StandaloneDemoSendTests(DatabaseTestCase):
    def test_requires_both_confirmations(self):
        with self.assertRaises(webjs.WebJsBlocked):
            webjs.demo_test_send_message(
                config=ready_config(), confirmed=True, opt_in_confirmed=False,
            )
        with self.assertRaises(webjs.WebJsBlocked):
            webjs.demo_test_send_message(
                config=ready_config(), confirmed=False, opt_in_confirmed=True,
            )

    def test_requires_consented_test_recipient(self):
        with self.assertRaises(webjs.WebJsConfigurationError):
            webjs.demo_test_send_message(
                config=ready_config(test_recipient=None),
                confirmed=True,
                opt_in_confirmed=True,
            )

    def test_sends_fixed_demo_message_without_any_draft(self):
        fake = FakeTransport([(200, body({"ok": True, "messageId": "wamid.demo.3"}))])
        with mock.patch.object(webjs, "_default_transport", fake):
            result = webjs.demo_test_send_message(
                config=ready_config(), actor="local-demo",
                confirmed=True, opt_in_confirmed=True,
            )
        self.assertEqual(fake.calls[0]["payload"]["to"], TEST_RECIPIENT)
        self.assertEqual(fake.calls[0]["payload"]["message"], webjs.DEMO_TEST_MESSAGE)
        self.assertEqual(result["outcome"], delivery.OUTCOME_ACCEPTED)
        self.assertIsNone(result["draft_id"])
        self.assertEqual(result["mode"], "standalone")
        self.assertFalse(result["recorded_as_delivery"])
        self.assertTrue(result["audit_recorded"])
        self.assertEqual(self.delivery_count(), 0)
        events = db.get_system_audit_events()
        self.assertTrue(any(e["Event"] == "webjs_demo_send" for e in events))
        serialized = json.dumps(events)
        self.assertNotIn(TOKEN, serialized)
        self.assertNotIn("9999999999", serialized)

    def test_rejects_empty_or_oversized_message(self):
        with self.assertRaises(webjs.WebJsBlocked):
            webjs.demo_test_send_message(
                "   ", config=ready_config(), confirmed=True,
                opt_in_confirmed=True,
            )
        with self.assertRaises(webjs.WebJsBlocked):
            webjs.demo_test_send_message(
                "x" * 4097, config=ready_config(), confirmed=True,
                opt_in_confirmed=True,
            )




if __name__ == "__main__":
    unittest.main()
