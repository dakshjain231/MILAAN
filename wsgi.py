"""Production WSGI entry point for MILAAN.

Run behind a TLS-terminating reverse proxy with a production WSGI server,
for example (see README "Production deployment"):

    gunicorn --workers 2 --bind 127.0.0.1:8000 wsgi:app

``create_app`` refuses to start when authentication is required
(APP_ENV=production) but MILAAN_BASIC_AUTH_USER /
MILAAN_BASIC_AUTH_PASSWORD_HASH are not configured. The local demo keeps
using scripts/run_primary.py (Flask's development server on 127.0.0.1).
"""

from __future__ import annotations

from milaan_web import create_app

app = create_app()
