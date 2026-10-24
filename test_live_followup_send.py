"""End-to-end tests for live WhatsApp Web delivery from the website.

The whole stack runs for real — HTTP route, allowlist, preflight, delivery
records — except the one hop that would leave the machine: the bridge's HTTP
transport is a fake. No test starts Node, loads Chromium, contacts WhatsApp
or sends a message, and every phone number here is synthetic.

Run: .venv/bin/python -m pytest test_live_followup_send.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent import store

import db
import delivery
import demo_workflow
import milaan_web
import webjs

TOKEN = "route-test-service-token-1234"
# Stands in for the operator's own number; never a supplier's.
ALLOWED_RECIPIENT = "918888888888"


def live_config(**overrides):
    values = {
        "enabled": True,
        "base_url": "http://127.0.0.1:8801",
        "token": TOKEN,
        "test_recipient": ALLOWED_RECIPIENT,
        "timeout_seconds": 5.0,
        "allowed_recipients": (ALLOWED_RECIPIENT,),
        "live_followups": True,
    }
    values.update(overrides)
    return webjs.WebJsConfig(**values)


def payload(data) -> bytes:
    return json.dumps(data).encode("utf-8")


def status_response(**overrides):
    body = {
        "ok": True,
        "status": "ready",
        "ready": True,
        "qrAvailable": False,
        "phoneMasked": "…8888",
    }
    body.update(overrides)
    return (200, payload(body))


class FakeTransport:
    """Stands in for webjs._default_transport; records every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "payload": body})
        if not self.responses:
            raise AssertionError(f"unexpected transport call: {method} {url}")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def sends(self):
        return [call for call in self.calls if call["url"].endswith("/send")]


class LiveFollowupSendRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="milaan-live-send-"))
        self.old_db = os.environ.get("DATABASE_PATH")
        self.old_agent = os.environ.get("AGENT_DB_PATH")
        os.environ["DATABASE_PATH"] = str(self.tmp / "milaan.db")
        os.environ["AGENT_DB_PATH"] = str(self.tmp / "agent.db")
        store.reset_initialized()
        self.app = milaan_web.create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        for key, value in (
            ("DATABASE_PATH", self.old_db),
            ("AGENT_DB_PATH", self.old_agent),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        store.reset_initialized()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers ---------------------------------------------------------

    def draft(self, *, recipient: str | None = ALLOWED_RECIPIENT, approve=True):
        """Seed the demo workspace and return one follow-up draft id."""
        case = demo_workflow.load_demo_workspace()
        generated = self.client.post(
            "/api/followups/generate", json={"batch_id": case["batch_id"]}
        )
        self.assertEqual(generated.status_code, 200)
        draft_id = self.client.get("/api/state").get_json()["followups"][0]["id"]
        if recipient is not None:
            set_phone = self.client.post(
                f"/api/followups/{draft_id}/contact-phone",
                json={"contact_phone": recipient},
            )
            self.assertEqual(set_phone.status_code, 200)
        if approve:
            approved = self.client.post(
                f"/api/followups/{draft_id}/approve", json={"confirmed": True}
            )
            self.assertEqual(approved.status_code, 200, approved.get_data(as_text=True))
        return draft_id

    def send(self, draft_id, responses, *, config=None, body=None):
        fake = FakeTransport(responses)
        request_body = {
            "mode": webjs.LIVE_MODE,
            "confirm_send": True,
            "opt_in_confirmed": True,
        }
        if body is not None:
            request_body = body
        with (
            mock.patch.object(
                milaan_web.webjs, "load_webjs_config",
                return_value=config or live_config(),
            ),
            mock.patch.object(webjs, "_default_transport", fake),
        ):
            response = self.client.post(
                f"/api/followups/{draft_id}/send", json=request_body
            )
        return response, fake

    def preflight(self, draft_id, responses, *, config=None):
        fake = FakeTransport(responses)
        with (
            mock.patch.object(
                milaan_web.webjs, "load_webjs_config",
                return_value=config or live_config(),
            ),
            mock.patch.object(webjs, "_default_transport", fake),
        ):
            response = self.client.get(
                f"/api/followups/{draft_id}/send-preflight"
            )
        return response, fake

    def delivery_rows(self):
        with db.db() as conn:
            return [
                dict(row)
                for row in conn.execute("SELECT * FROM message_deliveries")
            ]

    # --- preflight -------------------------------------------------------

    def test_preflight_reports_ready_to_send(self):
        draft_id = self.draft()
        response, fake = self.preflight(draft_id, [status_response()])
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["available"], body["problems"])
        self.assertTrue(body["recipient_allowed"])
        self.assertTrue(body["draft_eligible"])
        self.assertTrue(body["can_send"])
        self.assertIsNone(body["existing_delivery"])
        self.assertEqual(body["allowed_recipients_masked"], ["…8888"])
        self.assertEqual(body["recipient_masked"], "+91••••••8888")
        # Checking never sends.
        self.assertEqual(fake.sends(), [])
        self.assertEqual(self.delivery_rows(), [])
        self.assertNotIn(ALLOWED_RECIPIENT, response.get_data(as_text=True))

    def test_preflight_reports_the_live_switch_being_off(self):
        draft_id = self.draft()
        response, _ = self.preflight(
            draft_id, [status_response()], config=live_config(live_followups=False)
        )
        body = response.get_json()
        self.assertFalse(body["available"])
        self.assertFalse(body["can_send"])
        self.assertIn("MILAAN_WA_LIVE_FOLLOWUPS", " ".join(body["problems"]))

    def test_preflight_reports_a_supplier_recipient_as_not_allowed(self):
        # Keep the supplier's own number on the draft.
        draft_id = self.draft(recipient=None)
        response, _ = self.preflight(draft_id, [status_response()])
        body = response.get_json()
        self.assertTrue(body["available"], body["problems"])
        self.assertFalse(body["recipient_allowed"])
        self.assertFalse(body["can_send"])

    def test_preflight_for_a_missing_draft_is_404(self):
        response, _ = self.preflight(999_999, [])
        self.assertEqual(response.status_code, 404)

    # --- routing guards --------------------------------------------------

    def test_unknown_mode_is_refused(self):
        draft_id = self.draft()
        response, fake = self.send(
            draft_id, [], body={
                "mode": "whatsapp", "confirm_send": True,
                "opt_in_confirmed": True,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.delivery_rows(), [])

    def test_both_confirmations_are_required(self):
        draft_id = self.draft()
        for body in (
            {"mode": webjs.LIVE_MODE, "opt_in_confirmed": True},
            {"mode": webjs.LIVE_MODE, "confirm_send": True},
            {"mode": webjs.LIVE_MODE},
        ):
            response, fake = self.send(draft_id, [], body=body)
            self.assertEqual(response.status_code, 400, body)
            self.assertIn("No message was sent", response.get_json()["error"])
            self.assertEqual(fake.calls, [])
        self.assertEqual(self.delivery_rows(), [])

    def test_mock_mode_still_simulates_without_touching_the_bridge(self):
        draft_id = self.draft()
        response, fake = self.send(
            draft_id, [], body={
                "mode": "mock", "confirm_send": True, "opt_in_confirmed": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        record = response.get_json()["delivery"]
        self.assertEqual(record["provider"], delivery.PROVIDER_MOCK)
        self.assertEqual(record["status"], "SENT")
        self.assertTrue(record["message_id"].startswith("mock-"))
        self.assertEqual(fake.calls, [])

    def test_supplier_recipient_is_refused_with_nothing_sent(self):
        draft_id = self.draft(recipient=None)
        response, fake = self.send(draft_id, [status_response()])
        self.assertEqual(response.status_code, 409)
        self.assertIn("allowlist", response.get_json()["error"])
        self.assertIn("No message was sent", response.get_json()["error"])
        self.assertEqual(fake.sends(), [])
        self.assertEqual(self.delivery_rows(), [])

    def test_unlinked_bridge_is_refused_with_nothing_sent(self):
        draft_id = self.draft()
        response, fake = self.send(
            draft_id,
            [status_response(status="qr", ready=False, qrAvailable=True)],
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("QR", response.get_json()["error"])
        self.assertEqual(fake.sends(), [])
        self.assertEqual(self.delivery_rows(), [])

    def test_unapproved_draft_is_refused(self):
        draft_id = self.draft(approve=False)
        response, fake = self.send(draft_id, [status_response()])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(fake.sends(), [])
        self.assertEqual(self.delivery_rows(), [])

    # --- real delivery ---------------------------------------------------

    def test_accepted_send_records_the_real_status_and_message_id(self):
        draft_id = self.draft()
        response, fake = self.send(
            draft_id,
            [
                status_response(),
                (200, payload({"ok": True, "messageId": "wamid.HBgM1234"})),
            ],
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body["mode"], webjs.LIVE_MODE)
        self.assertFalse(body["simulated"])
        record = body["delivery"]
        self.assertEqual(record["provider"], delivery.PROVIDER_WHATSAPP_WEB)
        self.assertEqual(record["status"], "SENT")
        self.assertEqual(record["message_id"], "wamid.HBgM1234")
        self.assertEqual(record["to"], "+91••••••8888")
        self.assertTrue(record["opt_in"])
        # Exactly one real send, to the allowlisted number only.
        sends = fake.sends()
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0]["payload"]["to"], ALLOWED_RECIPIENT)
        rows = self.delivery_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider"], delivery.PROVIDER_WHATSAPP_WEB)
        self.assertEqual(rows[0]["provider_message_id"], "wamid.HBgM1234")
        # The number itself never appears in an API response.
        self.assertNotIn(ALLOWED_RECIPIENT, response.get_data(as_text=True))
        # The follow-up now counts as contacted (mock sends never do).
        drafts = self.client.get("/api/state").get_json()["followups"]
        contacted = [d for d in drafts if d["id"] == draft_id][0]
        self.assertTrue(contacted["last_followup"])

    def test_bridge_failure_is_not_reported_as_delivered(self):
        draft_id = self.draft()
        response, _ = self.send(
            draft_id,
            [status_response(), (409, payload({"ok": False, "error": "not_ready"}))],
        )
        self.assertEqual(response.status_code, 200)
        record = response.get_json()["delivery"]
        self.assertEqual(record["status"], "FAILED")
        self.assertEqual(record["message_id"], "")
        self.assertTrue(record["error_code"])

    def test_ambiguous_outcome_is_recorded_as_unknown(self):
        draft_id = self.draft()
        response, _ = self.send(
            draft_id,
            [
                status_response(),
                (502, payload({"ok": False, "error": "send_outcome_unknown"})),
            ],
        )
        record = response.get_json()["delivery"]
        self.assertEqual(record["status"], "UNKNOWN")
        self.assertEqual(record["message_id"], "")

    def test_a_webjs_delivery_is_never_retried_automatically(self):
        draft_id = self.draft()
        response, _ = self.send(
            draft_id,
            [
                status_response(),
                (502, payload({"ok": False, "error": "send_outcome_unknown"})),
            ],
        )
        delivery_id = response.get_json()["delivery"]["id"]
        retried = self.client.post(f"/api/deliveries/{delivery_id}/retry")
        self.assertEqual(retried.status_code, 409)
        self.assertIn("never retried automatically", retried.get_json()["error"])

    def test_state_reports_the_live_channel_and_masked_allowlist(self):
        with mock.patch.object(
            milaan_web.webjs, "load_webjs_config", return_value=live_config()
        ):
            provider = self.client.get("/api/state").get_json()["provider"]
        self.assertTrue(provider["webjs"])
        self.assertTrue(provider["webjs_live"])
        self.assertEqual(provider["webjs_allowed_recipients"], ["…8888"])
        # Cloud API stays off in this configuration.
        self.assertFalse(provider["cloud"])


class ProviderCheckMigrationTests(unittest.TestCase):
    """The provider CHECK migration keeps existing deliveries intact."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="milaan-provider-migration-"))
        self.old_db = os.environ.get("DATABASE_PATH")
        os.environ["DATABASE_PATH"] = str(self.tmp / "milaan.db")
        db.init_db()

    def tearDown(self):
        if self.old_db is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = self.old_db
        shutil.rmtree(self.tmp, ignore_errors=True)

    def needs_migration(self) -> bool:
        conn = sqlite3.connect(db.get_database_path())
        conn.row_factory = sqlite3.Row
        try:
            return db._needs_message_delivery_provider_migration(conn)
        finally:
            conn.close()

    def downgrade_provider_check(self) -> None:
        """Rebuild message_deliveries with the pre-integration CHECK."""
        old_ddl = db.MESSAGE_DELIVERIES_DDL.replace(
            "'whatsapp_cloud_api', 'mock', 'whatsapp_web_js'",
            "'whatsapp_cloud_api', 'mock'",
        ).replace(
            "CREATE TABLE IF NOT EXISTS message_deliveries (",
            "CREATE TABLE message_deliveries_old (",
        )
        self.assertNotIn("whatsapp_web_js", old_ddl)
        with db.db() as conn:
            columns = ", ".join(
                row["name"]
                for row in conn.execute("PRAGMA table_info(message_deliveries)")
            )
            conn.executescript(
                old_ddl
                + f"INSERT INTO message_deliveries_old ({columns}) "
                + f"SELECT {columns} FROM message_deliveries;"
                + "DROP TABLE message_deliveries;"
                + "ALTER TABLE message_deliveries_old "
                + "RENAME TO message_deliveries;"
            )

    def test_existing_deliveries_survive_and_the_new_provider_is_accepted(self):
        case = demo_workflow.load_demo_workspace()
        draft_id = db.generate_followup_drafts(case["batch_id"])["draft_ids"][0]
        db.approve_followup_draft(draft_id, confirmed=True)
        record = delivery.deliver_approved_draft(
            draft_id, delivery.MockDeliveryProvider()
        )
        self.assertEqual(record["status"], "SENT")

        self.downgrade_provider_check()
        with db.db() as conn:
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'message_deliveries'"
            ).fetchone()["sql"]
        self.assertNotIn("whatsapp_web_js", sql)
        self.assertTrue(self.needs_migration())

        db.init_db()  # migrates; must not drop or change the stored delivery

        with db.db() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM message_deliveries ORDER BY id"
                )
            ]
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'message_deliveries'"
            ).fetchone()["sql"]
            indexes = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'index' AND tbl_name = 'message_deliveries'"
                )
            }
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        self.assertIn("whatsapp_web_js", sql)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], record["id"])
        self.assertEqual(rows[0]["provider"], delivery.PROVIDER_MOCK)
        self.assertEqual(rows[0]["status"], "SENT")
        self.assertEqual(
            rows[0]["idempotency_key"], record["idempotency_key"]
        )
        self.assertEqual(
            rows[0]["message_body_snapshot"], record["message_body_snapshot"]
        )
        self.assertIn("idx_message_deliveries_draft_unique", indexes)
        self.assertIn("idx_message_deliveries_provider", indexes)
        self.assertEqual(violations, [])

    def test_repeated_init_does_not_rebuild_again(self):
        # Idempotent: a current database needs no migration.
        self.assertFalse(self.needs_migration())
        db.init_db()
        db.init_db()
        with db.db() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertIn("message_deliveries", tables)
        self.assertNotIn("message_deliveries_new", tables)


PAGE = Path(__file__).resolve().parent / "agent" / "web" / "primary.html"


@unittest.skipIf(shutil.which("node") is None, "Node.js is not installed")
class LiveSendPanelTests(unittest.TestCase):
    """The panel's own logic, run in Node exactly as the browser would."""

    FUNCTIONS = (
        "liveSendPanel", "deliveryProviderLabel", "deliveryStatusNote",
        "liveSendToast",
    )

    def run_js(self, expression: str):
        from test_live_browser_regressions import _extract_function

        html = PAGE.read_text(encoding="utf-8")
        code = "function esc(v){return String(v == null ? '' : v);}\n"
        code += "\n".join(
            _extract_function(html, name) for name in self.FUNCTIONS
        )
        code += f"\nprocess.stdout.write(JSON.stringify({expression}));"
        out = subprocess.run(
            ["node", "-e", code], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def panel(self, pre) -> str:
        return self.run_js(f"liveSendPanel({{id: 1}}, {json.dumps(pre)})")

    def ready_preflight(self, **overrides):
        value = {
            "draft_id": 1,
            "available": True,
            "problems": [],
            "recipient_allowed": True,
            "draft_eligible": True,
            "draft_problems": [],
            "existing_delivery": None,
            "can_send": True,
            "recipient_masked": "+91••••••8888",
            "allowed_recipients_masked": ["…8888"],
            "linked_phone_masked": "…8888",
        }
        value.update(overrides)
        return value

    def test_send_button_appears_only_when_a_real_send_could_run(self):
        panel = self.panel(self.ready_preflight())
        self.assertIn("Send WhatsApp message", panel)
        # The button arms a second confirmation; it never sends directly.
        self.assertIn("armLiveSend(1)", panel)
        self.assertNotIn("confirmLiveSend(1)", panel)
        self.assertIn("Nothing is sent until you confirm", panel)

    def test_no_send_button_while_the_bridge_is_unavailable(self):
        panel = self.panel(
            self.ready_preflight(
                available=False,
                can_send=False,
                problems=["the local WhatsApp Web bridge is not reachable"],
            )
        )
        self.assertNotIn("Send WhatsApp message", panel)
        self.assertIn("not reachable", panel)

    def test_no_send_button_for_a_recipient_outside_the_allowlist(self):
        panel = self.panel(
            self.ready_preflight(
                recipient_allowed=False,
                can_send=False,
                recipient_masked="+91••••••2333",
            )
        )
        self.assertNotIn("Send WhatsApp message", panel)
        self.assertIn("not on the WhatsApp Web allowlist", panel)
        self.assertIn("Suppliers are never messaged", panel)

    def test_no_send_button_once_a_delivery_exists(self):
        panel = self.panel(
            self.ready_preflight(
                can_send=False,
                existing_delivery={
                    "provider": "whatsapp_web_js", "status": "SENT",
                    "message_id": "wamid.1",
                },
            )
        )
        self.assertNotIn("Send WhatsApp message", panel)
        self.assertIn("already has a delivery record", panel)
        self.assertIn("wamid.1", panel)

    def test_while_checking_nothing_is_offered(self):
        panel = self.run_js("liveSendPanel({id: 1}, null)")
        self.assertNotIn("Send WhatsApp message", panel)
        self.assertIn("Checking", panel)

    def test_a_simulation_is_never_described_as_a_delivery(self):
        self.assertEqual(
            self.run_js('deliveryProviderLabel("mock")'), "Simulation"
        )
        self.assertEqual(
            self.run_js('deliveryProviderLabel("whatsapp_web_js")'),
            "WhatsApp Web",
        )
        note = self.run_js('deliveryStatusNote({provider: "mock", status: "SENT"})')
        self.assertIn("no WhatsApp message was sent", note)

    def test_toasts_report_the_recorded_outcome_honestly(self):
        sent, kind = self.run_js(
            'liveSendToast({status: "SENT", message_id: "wamid.9", to: "…8888"})'
        )
        self.assertEqual(kind, "ok")
        self.assertIn("wamid.9", sent)
        self.assertIn("not a read receipt", sent)

        unknown, kind = self.run_js(
            'liveSendToast({status: "UNKNOWN", message_id: "", error_code: "SERVICE_TIMEOUT"})'
        )
        self.assertEqual(kind, "bad")
        self.assertIn("may or may not", unknown)

        failed, kind = self.run_js(
            'liveSendToast({status: "FAILED", message_id: "", error_code: "SERVICE_NOT_READY", error: "not linked"})'
        )
        self.assertEqual(kind, "bad")
        self.assertIn("Not sent", failed)

        # A SENT record without a message id is never announced as sent.
        missing, kind = self.run_js('liveSendToast({status: "SENT", message_id: ""})')
        self.assertEqual(kind, "bad")
        self.assertNotIn("Sent to", missing)


if __name__ == "__main__":
    unittest.main()
