# MILAAN merge report (archived)

Date: 2026-10-22

This document previously recorded an intermediate integration state in which a
Streamlit workspace and two standalone HTML dashboards coexisted. Those
duplicate user interfaces have been removed. They are **not** part of the
current application, and the architectural detail from that earlier report no
longer applies.

## Final architecture

- `milaan_web.py` is the only web application.
- `agent/web/primary.html` is the only frontend.
- `run_milaan.sh` starts that application; `--demo` uses the isolated synthetic
  database and never starts WhatsApp, the webhook, or the inbound agent.
- The optional `agent.server` webhook service and `whatsapp_service` bridge are
  background services. They do not serve a competing dashboard.

## Removed duplicate UIs

- Streamlit app: `app.py`, `pages/`, `.streamlit/`, `scripts/run_app.py`
- Standalone agent dashboard: `agent/web.py`, `agent/web/dashboard.html`
- Superseded dashboard module/tests: `agent_dashboard.py`, `test_dashboard.py`

The backend modules these UIs used remain: `db.py`, `delivery.py`,
`agent_views.py`, `report_workbook.py`, `security.py`, `agent/`, and the
WhatsApp bridge/webhook services.

## Verification

- Python: `438 passed, 122 subtests passed` (`pytest -q --ignore=whatsapp_service`).
- WhatsApp service: `18/18` tests passed; `npm run check` passed.
- Primary UI: all ten navigation pages load with the same theme; `/dashboard`,
  `/api/dashboard`, and `/_stcore/health` return `404`.

No real supplier message is sent by tests or by the synthetic demo.
