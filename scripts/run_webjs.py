#!/usr/bin/env python3
"""Load the repository-root .env, then start the local WhatsApp Web demo.

This launcher is for the whatsapp-web.js demo service only. It is NOT a
production delivery path and it never sends supplier messages: the demo
sends a fixed test message to an explicitly configured test recipient.

Usage:
    .venv/bin/python scripts/run_webjs.py

Start the app separately with ``scripts/run_app.py``. Both processes must
see the same MILAAN_WA_SERVICE_TOKEN.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
SERVICE_DIR = PROJECT_ROOT / "whatsapp_service"
sys.path.insert(0, str(SCRIPTS_DIR))

import env_loader  # noqa: E402


def main() -> int:
    env_loader.load_dotenv()

    if not (SERVICE_DIR / "package.json").is_file():
        print(f"WhatsApp service not found at {SERVICE_DIR}", file=sys.stderr)
        return 1
    if not (SERVICE_DIR / "node_modules").is_dir():
        print(
            "Node dependencies are missing. Run:\n"
            "    cd whatsapp_service && npm ci",
            file=sys.stderr,
        )
        return 1
    if shutil.which("node") is None:
        print("Node.js 18+ is required on PATH.", file=sys.stderr)
        return 1

    os.chdir(SERVICE_DIR)
    return subprocess.call(["node", "src/index.js"])


if __name__ == "__main__":
    raise SystemExit(main())
