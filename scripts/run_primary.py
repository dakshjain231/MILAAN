#!/usr/bin/env python3
"""Load .env, then start the primary MILAAN web application.

Usage:
    .venv/bin/python scripts/run_primary.py --port 8501
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(PROJECT_ROOT))

import env_loader  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the MILAAN primary UI.")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    env_loader.load_dotenv()
    os.chdir(PROJECT_ROOT)
    if args.port is not None:
        os.environ["MILAAN_UI_PORT"] = str(args.port)

    from milaan_web import main as run

    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
