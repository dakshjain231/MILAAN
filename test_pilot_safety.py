"""Whole-system safety invariants for the +918888888888 pilot.

These are regression locks rather than tests of new behaviour: each one pins
a guarantee that is currently true and must stay true. They assert the three
enforcement layers together — the bridge's own allowlist, the Python
provider's pre-flight check, and the approval gate — plus the Cloud API
staying switched off, because the guarantee is only as good as its weakest
layer and a single-layer test would not notice if another were removed.

No test here performs a send: the bridge is never started, and the one
provider test that reaches the HTTP boundary asserts the refusal happens
*before* any request is attempted.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import db
import delivery
import inbound
import webjs

PILOT = "918888888888"
SUPPLIER = "919812345678"
ROOT = Path(__file__).resolve().parent

# The environment MILAAN actually ships with: a test recipient and no
# explicit allowlist, so the allowlist must fall back to that one number.
PILOT_ENV = {
    "MILAAN_WA_ENABLED": "true",
    "MILAAN_WA_SERVICE_URL": "http://127.0.0.1:8801",
    "MILAAN_WA_SERVICE_TOKEN": "x" * 24,
    "MILAAN_WA_TEST_RECIPIENT": PILOT,
    "MILAAN_WA_LIVE_FOLLOWUPS": "true",
}


class ExplodingTransport:
    """Fails loudly if any code path tries to reach the network."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError(
            "a send attempt reached the HTTP transport; the allowlist should "
            "have refused it first"
        )


class OutboundAllowlistTests(unittest.TestCase):
    def test_the_allowlist_is_exactly_the_pilot_number(self):
        config = webjs.load_webjs_config(PILOT_ENV)
        self.assertEqual(config.allowlist_digits(), (PILOT,))
        self.assertTrue(config.is_allowed_recipient(PILOT))
        self.assertTrue(config.is_allowed_recipient("+91 88888 88888"))

    def test_a_supplier_number_is_not_allowed(self):
        config = webjs.load_webjs_config(PILOT_ENV)
        self.assertFalse(config.is_allowed_recipient(SUPPLIER))

    def test_a_supplier_send_never_reaches_the_transport(self):
        """The refusal must happen locally, before any HTTP call."""
        config = webjs.load_webjs_config(PILOT_ENV)
        transport = ExplodingTransport()
        original = webjs._default_transport
        webjs._default_transport = transport
        try:
            response = webjs.WhatsAppWebJsProvider(config).send(SUPPLIER, "hello")
        finally:
            webjs._default_transport = original
        self.assertEqual(response.outcome, delivery.OUTCOME_INVALID_REQUEST)
        self.assertEqual(response.error_code, "RECIPIENT_NOT_ALLOWED")
        self.assertEqual(transport.calls, 0)

    def test_an_empty_allowlist_blocks_every_send(self):
        config = webjs.load_webjs_config({
            **PILOT_ENV, "MILAAN_WA_TEST_RECIPIENT": "",
            "MILAAN_WA_ALLOWED_RECIPIENTS": "",
        })
        self.assertEqual(config.allowlist_digits(), ())
        self.assertFalse(config.is_allowed_recipient(PILOT))
        self.assertFalse(config.ready)

    def test_the_allowlist_cannot_be_widened_by_leaving_it_unset(self):
        config = webjs.load_webjs_config(PILOT_ENV)
        self.assertNotIn(SUPPLIER, config.allowlist_digits())
        self.assertEqual(len(config.allowlist_digits()), 1)


class BridgeAllowlistTests(unittest.TestCase):
    """The Node bridge keeps its own allowlist, independent of the caller."""

    def _node_eval(self, script: str) -> dict:
        result = subprocess.run(
            ["node", "-e", script],
            cwd=str(ROOT / "whatsapp_service"),
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_the_bridge_allows_only_the_pilot_number(self):
        payload = self._node_eval(
            "const {loadConfig, isAllowedRecipient} = require('./src/config');"
            f"const c = loadConfig({{MILAAN_WA_SERVICE_TOKEN: '{'x' * 24}',"
            f" MILAAN_WA_TEST_RECIPIENT: '{PILOT}'}});"
            "console.log(JSON.stringify({allowed: c.allowedRecipients,"
            f" pilot: isAllowedRecipient(c, '{PILOT}'),"
            f" supplier: isAllowedRecipient(c, '{SUPPLIER}')}}));"
        )
        self.assertEqual(payload["allowed"], [PILOT])
        self.assertTrue(payload["pilot"])
        self.assertFalse(payload["supplier"])

    def test_the_bridge_blocks_everything_with_an_empty_allowlist(self):
        payload = self._node_eval(
            "const {loadConfig, isAllowedRecipient} = require('./src/config');"
            f"const c = loadConfig({{MILAAN_WA_SERVICE_TOKEN: '{'x' * 24}'}});"
            "console.log(JSON.stringify({allowed: c.allowedRecipients,"
            f" pilot: isAllowedRecipient(c, '{PILOT}')}}));"
        )
        self.assertEqual(payload["allowed"], [])
        self.assertFalse(payload["pilot"])


class CloudApiStaysOffTests(unittest.TestCase):
    def test_the_cloud_api_is_not_ready_without_its_switch(self):
        config = delivery.load_whatsapp_config({})
        self.assertFalse(config.ready)
        self.assertIn("WHATSAPP_ENABLED is not true", " ".join(config.problems()))

    def test_credentials_alone_do_not_enable_the_cloud_api(self):
        config = delivery.load_whatsapp_config({
            "WHATSAPP_ACCESS_TOKEN": "token-value",
            "WHATSAPP_PHONE_NUMBER_ID": "1234567890",
        })
        self.assertFalse(config.ready)

    def test_the_repository_env_example_does_not_enable_the_cloud_api(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("WHATSAPP_ENABLED="):
                self.assertNotIn(
                    stripped.split("=", 1)[1].strip().lower(),
                    ("true", "1", "yes", "on"),
                    "the shipped example must not enable Cloud API delivery",
                )


class InboundSenderTests(unittest.TestCase):
    def test_inbound_defaults_to_the_same_single_number(self):
        config = inbound.load_inbound_config(PILOT_ENV)
        self.assertEqual(config.senders, (PILOT,))
        self.assertTrue(config.sender_allowed(PILOT))
        self.assertFalse(config.sender_allowed(SUPPLIER))

    def test_inbound_with_no_configuration_accepts_nobody(self):
        config = inbound.load_inbound_config({})
        self.assertEqual(config.senders, ())
        self.assertFalse(config.sender_allowed(PILOT))


class NoAutomaticSendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="milaan-pilot-safety-")
        self._saved = os.environ.get("DATABASE_PATH")
        os.environ["DATABASE_PATH"] = str(Path(self.tmp) / "milaan.db")
        db.init_db()

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_live_send_without_both_confirmations_is_blocked(self):
        config = webjs.load_webjs_config(PILOT_ENV)
        for confirmed, opted_in in ((False, False), (True, False), (False, True)):
            with self.subTest(confirmed=confirmed, opt_in=opted_in):
                with self.assertRaises(webjs.WebJsBlocked):
                    webjs.send_followup_live(
                        1, config=config,
                        confirmed=confirmed, opt_in_confirmed=opted_in,
                    )

    def test_a_demo_send_without_both_confirmations_is_blocked(self):
        config = webjs.load_webjs_config(PILOT_ENV)
        with self.assertRaises(webjs.WebJsBlocked):
            webjs.demo_test_send_message(config=config, confirmed=True)

    def test_the_bridge_has_no_auto_reply_path(self):
        """An inbound message must not be able to trigger an outgoing one."""
        source = (ROOT / "whatsapp_service" / "src" / "inbound.js").read_text(
            encoding="utf-8"
        )
        for forbidden in ("sendMessage", "sendText", "reply(", "sendAndConfirm"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
