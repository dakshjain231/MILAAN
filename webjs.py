"""Loopback-only client for the local ``whatsapp-web.js`` service.

This is deliberately separate from the Cloud API delivery pipeline. Session
credentials live in the Node service's gitignored ``.wwebjs_auth`` directory
and are never read or logged here.

Two entry points use this bridge:

- :func:`demo_test_send_message` / :func:`demo_test_send` send one message to
  the configured test recipient and record only an audit event.
- :func:`send_followup_live` is the real Supplier Follow-ups delivery path.
  It creates a ``message_deliveries`` row through ``delivery.py``, so the
  recorded status and WhatsApp message ID are the provider's own.

Every send through this module passes an allowlist gate
(``MILAAN_WA_ALLOWED_RECIPIENTS``, defaulting to ``MILAAN_WA_TEST_RECIPIENT``)
inside :class:`WhatsAppWebJsProvider`. A recipient that is not on the
allowlist is refused locally, before any HTTP call: supplier numbers can
never be reached through this channel, however the provider is invoked.
Live follow-up delivery additionally requires
``MILAAN_WA_LIVE_FOLLOWUPS=true``, a linked WhatsApp Web session and two
explicit confirmations per send; nothing is ever sent automatically.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlparse

import requests

import db
import delivery

DEFAULT_BASE_URL = "http://127.0.0.1:8801"
PROVIDER_WEBJS = "whatsapp_web_js"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MIN_TOKEN_LENGTH = 16
DEFAULT_TIMEOUT_SECONDS = 8.0
_TRUE_VALUES = {"1", "true", "yes", "on"}

DEMO_TEST_MESSAGE = (
    "MILAAN WhatsApp Web demo test. This message confirms that the local "
    "WhatsApp Web service can send to the consented test number. No supplier "
    "action is required."
)


class WebJsError(RuntimeError):
    """Base error with a safe, non-secret message."""


class WebJsConfigurationError(WebJsError):
    """The demo service is disabled or misconfigured; nothing was sent."""


class WebJsBlocked(WebJsError):
    """Draft state or missing confirmations block the demo send."""


@dataclass(frozen=True)
class WebJsConfig:
    enabled: bool = False
    base_url: str = DEFAULT_BASE_URL
    token: str | None = None
    test_recipient: str | None = None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    issues: tuple[str, ...] = field(default_factory=tuple)
    # Numbers this bridge may message at all. Empty means "the configured
    # test recipient only"; it never means "anyone".
    allowed_recipients: tuple[str, ...] = field(default_factory=tuple)
    # Master switch for real Supplier Follow-ups delivery over this bridge.
    live_followups: bool = False

    def allowlist_digits(self) -> tuple[str, ...]:
        """Normalized digits of every number this bridge may message.

        Falls back to the configured test recipient so an operator cannot
        widen the allowlist by leaving it unset. Implausible entries are
        dropped here and reported by :meth:`problems`.
        """
        raw = self.allowed_recipients or (
            (self.test_recipient,) if self.test_recipient else ()
        )
        digits: list[str] = []
        for value in raw:
            normalized = db.normalize_phone_digits(value)
            if normalized is not None and normalized not in digits:
                digits.append(normalized)
        return tuple(digits)

    def is_allowed_recipient(self, value: str | None) -> bool:
        normalized = db.normalize_phone_digits(value)
        if normalized is None:
            return False
        return normalized in self.allowlist_digits()

    def problems(self) -> list[str]:
        problems = list(self.issues)
        if not self.enabled:
            problems.append("MILAAN_WA_ENABLED is not true")
            return problems

        for value in self.allowed_recipients:
            if db.normalize_phone_digits(value) is None:
                problems.append(
                    "MILAAN_WA_ALLOWED_RECIPIENTS contains a number that is "
                    "not plausible"
                )
                break
        if not self.allowlist_digits():
            problems.append(
                "no allowed recipient is configured "
                "(MILAAN_WA_ALLOWED_RECIPIENTS or MILAAN_WA_TEST_RECIPIENT)"
            )

        parsed = urlparse((self.base_url or "").strip())
        if parsed.scheme != "http":
            problems.append("MILAAN_WA_SERVICE_URL must be an http URL")
        if (parsed.hostname or "").lower() not in LOOPBACK_HOSTS:
            problems.append("MILAAN_WA_SERVICE_URL must use a loopback host")
        if parsed.username or parsed.password:
            problems.append("MILAAN_WA_SERVICE_URL must not contain credentials")
        if not self.token or len(self.token.strip()) < MIN_TOKEN_LENGTH:
            problems.append(
                "MILAAN_WA_SERVICE_TOKEN is missing or shorter than "
                f"{MIN_TOKEN_LENGTH} characters"
            )
        if not self.test_recipient:
            problems.append("MILAAN_WA_TEST_RECIPIENT is missing")
        elif db.normalize_phone_digits(self.test_recipient) is None:
            problems.append(
                "MILAAN_WA_TEST_RECIPIENT must be a plausible phone number"
            )
        if not 0 < self.timeout_seconds <= 30:
            problems.append("MILAAN_WA_TIMEOUT_SECONDS must be between 0 and 30")
        return problems

    @property
    def ready(self) -> bool:
        return not self.problems()

    def safe_summary(self) -> dict:
        return {
            "enabled": self.enabled,
            "ready": self.ready,
            "base_url": (self.base_url or "").strip(),
            "test_recipient_masked": mask_phone(self.test_recipient),
            "timeout_seconds": self.timeout_seconds,
            "problems": self.problems(),
            "live_followups": self.live_followups,
            "allowed_recipients_masked": [
                mask_phone(value) for value in self.allowlist_digits()
            ],
        }


def _env_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in _TRUE_VALUES


def _parse_recipients(raw: str | None) -> tuple[str, ...]:
    """Split a comma- or semicolon-separated allowlist, preserving order.

    Spaces inside an entry are kept, so ``+91 88888 88888`` stays one number.
    """
    if not raw:
        return ()
    parts = [piece.strip() for piece in str(raw).replace(";", ",").split(",")]
    return tuple(piece for piece in parts if piece)


def load_webjs_config(
    environ: Mapping[str, str] | None = None,
) -> WebJsConfig:
    env = os.environ if environ is None else environ
    base_url = (
        env.get("MILAAN_WA_SERVICE_URL") or DEFAULT_BASE_URL
    ).strip()
    token = (env.get("MILAAN_WA_SERVICE_TOKEN") or "").strip() or None
    test_recipient = (
        env.get("MILAAN_WA_TEST_RECIPIENT") or ""
    ).strip() or None

    issues: list[str] = []
    timeout = DEFAULT_TIMEOUT_SECONDS
    raw_timeout = env.get("MILAAN_WA_TIMEOUT_SECONDS")
    if raw_timeout:
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError):
            issues.append("MILAAN_WA_TIMEOUT_SECONDS must be a number")

    return WebJsConfig(
        enabled=_env_bool(env.get("MILAAN_WA_ENABLED"), False),
        base_url=base_url,
        token=token,
        test_recipient=test_recipient,
        timeout_seconds=timeout,
        issues=tuple(issues),
        allowed_recipients=_parse_recipients(
            env.get("MILAAN_WA_ALLOWED_RECIPIENTS")
        ),
        live_followups=_env_bool(env.get("MILAAN_WA_LIVE_FOLLOWUPS"), False),
    )


def mask_phone(value: str | None) -> str | None:
    digits = db.normalize_phone_digits(value)
    if digits is None:
        if not value:
            return None
        digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return None
    if len(digits) <= 4:
        return "\u2026"
    return "\u2026" + digits[-4:]


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any] | None,
    timeout: float,
) -> tuple[int, bytes]:
    response = requests.request(
        method,
        url,
        headers=dict(headers),
        json=payload,
        timeout=timeout,
    )
    return response.status_code, response.content


def _call(
    config: WebJsConfig,
    method: str,
    path: str,
    payload: Mapping[str, Any] | None = None,
) -> tuple[int, Any]:
    problems = config.problems()
    if problems:
        raise WebJsConfigurationError(
            "WhatsApp Web demo is not configured: " + ", ".join(problems)
        )
    url = config.base_url.rstrip("/") + path
    headers = {
        "Authorization": f"Bearer {config.token}",
        "Accept": "application/json",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    status, body = _default_transport(
        method, url, headers, payload, config.timeout_seconds
    )
    try:
        decoded = json.loads(body.decode("utf-8")) if body else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        decoded = None
    return status, decoded


# Bridge error codes for failures after the client attempted the send.
_AMBIGUOUS_SEND_CODES = {"send_outcome_unknown", "missing_message_id", "send_failed"}


def _payload_error(payload: Any, secrets: tuple[str | None, ...] = ()) -> str | None:
    if isinstance(payload, dict):
        value = payload.get("error")
        if isinstance(value, str) and value.strip():
            return delivery.sanitize_error(value, secrets=secrets, limit=80)
    return None


def service_status(config: WebJsConfig | None = None) -> dict:
    """Return the local service status without ever raising."""
    cfg = config or load_webjs_config()
    if not cfg.enabled:
        return {
            "reachable": False,
            "status": "disabled",
            "ready": False,
            "qr_available": False,
            "phone_masked": None,
            "problems": cfg.problems(),
        }
    if cfg.problems():
        return {
            "reachable": False,
            "status": "misconfigured",
            "ready": False,
            "qr_available": False,
            "phone_masked": None,
            "problems": cfg.problems(),
        }
    try:
        status, payload = _call(cfg, "GET", "/status")
    except Exception:
        return {
            "reachable": False,
            "status": "unreachable",
            "ready": False,
            "qr_available": False,
            "phone_masked": None,
            "problems": [],
        }
    if status in (401, 403):
        return {
            "reachable": True,
            "status": "unauthorized",
            "ready": False,
            "qr_available": False,
            "phone_masked": None,
            "problems": ["MILAAN_WA_SERVICE_TOKEN was rejected"],
        }
    if status != 200 or not isinstance(payload, dict):
        return {
            "reachable": True,
            "status": "error",
            "ready": False,
            "qr_available": False,
            "phone_masked": None,
            "problems": [],
        }
    return {
        "reachable": True,
        "status": str(payload.get("status") or "unknown"),
        "ready": payload.get("ready") is True,
        "qr_available": payload.get("qrAvailable") is True,
        "phone_masked": payload.get("phoneMasked"),
        "since": payload.get("since"),
        "problems": [],
    }


def fetch_qr_data_url(config: WebJsConfig | None = None) -> str | None:
    cfg = config or load_webjs_config()
    try:
        status, payload = _call(cfg, "GET", "/qr")
    except Exception:
        return None
    if status != 200 or not isinstance(payload, dict):
        return None
    qr = payload.get("qr")
    if not isinstance(qr, str) or not qr.startswith("data:image/png;base64,"):
        return None
    if len(qr) > 2_000_000:
        return None
    return qr


def fetch_qr_png(config: WebJsConfig | None = None) -> bytes | None:
    data_url = fetch_qr_data_url(config)
    if not data_url:
        return None
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
    except (ValueError, binascii.Error):
        return None
    if not raw.startswith(b"\x89PNG\r\n\x1a\n") or len(raw) > 2_000_000:
        return None
    return raw


class WhatsAppWebJsProvider:
    """Provider-shaped adapter for the local whatsapp-web.js service.

    Every send passes the configured allowlist first, so this provider can
    only ever reach a number the operator listed in
    ``MILAAN_WA_ALLOWED_RECIPIENTS`` (or ``MILAAN_WA_TEST_RECIPIENT``).
    A supplier number is refused locally, with no HTTP call, whichever code
    path calls :meth:`send`. ``max_attempts`` is 1: WhatsApp Web may have
    accepted a message whose outcome came back unknown, so an automatic
    retry could duplicate it.
    """

    name = PROVIDER_WEBJS
    max_attempts = 1

    def __init__(self, config: WebJsConfig) -> None:
        self.config = config

    @property
    def is_ready(self) -> bool:
        return self.config.ready

    def send(
        self,
        recipient_phone: str,
        message_body: str,
        *,
        idempotency_key: str | None = None,
        template_name: str | None = None,
        template_language: str | None = None,
    ) -> delivery.ProviderResponse:
        if template_name or template_language:
            return delivery.ProviderResponse(
                outcome=delivery.OUTCOME_CONFIG_ERROR,
                error_code="TEMPLATE_UNSUPPORTED",
                error_message=(
                    "The local WhatsApp Web demo sends plain text only; "
                    "templates are not supported."
                ),
            )
        recipient = delivery.normalize_recipient_phone(recipient_phone)
        if recipient is None:
            return delivery.ProviderResponse(
                outcome=delivery.OUTCOME_INVALID_REQUEST,
                error_code="INVALID_RECIPIENT",
                error_message="Recipient phone number is not plausible.",
            )
        if not self.config.is_allowed_recipient(recipient):
            # Hard stop before any network call: this channel is restricted
            # to the configured allowlist, so suppliers are unreachable here.
            return delivery.ProviderResponse(
                outcome=delivery.OUTCOME_INVALID_REQUEST,
                error_code="RECIPIENT_NOT_ALLOWED",
                error_message=(
                    "This recipient is not on the WhatsApp Web allowlist. "
                    "No message was sent."
                ),
            )
        try:
            status, payload = _call(
                self.config,
                "POST",
                "/send",
                {
                    "to": recipient,
                    "message": message_body,
                    "idempotencyKey": idempotency_key,
                },
            )
        except WebJsConfigurationError as exc:
            return delivery.ProviderResponse(
                outcome=delivery.OUTCOME_CONFIG_ERROR,
                error_code="SERVICE_NOT_CONFIGURED",
                error_message=delivery.sanitize_error(
                    str(exc), secrets=(self.config.token,)
                ),
            )
        except requests.Timeout:
            return delivery.ProviderResponse(
                outcome=delivery.OUTCOME_UNKNOWN,
                error_code="SERVICE_TIMEOUT",
                error_message=(
                    "The local service did not answer in time; the outcome "
                    "is unknown."
                ),
            )
        except Exception as exc:
            return delivery.ProviderResponse(
                outcome=delivery.OUTCOME_UNKNOWN,
                error_code="SERVICE_UNREACHABLE",
                error_message=delivery.sanitize_error(
                    f"{type(exc).__name__}: service request failed",
                    secrets=(self.config.token,),
                ),
            )

        code = _payload_error(payload, secrets=(self.config.token,))
        if status == 200 and isinstance(payload, dict) and payload.get("ok") is True:
            message_id = payload.get("messageId")
            if isinstance(message_id, str) and message_id.strip():
                return delivery.ProviderResponse(
                    outcome=delivery.OUTCOME_ACCEPTED,
                    provider_message_id=message_id.strip()[:200],
                    http_status=status,
                )
            return delivery.ProviderResponse(
                outcome=delivery.OUTCOME_UNKNOWN,
                http_status=status,
                error_code="MISSING_PROVIDER_MESSAGE_ID",
                error_message=(
                    "The local service accepted the request without a valid "
                    "message ID."
                ),
            )
        if code == "recipient_not_allowed":
            # The Node service keeps its own allowlist; a refusal there means
            # nothing was handed to WhatsApp.
            return delivery.ProviderResponse(
                outcome=delivery.OUTCOME_INVALID_REQUEST,
                http_status=status,
                error_code="RECIPIENT_NOT_ALLOWED",
                error_message=(
                    "The local service refused the recipient: it is not on "
                    "the service allowlist. No message was sent."
                ),
            )
        if status in (401, 403):
            return delivery.ProviderResponse(
                outcome=delivery.OUTCOME_AUTH_ERROR,
                http_status=status,
                error_code=code or "UNAUTHORIZED",
                error_message="The local service rejected the service token.",
            )
        if status == 409:
            return delivery.ProviderResponse(
                outcome=delivery.OUTCOME_CONFIG_ERROR,
                http_status=status,
                error_code=code or "SERVICE_NOT_READY",
                error_message=(
                    "The WhatsApp Web client is not linked or not ready."
                ),
            )
        if status in (400, 413, 422):
            return delivery.ProviderResponse(
                outcome=delivery.OUTCOME_INVALID_REQUEST,
                http_status=status,
                error_code=code or f"HTTP_{status}",
                error_message=(
                    "The local service rejected the message request."
                ),
            )
        if code in _AMBIGUOUS_SEND_CODES:
            # The client threw or returned no id after attempting the send:
            # WhatsApp may already have the message, so a retry could
            # duplicate it. Preserve the outcome as unknown.
            return delivery.ProviderResponse(
                outcome=delivery.OUTCOME_UNKNOWN,
                http_status=status,
                error_code=code,
                error_message=(
                    "WhatsApp Web did not confirm the send; the message may "
                    "or may not have been delivered. Check before retrying."
                ),
            )
        if status == 429 or 500 <= status < 600:
            return delivery.ProviderResponse(
                outcome=delivery.OUTCOME_RETRYABLE,
                http_status=status,
                error_code=code or f"HTTP_{status}",
                error_message=(
                    "The local service reported a transient failure."
                ),
            )
        return delivery.ProviderResponse(
            outcome=delivery.OUTCOME_UNKNOWN,
            http_status=status,
            error_code=code or f"HTTP_{status}",
            error_message="The local service outcome could not be established.",
        )


def _send_demo_body(
    body: str,
    *,
    cfg: WebJsConfig,
    recipient: str,
    actor: str,
    subject_type: str,
    subject_id: str,
    extra_details: dict,
) -> dict:
    response = WhatsAppWebJsProvider(cfg).send(recipient, body)
    masked = mask_phone(recipient)
    error_message = (
        delivery.sanitize_error(response.error_message, secrets=(cfg.token,))
        if response.error_message else None
    )
    details = {
        "provider": PROVIDER_WEBJS,
        **extra_details,
        "test_recipient_masked": masked,
        "outcome": response.outcome,
        "error_code": response.error_code,
        "provider_message_id": response.provider_message_id,
        "recorded_as_delivery": False,
    }
    result = {
        "provider": PROVIDER_WEBJS,
        "recipient_masked": masked,
        "body": body,
        "outcome": response.outcome,
        "provider_message_id": response.provider_message_id,
        "http_status": response.http_status,
        "error_code": response.error_code,
        "error_message": error_message,
        "simulated": False,
        "recorded_as_delivery": False,
        "audit_recorded": False,
        **extra_details,
    }
    try:
        db.record_system_audit_event(
            actor=actor,
            event_type="webjs_demo_send",
            subject_type=subject_type,
            subject_id=subject_id,
            details=details,
        )
        result["audit_recorded"] = True
    except Exception:
        result["audit_recorded"] = False
    return result


def _require_demo_prerequisites(
    *, config: WebJsConfig | None, confirmed: bool, opt_in_confirmed: bool
) -> tuple[WebJsConfig, str]:
    if not confirmed or not opt_in_confirmed:
        raise WebJsBlocked(
            "Demo send blocked: explicit send approval and confirmation that "
            "the test recipient consented are both required."
        )
    cfg = config or load_webjs_config()
    if not cfg.ready:
        raise WebJsConfigurationError(
            "Demo send blocked: " + ", ".join(cfg.problems()) + "."
        )
    recipient = db.normalize_phone_digits(cfg.test_recipient)
    if recipient is None:
        raise WebJsConfigurationError(
            "Demo send blocked: MILAAN_WA_TEST_RECIPIENT is not plausible."
        )
    return cfg, recipient


def demo_test_send(
    draft_id: int,
    *,
    config: WebJsConfig | None = None,
    actor: str = "local-demo",
    confirmed: bool = False,
    opt_in_confirmed: bool = False,
) -> dict:
    """Send one approved follow-up preview to the consented test recipient.

    This never creates a ``message_deliveries`` row and never uses the
    supplier's phone number. Both explicit confirmations are mandatory.
    """
    cfg, recipient = _require_demo_prerequisites(
        config=config,
        confirmed=confirmed,
        opt_in_confirmed=opt_in_confirmed,
    )
    draft = db.get_followup_draft(draft_id)
    if draft is None:
        raise WebJsBlocked("Demo send blocked: draft not found.")
    eligibility = db.get_delivery_eligibility(draft_id)
    if not eligibility["eligible"]:
        raise WebJsBlocked(
            "Demo send blocked: " + ", ".join(eligibility["problems"]) + "."
        )
    body = eligibility["approved_text"]
    if not body:
        raise WebJsBlocked("Demo send blocked: approved message is missing.")
    return _send_demo_body(
        body,
        cfg=cfg,
        recipient=recipient,
        actor=actor,
        subject_type="followup_draft",
        subject_id=str(draft_id),
        extra_details={"draft_id": draft_id, "mode": "approved_draft"},
    )


def demo_test_send_message(
    message: str | None = None,
    *,
    config: WebJsConfig | None = None,
    actor: str = "local-demo",
    confirmed: bool = False,
    opt_in_confirmed: bool = False,
) -> dict:
    """Send the fixed demo test message without any draft or supplier data.

    The message goes only to ``MILAAN_WA_TEST_RECIPIENT``. No supplier phone
    number is read, no ``message_deliveries`` row is created, and only the
    existing safe ``webjs_demo_send`` audit event is recorded.
    """
    cfg, recipient = _require_demo_prerequisites(
        config=config,
        confirmed=confirmed,
        opt_in_confirmed=opt_in_confirmed,
    )
    body = (DEMO_TEST_MESSAGE if message is None else str(message)).strip()
    if not body:
        raise WebJsBlocked("Demo send blocked: the test message is empty.")
    if len(body) > 4096:
        raise WebJsBlocked(
            "Demo send blocked: the test message is longer than 4096 characters."
        )
    return _send_demo_body(
        body,
        cfg=cfg,
        recipient=recipient,
        actor=actor,
        subject_type="whatsapp_web_demo",
        subject_id="demo-test",
        extra_details={"draft_id": None, "mode": "standalone"},
    )


# --- live Supplier Follow-ups delivery ------------------------------------

LIVE_MODE = "whatsapp_web"


def live_send_preflight(config: WebJsConfig | None = None) -> dict:
    """Report whether a live follow-up send could run right now.

    Never sends and never raises. ``available`` is True only when the live
    switch is on, the configuration is complete, the local bridge answers and
    its WhatsApp Web session is linked and ready.
    """
    cfg = config or load_webjs_config()
    problems: list[str] = []
    if not cfg.live_followups:
        problems.append(
            "live WhatsApp Web delivery is off (MILAAN_WA_LIVE_FOLLOWUPS is "
            "not true)"
        )
    problems.extend(cfg.problems())
    status = service_status(cfg)
    if not status["reachable"]:
        # "disabled" and "misconfigured" are already covered by cfg.problems().
        if status["status"] not in ("disabled", "misconfigured"):
            problems.append(
                "the local WhatsApp Web bridge is not reachable on "
                f"{(cfg.base_url or '').strip()}"
            )
    elif not status["ready"]:
        if status["qr_available"]:
            problems.append(
                "the WhatsApp Web session is not linked: scan the QR code in "
                "Settings first"
            )
        else:
            problems.append(
                "the WhatsApp Web session is not ready "
                f"(status: {status['status']})"
            )
    deduped: list[str] = []
    for problem in problems:
        if problem not in deduped:
            deduped.append(problem)
    return {
        "mode": LIVE_MODE,
        "available": not deduped,
        "problems": deduped,
        "status": status,
        "live_followups": cfg.live_followups,
        "allowed_recipients_masked": [
            mask_phone(value) for value in cfg.allowlist_digits()
        ],
        "linked_phone_masked": status.get("phone_masked"),
    }


def draft_recipient_allowed(
    draft_id: int, config: WebJsConfig | None = None
) -> bool:
    """True if this draft's approved recipient is on the allowlist."""
    cfg = config or load_webjs_config()
    eligibility = db.get_delivery_eligibility(draft_id)
    return cfg.is_allowed_recipient(eligibility["approved_recipient_phone"])


def send_followup_live(
    draft_id: int,
    *,
    config: WebJsConfig | None = None,
    actor: str = "ui",
    confirmed: bool = False,
    opt_in_confirmed: bool = False,
) -> dict:
    """Deliver an approved follow-up through the local WhatsApp Web bridge.

    Unlike :func:`demo_test_send` this is the real Supplier Follow-ups
    delivery path: it creates a ``message_deliveries`` record through
    ``delivery.py``, so the returned status (SENT / FAILED / UNKNOWN /
    RETRY_PENDING) and ``provider_message_id`` are the bridge's own answer,
    never a simulation.

    Both confirmations are mandatory, the bridge must be live and linked, and
    the draft's approved recipient must be on the allowlist. Any failure
    raises or is recorded honestly; nothing is reported as delivered unless
    WhatsApp Web returned a message ID.
    """
    if not confirmed or not opt_in_confirmed:
        raise WebJsBlocked(
            "Live send blocked: explicit send confirmation and confirmation "
            "that the recipient agreed to receive this message are both "
            "required. No message was sent."
        )
    cfg = config or load_webjs_config()
    preflight = live_send_preflight(cfg)
    if not preflight["available"]:
        raise WebJsConfigurationError(
            "Live send blocked: " + "; ".join(preflight["problems"])
            + ". No message was sent."
        )
    eligibility = db.get_delivery_eligibility(draft_id)
    if eligibility["draft"] is None:
        raise KeyError(f"follow-up draft {draft_id} not found")
    if not eligibility["eligible"]:
        raise WebJsBlocked(
            "Live send blocked: " + ", ".join(eligibility["problems"])
            + ". No message was sent."
        )
    recipient = eligibility["approved_recipient_phone"]
    if not cfg.is_allowed_recipient(recipient):
        allowed = ", ".join(
            value or "?" for value in preflight["allowed_recipients_masked"]
        )
        raise WebJsBlocked(
            "Live send blocked: this follow-up's approved recipient is not on "
            f"the WhatsApp Web allowlist ({allowed or 'empty'}). Set the "
            "draft's number to an allowlisted number before approving it. "
            "No message was sent."
        )

    record = delivery.deliver_approved_draft(
        draft_id,
        WhatsAppWebJsProvider(cfg),
        opt_in_confirmed=True,
    )
    try:
        db.record_system_audit_event(
            actor=actor,
            event_type="webjs_live_followup_send",
            subject_type="followup_draft",
            subject_id=str(draft_id),
            details={
                "provider": PROVIDER_WEBJS,
                "mode": LIVE_MODE,
                "delivery_id": record.get("id"),
                "status": record.get("status"),
                "recipient_masked": mask_phone(
                    record.get("recipient_phone_snapshot")
                ),
                "provider_message_id": record.get("provider_message_id"),
                "error_code": record.get("last_error_code"),
                "recorded_as_delivery": True,
            },
        )
    except Exception:
        # The delivery record is the source of truth; a failed audit write
        # must not hide or alter the real outcome.
        pass
    return record
