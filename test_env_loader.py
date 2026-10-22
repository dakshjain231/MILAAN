"""Tests for the deterministic .env loader used by the local launchers.

No real credentials are read or written; every case uses an in-memory dict or
a temporary file.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "milaan_env_loader", ROOT / "scripts" / "env_loader.py"
)
assert _SPEC is not None and _SPEC.loader is not None
env_loader = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(env_loader)


class ParseDotenvTests(unittest.TestCase):
    def test_basic_pairs_comments_and_export(self):
        parsed = env_loader.parse_dotenv(
            "\n".join([
                "# comment",
                "",
                "APP_ENV=development",
                "export DATABASE_PATH=milaan.db",
                "  SPACED = value  ",
            ])
        )
        self.assertEqual(parsed["APP_ENV"], "development")
        self.assertEqual(parsed["DATABASE_PATH"], "milaan.db")
        self.assertEqual(parsed["SPACED"], "value")

    def test_quotes_and_inline_comments(self):
        parsed = env_loader.parse_dotenv(
            "\n".join([
                'DOUBLE="keep # inside"',
                "SINGLE='value'",
                "PLAIN=value # trailing comment",
                "HASH=abc#def",
            ])
        )
        self.assertEqual(parsed["DOUBLE"], "keep # inside")
        self.assertEqual(parsed["SINGLE"], "value")
        self.assertEqual(parsed["PLAIN"], "value")
        self.assertEqual(parsed["HASH"], "abc#def")

    def test_malformed_lines_are_ignored(self):
        parsed = env_loader.parse_dotenv(
            "\n".join([
                "NO_EQUALS_HERE",
                "1INVALID=value",
                "=missing-key",
                "GOOD=ok",
            ])
        )
        self.assertEqual(parsed, {"GOOD": "ok"})


class LoadDotenvTests(unittest.TestCase):
    def test_missing_file_is_a_noop(self):
        target = {"KEEP": "yes"}
        applied = env_loader.load_dotenv(
            target=target, path=Path("/nonexistent/milaan/.env")
        )
        self.assertEqual(applied, {})
        self.assertEqual(target, {"KEEP": "yes"})

    def test_existing_environment_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "APP_ENV=from-file\nNEW_KEY=file-only\n", encoding="utf-8"
            )
            target = {"APP_ENV": "from-shell"}
            applied = env_loader.load_dotenv(target=target, path=env_file)
        self.assertEqual(target["APP_ENV"], "from-shell")
        self.assertEqual(target["NEW_KEY"], "file-only")
        self.assertEqual(applied, {"NEW_KEY": "file-only"})

    def test_unreadable_file_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / ".env"
            directory.mkdir()
            applied = env_loader.load_dotenv(
                target={}, path=directory
            )
        self.assertEqual(applied, {})


if __name__ == "__main__":
    unittest.main()
