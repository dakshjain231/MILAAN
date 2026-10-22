#!/usr/bin/env python3
"""Load the repository-root .env, then start the Streamlit app.

Usage:
    .venv/bin/python scripts/run_app.py              # http://127.0.0.1:8501
    .venv/bin/python scripts/run_app.py --server.port 8502

Existing environment variables always win over .env. Extra arguments are
passed straight through to ``streamlit run``.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import env_loader  # noqa: E402


def main() -> int:
    env_loader.load_dotenv()
    os.chdir(PROJECT_ROOT)

    executable_dir = Path(sys.executable).parent
    candidate = executable_dir / (
        "streamlit.exe" if os.name == "nt" else "streamlit"
    )
    if candidate.is_file():
        streamlit_bin = candidate
    else:
        found = shutil.which("streamlit")
        if not found:
            print(
                "Streamlit is not installed next to this interpreter. "
                "Install dependencies into the active virtual environment, "
                "then run this launcher with that environment's python.",
                file=sys.stderr,
            )
            return 1
        streamlit_bin = Path(found)

    passthrough = sys.argv[1:]
    argv = [str(streamlit_bin), "run", str(PROJECT_ROOT / "app.py")]
    if not any(
        item == "--server.address" or item.startswith("--server.address=")
        for item in passthrough
    ):
        argv.extend(["--server.address", "127.0.0.1"])
    argv.extend(passthrough)
    os.execv(str(streamlit_bin), argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
