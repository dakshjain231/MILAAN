"""Create a consistent SQLite backup without stopping MILAAN.

Usage:
  DATABASE_PATH=/srv/milaan/milaan.db python scripts/backup_database.py /srv/backups
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import db  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/backup_database.py BACKUP_DIRECTORY")
    destination = Path(sys.argv[1]).expanduser().resolve()
    if not destination.exists() or not destination.is_dir():
        raise SystemExit("BACKUP_DIRECTORY must be an existing directory")
    try:
        print(db.backup_database(destination))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
