"""Session-wide safety net for the test suite.

Individual tests already point DATABASE_PATH / AGENT_DB_PATH at temporary
files. This fixture makes the default for the whole session a throwaway
location too, so a test that forgets cannot write to the real milaan.db or
agent database, and it removes deployment settings that would change app
behaviour (authentication, allowed hosts) inherited from a developer shell.

The WhatsApp settings are cleared for the same reason: with a developer's
live bridge configuration in the environment, a test that exercises the
delivery path could reach a real phone. Cleared here, every WhatsApp send
path is disabled unless the test passes an explicit config object.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest

_ISOLATED = ("DATABASE_PATH", "AGENT_DB_PATH", "AGENT_MEDIA_DIR")
_CLEARED = ("APP_ENV", "MILAAN_AUTH_REQUIRED", "MILAAN_BASIC_AUTH_USER",
            "MILAAN_BASIC_AUTH_PASSWORD_HASH", "MILAAN_ALLOWED_HOSTS",
            "MILAAN_WA_ENABLED", "MILAAN_WA_LIVE_FOLLOWUPS",
            "MILAAN_WA_SERVICE_URL", "MILAAN_WA_SERVICE_TOKEN",
            "MILAAN_WA_TEST_RECIPIENT", "MILAAN_WA_ALLOWED_RECIPIENTS",
            "WHATSAPP_ENABLED", "WHATSAPP_ACCESS_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID")


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
