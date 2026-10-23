"""Read-only adapters that expose the WhatsApp agent database to the UI.

The agent subsystem keeps its own SQLite database (``agent_data/milaan_agent.db``
by default) and its own schema.  The primary workspace keeps a separate
database (``milaan.db``).  This module never writes and never moves data
between the two stores; it only reads the agent store and masks personal phone
numbers before they reach a page or a log line.

If the agent database is missing or has not been initialised, every function
returns an explicit ``available: False`` payload instead of inventing data.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from agent import store

STATE_LABEL = {
    "PENDING_2B": "Waiting for 2B",
    "NEEDS_INPUT": "Needs input",
    "INVALID": "Invalid",
    "MATCHED": "Matched",
    "INELIGIBLE": "Not claimable",
    "QRMP_WAIT": "Quarterly filer · wait",
    "TO_CHASE": "Awaiting approval",
    "CHASED": "Supplier messaged",
    "PROMISED": "Supplier promised",
    "RECOVERED": "Recovered",
    "ESCALATED": "Escalated to trader",
    "CHANNEL_BLOCKED": "Send failed · call",
    "SKIPPED": "Skipped by trader",
}

CHASE_STATES = {
    "TO_CHASE",
    "CHASED",
    "PROMISED",
    "RECOVERED",
    "ESCALATED",
    "CHANNEL_BLOCKED",
    "SKIPPED",
}
AT_RISK_STATES = {
    "TO_CHASE",
    "CHASED",
    "PROMISED",
    "CHANNEL_BLOCKED",
    "ESCALATED",
}

_PHONE_KEYS = {
    "phone",
    "to",
    "from",
    "sender",
    "recipient",
    "trader",
    "wa_id",
    "to_phone",
    "from_phone",
    "supplier_phone",
    "trader_phone",
    "phone_number",
    "phone_masked",
}
_PHONE_LIKE = re.compile(r"^\+?\d[\d\s\-()]{8,}$")


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def mask_phone(value: Any) -> str:
    """Return a display-safe phone number. Never raises."""
    digits = _digits(value)
    if not digits:
        return ""
    if len(digits) <= 4:
        return "…"
    if len(digits) <= 7:
        return digits[:1] + "•" * (len(digits) - 3) + digits[-2:]
    return "+" + digits[:2] + "•" * (len(digits) - 6) + digits[-4:]


def _mask_string(value: str) -> str:
    return mask_phone(value) if _PHONE_LIKE.fullmatch(value.strip()) else value


def mask_payload(value: Any, key: str | None = None) -> Any:
    """Recursively mask phone numbers in an agent trace payload."""
    if isinstance(value, dict):
        return {k: mask_payload(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_payload(item, key) for item in value]
    if isinstance(value, tuple):
        return [mask_payload(item, key) for item in value]
    if isinstance(value, str):
        if key and key.lower() in _PHONE_KEYS:
            return mask_phone(value)
        return _mask_string(value)
    return value


def database_info() -> dict:
    path = Path(store.db_path())
    size = path.stat().st_size if path.is_file() else 0
    return {
        "path": str(path),
        # A zero-byte file can be left behind by a failed/open attempt; it is
        # not an initialised agent database and must not look "available".
        "exists": path.is_file() and size > 0,
        "size_bytes": size,
    }


def _read_traders() -> list[dict]:
    path = Path(store.db_path())
    if not path.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT phone, name, lang, created_at FROM agent_traders "
                "ORDER BY created_at"
            )
            return [dict(row) for row in rows]
    except sqlite3.Error:
        return []


def list_traders() -> list[dict]:
    traders = []
    for row in _read_traders():
        phone = str(row.get("phone") or "")
        if not phone:
            continue
        traders.append(
            {
                "phone": phone,
                "phone_masked": mask_phone(phone),
                "name": row.get("name") or "Trader",
                "lang": row.get("lang") or "",
                "created_at": row.get("created_at"),
            }
        )
    return traders


def trader_options() -> list[tuple[str, str]]:
    return [
        (trader["phone"], f"{trader['name']} · {trader['phone_masked']}")
        for trader in list_traders()
    ]


def _invoice_case(invoice: dict) -> dict:
    return {
        "id": invoice.get("id"),
        "supplier": invoice.get("supplier_name") or "",
        "gstin": invoice.get("supplier_gstin") or "",
        "invoice": invoice.get("invoice_number") or "",
        "date": invoice.get("invoice_date") or "",
        "period": invoice.get("tax_period") or "",
        "gst": (invoice.get("total_gst_paise") or 0) / 100,
        "state": invoice.get("state") or "",
        "state_label": STATE_LABEL.get(invoice.get("state"), invoice.get("state") or ""),
        "cause": invoice.get("diagnosis") or "",
        "why": invoice.get("state_reason") or "",
        "eligibility": invoice.get("eligibility") or "",
        "chases": invoice.get("chase_count") or 0,
        "last_chased": invoice.get("last_chased_at"),
        "next_check": invoice.get("next_check_at"),
        "recovered_at": invoice.get("recovered_at"),
        "source": invoice.get("provenance") or "",
        "pending_field": invoice.get("pending_field"),
    }


def overview(trader: str | None = None, trace_limit: int = 250) -> dict:
    """Return a display-safe snapshot of one agent trader."""
    info = database_info()
    traders = list_traders()
    if not info["exists"]:
        return {
            "available": False,
            "reason": "Agent database not found.",
            "database": info,
            "traders": [],
            "trader": None,
            "invoices": [],
            "cases": [],
            "conversation": [],
            "outbound": [],
            "trace": [],
            "counts": {},
            "totals": {},
        }
    if not traders:
        return {
            "available": True,
            "empty": True,
            "reason": "No agent conversations have been recorded yet.",
            "database": info,
            "traders": [],
            "trader": None,
            "invoices": [],
            "cases": [],
            "conversation": [],
            "outbound": [],
            "trace": [],
            "counts": {},
            "totals": {},
        }

    selected = next((item for item in traders if item["phone"] == trader), traders[0])
    phone = selected["phone"]
    invoices = store.get_invoices(phone)
    cases = [_invoice_case(invoice) for invoice in invoices]
    counts = {
        "bills": len(invoices),
        "at_risk": sum(1 for invoice in invoices if invoice.get("state") in AT_RISK_STATES),
        "chases": sum(1 for invoice in invoices if invoice.get("state") in CHASE_STATES),
        "recovered": sum(1 for invoice in invoices if invoice.get("state") == "RECOVERED"),
        "matched": sum(1 for invoice in invoices if invoice.get("state") == "MATCHED"),
    }
    totals = {
        "gst": sum((invoice.get("total_gst_paise") or 0) for invoice in invoices) / 100,
        "at_risk": sum(
            (invoice.get("total_gst_paise") or 0)
            for invoice in invoices
            if invoice.get("state") in AT_RISK_STATES
        )
        / 100,
        "recovered": sum(
            (invoice.get("total_gst_paise") or 0)
            for invoice in invoices
            if invoice.get("state") == "RECOVERED"
        )
        / 100,
    }

    suppliers = {item.get("gstin"): item for item in store.get_suppliers(phone)}
    conversation = _conversation(phone, suppliers)
    outbound = [
        {
            "id": item.get("id"),
            "to": mask_phone(item.get("to_phone")),
            "gstin": item.get("supplier_gstin") or "",
            "status": item.get("status") or "",
            "attempts": item.get("attempts") or 0,
            "error": item.get("error") or "",
            "at": item.get("created_at"),
        }
        for item in store.get_outbound(phone)
    ]
    trace = []
    for row in store.get_trace(phone, limit=trace_limit):
        raw = row.get("payload") or "{}"
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            payload = raw
        trace.append(
            {
                "id": row.get("id"),
                "kind": row.get("kind") or "",
                "name": row.get("name") or "",
                "turn": row.get("turn_id") or "",
                "at": row.get("created_at"),
                "payload": mask_payload(payload),
            }
        )

    return {
        "available": True,
        "empty": False,
        "database": info,
        "traders": traders,
        "trader": selected,
        "invoices": invoices,
        "cases": cases,
        "conversation": conversation,
        "outbound": outbound,
        "trace": trace,
        "counts": counts,
        "totals": totals,
        "suppliers": [
            {
                "gstin": item.get("gstin"),
                "name": item.get("name") or "",
                "phone": mask_phone(item.get("phone")),
                "consent": bool(item.get("consent")),
            }
            for item in suppliers.values()
        ],
    }


def _conversation(phone: str, suppliers: dict) -> list[dict]:
    """Trader + supplier messages, newest last, with masked phone numbers."""
    path = Path(store.db_path())
    if not path.is_file():
        return []
    supplier_phones = {
        str(item.get("phone")) for item in suppliers.values() if item.get("phone")
    }
    allowed = {phone, *supplier_phones}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM agent_messages ORDER BY id DESC LIMIT 500"
            )
            messages = [
                dict(row)
                for row in rows
                if str(row["phone"] or "") in allowed
            ]
    except sqlite3.Error:
        return []
    result = []
    for message in reversed(messages):
        message_phone = str(message.get("phone") or "")
        result.append(
            {
                "phone": mask_phone(message_phone),
                "who": "Trader" if message_phone == phone else "Supplier",
                "direction": message.get("direction") or "",
                "role": message.get("role") or "",
                "body": message.get("body") or "",
                "media": message.get("media_type") or "",
                "at": message.get("created_at") or "",
            }
        )
    return result
