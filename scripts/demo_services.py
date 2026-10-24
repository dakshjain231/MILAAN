#!/usr/bin/env python3
"""Background service manager for a clean local MILAAN demo.

Used by ``start_milaan_demo.sh``, ``status_milaan_demo.sh`` and
``stop_milaan_demo.sh`` in the repository root. It reuses the existing entry
points (``scripts/run_primary.py``, ``scripts/run_webjs.py``,
``scripts/run_webhook.py``, ``python -m agent.server``) and only adds process
management:

- every service runs detached in its own session (``setsid``), stdin from
  /dev/null and output appended to ``logs/demo/<service>.log``, so it keeps
  running after the starting Terminal window is closed;
- each started process is recorded in ``.run/demo/<service>.json`` with its
  PID and kernel start time, so ``stop`` signals only processes this launcher
  started, and never a recycled PID that now belongs to something else;
- a service is started only when the configuration says the architecture
  needs it. Nothing here ever sends a WhatsApp message.

Configuration comes from the process environment plus the repository-root
``.env`` (via ``scripts/env_loader.py``; real environment variables win).
Only variable *names* are ever printed, never their values.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(ROOT))

import env_loader  # noqa: E402

RUN_DIR = ROOT / ".run" / "demo"
LOG_DIR = ROOT / "logs" / "demo"
LOCK_FILE = RUN_DIR / "launcher.lock"
WA_SESSION_DIR = ROOT / "whatsapp_service" / ".wwebjs_auth"
LOG_ROTATE_BYTES = 10 * 1024 * 1024
TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Service:
    name: str
    label: str
    marker: str  # substring that must appear in the recorded process command

    @property
    def state_file(self) -> Path:
        return RUN_DIR / f"{self.name}.json"

    @property
    def log_file(self) -> Path:
        return LOG_DIR / f"{self.name}.log"


WEB = Service("web", "MILAAN website", "scripts/run_primary.py")
WHATSAPP_WEB = Service("whatsapp-web", "WhatsApp Web bridge (whatsapp-web.js)", "src/index.js")
WEBHOOK = Service("webhook", "Cloud API webhook receiver", "scripts/run_webhook.py")
AGENT = Service("agent", "Inbound agent server", "agent.server")
SERVICES = (WEB, WHATSAPP_WEB, WEBHOOK, AGENT)


# --- small helpers ---------------------------------------------------------

def say(message: str = "") -> None:
    print(message, flush=True)


def is_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in TRUE_VALUES


def load_env() -> dict[str, str]:
    """Process environment merged with .env, without mutating os.environ."""
    env = dict(os.environ)
    env_loader.load_dotenv(target=env)
    return env


def http_get(url: str, timeout: float = 1.5) -> tuple[int | None, dict]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            body = response.read(65536)
            code = response.status
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return None, {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        payload = {}
    return code, payload if isinstance(payload, dict) else {}


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def url_host(bind: str) -> str:
    return f"[{bind}]" if ":" in bind else bind


def ps_field(pid: int, field: str) -> str | None:
    try:
        out = subprocess.run(
            ["ps", "-o", f"{field}=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return value or None


def pid_alive(pid: int) -> bool:
    """True for a running process; an exited-but-unreaped zombie is not alive."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    state = ps_field(pid, "stat") or ""
    return not state.startswith("Z")


def descendants(pid: int) -> list[int]:
    """All live descendants of ``pid`` (children, grandchildren, ...)."""
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found: list[int] = []
    stack = list(children.get(pid, []))
    while stack:
        child = stack.pop()
        found.append(child)
        stack.extend(children.get(child, []))
    return found


def rotate_log(path: Path) -> None:
    try:
        if path.stat().st_size > LOG_ROTATE_BYTES:
            path.replace(path.with_suffix(path.suffix + ".1"))
    except FileNotFoundError:
        pass


def tail(path: Path, lines: int = 15) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def short(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


# --- state files -----------------------------------------------------------

def read_state(service: Service) -> dict | None:
    try:
        data = json.loads(service.state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("pid"), int) else None


def write_state(service: Service, data: dict) -> None:
    tmp = service.state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(service.state_file)


def clear_state(service: Service) -> None:
    service.state_file.unlink(missing_ok=True)


def owned_pid(service: Service) -> int | None:
    """PID of the live process this launcher recorded, else None.

    A PID only counts when it is alive, has the same kernel start time that
    was recorded at launch, and still runs the expected command. That rules
    out a PID that the OS has since reused for an unrelated program.
    """
    state = read_state(service)
    if not state:
        return None
    pid = state["pid"]
    if not pid_alive(pid):
        return None
    if ps_field(pid, "lstart") != state.get("lstart"):
        return None
    command = ps_field(pid, "command") or ""
    if service.marker not in command:
        return None
    return pid


def drop_stale_state(service: Service) -> bool:
    if service.state_file.exists() and owned_pid(service) is None:
        clear_state(service)
        return True
    return False


# --- start -----------------------------------------------------------------

def spawn(service: Service, argv: list[str], env: dict[str, str], port: int | None) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rotate_log(service.log_file)
    with open(service.log_file, "ab", buffering=0) as log:
        log.write(
            f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} starting {service.label} =====\n".encode()
        )
        process = subprocess.Popen(
            argv,
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own session: no SIGHUP when Terminal closes
            close_fds=True,
        )
    # ps needs a moment to report the exec'd command line.
    lstart = None
    for _ in range(20):
        lstart = ps_field(process.pid, "lstart")
        if lstart:
            break
        time.sleep(0.05)
    write_state(
        service,
        {
            "name": service.name,
            "pid": process.pid,
            "lstart": lstart,
            "port": port,
            "log": short(service.log_file),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
    return process.pid


def wait_healthy(pid: int, check, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        if not pid_alive(pid):
            return False
        time.sleep(0.4)
    return check()


def clear_stale_browser_lock() -> None:
    """Chromium leaves SingletonLock behind if a previous bridge died hard."""
    session = WA_SESSION_DIR / "session"
    lock = session / "SingletonLock"
    if not lock.is_symlink():
        return
    target = os.readlink(lock)
    pid_text = target.rsplit("-", 1)[-1]
    if not pid_text.isdigit():
        return
    if pid_alive(int(pid_text)):
        return
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "DevToolsActivePort"):
        (session / name).unlink(missing_ok=True)


def web_healthy(port: int) -> bool:
    code, payload = http_get(f"http://127.0.0.1:{port}/health")
    return code == 200 and payload.get("app") == "milaan-primary" and payload.get("ok") is True


def wa_settings(env: dict[str, str]) -> tuple[int, str]:
    raw = (env.get("MILAAN_WA_SERVICE_PORT") or "8801").strip()
    port = int(raw) if raw.isdigit() else 8801
    bind = (env.get("MILAAN_WA_SERVICE_BIND") or "127.0.0.1").strip()
    return port, bind


def wa_healthy(port: int, bind: str) -> bool:
    code, payload = http_get(f"http://{url_host(bind)}:{port}/health")
    return code == 200 and payload.get("service") == "milaan-whatsapp-web"


def wa_node_problems(env: dict[str, str]) -> list[str]:
    """What the Node bridge itself needs before it will start."""
    problems = []
    token = (env.get("MILAAN_WA_SERVICE_TOKEN") or "").strip()
    if len(token) < 16:
        problems.append("MILAAN_WA_SERVICE_TOKEN is missing or shorter than 16 characters")
    bind = (env.get("MILAAN_WA_SERVICE_BIND") or "127.0.0.1").strip().lower()
    if bind not in {"127.0.0.1", "localhost", "::1"}:
        problems.append("MILAAN_WA_SERVICE_BIND must be a loopback address")
    if shutil.which("node", path=env.get("PATH")) is None:
        problems.append("Node.js is not on PATH")
    if not (ROOT / "whatsapp_service" / "node_modules").is_dir():
        problems.append("whatsapp_service/node_modules is missing (run: cd whatsapp_service && npm ci)")
    return problems


class StartRun:
    def __init__(self, python: str, env: dict[str, str]):
        self.python = python
        self.env = env
        self.started: list[Service] = []
        self.failed = False

    def rollback(self) -> None:
        for service in reversed(self.started):
            stop_service(service, quiet=True)

    def start_whatsapp_web(self, show_browser: bool) -> None:
        if not is_true(self.env.get("MILAAN_WA_ENABLED")):
            say(f"  - {WHATSAPP_WEB.label}: not started (MILAAN_WA_ENABLED is not true)")
            return
        port, bind = wa_settings(self.env)
        pid = owned_pid(WHATSAPP_WEB)
        if pid:
            say(f"  ✓ {WHATSAPP_WEB.label}: already running (PID {pid}, port {port})")
            return
        problems = wa_node_problems(self.env)
        if problems:
            say(f"  ! {WHATSAPP_WEB.label}: NOT started. Missing: " + "; ".join(problems))
            return
        if port_in_use(port, bind if bind != "localhost" else "127.0.0.1"):
            if wa_healthy(port, bind):
                say(f"  ✓ {WHATSAPP_WEB.label}: already running on port {port} "
                    "(started outside this launcher; it will not be stopped by it)")
            else:
                say(f"  ! {WHATSAPP_WEB.label}: NOT started. Port {port} is used by another program.")
            return
        clear_stale_browser_lock()
        child_env = dict(self.env)
        if not show_browser:
            # The QR code is shown inside MILAAN, so the bridge's Chromium
            # window is not needed on screen during a presentation.
            child_env["MILAAN_WA_HEADLESS"] = "true"
        pid = spawn(WHATSAPP_WEB, [self.python, "scripts/run_webjs.py"], child_env, port)
        self.started.append(WHATSAPP_WEB)
        if wait_healthy(pid, lambda: wa_healthy(port, bind), 25):
            say(f"  ✓ {WHATSAPP_WEB.label}: started (PID {pid}, port {port})")
        else:
            say(f"  ! {WHATSAPP_WEB.label}: did not become healthy; see {short(WHATSAPP_WEB.log_file)}")
            for line in tail(WHATSAPP_WEB.log_file, 6):
                say(f"      {line}")
            if not pid_alive(pid):
                clear_state(WHATSAPP_WEB)
                self.started.remove(WHATSAPP_WEB)

    def start_webhook(self) -> None:
        import delivery  # noqa: PLC0415  (reads config only)

        config = delivery.load_whatsapp_config(self.env)
        if not config.webhook_enabled:
            say(f"  - {WEBHOOK.label}: not started (WHATSAPP_WEBHOOK_ENABLED is not true)")
            return
        if not config.webhook_ready:
            say(f"  ! {WEBHOOK.label}: NOT started. " + "; ".join(config.webhook_problems()))
            return
        pid = owned_pid(WEBHOOK)
        if pid:
            say(f"  ✓ {WEBHOOK.label}: already running (PID {pid}, port {config.webhook_port})")
            return
        if port_in_use(int(config.webhook_port)):
            say(f"  ! {WEBHOOK.label}: NOT started. Port {config.webhook_port} is already in use.")
            return
        pid = spawn(WEBHOOK, [self.python, "scripts/run_webhook.py"], self.env, int(config.webhook_port))
        self.started.append(WEBHOOK)
        if wait_healthy(pid, lambda: port_in_use(int(config.webhook_port)), 10):
            say(f"  ✓ {WEBHOOK.label}: started (PID {pid}, {config.webhook_bind}:{config.webhook_port})")
        else:
            say(f"  ! {WEBHOOK.label}: exited during startup; see {short(WEBHOOK.log_file)}")
            clear_state(WEBHOOK)
            self.started.remove(WEBHOOK)

    def start_agent(self) -> None:
        port_text = (self.env.get("AGENT_PORT") or "5050").strip()
        port = int(port_text) if port_text.isdigit() else 5050
        pid = owned_pid(AGENT)
        if pid:
            say(f"  ✓ {AGENT.label}: already running (PID {pid}, port {port})")
            return
        if port_in_use(port):
            say(f"  ! {AGENT.label}: NOT started. Port {port} is already in use.")
            return
        pid = spawn(AGENT, [self.python, "-m", "agent.server"], self.env, port)
        self.started.append(AGENT)
        ok = wait_healthy(pid, lambda: http_get(f"http://127.0.0.1:{port}/health")[0] == 200, 15)
        if ok:
            say(f"  ✓ {AGENT.label}: started (PID {pid}, port {port})")
        else:
            say(f"  ! {AGENT.label}: did not become healthy; see {short(AGENT.log_file)}")

    def start_web(self, port: int) -> bool:
        pid = owned_pid(WEB)
        if pid:
            if web_healthy(port):
                say(f"  ✓ {WEB.label}: already running (PID {pid}, port {port})")
                return True
            say(f"  ! {WEB.label}: process {pid} is running but /health is not OK; "
                f"see {short(WEB.log_file)} (try ./stop_milaan_demo.sh then start again)")
            return False
        if port_in_use(port):
            if web_healthy(port):
                say(f"  ✓ {WEB.label}: already running on port {port} "
                    "(started outside this launcher, e.g. ./run_milaan.sh; it will not be stopped by it)")
                return True
            say(f"  ✗ {WEB.label}: port {port} is used by another program. Not starting a duplicate.")
            return False
        env = dict(self.env)
        env["PYTHONUNBUFFERED"] = "1"
        pid = spawn(WEB, [self.python, "scripts/run_primary.py", "--port", str(port)], env, port)
        self.started.append(WEB)
        if wait_healthy(pid, lambda: web_healthy(port), 60):
            say(f"  ✓ {WEB.label}: started (PID {pid}, port {port})")
            return True
        say(f"  ✗ {WEB.label}: did not pass its health check. Last log lines "
            f"({short(WEB.log_file)}):")
        for line in tail(WEB.log_file, 15):
            say(f"      {line}")
        return False


def cmd_start(args: argparse.Namespace) -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            say("Another start/stop is in progress. Try again in a few seconds.")
            return 1

        for service in SERVICES:
            if drop_stale_state(service):
                say(f"  · removed stale PID file for {service.label}")

        if owned_pid(WEB) is None and port_in_use(args.port) and not web_healthy(args.port):
            say(f"✗ Port {args.port} is used by another program, so MILAAN cannot start there.")
            say(f"  Find it with: lsof -nP -iTCP:{args.port} -sTCP:LISTEN")
            say("  Nothing was started.")
            return 1

        env = load_env()
        python = sys.executable
        say("Starting MILAAN demo services in the background...")
        run = StartRun(python, env)
        run.start_whatsapp_web(args.show_browser)
        run.start_webhook()
        if args.with_agent:
            run.start_agent()
        if not run.start_web(args.port):
            if run.started:
                say("Rolling back services started by this attempt...")
                run.rollback()
            say("")
            say("MILAAN did not start. Fix the error above and run ./start_milaan_demo.sh again.")
            return 1

    say("")
    say("MILAAN is ready.")
    say(f"  Website:  http://127.0.0.1:{args.port}/")
    say("  Status:   ./status_milaan_demo.sh")
    say("  Stop:     ./stop_milaan_demo.sh")
    say(f"  Logs:     {short(LOG_DIR)}/")
    say("")
    say("Services keep running after this Terminal window is closed.")
    say("No WhatsApp message is sent at startup; follow-ups still need approval and explicit confirmation.")
    return 0


# --- status ----------------------------------------------------------------

def describe_managed(service: Service) -> tuple[int | None, str]:
    pid = owned_pid(service)
    if pid:
        return pid, f"running (PID {pid})"
    if service.state_file.exists():
        return None, "not running (stale PID file; the next start/stop cleans it up)"
    return None, "not running"


def cmd_status(args: argparse.Namespace) -> int:
    env = load_env()
    healthy = True
    say("MILAAN demo status")
    say("")

    # Website
    pid, text = describe_managed(WEB)
    state = read_state(WEB) or {}
    port = int(state.get("port") or args.port)
    code, payload = http_get(f"http://127.0.0.1:{port}/health")
    is_milaan = code == 200 and payload.get("app") == "milaan-primary"
    if pid and is_milaan and payload.get("ok") is True:
        health = "healthy (HTTP 200, database ready)"
    elif is_milaan and not pid:
        health = "answering, but started outside this launcher"
        text = "running (not managed by the demo launcher)"
    elif is_milaan:
        health = "answering, database NOT ready"
        healthy = False
    elif code is not None:
        health = f"port {port} answers HTTP {code}, but not as MILAAN"
        healthy = False
    else:
        health = "not reachable"
        healthy = False
    say(f"{WEB.label}")
    say(f"  process: {text}")
    say(f"  url:     http://127.0.0.1:{port}/   → {health}")
    say(f"  log:     {short(WEB.log_file)}")
    say("")

    # WhatsApp Web bridge
    say(f"{WHATSAPP_WEB.label}")
    if not is_true(env.get("MILAAN_WA_ENABLED")):
        say("  disabled (MILAAN_WA_ENABLED is not true); no separate process needed")
    else:
        import webjs  # noqa: PLC0415

        wa_port, wa_bind = wa_settings(env)
        pid, text = describe_managed(WHATSAPP_WEB)
        alive = wa_healthy(wa_port, wa_bind)
        if alive and not pid:
            text = "running (not managed by the demo launcher)"
        say(f"  process: {text}")
        say(f"  health:  http://{url_host(wa_bind)}:{wa_port}/health → "
            + ("healthy" if alive else "not reachable"))
        config = webjs.load_webjs_config(env)
        problems = config.problems()
        if problems:
            say("  config:  incomplete: " + "; ".join(problems))
        if alive:
            st = webjs.service_status(config)
            link = str(st.get("status") or "unknown")
            if st.get("ready"):
                say(f"  link:    ready, linked as {st.get('phone_masked') or 'a masked number'}")
            elif st.get("qr_available") or link == "qr":
                say("  link:    waiting for QR scan (open MILAAN Settings and scan with the test phone)")
            else:
                say(f"  link:    {link}")
            say(f"  test recipient: {config.safe_summary().get('test_recipient_masked') or 'not set'}")
        else:
            healthy = False
        say(f"  log:     {short(WHATSAPP_WEB.log_file)}")
    say("")

    # Cloud API + webhook + agent (configuration-driven)
    import delivery  # noqa: PLC0415

    cloud = delivery.load_whatsapp_config(env)
    say("WhatsApp Cloud API (outbound supplier follow-ups, sent by the website itself)")
    if not cloud.enabled:
        say("  live delivery: OFF (WHATSAPP_ENABLED is not true); only mock delivery is available")
    elif cloud.ready:
        say("  live delivery: configured · number " + str(cloud.safe_summary()["phone_number_id_masked"]))
    else:
        say("  live delivery: NOT ready: " + "; ".join(cloud.problems()))
    say("")

    say(f"{WEBHOOK.label} (delivery statuses + inbound replies)")
    pid, text = describe_managed(WEBHOOK)
    if not cloud.webhook_enabled:
        say("  disabled (WHATSAPP_WEBHOOK_ENABLED is not true)")
    elif not cloud.webhook_ready:
        say("  NOT ready: " + "; ".join(cloud.webhook_problems()))
    else:
        listening = port_in_use(int(cloud.webhook_port))
        say(f"  process: {text}")
        say(f"  port:    {cloud.webhook_bind}:{cloud.webhook_port} → "
            + ("listening" if listening else "not listening"))
        say("  note:    Meta needs a public HTTPS URL; localhost alone cannot receive webhooks")
        if not listening:
            healthy = False
    if pid or cloud.webhook_enabled:
        say(f"  log:     {short(WEBHOOK.log_file)}")
    say("")

    pid, text = describe_managed(AGENT)
    port_text = (env.get("AGENT_PORT") or "5050").strip()
    agent_port = int(port_text) if port_text.isdigit() else 5050
    say(f"{AGENT.label} (optional, ./start_milaan_demo.sh --with-agent)")
    say(f"  process: {text}")
    if pid:
        code, _ = http_get(f"http://127.0.0.1:{agent_port}/health")
        say(f"  health:  http://127.0.0.1:{agent_port}/health → "
            + ("healthy" if code == 200 else "not reachable"))
        say(f"  log:     {short(AGENT.log_file)}")
    say("")
    say("Overall: " + ("OK" if healthy else "ATTENTION NEEDED"))
    return 0 if healthy else 1


# --- stop ------------------------------------------------------------------

def stop_service(service: Service, quiet: bool = False) -> str:
    state = read_state(service)
    if state is None:
        clear_state(service)
        return "not running"
    pid = owned_pid(service)
    if pid is None:
        clear_state(service)
        return "not running (stale PID file removed; nothing was signalled)"

    # Snapshot the tree first: Puppeteer starts Chromium in its own process
    # group, so it is a descendant but not a member of our session's group.
    family = [(child, ps_field(child, "lstart")) for child in descendants(pid)]
    try:
        os.killpg(pid, signal.SIGTERM)  # own session ⇒ group id == pid
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def survivors() -> list[int]:
        alive = [pid] if pid_alive(pid) else []
        for child, lstart in family:
            if lstart and pid_alive(child) and ps_field(child, "lstart") == lstart:
                alive.append(child)
        return alive

    deadline = time.monotonic() + 12
    while survivors() and time.monotonic() < deadline:
        time.sleep(0.25)
    forced = survivors()
    for victim in forced:
        try:
            os.kill(victim, signal.SIGKILL)
        except ProcessLookupError:
            pass
    clear_state(service)
    result = f"stopped (PID {pid})" + (" — had to force-kill" if forced else "")
    if not quiet:
        say(f"  ✓ {service.label}: {result}")
    return result


def cmd_stop(_: argparse.Namespace) -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            say("Another start/stop is in progress. Try again in a few seconds.")
            return 1
        say("Stopping MILAAN demo services (only processes started by ./start_milaan_demo.sh)...")
        anything = False
        # Website first so it stops calling the bridge, then the rest.
        for service in (WEB, AGENT, WEBHOOK, WHATSAPP_WEB):
            if not service.state_file.exists():
                continue
            anything = True
            result = stop_service(service, quiet=True)
            say(f"  · {service.label}: {result}")
        if not anything:
            say("  nothing to stop (no demo services are recorded as running)")
    if port_in_use(8501) and web_healthy(8501):
        say("Note: a MILAAN instance not started by this launcher is still answering on port 8501.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="MILAAN background demo services")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--port", type=int, default=8501)
    start.add_argument("--with-agent", action="store_true",
                       help="also start the optional inbound agent server")
    start.add_argument("--show-browser", action="store_true",
                       help="keep MILAAN_WA_HEADLESS from .env instead of forcing a hidden browser")
    status = sub.add_parser("status")
    status.add_argument("--port", type=int, default=8501)
    sub.add_parser("stop")
    args = parser.parse_args()
    os.chdir(ROOT)
    if getattr(args, "port", 8501) < 1 or getattr(args, "port", 8501) > 65535:
        parser.error("port must be 1-65535")
    return {"start": cmd_start, "status": cmd_status, "stop": cmd_stop}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
