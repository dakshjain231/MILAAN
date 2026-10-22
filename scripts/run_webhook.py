#!/usr/bin/env python3
"""Load the repository-root .env, then start the verified webhook receiver.

Usage:
    .venv/bin/python scripts/run_webhook.py
    .venv/bin/python scripts/run_webhook.py --port 8443 --bind 127.0.0.1

Requires WHATSAPP_WEBHOOK_ENABLED=true, WHATSAPP_APP_SECRET and
WHATSAPP_VERIFY_TOKEN. In production, terminate HTTPS at a reverse proxy and
keep the receiver bound to loopback. Extra arguments pass through to
``webhook.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(PROJECT_ROOT))

import env_loader  # noqa: E402


def main() -> int:
    env_loader.load_dotenv()
    os.chdir(PROJECT_ROOT)

    import webhook  # noqa: E402  (import after .env is loaded)

    sys.argv = ["webhook.py", *sys.argv[1:]]
    return webhook.main()


if __name__ == "__main__":
    raise SystemExit(main())
