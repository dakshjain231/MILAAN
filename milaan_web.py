"""MILAAN primary web application.

Serves the custom MILAAN workspace on localhost. The browser is only a view:
parsing, validation, reconciliation, draft generation, approval and delivery
guards stay in the existing Python modules.

Run:
    .venv/bin/python scripts/run_primary.py --port 8501
"""

from __future__ import annotations

import csv
import hmac
import io
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, request
from werkzeug.security import check_password_hash

import agent_views
import db
import delivery
import followups
import gstr2b
import invoice_import
import report_workbook
import security
import service_health
import validation
import webjs

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "agent" / "web" / "primary.html"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_ROWS = 5000
DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")
ORG_FIELDS = ("company_name", "gstin", "email", "phone")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HEALTH_LOCK = threading.Lock()
_HEALTH_CACHE: dict[str, Any] = {"at": 0.0, "value": None}


# --- display-safe helpers -------------------------------------------------

def _mask_phone(value: Any) -> str:
    return agent_views.mask_phone(value)


def _rupees(paise: Any) -> float | None:
    if paise is None:
        return None
    try:
        return round(int(paise) / 100, 2)
    except (TypeError, ValueError):
        return None


def _json_list(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return [str(raw)] if raw else []
    return value if isinstance(value, list) else [str(value)]


def _safe_details(raw: Any) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        value = raw
    return agent_views.mask_payload(value)


_PLAIN_NUMBER = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


def _csv_cell(value: Any) -> str:
    """Neutralise spreadsheet formula injection without mangling numbers."""
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    stripped = text.lstrip(" \t\r")
    if stripped and stripped[0] in ("=", "+", "-", "@", "\t", "\r"):
        if not _PLAIN_NUMBER.match(stripped):
            return "'" + text
    return text


def _database_metadata() -> dict:
    path = db.get_database_path()
    demo_path = (ROOT / "demo" / "milaan_demo.db").resolve()
    agent = agent_views.database_info()
    return {
        "path": str(path),
        "name": path.name,
        "exists": path.is_file(),
        "demo": path == demo_path,
        "agent_path": agent["path"],
        "agent_exists": agent["exists"],
    }


def _error(message: str, status: int = 400, **extra):
    payload = {"error": message, **extra}
    return jsonify(payload), status


def _int_arg(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return number


def _read_upload() -> tuple[bytes, str]:
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise ValueError("Choose a CSV or XLSX file first.")
    content = upload.read()
    if not content:
        raise ValueError("The selected file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("The selected file is larger than 25 MB.")
    return content, Path(upload.filename).name


# --- read models ----------------------------------------------------------

def _invoices() -> list[dict]:
    with db.db() as conn:
        rows = conn.execute(
            """SELECT i.id, i.invoice_number, i.invoice_date, i.tax_period,
                      i.taxable_value, i.gst_rate, i.cgst, i.sgst, i.igst,
                      i.total_gst, i.invoice_total, i.source,
                      i.validation_status, i.case_status, i.created_at,
                      s.supplier_name, s.gstin, s.contact_phone
               FROM invoices i
               LEFT JOIN suppliers s ON s.id = i.supplier_id
               ORDER BY i.invoice_date DESC, i.invoice_number DESC"""
        ).fetchall()
    return [
        {
            "id": row["id"],
            "number": row["invoice_number"],
            "supplier": row["supplier_name"] or "",
            "gstin": row["gstin"] or "",
            "phone": _mask_phone(row["contact_phone"]),
            "date": row["invoice_date"],
            "period": row["tax_period"],
            "taxable": _rupees(row["taxable_value"]),
            "rate": row["gst_rate"],
            "cgst": _rupees(row["cgst"]),
            "sgst": _rupees(row["sgst"]),
            "igst": _rupees(row["igst"]),
            "gst": _rupees(row["total_gst"]),
            "total": _rupees(row["invoice_total"]),
            "source": row["source"],
            "validation": row["validation_status"],
            "case": row["case_status"],
            "created": row["created_at"],
        }
        for row in rows
    ]


def _suppliers() -> list[dict]:
    with db.db() as conn:
        rows = conn.execute(
            """SELECT id, supplier_name, gstin, contact_phone, created_at
               FROM suppliers ORDER BY supplier_name"""
        ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["supplier_name"],
            "gstin": row["gstin"],
            "phone": _mask_phone(row["contact_phone"]),
            "created": row["created_at"],
        }
        for row in rows
    ]


def _gstr2b() -> dict:
    with db.db() as conn:
        imports = conn.execute(
            """SELECT id, file_name, imported_at, source_format, total_rows,
                      accepted_rows, rejected_rows, file_hash
               FROM gstr2b_imports ORDER BY id DESC LIMIT 50"""
        ).fetchall()
        selected = imports[0]["id"] if imports else None
        period_rows = conn.execute(
            """SELECT import_id, MIN(tax_period) AS period
               FROM gstr2b_rows
               WHERE tax_period IS NOT NULL AND tax_period != ''
               GROUP BY import_id"""
        ).fetchall()
        periods = {row["import_id"]: row["period"] for row in period_rows}
        # Return rows for every visible batch so selecting an older batch in
        # the UI never shows an empty evidence table. MAX_ROWS bounds payload.
        rows = conn.execute(
            """SELECT r.id, r.import_id, r.row_number, r.supplier_gstin,
                      r.invoice_number, r.invoice_date, r.tax_period,
                      r.taxable_value, r.cgst, r.sgst, r.igst, r.total_gst,
                      r.validation_status, r.errors, sp.supplier_name
               FROM gstr2b_rows r
               LEFT JOIN suppliers sp ON sp.gstin = r.supplier_gstin
               ORDER BY r.import_id DESC, r.row_number LIMIT ?""",
            (MAX_ROWS,),
        ).fetchall()
    return {
        "imports": [
            {
                "id": row["id"],
                "file": row["file_name"],
                "at": row["imported_at"],
                "format": row["source_format"],
                "total": row["total_rows"],
                "accepted": row["accepted_rows"],
                "rejected": row["rejected_rows"],
                "hash": row["file_hash"][:12] + "…",
                "period": periods.get(row["id"]),
            }
            for row in imports
        ],
        "selected": selected,
        "rows": [
            {
                "id": row["id"],
                "batch": row["import_id"],
                "row": row["row_number"],
                "gstin": row["supplier_gstin"] or "",
                "supplier": row["supplier_name"] or "",
                "number": row["invoice_number"] or "",
                "date": row["invoice_date"] or "",
                "period": row["tax_period"] or "",
                "taxable": _rupees(row["taxable_value"]),
                "cgst": _rupees(row["cgst"]),
                "sgst": _rupees(row["sgst"]),
                "igst": _rupees(row["igst"]),
                "gst": _rupees(row["total_gst"]),
                "validation": row["validation_status"],
                "issues": _json_list(row["errors"]),
            }
            for row in rows
        ],
    }


def _result_rows(conn, run_id: int) -> list:
    """Invoice-level results of one run with both sides' stored values.

    The GSTR-2B side is read from the matched ``gstr2b_rows`` row; when no
    row is attached its values stay NULL instead of being estimated. Rows
    found only in GSTR-2B (``IN_2B_NOT_IN_BOOKS``) have no purchase-register
    side, so those values stay NULL too.
    """
    return conn.execute(
        """SELECT rr.id, rr.invoice_id, rr.gstr2b_row_id, rr.match_status,
                  rr.match_method, rr.amount_difference_paise, rr.reason_codes,
                  rr.tax_period, rr.invoice_total_gst_paise,
                  rr.gstr2b_invoice_number, rr.gstr2b_total_gst_paise,
                  COALESCE(i.invoice_number, rr.invoice_number) AS invoice_number,
                  i.invoice_date,
                  i.taxable_value AS invoice_taxable_paise,
                  i.invoice_total AS invoice_total_paise,
                  COALESCE(s.gstin, rr.supplier_gstin) AS supplier_gstin,
                  COALESCE(s.supplier_name, known.supplier_name) AS supplier_name,
                  g.taxable_value AS twob_taxable_paise,
                  g.invoice_date AS twob_invoice_date
           FROM reconciliation_results rr
           LEFT JOIN invoices i ON i.id = rr.invoice_id
           LEFT JOIN suppliers s ON s.id = i.supplier_id
           LEFT JOIN suppliers known ON known.gstin = rr.supplier_gstin
           LEFT JOIN gstr2b_rows g ON g.id = rr.gstr2b_row_id
           WHERE rr.run_id = ?
           ORDER BY rr.invoice_id IS NULL,
                    COALESCE(i.invoice_date, g.invoice_date),
                    COALESCE(i.invoice_number, rr.invoice_number)""",
        (run_id,),
    ).fetchall()


def _result_view(row) -> dict:
    invoice_taxable = row["invoice_taxable_paise"]
    twob_taxable = row["twob_taxable_paise"]
    taxable_difference = (
        invoice_taxable - twob_taxable
        if invoice_taxable is not None and twob_taxable is not None
        else None
    )
    return {
        "id": row["id"],
        "invoice_id": row["invoice_id"],
        "number": row["invoice_number"],
        "date": row["invoice_date"] or row["twob_invoice_date"],
        "supplier": row["supplier_name"] or "",
        "gstin": row["supplier_gstin"] or "",
        "period": row["tax_period"] or "",
        "invoice_taxable": _rupees(invoice_taxable),
        "invoice_total": _rupees(row["invoice_total_paise"]),
        "invoice_gst": (
            _rupees(row["invoice_total_gst_paise"]) if row["invoice_id"] is not None else None
        ),
        "status": row["match_status"],
        "method": row["match_method"],
        "row_id": row["gstr2b_row_id"],
        "twob_number": row["gstr2b_invoice_number"] or "",
        "twob_date": row["twob_invoice_date"],
        "twob_taxable": _rupees(twob_taxable),
        "twob_gst": _rupees(row["gstr2b_total_gst_paise"]),
        # Signed invoice minus GSTR-2B, as computed by the engine.
        "taxable_difference": _rupees(taxable_difference),
        "difference": _rupees(row["amount_difference_paise"]),
        "reasons": _json_list(row["reason_codes"]),
    }


def _reconciliation() -> dict:
    with db.db() as conn:
        runs = conn.execute(
            """SELECT r.*, g.file_name
               FROM reconciliation_runs r
               JOIN gstr2b_imports g ON g.id = r.gstr2b_import_id
               ORDER BY r.id DESC LIMIT 50"""
        ).fetchall()
        latest = next((row for row in runs if row["is_latest"]), None)
        results = []
        if latest is not None:
            results = _result_rows(conn, latest["id"])
    return {
        "runs": [
            {
                "id": row["id"],
                "batch": row["gstr2b_import_id"],
                "file": row["file_name"],
                "at": row["created_at"],
                "latest": bool(row["is_latest"]),
                "tolerance": row["amount_tolerance_paise"],
                "invoices": row["invoice_count"],
                "matched": row["matched_count"],
                "amount_mismatch": row["amount_mismatch_count"],
                "missing": row["missing_count"],
                "review": row["needs_review_count"],
                "not_eligible": row["not_eligible_count"],
            }
            for row in runs
        ],
        "latest": (
            {
                "id": latest["id"],
                "batch": latest["gstr2b_import_id"],
                "file": latest["file_name"],
                "at": latest["created_at"],
                "latest": True,
                "tolerance": latest["amount_tolerance_paise"],
                "invoices": latest["invoice_count"],
                "matched": latest["matched_count"],
                "amount_mismatch": latest["amount_mismatch_count"],
                "missing": latest["missing_count"],
                "review": latest["needs_review_count"],
                "not_eligible": latest["not_eligible_count"],
            }
            if latest is not None
            else None
        ),
        "results": [_result_view(row) for row in results],
    }


def _reconciliation_run(run_id: int) -> dict:
    """Return one persisted run and its invoice-level evidence."""
    run = db.get_reconciliation_run(run_id)
    if run is None:
        raise KeyError(f"reconciliation run {run_id} not found")
    with db.db() as conn:
        results = _result_rows(conn, run_id)
    return {
        "run": {
            "id": run["id"],
            "batch": run["gstr2b_import_id"],
            "file": run["file_name"],
            "at": run["created_at"],
            "latest": bool(run["is_latest"]),
            "tolerance": run["amount_tolerance_paise"],
            "invoices": run["invoice_count"],
            "matched": run["matched_count"],
            "amount_mismatch": run["amount_mismatch_count"],
            "missing": run["missing_count"],
            "review": run["needs_review_count"],
            "not_eligible": run["not_eligible_count"],
        },
        "results": [_result_view(row) for row in results],
    }


def _draft_issue_codes(reasons: list[str]) -> list[str]:
    """Reconciliation statuses behind a draft, e.g. ``MISSING_FROM_2B``.

    Draft invoice reasons are stored as ``<match status>:<reason code>``.
    """
    codes = []
    for reason in reasons:
        code = str(reason or "").split(":", 1)[0].strip()
        if code and code not in codes:
            codes.append(code)
    return codes


def _draft_rows() -> list[dict]:
    with db.db() as conn:
        rows = conn.execute(
            """SELECT d.*,
                      (SELECT COUNT(*) FROM followup_draft_invoices di
                       WHERE di.draft_id = d.id) AS invoice_count
               FROM followup_drafts d ORDER BY d.id DESC"""
        ).fetchall()
        reasons: dict[int, list[str]] = {}
        periods: dict[int, set[str]] = {}
        for item in conn.execute(
            "SELECT draft_id, reason, tax_period FROM followup_draft_invoices ORDER BY id"
        ):
            reasons.setdefault(item["draft_id"], []).append(item["reason"])
            if item["tax_period"]:
                periods.setdefault(item["draft_id"], set()).add(item["tax_period"])
        # Only real (non-mock) sends count as a follow-up having happened.
        contact: dict[int, dict] = {}
        for item in conn.execute(
            """SELECT followup_draft_id AS draft_id,
                      MAX(sent_at) AS last_sent,
                      MIN(CASE WHEN status = 'RETRY_PENDING'
                               THEN next_attempt_at END) AS next_attempt
               FROM message_deliveries
               WHERE provider != ?
               GROUP BY followup_draft_id""",
            (delivery.PROVIDER_MOCK,),
        ):
            contact[item["draft_id"]] = dict(item)
    views = []
    for row in rows:
        recovery = db.get_followup_recovery_status(row["id"])["status"]
        sent = contact.get(row["id"], {})
        views.append({
            "workflow_status": followups.followup_workflow_status(row["status"], recovery),
            "recovery_status": recovery,
            "issues": _draft_issue_codes(reasons.get(row["id"], [])),
            "periods": sorted(periods.get(row["id"], ())),
            "last_followup": sent.get("last_sent"),
            "next_followup": sent.get("next_attempt"),
            **_draft_base(row),
        })
    return views


def _draft_base(row) -> dict:
    return {
        "id": row["id"],
        "supplier": row["supplier_name"],
        "gstin": row["supplier_gstin"] or "",
        "phone": _mask_phone(row["contact_phone"]),
        "run": row["reconciliation_run_id"],
        "batch": row["gstr2b_import_id"],
        "status": row["status"],
        "channel": "available" if row["channel_available"] else "unavailable",
        "invoices": row["invoice_count"],
        "taxable": _rupees(row["total_taxable_value_paise"]),
        "gst": _rupees(row["total_gst_paise"]),
        "reasons": _json_list(row["review_reasons"]),
        "created": row["created_at"],
        "updated": row["updated_at"],
        "approved_at": row["approved_at"],
    }


def _deliveries() -> list[dict]:
    with db.db() as conn:
        rows = conn.execute(
            """SELECT m.*, d.supplier_name, d.supplier_gstin
               FROM message_deliveries m
               JOIN followup_drafts d ON d.id = m.followup_draft_id
               ORDER BY m.id DESC LIMIT 200"""
        ).fetchall()
    return [_delivery_row(row) for row in rows]


def _delivery_row(row) -> dict:
    return {
        "id": row["id"],
        "draft": row["followup_draft_id"],
        "supplier": row["supplier_name"] if "supplier_name" in row.keys() else "",
        "gstin": row["supplier_gstin"] if "supplier_gstin" in row.keys() else "",
        "provider": row["provider"],
        "to": _mask_phone(row["recipient_phone_snapshot"]),
        "status": row["status"],
        "attempts": row["attempt_count"],
        "max_attempts": row["max_attempts"],
        "error_code": row["last_error_code"] or "",
        "error": row["last_error_message"] or "",
        "created": row["created_at"],
        "sent_at": row["sent_at"],
        "next_attempt": row["next_attempt_at"],
        "opt_in": bool(row["opt_in_confirmed"]),
        "template": row["template_name"] or "",
    }


def _audit() -> list[dict]:
    events = []
    for item in db.get_system_audit_events(150):
        events.append(
            {
                "at": item["At"],
                "actor": item["Actor"],
                "event": item["Event"],
                "subject": item["Subject"],
                "details": _safe_details(item["Details"]),
                "source": "system",
            }
        )
    for item in db.get_all_followup_draft_events(150):
        events.append(
            {
                "at": item["At"],
                "actor": "system",
                "event": f"draft_{item['Event'].lower()}",
                "subject": f"followup_draft:{item['Draft']}",
                "details": {
                    "supplier": item["Supplier"],
                    "from": item["From"],
                    "to": item["To"],
                    "note": item["Note"],
                },
                "source": "follow-up",
            }
        )
    events.sort(key=lambda item: item["at"] or "", reverse=True)
    return events[:200]


def _summary(invoices: list[dict], gst: dict, recon: dict, drafts: list[dict],
             deliveries: list[dict]) -> dict:
    validation = {key: 0 for key in db.VALIDATION_STATUSES}
    for invoice in invoices:
        validation[invoice["validation"]] = validation.get(invoice["validation"], 0) + 1
    draft_counts: dict[str, int] = {}
    for draft in drafts:
        draft_counts[draft["status"]] = draft_counts.get(draft["status"], 0) + 1
    delivery_counts: dict[str, int] = {}
    for item in deliveries:
        delivery_counts[item["status"]] = delivery_counts.get(item["status"], 0) + 1
    return {
        "invoices": len(invoices),
        "gst_total": round(sum(item["gst"] or 0 for item in invoices), 2),
        "invoice_total": round(sum(item["total"] or 0 for item in invoices), 2),
        "validation": validation,
        "gstr2b_batches": len(gst["imports"]),
        "gstr2b_rows": len(gst["rows"]),
        "recon_runs": len(recon["runs"]),
        "latest_run": recon["latest"],
        "drafts": draft_counts,
        "draft_total": len(drafts),
        "deliveries": delivery_counts,
        "delivery_total": len(deliveries),
        "followup_needed": (
            (recon["latest"] or {}).get("missing", 0)
            + (recon["latest"] or {}).get("amount_mismatch", 0)
            + (recon["latest"] or {}).get("review", 0)
        ),
    }


def _qr_payload() -> dict:
    try:
        config = webjs.load_webjs_config()
        status = webjs.service_status(config)
        qr = webjs.fetch_qr_data_url(config)
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}", "qr": None}
    return {
        "available": bool(qr),
        "qr": qr,
        "enabled": config.enabled,
        "status": status.get("status") or "unknown",
        "ready": bool(status.get("ready")),
    }


def _organization() -> dict:
    """Stored organisation details; unset values stay empty, never invented."""
    values = {field: db.get_system_meta(f"org_{field}", "") or "" for field in ORG_FIELDS}
    values["configured"] = bool(values["company_name"])
    return values


def _validate_organization(payload: dict) -> tuple[dict, dict]:
    values: dict[str, str] = {}
    problems: dict[str, str] = {}
    for field in ORG_FIELDS:
        if field not in payload:
            continue
        raw = payload[field]
        if raw is None:
            raw = ""
        if not isinstance(raw, str):
            problems[field] = "Must be text."
            continue
        values[field] = raw.strip()
    name = values.get("company_name", "")
    if "company_name" in values and not name:
        problems["company_name"] = "Company name is required."
    elif len(name) > 120:
        problems["company_name"] = "Company name must be 120 characters or fewer."
    gstin = values.get("gstin", "")
    if gstin:
        check = validation.validate_gstin(gstin)
        if check.errors:
            problems["gstin"] = check.errors[0].message
        else:
            values["gstin"] = check.normalized or gstin.upper()
    email = values.get("email", "")
    if email and (len(email) > 254 or not _EMAIL_RE.match(email)):
        problems["email"] = "Enter a valid email address."
    phone = values.get("phone", "")
    if phone:
        digits = re.sub(r"\D", "", phone)
        if not re.fullmatch(r"[+\d][\d\s()-]*", phone) or not 7 <= len(digits) <= 15:
            problems["phone"] = "Enter a phone number with 7 to 15 digits."
    return values, problems


def _allowed_hosts() -> set[str]:
    configured = os.environ.get("MILAAN_ALLOWED_HOSTS", "")
    hosts = {h.strip().lower() for h in configured.split(",") if h.strip()}
    return hosts | set(DEFAULT_ALLOWED_HOSTS)


def _host_allowed(host: str) -> bool:
    """Exact host names, or ``.suffix`` entries matching any subdomain."""
    name = _host_name(host)
    for allowed in _allowed_hosts():
        if allowed.startswith("."):
            if name.endswith(allowed) and len(name) > len(allowed):
                return True
        elif name == allowed:
            return True
    return False


def _host_name(host: str) -> str:
    host = (host or "").strip().lower()
    if host.startswith("["):
        return host[: host.find("]") + 1] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


# --- application ----------------------------------------------------------

def create_app() -> Flask:
    policy = security.load_security_config()
    if policy.auth_required and not policy.basic_auth_configured:
        raise RuntimeError(
            "Authentication is required (APP_ENV=production or "
            "MILAAN_AUTH_REQUIRED=true) but MILAAN_BASIC_AUTH_USER and "
            "MILAAN_BASIC_AUTH_PASSWORD_HASH are not set. Refusing to start "
            "without authentication."
        )
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.config["JSON_SORT_KEYS"] = False
    db.init_db()

    @app.before_request
    def _protect_request():
        # DNS-rebinding guard: only answer for known host names.
        if not _host_allowed(request.host):
            return _error("Unknown host.", 400)
        # Cross-site request guard for every state-changing request. Browsers
        # send Origin/Sec-Fetch-Site; a write from another site is refused.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            fetch_site = (request.headers.get("Sec-Fetch-Site") or "").lower()
            if fetch_site in ("cross-site", "same-site"):
                return _error("Cross-site requests are not allowed.", 403)
            origin = request.headers.get("Origin")
            if origin and origin != "null":
                if urlsplit(origin).netloc.lower() != request.host.lower():
                    return _error("Cross-site requests are not allowed.", 403)
            elif origin == "null":
                return _error("Cross-site requests are not allowed.", 403)
        if policy.basic_auth_configured and request.path != "/health":
            auth = request.authorization
            user_ok = bool(auth and auth.type == "basic" and auth.username) and hmac.compare_digest(
                (auth.username or "").encode(), policy.basic_auth_user.encode()
            )
            password_ok = bool(auth and auth.password) and check_password_hash(
                policy.basic_auth_password_hash, auth.password or ""
            )
            if not (user_ok and password_ok):
                response = Response("Authentication required.", 401)
                response.headers["WWW-Authenticate"] = 'Basic realm="MILAAN", charset="UTF-8"'
                return response
        return None

    @app.after_request
    def _headers(response):
        if request.path.startswith("/api/") or request.path == "/":
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    @app.errorhandler(413)
    def _too_large(_):
        return _error("The selected file is larger than 25 MB.", 413)

    @app.get("/")
    def home():
        return Response(PAGE.read_text(encoding="utf-8"), mimetype="text/html")

    @app.get("/favicon.ico")
    def favicon():
        return Response(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 34 34">'
            '<path d="M4 7c5 1 10 6 12 20C10 25 5 18 4 7z" fill="#4f46e5"/>'
            '<path d="M30 7c-5 1-10 6-12 20 6-2 11-9 12-20z" fill="#818cf8"/>'
            "</svg>",
            mimetype="image/svg+xml",
        )

    @app.get("/health")
    def health():
        try:
            ready = db.is_db_ready()
        except Exception:
            ready = False
        return jsonify({"ok": ready, "app": "milaan-primary", "port": os.environ.get("MILAAN_UI_PORT", "8501")})

    @app.get("/api/state")
    def state():
        invoices = _invoices()
        gst = _gstr2b()
        recon = _reconciliation()
        drafts = _draft_rows()
        deliveries = _deliveries()
        agent = agent_views.overview()
        return jsonify(
            {
                "now": time.strftime("%Y-%m-%d %H:%M:%S"),
                "summary": _summary(invoices, gst, recon, drafts, deliveries),
                "invoices": invoices,
                "suppliers": _suppliers(),
                "gstr2b": gst,
                "reconciliation": recon,
                "followups": drafts,
                "deliveries": deliveries,
                "audit": _audit(),
                "agent": agent,
                "database": _database_metadata(),
                "provider": {
                    "cloud": delivery.load_whatsapp_config().ready,
                    "webjs": bool(webjs.load_webjs_config().ready),
                },
            }
        )

    @app.get("/api/reconciliations/<int:run_id>")
    def reconciliation_run(run_id: int):
        try:
            return jsonify(_reconciliation_run(run_id))
        except KeyError:
            return _error("Reconciliation run not found.", 404)

    @app.get("/api/followups/eligibility/<int:batch_id>")
    def followup_eligibility(batch_id: int):
        try:
            eligibility = db.get_followup_eligibility(batch_id)
        except ValueError as exc:
            return _error(str(exc), 404)
        return jsonify(
            {
                "run": (
                    {
                        "id": eligibility["run"]["id"],
                        "batch": eligibility["run"]["gstr2b_import_id"],
                        "file": eligibility["run"]["file_name"],
                        "at": eligibility["run"]["created_at"],
                    }
                    if eligibility["run"]
                    else None
                ),
                "eligible_invoice_count": eligibility["eligible_invoice_count"],
                "plans": [
                    {
                        "supplier": plan["supplier_name"],
                        "gstin": plan["supplier_gstin"],
                        "phone": _mask_phone(plan["contact_phone"]),
                        "channel": (
                            "available"
                            if plan["channel_available"]
                            else "unavailable"
                        ),
                        "status": plan["status"],
                        "reasons": plan["review_reasons"],
                        "invoices": plan["invoices"],
                        "taxable": _rupees(plan["total_taxable_value_paise"]),
                        "gst": _rupees(plan["total_gst_paise"]),
                        "draft_text": plan["draft_text"],
                    }
                    for plan in eligibility["plans"]
                ],
                "excluded": eligibility["excluded"],
            }
        )

    @app.get("/api/health")
    def health_report():
        now = time.monotonic()
        with _HEALTH_LOCK:
            if _HEALTH_CACHE["value"] is not None and now - _HEALTH_CACHE["at"] < 5:
                return jsonify(_HEALTH_CACHE["value"])
        value = service_health.collect()
        with _HEALTH_LOCK:
            _HEALTH_CACHE["at"] = now
            _HEALTH_CACHE["value"] = value
        return jsonify(value)

    @app.get("/api/whatsapp/qr")
    def whatsapp_qr():
        return jsonify(_qr_payload())

    @app.get("/api/whatsapp/status")
    def whatsapp_status():
        try:
            config = webjs.load_webjs_config()
            status = webjs.service_status(config)
            summary = config.safe_summary()
        except Exception as exc:
            return _error(
                f"Could not read WhatsApp Web configuration ({type(exc).__name__}).",
                500,
            )
        return jsonify({"config": summary, "status": status})

    @app.post("/api/whatsapp/demo-send")
    def whatsapp_demo_send():
        payload = request.get_json(silent=True) or {}
        if payload.get("confirm_send") is not True:
            return _error("Explicit send confirmation is required. No message was sent.")
        if payload.get("opt_in_confirmed") is not True:
            return _error("Recipient opt-in must be confirmed. No message was sent.")
        try:
            config = webjs.load_webjs_config()
            result = webjs.demo_test_send_message(
                config=config,
                actor="ui",
                confirmed=True,
                opt_in_confirmed=True,
            )
        except (webjs.WebJsBlocked, webjs.WebJsConfigurationError) as exc:
            return _error(str(exc), 409)
        except Exception as exc:
            return _error(f"Demo send failed ({type(exc).__name__}).", 502)
        return jsonify(
            {
                "outcome": result["outcome"],
                "provider_message_id": result.get("provider_message_id"),
                "error_code": result.get("error_code"),
            }
        )

    @app.post("/api/invoices/preview")
    def invoice_preview():
        try:
            content, name = _read_upload()
            parsed = invoice_import.parse_invoice_file(content, name)
        except ValueError as exc:
            return _error(str(exc))
        accepted = sum(1 for row in parsed.rows if row.accepted)
        rejected = [
            {
                "row": row.row_number,
                "invoice": row.invoice_number,
                "supplier": row.supplier_name,
                "errors": [
                    {"code": issue.code, "field": issue.field, "message": issue.message}
                    for issue in row.errors
                ],
            }
            for row in parsed.rows
            if not row.accepted
        ]
        return jsonify(
            {
                "file": parsed.file_name,
                "format": parsed.source_format,
                "total": len(parsed.rows),
                "accepted": accepted,
                "rejected": rejected,
                "hash": parsed.file_hash[:12] + "…",
            }
        )

    @app.post("/api/invoices/import")
    def invoice_import_endpoint():
        try:
            content, name = _read_upload()
            parsed = invoice_import.parse_invoice_file(content, name)
            result = db.save_invoice_import(parsed)
        except ValueError as exc:
            return _error(str(exc))
        try:
            db.record_system_audit_event(
                actor="ui",
                event_type="invoice_import",
                subject_type="invoice_file",
                subject_id=result.get("file_name", name),
                details={
                    "imported": result["imported"],
                    "duplicates": result["duplicates"],
                    "rejected": result["rejected_rows"],
                    "suppliers_created": result["suppliers_created"],
                },
            )
        except Exception:
            pass
        return jsonify(result)

    @app.post("/api/invoices/revalidate")
    def revalidate():
        try:
            entries = db.revalidate_all_invoices()
        except Exception as exc:
            return _error(f"Revalidation failed ({type(exc).__name__}).", 500)
        counts = {"valid": 0, "invalid": 0, "needs_review": 0}
        for entry in entries:
            counts[entry["result"].status] = counts.get(entry["result"].status, 0) + 1
        return jsonify({"checked": len(entries), "counts": counts})

    @app.post("/api/gstr2b/preview")
    def gstr_preview():
        try:
            content, name = _read_upload()
            parsed = gstr2b.parse_gstr2b(content, name)
        except ValueError as exc:
            return _error(str(exc))
        duplicates = db.find_gstr2b_imports_by_hash(parsed.file_hash)
        rejected = [
            {
                "row": row.row_number,
                "invoice": row.invoice_number,
                "errors": [
                    {"code": issue.code, "field": issue.field, "message": issue.message}
                    for issue in row.errors
                ],
            }
            for row in parsed.rows
            if row.validation_status != "valid"
        ]
        return jsonify(
            {
                "file": parsed.file_name,
                "format": parsed.source_format,
                "total": len(parsed.rows),
                "accepted": sum(1 for row in parsed.rows if row.validation_status == "valid"),
                "rejected": rejected,
                "duplicates": [
                    {"id": item["id"], "file": item["file_name"], "at": item["imported_at"]}
                    for item in duplicates
                ],
                "hash": parsed.file_hash[:12] + "…",
            }
        )

    @app.post("/api/gstr2b/import")
    def gstr_import():
        force = str(request.form.get("force") or "").lower() in ("1", "true", "yes")
        try:
            content, name = _read_upload()
            parsed = gstr2b.parse_gstr2b(content, name)
            duplicates = db.find_gstr2b_imports_by_hash(parsed.file_hash)
            if duplicates and not force:
                return _error(
                    "This exact GSTR-2B file was already imported. Confirm a new batch to import it again.",
                    409,
                    duplicates=[
                        {"id": item["id"], "file": item["file_name"], "at": item["imported_at"]}
                        for item in duplicates
                    ],
                )
            result = db.save_gstr2b_import(parsed)
        except ValueError as exc:
            return _error(str(exc))
        try:
            db.record_system_audit_event(
                actor="ui",
                event_type="gstr2b_import",
                subject_type="gstr2b_batch",
                subject_id=result["import_id"],
                details={
                    "file": result["file_name"],
                    "total": result["total_rows"],
                    "accepted": result["accepted_rows"],
                    "rejected": result["rejected_rows"],
                    "forced": force,
                },
            )
        except Exception:
            pass
        return jsonify(result)

    @app.post("/api/reconciliations")
    def reconcile():
        payload = request.get_json(silent=True) or {}
        try:
            batch_id = _int_arg(payload.get("batch_id"), "batch_id")
            tolerance = _int_arg(payload.get("tolerance", 0), "tolerance")
            if not 0 <= tolerance <= 100000:
                raise ValueError("tolerance must be between 0 and 100000 paise")
            result = db.reconcile_gstr2b_import(batch_id, tolerance)
        except ValueError as exc:
            return _error(str(exc))
        try:
            db.record_system_audit_event(
                actor="ui",
                event_type="reconciliation_run",
                subject_type="reconciliation_run",
                subject_id=result["run_id"],
                details={
                    "batch": batch_id,
                    "tolerance_paise": tolerance,
                    "matched": result["matched_count"],
                    "amount_mismatch": result["amount_mismatch_count"],
                    "missing": result["missing_count"],
                    "needs_review": result["needs_review_count"],
                },
            )
        except Exception:
            pass
        return jsonify(result)

    @app.post("/api/followups/generate")
    def generate_followups():
        payload = request.get_json(silent=True) or {}
        try:
            batch_id = _int_arg(payload.get("batch_id"), "batch_id")
            result = db.generate_followup_drafts(batch_id)
        except ValueError as exc:
            return _error(str(exc))
        try:
            db.record_system_audit_event(
                actor="ui",
                event_type="followup_drafts_generated",
                subject_type="gstr2b_batch",
                subject_id=batch_id,
                details={key: result[key] for key in ("created_drafts", "existing_drafts", "eligible_invoices")},
            )
        except Exception:
            pass
        return jsonify(result)

    def _draft_detail(draft_id: int) -> dict | None:
        draft = db.get_followup_draft(draft_id)
        if draft is None:
            return None
        clean = dict(draft)
        clean["contact_phone"] = _mask_phone(draft.get("contact_phone"))
        clean["approved_recipient_phone"] = _mask_phone(draft.get("approved_recipient_phone"))
        clean["review_reasons"] = _json_list(draft.get("review_reasons"))
        eligibility = db.get_delivery_eligibility(draft_id)
        clean_eligibility = {
            "eligible": eligibility["eligible"],
            "problems": eligibility["problems"],
            "approved_text": eligibility["approved_text"],
            "approved_recipient_phone": _mask_phone(eligibility["approved_recipient_phone"]),
            "current_contact_phone": _mask_phone(eligibility["current_contact_phone"]),
        }
        return {
            "draft": clean,
            "invoices": db.get_followup_draft_invoices(draft_id),
            "events": db.get_followup_draft_events(draft_id),
            "approval_problems": db.get_followup_approval_problems(draft_id),
            "delivery_eligibility": clean_eligibility,
            "deliveries": [_delivery_row(row) for row in db.get_message_deliveries(draft_id=draft_id)],
            "verifications": db.get_followup_verifications(draft_id),
            "recovery": db.get_followup_recovery_status(draft_id),
            "batches": db.get_gstr2b_imports(50),
        }

    @app.get("/api/followups/<int:draft_id>")
    def followup_detail(draft_id: int):
        detail = _draft_detail(draft_id)
        if detail is None:
            return _error("Follow-up draft not found.", 404)
        return jsonify(detail)

    @app.patch("/api/followups/<int:draft_id>")
    def edit_followup(draft_id: int):
        payload = request.get_json(silent=True) or {}
        text = payload.get("draft_text")
        if not isinstance(text, str):
            return _error("draft_text is required.")
        try:
            db.update_followup_draft_text(draft_id, text)
            detail = _draft_detail(draft_id)
        except KeyError:
            return _error("Follow-up draft not found.", 404)
        except ValueError as exc:
            return _error(str(exc))
        return jsonify(detail)

    @app.post("/api/followups/<int:draft_id>/approve")
    def approve_followup(draft_id: int):
        payload = request.get_json(silent=True) or {}
        if payload.get("confirmed") is not True:
            return _error("Explicit confirmation is required. Nothing was approved or sent.")
        try:
            db.approve_followup_draft(draft_id, confirmed=True)
            detail = _draft_detail(draft_id)
        except KeyError:
            return _error("Follow-up draft not found.", 404)
        except ValueError as exc:
            return _error(str(exc))
        return jsonify(detail)

    @app.post("/api/followups/<int:draft_id>/skip")
    def skip_followup(draft_id: int):
        try:
            db.skip_followup_draft(draft_id)
            detail = _draft_detail(draft_id)
        except KeyError:
            return _error("Follow-up draft not found.", 404)
        except ValueError as exc:
            return _error(str(exc))
        return jsonify(detail)

    @app.post("/api/followups/<int:draft_id>/verify")
    def verify_followup(draft_id: int):
        payload = request.get_json(silent=True) or {}
        try:
            later_id = _int_arg(payload.get("later_batch_id"), "later_batch_id")
            outcomes = db.verify_followup_with_later_import(draft_id, later_id)
            detail = _draft_detail(draft_id)
        except KeyError:
            return _error("Follow-up draft not found.", 404)
        except ValueError as exc:
            return _error(str(exc))
        return jsonify({"outcomes": outcomes, "detail": detail})

    def _provider_for(row: dict):
        if row["provider"] == delivery.PROVIDER_MOCK:
            return delivery.MockDeliveryProvider(max_attempts=row["max_attempts"])
        config = delivery.load_whatsapp_config()
        if not config.ready:
            raise delivery.DeliveryConfigurationError(
                "WhatsApp Cloud API is not configured. No message was sent."
            )
        return delivery.WhatsAppCloudApiProvider(config)

    @app.post("/api/followups/<int:draft_id>/send")
    def send_followup(draft_id: int):
        payload = request.get_json(silent=True) or {}
        mode = str(payload.get("mode") or "").strip().lower()
        if mode not in ("mock", "live"):
            return _error("mode must be 'mock' or 'live'.")
        if payload.get("confirm_send") is not True:
            return _error("Explicit send confirmation is required. No message was sent.")
        if payload.get("opt_in_confirmed") is not True:
            return _error("Recipient opt-in must be confirmed. No message was sent.")
        try:
            if mode == "mock":
                provider = delivery.MockDeliveryProvider()
                record = delivery.deliver_approved_draft(
                    draft_id, provider, opt_in_confirmed=True
                )
            else:
                config = delivery.load_whatsapp_config()
                if not config.ready:
                    return _error("WhatsApp Cloud API is not configured. No message was sent.")
                provider = delivery.WhatsAppCloudApiProvider(config)
                template = str(payload.get("template_name") or "").strip() or None
                language = str(payload.get("template_language") or "en").strip() or "en"
                record = delivery.deliver_approved_draft(
                    draft_id,
                    provider,
                    opt_in_confirmed=True,
                    template_name=template,
                    template_language=language if template else None,
                )
        except (delivery.DeliveryBlockedError, delivery.DeliveryConfigurationError) as exc:
            return _error(str(exc), 409)
        except KeyError:
            return _error("Follow-up draft not found.", 404)
        except Exception as exc:
            return _error(f"Delivery failed ({type(exc).__name__}). Check the delivery log.", 502)
        return jsonify({"delivery": _delivery_row(record), "mode": mode})

    @app.post("/api/deliveries/<int:delivery_id>/retry")
    def retry_delivery(delivery_id: int):
        row = db.get_message_delivery(delivery_id)
        if row is None:
            return _error("Delivery not found.", 404)
        try:
            provider = _provider_for(row)
            result = delivery.retry_message_delivery(delivery_id, provider)
        except (delivery.DeliveryBlockedError, delivery.DeliveryConfigurationError) as exc:
            return _error(str(exc), 409)
        except Exception as exc:
            return _error(f"Retry failed ({type(exc).__name__}).", 502)
        return jsonify({"delivery": _delivery_row(result)})

    @app.post("/api/deliveries/<int:delivery_id>/resolve")
    def resolve_delivery(delivery_id: int):
        payload = request.get_json(silent=True) or {}
        try:
            delivery.resolve_unknown(
                delivery_id,
                outcome=str(payload.get("outcome") or ""),
                resolved_by=str(payload.get("resolved_by") or ""),
                note=str(payload.get("note") or ""),
                provider_message_id=payload.get("provider_message_id"),
            )
        except KeyError:
            return _error("Delivery not found.", 404)
        except ValueError as exc:
            return _error(str(exc))
        row = db.get_message_delivery(delivery_id)
        return jsonify({"delivery": _delivery_row(row)})

    @app.get("/api/export/reconciliation.xlsx")
    @app.get("/api/reconciliation/<int:run_id>/workbook.xlsx")
    def export_reconciliation_workbook(run_id: int | None = None):
        try:
            if run_id is None:
                raw_run_id = request.args.get("run_id")
                if raw_run_id is None or str(raw_run_id).strip() == "":
                    latest = db.get_latest_reconciliation_run()
                    if latest is None:
                        return _error("No reconciliation run exists yet.", 404)
                    run_id = latest["id"]
                else:
                    run_id = _int_arg(raw_run_id, "run_id")
            content = report_workbook.build_reconciliation_workbook(run_id)
        except ValueError as exc:
            status = 404 if "not found" in str(exc) else 400
            return _error(str(exc), status)
        return Response(
            content,
            mimetype=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": (
                    f"attachment; filename=milaan-reconciliation-"
                    f"run-{run_id}.xlsx"
                )
            },
        )

    @app.get("/api/export/<kind>.csv")
    def export_csv(kind: str):
        if kind == "invoices":
            rows = _invoices()
            columns = [
                ("id", "ID"), ("number", "Invoice"), ("supplier", "Supplier"),
                ("gstin", "GSTIN"), ("date", "Date"), ("period", "Tax period"),
                ("taxable", "Taxable"), ("rate", "Rate %"), ("gst", "Total GST"),
                ("total", "Invoice total"), ("validation", "Validation"),
                ("source", "Source"),
            ]
        elif kind == "reconciliation":
            rows = _reconciliation()["results"]
            columns = [
                ("invoice_id", "Invoice ID"), ("number", "Invoice"),
                ("supplier", "Supplier"), ("gstin", "GSTIN"),
                ("period", "Tax period"), ("invoice_taxable", "Invoice taxable"),
                ("invoice_gst", "Invoice GST"), ("twob_number", "2B invoice"),
                ("twob_taxable", "2B taxable"), ("twob_gst", "2B GST"),
                ("taxable_difference", "Taxable difference"),
                ("difference", "GST difference"), ("status", "Status"),
                ("method", "Method"),
            ]
        elif kind == "followups":
            rows = _draft_rows()
            columns = [
                ("id", "Draft"), ("supplier", "Supplier"), ("gstin", "GSTIN"),
                ("phone", "WhatsApp"), ("status", "Draft status"),
                ("workflow_status", "Follow-up status"),
                ("recovery_status", "Recovery evidence"),
                ("invoices", "Bills"), ("taxable", "Taxable"), ("gst", "GST"),
                ("created", "Created"),
            ]
        elif kind == "audit":
            rows = _audit()
            columns = [
                ("at", "At"), ("actor", "Actor"), ("event", "Event"),
                ("subject", "Subject"), ("source", "Source"),
            ]
        else:
            return _error("Unknown export.", 404)

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow([label for _, label in columns])
        for row in rows:
            writer.writerow([_csv_cell(row.get(key, "")) for key, _ in columns])
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=milaan-{kind}.csv"},
        )

    @app.get("/api/invoices/template.csv")
    def invoice_template():
        return Response(
            invoice_import.template_csv(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=milaan-invoice-template.csv"},
        )

    @app.post("/api/data/clear")
    def clear_data():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get("confirm") is not True:
            return _error(
                "Explicit confirmation is required to clear all data. Nothing was deleted.",
                400,
            )
        try:
            backup = db.backup_database(label="pre-clear")
        except Exception as exc:
            return _error(
                f"Could not create a safety backup ({type(exc).__name__}); nothing was deleted.",
                500,
            )
        counts = {
            "invoices": len(_invoices()),
            "gstr2b_batches": len(db.get_gstr2b_imports(1_000_000)),
        }
        try:
            db.clear_all_data()
        except Exception as exc:
            return _error(
                f"Clearing data failed ({type(exc).__name__}). A backup was saved at {backup}.",
                500,
            )
        db.record_system_audit_event(
            actor="ui",
            event_type="data_cleared",
            subject_type="workspace",
            subject_id="all",
            details={**counts, "backup": backup.name},
        )
        return jsonify({
            "ok": True,
            "message": "All data cleared. A backup was saved first.",
            "backup": str(backup),
        })

    @app.route("/api/settings/organization", methods=["GET", "POST"])
    def organization_settings():
        if request.method == "POST":
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return _error("Send the organization details as a JSON object.")
            values, problems = _validate_organization(payload)
            if problems:
                return _error(
                    "Please correct the highlighted organization details. Nothing was saved.",
                    400,
                    fields=problems,
                )
            for field, value in values.items():
                db.set_system_meta(f"org_{field}", value)
            return jsonify({
                "ok": True,
                "message": "Organization details saved.",
                "organization": _organization(),
            })
        return jsonify(_organization())

    return app



def main() -> None:
    port = int(os.environ.get("MILAAN_UI_PORT", "8501"))
    app = create_app()
    print(f"MILAAN primary UI on http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
