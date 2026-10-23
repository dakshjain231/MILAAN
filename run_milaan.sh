#!/usr/bin/env bash
# MILAAN — one-command local launcher.
#
#   ./run_milaan.sh              start the primary MILAAN web UI (and WhatsApp Web if enabled)
#   ./run_milaan.sh --stop       stop the processes this launcher started
#   ./run_milaan.sh --no-whatsapp
#   ./run_milaan.sh --with-agent start the optional inbound agent server (:5050)
#   ./run_milaan.sh --demo       use the isolated synthetic demo database
#   ./run_milaan.sh --port 8502
#
# The launcher loads the repository-root .env through scripts/env_loader.py.
# It never prints .env values or service tokens. Logs go to logs/.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1

RUN_DIR="$ROOT/.run"
LOG_DIR="$ROOT/logs"
PID_FILE="$RUN_DIR/milaan.pids"
PORT=8501
START_WA=1
START_AGENT=0
DEMO=0
ACTION="start"
OWNER_PID=$$

PY="$ROOT/.venv/bin/python"
if [ -n "${MILAAN_PYTHON:-}" ] && [ -x "$MILAAN_PYTHON" ]; then
  PY="$MILAAN_PYTHON"   # CI / advanced: point at an existing prepared venv
elif [ ! -x "$PY" ]; then
  PY="$(command -v python3 || true)"
fi

usage() {
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --stop) ACTION="stop" ;;
    --no-whatsapp) START_WA=0 ;;
    --with-agent) START_AGENT=1 ;;
    --demo) DEMO=1 ;;
    --port)
      shift
      PORT="${1:-}"
      if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
        echo "Invalid port: $PORT" >&2
        exit 2
      fi
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if [ -z "$PY" ] || ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3 is required but was not found." >&2
  exit 1
fi

mkdir -p "$RUN_DIR" "$LOG_DIR"

log() { printf '%s\n' "$*"; }

pid_alive() {
  [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null
}

port_free() {
  "$PY" - "$1" <<'PY'
import socket
import sys

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(0.4)
try:
    sock.bind(("127.0.0.1", int(sys.argv[1])))
    sys.exit(0)
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
}

http_ok() {
  curl -fsS --max-time 1.2 "$1" >/dev/null 2>&1
}

clear_stale_browser_lock() {
  # Chromium leaves SingletonLock behind if the previous bridge died hard.
  # A stale lock makes the next launch silently sit at "disconnected".
  local session_dir="$ROOT/whatsapp_service/.wwebjs_auth/session"
  local lock="$session_dir/SingletonLock"
  [ -L "$lock" ] || return 0
  local target pid
  target="$(readlink "$lock" 2>/dev/null || true)"
  pid="${target##*-}"
  case "$pid" in
    ''|*[!0-9]*) return 0 ;;
  esac
  if kill -0 "$pid" 2>/dev/null; then
    log "WhatsApp browser session is held by live process $pid; leaving its lock alone."
    return 0
  fi
  log "Removing stale WhatsApp browser lock left by exited process $pid."
  rm -f "$lock" "$session_dir/SingletonCookie" "$session_dir/SingletonSocket" "$session_dir/DevToolsActivePort"
}

record_pid() {
  # owner:name:pid:port — owner lets one launcher stop only its own services.
  printf '%s:%s:%s:%s\n' "$OWNER_PID" "$1" "$2" "$3" >> "$PID_FILE"
}

stop_services() {
  if [ ! -f "$PID_FILE" ]; then
    return 0
  fi
  local filter="${1:-}"
  local entries=()
  local keep=()
  local line owner name pid port a b c d
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    IFS=: read -r a b c d <<< "$line"
    if [ -n "$d" ]; then
      owner="$a"; name="$b"; pid="$c"; port="$d"
    else
      owner=""; name="$a"; pid="$b"; port="$c"
    fi
    if [ -n "$filter" ] && [ "$owner" != "$filter" ]; then
      keep+=("$line")
    else
      entries+=("$line")
    fi
  done < "$PID_FILE"
  # Replace the PID file before signalling processes; the supervised
  # launcher may call this again from its exit trap.
  if [ "${#keep[@]}" -gt 0 ]; then
    local kept
    for kept in "${keep[@]}"; do printf '%s\n' "$kept"; done > "$PID_FILE"
  else
    rm -f "$PID_FILE"
  fi
  if [ "${#entries[@]}" -eq 0 ]; then
    return 0
  fi
  if [ -n "$filter" ]; then
    log "Stopping services started by this launcher..."
  else
    log "Stopping all MILAAN services started by this launcher..."
  fi
  for line in "${entries[@]}"; do
    IFS=: read -r a b c d <<< "$line"
    if [ -n "$d" ]; then pid="$c"; else pid="$b"; fi
    if pid_alive "${pid:-}"; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  local waited=0
  while [ "$waited" -lt 40 ]; do
    local alive=0
    for line in "${entries[@]}"; do
      IFS=: read -r a b c d <<< "$line"
      if [ -n "$d" ]; then pid="$c"; else pid="$b"; fi
      if pid_alive "${pid:-}"; then alive=1; fi
    done
    [ "$alive" -eq 0 ] && break
    sleep 0.25
    waited=$((waited + 1))
  done
  for line in "${entries[@]}"; do
    IFS=: read -r a b c d <<< "$line"
    if [ -n "$d" ]; then pid="$c"; else pid="$b"; fi
    if pid_alive "${pid:-}"; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
  log "Stopped."
}

if [ "$ACTION" = "stop" ]; then
  if [ ! -f "$PID_FILE" ]; then
    log "No launcher PID file found; nothing to stop."
    exit 0
  fi
  stop_services
  exit 0
fi

if ! "$PY" -c 'import flask' >/dev/null 2>&1; then
  cat >&2 <<'EOF'
MILAAN dependencies are missing from the selected Python environment.

  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements-agent.txt

Then run ./run_milaan.sh again.
EOF
  exit 1
fi

# A previous launcher run already owns this UI port (any owner).
if [ -f "$PID_FILE" ]; then
  ui_line="$(awk -F: -v p="$PORT" '($1=="ui" && $3==p) || ($2=="ui" && $4==p) {print; exit}' "$PID_FILE")"
  if [ -n "$ui_line" ]; then
    fields="$(printf '%s' "$ui_line" | awk -F: '{print NF}')"
    if [ "$fields" -ge 4 ]; then
      ui_pid="$(printf '%s' "$ui_line" | cut -d: -f3)"
    else
      ui_pid="$(printf '%s' "$ui_line" | cut -d: -f2)"
    fi
    if pid_alive "${ui_pid:-}"; then
      log "MILAAN is already running on this port (launcher PID $ui_pid)."
      log "Open: http://127.0.0.1:${PORT}"
      log "Stop it with: ./run_milaan.sh --stop"
      exit 0
    fi
  fi
fi

if ! port_free "$PORT"; then
  log "Port $PORT is already in use. MILAAN will not start a duplicate." >&2
  if http_ok "http://127.0.0.1:${PORT}/health"; then
    log "An existing MILAAN service answered on that port. Open http://127.0.0.1:${PORT}" >&2
  else
    log "Stop the process using it, or choose another port with --port." >&2
  fi
  exit 1
fi

if [ "$DEMO" -eq 1 ]; then
  DEMO_DB="$ROOT/demo/milaan_demo.db"
  export DATABASE_PATH="$DEMO_DB"
  export AGENT_DB_PATH="$ROOT/demo/milaan_agent_demo.db"
  export MILAAN_WA_ENABLED=false
  export WHATSAPP_ENABLED=false
  export WHATSAPP_WEBHOOK_ENABLED=false
  START_WA=0
  START_AGENT=0
  if [ ! -f "$DEMO_DB" ]; then
    log "Seeding the isolated synthetic demo database..."
    if ! "$PY" demo/setup_demo.py; then
      log "ERROR: could not seed demo/milaan_demo.db." >&2
      exit 1
    fi
  fi
  log "DEMO MODE: synthetic data only — demo/milaan_demo.db; WhatsApp, Cloud API, webhook and agent disabled."
  log "Reset later with: .venv/bin/python demo/setup_demo.py"
fi

started=0
cleanup() {
  status=$?
  trap - INT TERM EXIT
  if [ "$started" -eq 1 ]; then
    log ""
    log "MILAAN is shutting down."
    stop_services "$OWNER_PID"
  fi
  exit "$status"
}
trap cleanup INT TERM EXIT

# ---------------------------------------------------------------------------
# Optional WhatsApp Web bridge (whatsapp-web.js), localhost only.
# ---------------------------------------------------------------------------
if [ "$START_WA" -eq 1 ]; then
  read -r WA_ENABLED WA_PORT WA_BIND < <("$PY" - <<'PY'
import os
import sys

sys.path.insert(0, "scripts")
import env_loader

env_loader.load_dotenv()
print(
    (os.environ.get("MILAAN_WA_ENABLED") or "false").strip().lower(),
    (os.environ.get("MILAAN_WA_SERVICE_PORT") or "8801").strip(),
    (os.environ.get("MILAAN_WA_SERVICE_BIND") or "127.0.0.1").strip(),
)
PY
)
  case "$(printf '%s' "$WA_ENABLED" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on)
      if ! command -v node >/dev/null 2>&1; then
        log "WARNING: MILAAN_WA_ENABLED=true but Node.js is not installed; the WhatsApp Web bridge was not started."
      elif [ ! -d "$ROOT/whatsapp_service/node_modules" ]; then
        log "WARNING: WhatsApp Web dependencies are missing. Run: cd whatsapp_service && npm ci"
      else
        host="$WA_BIND"
        [ "$host" = "::1" ] && host="[::1]"
        health="http://${host}:${WA_PORT}/health"
        if http_ok "$health"; then
          log "WhatsApp Web bridge already running at ${health}."
        else
          if ! port_free "$WA_PORT"; then
            log "WARNING: WhatsApp Web port $WA_PORT is in use by another process."
          else
            clear_stale_browser_lock
            log "Starting WhatsApp Web bridge on 127.0.0.1:${WA_PORT}..."
            "$PY" scripts/run_webjs.py >> "$LOG_DIR/whatsapp-web.log" 2>&1 &
            wa_pid=$!
            record_pid "whatsapp" "$wa_pid" "$WA_PORT"
            started=1
            ok=0
            for _ in $(seq 1 40); do
              if http_ok "$health"; then ok=1; break; fi
              if ! pid_alive "$wa_pid"; then break; fi
              sleep 0.5
            done
            if [ "$ok" -eq 1 ]; then
              log "WhatsApp Web bridge healthy."
            else
              log "WARNING: WhatsApp Web bridge did not become healthy. See logs/whatsapp-web.log."
              log "The primary UI will still start; Settings shows the exact service state."
            fi
          fi
        fi
      fi
      ;;
    *)
      log "WhatsApp Web bridge disabled (MILAAN_WA_ENABLED is not true)."
      ;;
  esac
fi

# ---------------------------------------------------------------------------
# Verified Cloud API webhook, when explicitly enabled and fully configured.
# ---------------------------------------------------------------------------
read -r WH_ENABLED WH_READY WH_PORT WH_BIND < <("$PY" - <<'PY'
import sys

sys.path.insert(0, "scripts")
import env_loader

env_loader.load_dotenv()
import delivery

config = delivery.load_whatsapp_config()
print(
    "true" if config.webhook_enabled else "false",
    "true" if config.webhook_ready else "false",
    config.webhook_port,
    config.webhook_bind,
)
PY
)
if [ "$WH_ENABLED" = "true" ]; then
  if [ "$WH_READY" != "true" ]; then
    log "WARNING: WHATSAPP_WEBHOOK_ENABLED=true but its configuration is incomplete; the receiver was not started."
  elif port_free "$WH_PORT"; then
    log "Starting verified webhook receiver on ${WH_BIND}:${WH_PORT}..."
    "$PY" scripts/run_webhook.py >> "$LOG_DIR/webhook.log" 2>&1 &
    webhook_pid=$!
    record_pid "webhook" "$webhook_pid" "$WH_PORT"
    started=1
    sleep 1
    if ! pid_alive "$webhook_pid"; then
      log "WARNING: the webhook receiver exited. See logs/webhook.log."
    fi
  else
    log "Webhook port $WH_PORT is already in use; not starting a duplicate receiver."
  fi
fi

# ---------------------------------------------------------------------------
# Optional inbound agent server (:5050). Off unless explicitly requested.
# ---------------------------------------------------------------------------
if [ "$START_AGENT" -eq 1 ]; then
  if http_ok "http://127.0.0.1:5050/health"; then
    log "Agent webhook server already running on :5050."
  elif ! port_free 5050; then
    log "WARNING: port 5050 is in use; the agent webhook server was not started."
  else
    log "Starting optional agent webhook server on :5050..."
    "$PY" -m agent.server >> "$LOG_DIR/agent-server.log" 2>&1 &
    agent_pid=$!
    record_pid "agent" "$agent_pid" "5050"
    started=1
    for _ in $(seq 1 20); do
      http_ok "http://127.0.0.1:5050/health" && break
      sleep 0.5
    done
  fi
fi

# ---------------------------------------------------------------------------
# Primary UI. This is the only URL the user needs.
# ---------------------------------------------------------------------------
log "Starting MILAAN primary UI on 127.0.0.1:${PORT}..."
"$PY" scripts/run_primary.py --port "$PORT" \
  >> "$LOG_DIR/milaan-ui.log" 2>&1 &
ui_pid=$!
record_pid "ui" "$ui_pid" "$PORT"
started=1

ui_ok=0
for _ in $(seq 1 120); do
  if http_ok "http://127.0.0.1:${PORT}/health"; then
    ui_ok=1
    break
  fi
  if ! pid_alive "$ui_pid"; then
    break
  fi
  sleep 0.5
done

if [ "$ui_ok" -ne 1 ]; then
  log "ERROR: MILAAN did not become healthy on port ${PORT}." >&2
  log "See logs/milaan-ui.log for the actionable error." >&2
  exit 1
fi

cat <<EOF

MILAAN is ready.

  Open:  http://127.0.0.1:${PORT}
  Stop:  ./run_milaan.sh --stop   (or press Ctrl+C in this terminal)

  Logs:  logs/milaan-ui.log
         logs/whatsapp-web.log   (when the bridge runs)
         logs/agent-server.log   (when --with-agent is used)

  Live sending is never automatic. Supplier follow-ups require an approved
  draft and explicit confirmation; tests use mocked delivery only.
EOF

if [ "$DEMO" -eq 1 ]; then
  cat <<EOF

  DEMO DATABASE: ${DATABASE_PATH}
  RESET:         .venv/bin/python demo/setup_demo.py
  No WhatsApp, Cloud API, webhook or external send path is enabled in demo mode.
EOF
fi

# Supervise the primary process and stop only what this launcher started.
wait "$ui_pid"
