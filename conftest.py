"""Session-wide safety net for the test suite.

Individual tests already point DATABASE_PATH / AGENT_DB_PATH at temporary
files. This fixture makes the default for the whole session a throwaway
location too, so a test that forgets cannot write to the real milaan.db or
agent database, and it removes deployment settings that would change app
behaviour (authentication, allowed hosts) inherited from a developer shell.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest

_ISOLATED = ("DATABASE_PATH", "AGENT_DB_PATH", "AGENT_MEDIA_DIR")
_CLEARED = ("APP_ENV", "MILAAN_AUTH_REQUIRED", "MILAAN_BASIC_AUTH_USER",
            "MILAAN_BASIC_AUTH_PASSWORD_HASH", "MILAAN_ALLOWED_HOSTS")


@pytest.fixture(autouse=True, scope="session")
def _isolated_storage():
    saved = {key: os.environ.get(key) for key in _ISOLATED + _CLEARED}
    root = tempfile.mkdtemp(prefix="milaan-test-session-")
    os.environ["DATABASE_PATH"] = os.path.join(root, "milaan.db")
    os.environ["AGENT_DB_PATH"] = os.path.join(root, "agent.db")
    os.environ["AGENT_MEDIA_DIR"] = os.path.join(root, "media")
    for key in _CLEARED:
        os.environ.pop(key, None)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    shutil.rmtree(root, ignore_errors=True)
