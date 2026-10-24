#!/usr/bin/env bash
# MILAAN demo launcher: status the background demo services.
# See scripts/demo_services.py for details. Never prints secrets.
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/demo_common.sh"
exec "$PY" scripts/demo_services.py status "$@"
