# MILAAN

MILAAN is an ITC (input tax credit) recovery workspace for small Indian
traders. It imports purchase bills and GSTR-2B data, validates them, runs a
deterministic reconciliation, explains every mismatch, prepares supplier
follow-ups, and keeps an auditable record of what the WhatsApp agent read,
decided, asked, and sent.

This repository is the integrated result of the MILAAN business-logic
repository and the WhatsApp agent implementation.

## One primary UI

Open **http://127.0.0.1:8501** after starting the launcher. MILAAN's primary
UI is the custom Flask + HTML workspace in `milaan_web.py` and
`agent/web/primary.html`. It contains:

- Dashboard
- Purchase invoices / bills (upload, validate, edit, import, export)
- GSTR-2B import (preview, import, duplicate-file guard)
- Reconciliation (run any batch, inspect every mismatch reason, reopen older runs, export CSV or an accountant-ready XLSX workbook)
- Exceptions (invoice-level evidence for every non-match, with field comparison)
- Suppliers (masked master data and bill/GST totals)
- Supplier follow-ups (read-only eligibility preview, generate, edit, approve, skip, verify later-statement recovery evidence)
- Delivery (mock/live safeguards, retry, resolve, history)
- Conversations (read-only agent message history)
- Agent trace (read-only tool calls, decisions, consent, messages)
- Audit log
- Settings (service health, database paths and demo status, WhatsApp QR/status, guarded test send)

There is no second dashboard or alternate web entry point. `milaan_web.py`
serves every page listed above, and `agent/web/primary.html` is the only
frontend. The optional inbox webhook server and WhatsApp bridge are background
services; they do not serve a competing user interface.

## Quick start (macOS)

Prerequisites: Python 3.11+, Node.js 18+ (only for the WhatsApp Web bridge),
and the project checked out locally.

```bash
cd "/Users/dakshjain/Desktop/IIT BOMBAY/MILAAN"

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-agent.txt

cd whatsapp_service
npm ci
cd ..

# Existing .env: keep it. For a fresh checkout:
cp .env.example .env
# Edit .env and set only the values you need. Never commit it.

./run_milaan.sh
```

Open **http://127.0.0.1:8501**.

To run the isolated synthetic walkthrough instead, stop any instance on port
8501 first:

```bash
./run_milaan.sh --stop
./run_milaan.sh --demo
```

This uses `demo/milaan_demo.db`, seeds it if missing, shows a demo banner, and
disables WhatsApp, Cloud API, webhook and agent processes. Reset it with
`.venv/bin/python demo/setup_demo.py`. See [DEMO.md](DEMO.md).

Stop with `Ctrl+C` in the launcher terminal or, from another terminal:

```bash
./run_milaan.sh --stop
```

The launcher loads `.env` through `scripts/env_loader.py`, checks
dependencies and ports, starts only the required processes, waits for health,
writes logs under `logs/`, and stops only the processes it started. It does
not start a duplicate server on a repeated run. It prints only the one URL
you need.

Useful options:

```bash
./run_milaan.sh --demo             # isolated synthetic database, no external send paths
./run_milaan.sh --port 8502        # different UI port
./run_milaan.sh --no-whatsapp      # UI only (no Node bridge)
./run_milaan.sh --with-agent       # also start the inbound agent server :5050
./run_milaan.sh --help
```

Advanced/CI: set `MILAAN_PYTHON=/path/to/prepared/venv/bin/python` to use
an existing interpreter instead of `./.venv`.


## Services and ports

| Port | Service | Required? | Notes |
| ---: | --- | --- | --- |
| 8501 | Primary MILAAN UI (`milaan_web.py`) | Yes | The only URL to open. |
| 8801 | WhatsApp Web bridge (`whatsapp-web.js`) | Only when `MILAAN_WA_ENABLED=true` | Loopback only. QR/linked session. |
| 8443 | Verified Cloud API webhook (`webhook.py`) | Optional | Bind `127.0.0.1`; put HTTPS in front. |
| 5050 | Inbound agent server (`agent.server`) | Optional | Loopback by default; set `AGENT_BIND` deliberately. It has no dashboard UI; conversation and trace views are inside the primary app. |

## WhatsApp configuration

There are three separate paths. They are not interchangeable.

### 1. WhatsApp Web (`whatsapp-web.js`) — local QR/linked device

Set these in `.env`:

```dotenv
MILAAN_WA_ENABLED=true
MILAAN_WA_SERVICE_URL=http://127.0.0.1:8801
MILAAN_WA_SERVICE_TOKEN=<long-random-token>
MILAAN_WA_TEST_RECIPIENT=<consenting-test-number>
MILAAN_WA_SERVICE_PORT=8801
MILAAN_WA_SERVICE_BIND=127.0.0.1
```

Run `./run_milaan.sh`. The bridge restores `whatsapp_service/.wwebjs_auth`
if the existing link is valid; it does **not** force a new QR login. If a
scan is needed, open **Settings** and use **Show WhatsApp QR** with the test
phone. Service health, database paths and the guarded demo test-send are in
**Settings** too. Never commit the auth folder or the token.

The demo test-send uses only `MILAAN_WA_TEST_RECIPIENT`. The supplier phone
on a draft is never used by the demo. Live outbound sends still require an
approved draft and explicit confirmation in the UI.

### 2. Meta WhatsApp Cloud API

Set `WHATSAPP_ENABLED=true`, `WHATSAPP_ACCESS_TOKEN`,
`WHATSAPP_PHONE_NUMBER_ID`, and the exact `WHATSAPP_API_VERSION` shown in
your Meta app. Clouds sends use the same approval and idempotency controls as
the mock path. Keep credentials out of `.env.example`, logs, screenshots, and
the repository.

### 3. Verified webhook receiver

Set `WHATSAPP_WEBHOOK_ENABLED=true`, `WHATSAPP_APP_SECRET`, and
`WHATSAPP_VERIFY_TOKEN`. The launcher starts the receiver automatically when
it is enabled and fully configured; to run it separately:

```bash
.venv/bin/python scripts/run_webhook.py
```

It verifies Meta signatures before accepting delivery statuses or inbound
replies. Put HTTPS and the correct callback URL in front of it; do not expose
it directly to the internet.

The optional inbound agent server (Twilio/Meta webhooks + read-only agent
dashboard) is separate:

```bash
./run_milaan.sh --with-agent
# or
.venv/bin/python -m agent.server
```

It binds to `127.0.0.1` by default. Set `AGENT_BIND=0.0.0.0` only for a
deliberate deployment with signed webhooks and a tunnel.

## Data and backups

MILAAN uses separate SQLite databases:

| Database | Owner | Contents |
| --- | --- | --- |
| `milaan.db` (`DATABASE_PATH`) | Primary MILAAN workspace | suppliers, invoices, GSTR-2B imports, reconciliation runs/results, follow-up drafts, delivery evidence, audit events |
| `agent_data/milaan_agent.db` (`AGENT_DB_PATH`) | WhatsApp agent | traders, agent invoices, agent GSTR-2B rows, decisions, trace, messages, outbound attempts |
| `demo/milaan_demo.db` (`DATABASE_PATH`) | Synthetic judge demo | disposable 4-supplier/5-invoice workspace used only by `--demo` |

They are intentionally separate. `./run_milaan.sh --demo` also points
`AGENT_DB_PATH` at `demo/milaan_agent_demo.db` (normally absent) so real agent
conversations never appear in the demo. **Settings** shows the active
operational and agent database paths plus whether demo mode is on.

The UI reads the agent database through a read-only adapter
(`agent_views.py`); it never copies tables or points one subsystem at an
incompatible schema. If the agent database is missing or
empty, the agent pages say so instead of showing synthetic values.

Back up the operational database with:

```bash
.venv/bin/python scripts/backup_database.py
```

Back up the agent database with the built-in SQLite backup API used by
`scripts/cleanup_placeholder_seed.py`, or copy it while the agent is stopped.
`backups/` is excluded from the final ZIP.

### Accidental placeholder seed

A previous demo seed used the literal trader value `+91`. The seven bills
with `seed:photo` provenance were confirmed synthetic; only those records and
their associated supplier/trace/history rows were removed. The pre-cleanup
backup is `backups/milaan_agent_pre_cleanup_20261022_230650.db`. No
operational invoice data was touched.

For future demos and tests, use a disposable database:

```bash
AGENT_DB_PATH=/tmp/milaan-agent-demo.db \
  .venv/bin/python -m agent.demo.seed \
  --trader <consenting-test-phone> \
  --supplier <consenting-test-phone>
```

`agent.demo.seed` now refuses placeholder values (such as `+91`), literal
example phone numbers, and a destructive reset of the default agent database
unless `AGENT_DB_PATH` or `--force-default-db` is used.

## Tests

Run the full Python suite (temporary databases, mocked delivery, no live
network sends):

```bash
.venv/bin/python -m pytest -q --ignore=whatsapp_service
```

Node service checks:

```bash
cd whatsapp_service
npm test
npm run check
```

Tests use temporary databases, mock delivery, and no live network sends.

## Troubleshooting

- **WhatsApp shows `disconnected` with no QR.** A previous bridge run may have
  left Chromium's `SingletonLock` in `whatsapp_service/.wwebjs_auth/session/`.
  The launcher removes stale locks automatically before starting the bridge.
  If a *live* browser process still holds the profile, stop it first.
**Port 8501 is already in use.** Run `./run_milaan.sh --stop`. If an unrelated
process owns the port, stop it or use `./run_milaan.sh --port 8502`.

**The UI starts but WhatsApp Web is unreachable.** Check
`logs/whatsapp-web.log`, confirm Node 18+ is installed, and run
`cd whatsapp_service && npm ci`. Verify `MILAAN_WA_SERVICE_TOKEN` is the same
for both processes (it is never printed).

**The QR is not visible.** Open **Settings → Show WhatsApp QR**. If the
existing linked session is valid, no QR is needed. Settings shows whether the
service is ready, waiting for a scan, or misconfigured.

**The agent pages are empty.** That is expected after the placeholder cleanup.
Seed a temporary database or message the agent when the inbound channel is
configured. Do not seed the real agent database with placeholders.

**A delivery is stuck or ambiguous.** Open **Audit log → Delivery evidence**.
Unknown outcomes are preserved explicitly and can be resolved only with
verified provider evidence; the app never guesses.

**I need a fresh environment.** Keep `.env`, databases, and the WhatsApp auth
folder; recreate `.venv` and `whatsapp_service/node_modules`, then rerun the
launcher.

## Limitations

- Live WhatsApp sending was **not** exercised during the merge verification.
  The local WhatsApp Web bridge was started, its health endpoint answered, and
  the existing session reported `ready` with a masked number; no message was
  sent. Cloud API and Twilio credentials are not configured in this checkout.
- Live OCR/LLM extraction was not tested (no LLM key configured); offline
  fixtures and deterministic extraction tests pass.
- The only web interface is `agent/web/primary.html`, served by
  `milaan_web.py`. Agent history is read-only and is shown in its Conversation
  and Agent trace pages.
- Reconciliation is deterministic and auditable; it does not query the live
  GST portal or verify registration status.
- The system does not run a background retry scheduler. Retries are explicit
  and bounded.
- The ZIP intentionally excludes `.env`, databases, WhatsApp auth/session
  state, `node_modules`, `.venv`, caches, and logs. Reconnect your local
  configuration after extraction.

## Further documentation

- [MERGE_REPORT.md](MERGE_REPORT.md) — archived merge history (pre-consolidation)
- [DEMO.md](DEMO.md) — isolated synthetic walkthrough
- [SETUP_AGENT.md](SETUP_AGENT.md) — inbound agent setup
- [EVALUATION.md](EVALUATION.md) — evaluation methodology and results
- [INTEGRATION_REVIEW.md](INTEGRATION_REVIEW.md) — integration review
