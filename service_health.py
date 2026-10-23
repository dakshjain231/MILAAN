"""Service health checks for the MILAAN workspace.

Every check returns a display-safe dictionary: status, a short explanation,
and an actionable next step.  No secret, token, session cookie, access token,
or full phone number is ever placed in a result.  A green check means the
service answered a health request; it does not prove that a WhatsApp message
was delivered.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path
from typing import Any

import requests

import db
import delivery
import webjs
from agent import store

AGENT_SERVER_HEALTH = "http://127.0.0.1:5050/health"


def _result(key: str, label: str, status: str, detail: str, action: str = "") -> dict:
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        "action": action,
    }


def operational_database() -> dict:
    path = Path(db.get_database_path())
    try:
        ready = db.is_db_ready()
    except Exception as exc:
        return _result(
            "operational_db",
            "Operational database",
            "error",
            f"Could not open {path.name}: {type(exc).__name__}",
            "Check file permissions and run the launcher again.",
        )
    if not path.is_file():
        return _result(
            "operational_db",
            "Operational database",
            "error",
            "Database file is missing.",
            "Start MILAAN once to initialise the workspace.",
        )
    if not ready:
        return _result(
            "operational_db",
            "Operational database",
            "error",
            "The database exists but its schema is incomplete.",
            "Run `python -c \"import db; db.init_db()\"` in the project.",
        )
    return _result(
        "operational_db",
        "Operational database",
        "ok",
        f"Ready · {path.name}",
    )


def agent_database() -> dict:
    path = Path(store.db_path())
    if not path.is_file() or path.stat().st_size == 0:
        return _result(
            "agent_db",
            "Agent database",
            "off",
            "Not created yet.",
            "Start the agent seed or send a WhatsApp message to create it.",
        )
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            traders = (
                connection.execute("SELECT COUNT(*) FROM agent_traders").fetchone()[0]
                if "agent_traders" in tables
                else 0
            )
    except sqlite3.Error as exc:
        return _result(
            "agent_db",
            "Agent database",
            "error",
            f"Could not read {path.name}: {type(exc).__name__}",
            "Check the agent database path and file permissions.",
        )
    if not tables:
        return _result(
            "agent_db",
            "Agent database",
            "error",
            "Database file has no schema.",
            "Run a safe seed against a temporary database.",
        )
    if not traders:
        return _result(
            "agent_db",
            "Agent database",
            "warn",
            "Schema ready, no trader records yet.",
            "Seed a temporary database or message the agent.",
        )
    return _result(
        "agent_db",
        "Agent database",
        "ok",
        f"Ready · {traders} trader record(s)",
    )


def whatsapp_web() -> dict:
    try:
        config = webjs.load_webjs_config()
        # Keep page renders quick; a full status request can wait longer in
        # the dedicated Delivery tab.
        fast = dataclasses.replace(
            config, timeout_seconds=min(2.0, float(config.timeout_seconds or 2.0))
        )
        status = webjs.service_status(fast)
    except Exception as exc:
        return _result(
            "whatsapp_web",
            "WhatsApp Web (whatsapp-web.js)",
            "error",
            f"Could not read configuration: {type(exc).__name__}",
            "Check MILAAN_WA_* variables in .env.",
        )
    if not config.enabled:
        return _result(
            "whatsapp_web",
            "WhatsApp Web (whatsapp-web.js)",
            "off",
            "Disabled by configuration.",
            "Set MILAAN_WA_ENABLED=true to use the local QR-linked channel.",
        )
    if config.problems():
        return _result(
            "whatsapp_web",
            "WhatsApp Web (whatsapp-web.js)",
            "error",
            "Configuration incomplete: " + "; ".join(config.problems()),
            "Fix the MILAAN_WA_* variables, then restart the launcher.",
        )
    if not status.get("reachable"):
        return _result(
            "whatsapp_web",
            "WhatsApp Web (whatsapp-web.js)",
            "error",
            "Service is not reachable at " + str(config.base_url),
            "Run `./run_milaan.sh`; it starts the local service when enabled.",
        )
    state = str(status.get("status") or "unknown")
    if status.get("ready"):
        return _result(
            "whatsapp_web",
            "WhatsApp Web (whatsapp-web.js)",
            "ok",
            "Linked as " + str(status.get("phone_masked") or "a masked number"),
        )
    if status.get("qr_available") or state in {"starting", "initializing", "qr"}:
        return _result(
            "whatsapp_web",
            "WhatsApp Web (whatsapp-web.js)",
            "warn",
            "Service running; a QR scan is required.",
            "Open Service health → WhatsApp Web and scan the QR with the test phone.",
        )
    return _result(
        "whatsapp_web",
        "WhatsApp Web (whatsapp-web.js)",
        "warn",
        "Service answered with state: " + state,
        "Open the Settings page for the latest link state.",
    )


def whatsapp_cloud() -> dict:
    try:
        config = delivery.load_whatsapp_config()
        summary = config.safe_summary()
    except Exception as exc:
        return _result(
            "whatsapp_cloud",
            "WhatsApp Cloud API",
            "error",
            f"Could not read configuration: {type(exc).__name__}",
            "Check WHATSAPP_* variables in .env.",
        )
    if not summary["enabled"]:
        return _result(
            "whatsapp_cloud",
            "WhatsApp Cloud API",
            "off",
            "Disabled by configuration. No cloud send path is active.",
            "Set WHATSAPP_ENABLED=true only when credentials are configured.",
        )
    if not summary["ready"]:
        return _result(
            "whatsapp_cloud",
            "WhatsApp Cloud API",
            "error",
            "Configuration incomplete: " + "; ".join(summary["problems"]),
            "Fix WHATSAPP_ACCESS_TOKEN, PHONE_NUMBER_ID and API_VERSION.",
        )
    return _result(
        "whatsapp_cloud",
        "WhatsApp Cloud API",
        "ok",
        "Credentials configured · number " + str(summary["phone_number_id_masked"]),
    )


def webhook_receiver() -> dict:
    try:
        config = delivery.load_whatsapp_config()
        summary = config.safe_summary()
    except Exception as exc:
        return _result(
            "webhook",
            "Verified webhook receiver",
            "error",
            f"Could not read configuration: {type(exc).__name__}",
            "Check WHATSAPP_* variables in .env.",
        )
    if not summary["webhook_enabled"]:
        return _result(
            "webhook",
            "Verified webhook receiver",
            "off",
            "Disabled by configuration.",
            "Set WHATSAPP_WEBHOOK_ENABLED=true and run scripts/run_webhook.py.",
        )
    if not summary["webhook_ready"]:
        return _result(
            "webhook",
            "Verified webhook receiver",
            "error",
            "Configuration incomplete: " + "; ".join(summary["webhook_problems"]),
            "Set WHATSAPP_APP_SECRET and WHATSAPP_VERIFY_TOKEN.",
        )
    return _result(
        "webhook",
        "Verified webhook receiver",
        "ok",
        "Configured · bind " + str(summary["webhook_bind"]) + ":" + str(summary["webhook_port"]),
        "Start it with scripts/run_webhook.py behind HTTPS.",
    )


def agent_server() -> dict:
    try:
        response = requests.get(AGENT_SERVER_HEALTH, timeout=0.8)
    except requests.RequestException:
        return _result(
            "agent_server",
            "Agent webhook server (:5050)",
            "off",
            "Not running (optional for the local workspace).",
            "Start with `python -m agent.server` only when inbound WhatsApp is configured.",
        )
    if response.status_code != 200:
        return _result(
            "agent_server",
            "Agent webhook server (:5050)",
            "warn",
            f"Answered HTTP {response.status_code}.",
            "Inspect the server log; no secret is shown here.",
        )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    provider = str(payload.get("provider") or "unknown")
    return _result(
        "agent_server",
        "Agent webhook server (:5050)",
        "ok",
        f"Running · provider {provider}",
    )


def collect() -> dict:
    checks = [
        operational_database(),
        agent_database(),
        whatsapp_web(),
        whatsapp_cloud(),
        webhook_receiver(),
        agent_server(),
    ]
    counts = {
        "ok": sum(1 for item in checks if item["status"] == "ok"),
        "warn": sum(1 for item in checks if item["status"] == "warn"),
        "error": sum(1 for item in checks if item["status"] == "error"),
    }
    return {
        "checks": checks,
        "counts": counts,
        "healthy": counts["error"] == 0,
    }
