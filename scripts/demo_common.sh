# Shared setup for start/status/stop_milaan_demo.sh. Sourced, not executed.
# Picks the project Python and makes Node visible for the WhatsApp Web bridge.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

PY="$ROOT/.venv/bin/python"
if [ -n "${MILAAN_PYTHON:-}" ] && [ -x "$MILAAN_PYTHON" ]; then
  PY="$MILAAN_PYTHON"
elif [ ! -x "$PY" ]; then
  PY="$(command -v python3 || true)"
fi
if [ -z "$PY" ]; then
  echo "Python 3 is required but was not found." >&2
  exit 1
fi
if ! "$PY" -c 'import flask, requests' >/dev/null 2>&1; then
  cat >&2 <<'MSG'
MILAAN dependencies are missing from the selected Python environment:

  python3 -m venv .venv
  .venv/bin/pip install -r requirements-agent.txt
MSG
  exit 1
fi

# Shells that lazy-load nvm may not have node on PATH yet.
if ! command -v node >/dev/null 2>&1 && [ -d "$HOME/.nvm/versions/node" ]; then
  NODE_BIN="$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -n 1)"
  [ -n "$NODE_BIN" ] && PATH="$NODE_BIN:$PATH" && export PATH
fi
