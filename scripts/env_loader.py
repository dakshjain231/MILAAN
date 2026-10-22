"""Deterministic, dependency-free loading of the repository-root ``.env``.

This is the only place MILAAN reads a ``.env`` file. The local launchers use
it so that operators no longer have to export variables into every shell by
hand. Rules, in order of priority:

1. Variables already present in the process environment always win. A
   ``.env`` file can never override a real deployment's configuration.
2. Only a regular file named ``.env`` in the repository root is considered.
   No parent-directory search and no per-directory magic.
3. Parsing is intentionally small: ``KEY=VALUE`` lines, blank lines, ``#``
   comments, optional single/double quotes and optional ``export`` prefixes.
   Anything that does not match is ignored rather than guessed.
4. Values are never logged. Callers receive only the names that were set.

Deployment environments must set real environment variables; the launchers
are a developer convenience, not a production secret store.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import MutableMapping

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EXPORT_PREFIX = re.compile(r"^export\s+", re.IGNORECASE)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _strip_inline_comment(value: str) -> str:
    if value[:1] in ("'", '"'):
        return value
    for marker in (" #", "\t#"):
        index = value.find(marker)
        if index != -1:
            return value[:index].rstrip()
    return value


def parse_dotenv(content: str) -> dict[str, str]:
    """Parse ``.env`` text into a dict without touching the environment."""
    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = _EXPORT_PREFIX.sub("", line)
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not _KEY.match(key):
            continue
        value = _strip_inline_comment(value.strip())
        values[key] = _unquote(value)
    return values


def load_dotenv(
    target: MutableMapping[str, str] | None = None,
    path: Path | None = None,
) -> dict[str, str]:
    """Merge ``.env`` into ``target`` (default: ``os.environ``).

    Existing keys are never overwritten. Returns only the keys that were
    added, so callers can report a count without exposing secret values.
    """
    env_file = Path(path) if path is not None else DEFAULT_ENV_FILE
    if not env_file.is_file():
        return {}
    try:
        content = env_file.read_text(encoding="utf-8")
    except OSError:
        return {}
    parsed = parse_dotenv(content)
    destination = os.environ if target is None else target
    applied: dict[str, str] = {}
    for key, value in parsed.items():
        if key not in destination:
            destination[key] = value
            applied[key] = value
    return applied


def load_for_launcher() -> int:
    """Load the root ``.env`` and return how many keys were applied."""
    return len(load_dotenv())
