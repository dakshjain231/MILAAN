"""Safety tests for the background demo launcher (scripts/demo_services.py).

The launcher must only ever signal processes it started: stale PID files
are dropped without signalling anything, a recycled PID that now belongs to
another program is left alone, and stop terminates the recorded process and
its children. These tests use a temporary runtime directory and harmless
sleep processes; no MILAAN service or WhatsApp process is started.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

import demo_services  # noqa: E402

SLEEPER = "import time; time.sleep(60)"
PARENT_WITH_CHILD = (
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
    "time.sleep(60)"
)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); treat it as gone.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
    return bool(state.strip()) and not state.strip().startswith("Z")


class LauncherSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="milaan-launcher-"))
        self.saved = (demo_services.RUN_DIR, demo_services.LOG_DIR)
        demo_services.RUN_DIR = self.tmp / "run"
        demo_services.LOG_DIR = self.tmp / "logs"
        demo_services.RUN_DIR.mkdir()
        self.spawned: list[subprocess.Popen] = []

    def tearDown(self):
        for proc in self.spawned:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        demo_services.RUN_DIR, demo_services.LOG_DIR = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def unrelated_process(self) -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-c", SLEEPER])
        self.spawned.append(proc)
        time.sleep(0.2)
        return proc

    def write_state(self, service, pid, lstart):
        service.state_file.write_text(json.dumps({"name": service.name, "pid": pid, "lstart": lstart}))

    def test_dead_pid_file_is_dropped_without_signalling(self):
        self.write_state(demo_services.WEB, 999_999, "x")
        self.assertIsNone(demo_services.owned_pid(demo_services.WEB))
        self.assertTrue(demo_services.drop_stale_state(demo_services.WEB))
        self.assertFalse(demo_services.WEB.state_file.exists())

    def test_reused_pid_running_another_program_is_never_signalled(self):
        other = self.unrelated_process()
        lstart = demo_services.ps_field(other.pid, "lstart")
        # Right PID and start time, but the command is not a MILAAN service.
        self.write_state(demo_services.WEB, other.pid, lstart)
        result = demo_services.stop_service(demo_services.WEB, quiet=True)
        self.assertIn("nothing was signalled", result)
        self.assertTrue(alive(other.pid))
        self.assertFalse(demo_services.WEB.state_file.exists())

    def test_pid_with_a_different_start_time_is_not_owned(self):
        other = self.unrelated_process()
        self.write_state(demo_services.WHATSAPP_WEB, other.pid, "Thu Jan  1 00:00:00 1970")
        self.assertIsNone(demo_services.owned_pid(demo_services.WHATSAPP_WEB))
        demo_services.stop_service(demo_services.WHATSAPP_WEB, quiet=True)
        self.assertTrue(alive(other.pid))

    def test_stop_terminates_the_started_process_and_its_children(self):
        bystander = self.unrelated_process()
        # The marker for WEB is "scripts/run_primary.py"; pass it as an argument.
        pid = demo_services.spawn(
            demo_services.WEB,
            [sys.executable, "-c", PARENT_WITH_CHILD, "scripts/run_primary.py"],
            dict(os.environ),
            port=None,
        )
        time.sleep(0.6)
        self.assertEqual(demo_services.owned_pid(demo_services.WEB), pid)
        children = demo_services.descendants(pid)
        self.assertTrue(children)
        demo_services.stop_service(demo_services.WEB, quiet=True)
        time.sleep(0.3)
        self.assertFalse(alive(pid))
        for child in children:
            self.assertFalse(alive(child))
        self.assertTrue(alive(bystander.pid))
        self.assertFalse(demo_services.WEB.state_file.exists())

    def test_spawned_service_is_detached_from_the_terminal_session(self):
        pid = demo_services.spawn(
            demo_services.WEB,
            [sys.executable, "-c", SLEEPER, "scripts/run_primary.py"],
            dict(os.environ),
            port=None,
        )
        try:
            self.assertEqual(os.getsid(pid), pid)
            self.assertNotEqual(os.getsid(pid), os.getsid(0))
            log = (demo_services.LOG_DIR / "web.log").read_text()
            self.assertIn("starting MILAAN website", log)
        finally:
            demo_services.stop_service(demo_services.WEB, quiet=True)

    def test_log_rotation_keeps_one_previous_file(self):
        demo_services.LOG_DIR.mkdir(parents=True, exist_ok=True)
        log = demo_services.LOG_DIR / "web.log"
        log.write_bytes(b"x" * (demo_services.LOG_ROTATE_BYTES + 1))
        demo_services.rotate_log(log)
        self.assertFalse(log.exists())
        self.assertTrue((demo_services.LOG_DIR / "web.log.1").exists())

    def test_database_paths_resolve_to_the_real_or_demo_workspace(self):
        self.assertEqual(demo_services.database_for({}), str((ROOT / "milaan.db").resolve()))
        self.assertEqual(demo_services.database_for({"DATABASE_PATH": "milaan.db"}),
                         str((ROOT / "milaan.db").resolve()))
        demo = demo_services.database_for({"DATABASE_PATH": str(demo_services.DEMO_DATABASE)})
        self.assertEqual(Path(demo).parent, ROOT / "demo")
        self.assertNotEqual(demo, demo_services.database_for({}))

    def test_closed_ports_are_not_reported_healthy(self):
        self.assertFalse(demo_services.web_healthy(1))
        self.assertFalse(demo_services.port_in_use(1))


if __name__ == "__main__":
    unittest.main()
