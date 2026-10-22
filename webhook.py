"""Minimal authenticated WhatsApp Cloud API webhook receiver.

Runs as a standalone stdlib HTTP server (no extra dependencies) and must be
reached over HTTPS in production. It handles:

- GET  /webhook/whatsapp  Meta's verification handshake (hub.challenge).
- POST /webhook/whatsapp  delivery statuses and inbound recipient replies,
  with ``X-Hub-Signature-256`` verified against WHATSAPP_APP_SECRET using
  the raw request body.

Design and safety:

- Never exposed publicly by default: binds to 127.0.0.1 unless
  WHATSAPP_WEBHOOK_BIND is explicitly set. In production it sits behind a
  reverse proxy that terminates HTTPS; see README.md.
- No credentials are ever logged or returned. Responses contain only status
  codes and short non-secret text.
- Processing is idempotent: duplicate webhook deliveries from Meta never
  apply a status or record a reply twice.
- All persistence goes through db.py; all audit events are recorded by
  delivery.process_webhook_payload().

Run:  WHATSAPP_WEBHOOK_ENABLED=true WHATSAPP_APP_SECRET=... \\
      WHATSAPP_VERIFY_TOKEN=... python webhook.py [--port 8443] [--bind 127.0.0.1]
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping

import delivery
import db

WEBHOOK_PATH = "/webhook/whatsapp"
MAX_BODY_BYTES = 1_048_576  # 1 MiB is far beyond any Meta payload


def _load_config() -> delivery.WhatsAppConfig:
    return delivery.load_whatsapp_config()


def handle_verify(
    params: Mapping[str, str], config: delivery.WhatsAppConfig
) -> tuple[int, bytes, str]:
    """Return (status, body, content_type) for the verification GET."""
    accepted, challenge_or_reason = delivery.verify_webhook_challenge(
        params, config.verify_token
    )
    if accepted:
        return 200, challenge_or_reason.encode("utf-8", errors="replace"), "text/plain"
    return 403, challenge_or_reason.encode("utf-8"), "text/plain"


def handle_notify(
    body: bytes, headers: Mapping[str, str], config: delivery.WhatsAppConfig
) -> tuple[int, bytes, str]:
    """Return (status, body, content_type) for the verified POST."""
    signature = headers.get("X-Hub-Signature-256")
    if not delivery.verify_webhook_signature(
        body, signature, config.app_secret
    ):
        return 403, b"signature verification failed", "text/plain"
    try:
        payload: Any = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, b"invalid JSON", "text/plain"
    if not isinstance(payload, dict):
        return 400, b"invalid payload", "text/plain"
    summary = delivery.process_webhook_payload(payload, config=config)
    # Meta expects a 200 quickly; acknowledge receipt without echoing data.
    return 200, b"EVENT_RECEIVED", "text/plain"


class WhatsAppWebhookHandler(BaseHTTPRequestHandler):
    server_version = "MILAAN-Webhook/1.0"

    def _config(self) -> delivery.WhatsAppConfig:
        config = getattr(self.server, "whatsapp_config", None)
        if config is None:
            config = _load_config()
        return config

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != WEBHOOK_PATH:
            self._send(404, b"not found", "text/plain")
            return
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(
            (key, value)
            for part in query.split("&")
            if "=" in part
            for key, value in [part.split("=", 1)]
        )
        status, body, content_type = handle_verify(params, self._config())
        self._send(status, body, content_type)

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != WEBHOOK_PATH:
            self._send(404, b"not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, b"bad content length", "text/plain")
            return
        if not 0 < length <= MAX_BODY_BYTES:
            self._send(413, b"payload too large", "text/plain")
            return
        body = self.rfile.read(length)
        status, body_out, content_type = handle_notify(
            body, dict(self.headers), self._config()
        )
        self._send(status, body_out, content_type)

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        # Never log the raw request line: Meta's handshake puts the verify
        # token in the query string, which must not reach any log.
        path = self.path.split("?", 1)[0]
        sys.stderr.write(f"webhook: {self.command} {path} {code}\n")

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress default messages; they can embed the query string.
        return


def build_server(
    config: delivery.WhatsAppConfig | None = None,
) -> ThreadingHTTPServer:
    cfg = config or _load_config()
    server = ThreadingHTTPServer(
        (cfg.webhook_bind, cfg.webhook_port), WhatsAppWebhookHandler
    )
    server.whatsapp_config = cfg
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--bind", default=None)
    args = parser.parse_args()
    config = _load_config()
    if args.port is not None or args.bind is not None:
        config = delivery.WhatsAppConfig(
            enabled=config.enabled,
            access_token=config.access_token,
            phone_number_id=config.phone_number_id,
            api_version=config.api_version,
            max_attempts=config.max_attempts,
            timeout_seconds=config.timeout_seconds,
            webhook_enabled=config.webhook_enabled,
            verify_token=config.verify_token,
            app_secret=config.app_secret,
            webhook_port=args.port if args.port is not None else config.webhook_port,
            webhook_bind=args.bind if args.bind is not None else config.webhook_bind,
            issues=config.issues,
        )
    if not config.webhook_ready:
        print(
            "Webhook not configured: "
            + ", ".join(config.webhook_problems()),
            file=sys.stderr,
        )
        return 2
    if config.webhook_bind not in ("127.0.0.1", "localhost"):
        print(
            "Refusing to bind webhook to a non-loopback address "
            f"({config.webhook_bind!r}). Bind to 127.0.0.1 and put an "
            "HTTPS reverse proxy in front of it.",
            file=sys.stderr,
        )
        return 2
    server = build_server(config)
    print(
        f"MILAAN WhatsApp webhook listening on "
        f"http://{config.webhook_bind}:{config.webhook_port}{WEBHOOK_PATH}"
    )
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping MILAAN webhook.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    db.init_db()
    raise SystemExit(main())
