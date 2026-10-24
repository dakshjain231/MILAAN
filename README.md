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
`agent/web/primary.html`. The sidebar has exactly six destinations:

1. **Dashboard** — eligible/ineligible ITC, latest reconciliation counts,
   monthly trend, vendor ITC and workflow progress, all derived from your
   records and filterable by period, GSTIN, supplier and status.
2. **Purchase Invoices** — CSV/XLSX upload with preview, validation status,
   filters and per-invoice details.
3. **GSTR-2B Import** — GST-portal JSON, portal Excel (.xlsx) or the CSV/XLSX
   template; preview, duplicate-file guard, per-row Imported/Rejected status.
4. **Reconciliation** — run against the latest GSTR-2B import, filter, and
   inspect stored values and reasons for every result (including rows that
   are only in GSTR-2B); export CSV or the accountant XLSX workbook.
5. **Supplier Follow-ups** — prepare drafts, edit, approve (explicit
   confirmation), skip, simulated or Cloud API send (guarded), and
   later-statement verification. "Resolved" requires later GSTR-2B evidence.
6. **Settings** (bottom of the sidebar) — organization details, uploads,
   WhatsApp status/QR/test send, and the protected clear-data action.

The header's **Sample View** toggle shows the reference sample data used for
screenshots; it is labelled "Sample Data" and never mixes with your records.

There is no second dashboard or alternate web entry point. `milaan_web.py`
serves every page, and `agent/web/primary.html` is the only frontend. The optional inbox webhook server and WhatsApp bridge are background
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

### Background launcher for presentations

`./run_milaan.sh` stays in the foreground. For a demo where no Terminal
window needs to stay open, use the background launcher instead:

```bash
./start_milaan_demo.sh     # starts the UI (and the WhatsApp Web bridge when enabled) detached
./status_milaan_demo.sh    # health of every component, ports and log paths
./stop_milaan_demo.sh      # stops only the processes this launcher started
./start_milaan_demo.sh --demo-db   # same, but on the disposable demo/sarcathon_demo.db
```

`--demo-db` never touches `milaan.db`; the header then reads "Demo Database".
The SARCathon walkthrough is in [DEMO.md](DEMO.md).

Processes run in their own sessions (they survive closing the Terminal), PID
and start-time records live in `.run/demo/`, and logs in `logs/demo/`
(rotated at 10 MB). Starting twice never creates duplicates, and stop never
signals a process whose PID was reused by another program. Nothing is sent to
WhatsApp at startup.

## Supported file formats

| Upload | Accepted | Notes |
| --- | --- | --- |
| Purchase invoices | CSV, XLSX | Must follow the downloadable template (`/api/invoices/template.csv`). Required: supplier_name, supplier_gstin, invoice_number, invoice_date, tax_period, taxable_value, gst_rate, cgst, sgst, igst, total_gst, invoice_total. Invalid rows are rejected and never imported. |
| GSTR-2B | GST-portal JSON (`data.docdata.b2b`), portal Excel (.xlsx), template CSV/XLSX | Amounts are exact (integer paise); ITC availability, reverse charge and cess are kept. PDF and legacy .xls are not supported. |

Uploads are limited to 25 MB. `demo/demo_purchase_invoices.csv` and
`demo/demo_gstr2b_portal.json` are a matching synthetic pair for walkthroughs.

### Sending files over WhatsApp instead of uploading them

With the bridge linked and `MILAAN_WA_INBOUND_ENABLED=true`, an allowlisted
number can send the same two file types **to** the linked WhatsApp account and
MILAAN imports them automatically — no browser upload. Settings → *Inbound
WhatsApp documents* is the receipt for every message, including the ones that
could not be read. Once a purchase register and a GSTR-2B batch share a tax
period, reconciliation runs and supplier follow-up drafts are prepared in the
same pass.

| Sent over WhatsApp | Result |
| --- | --- |
| CSV / XLSX purchase register | Imported; duplicates skipped by invoice key |
| JSON / CSV / XLSX GSTR-2B export | Imported as a batch; identical file not re-imported |
| PDF | **Not read** — no PDF text-extraction or OCR service is configured |
| Photo of an invoice | **Not read** — no vision/OCR service (`LLM_API_KEY` unset) |
| Voice note (Hindi or English) | **Not read** — no speech-to-text service (`GEMINI_API_KEY` unset) |
| Legacy `.xls`, video, anything else | **Not read** — logged with the reason |

Unsupported messages are never guessed at: each is recorded with the precise
blocker so the gap is visible rather than silent. Receiving a file never sends
a reply, and a prepared follow-up still needs the usual explicit approval and
two confirmations before anything can be delivered.


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

#### Real Supplier Follow-ups delivery over this bridge

Off by default. To turn it on, add:

```dotenv
MILAAN_WA_LIVE_FOLLOWUPS=true
MILAAN_WA_ALLOWED_RECIPIENTS=<your-own-number>   # comma separated
```

`MILAAN_WA_ALLOWED_RECIPIENTS` is a hard allowlist enforced twice — in
`webjs.py` before any HTTP call and again in the Node service — so a supplier
or any other number is refused with nothing sent. Approval freezes a draft's
recipient, so set the draft's number to an allowlisted one **before**
approving it.

In **Supplier Follow-ups**, open an approved draft: the *Send a real WhatsApp
message (WhatsApp Web)* panel checks the bridge and the linked session, then
offers a **Send WhatsApp message** button behind two tick-boxes and a final
"Yes, send now" confirmation. Nothing is ever sent automatically. The result
is a real `message_deliveries` row (provider `whatsapp_web_js`) carrying the
bridge's actual status — `SENT` with WhatsApp's message ID, `FAILED`, or
`UNKNOWN` when the outcome could not be established. `UNKNOWN` is never
retried automatically, because the message may already have arrived.
**Simulate send** remains available and is always recorded as a simulation.

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

Back up the operational database (a consistent, integrity-checked copy that
is safe while MILAAN runs) with:

```bash
mkdir -p backups && .venv/bin/python scripts/backup_database.py backups
```

**Clear All Data** in Settings requires an explicit confirmation, writes a
backup to `backups/` before deleting anything, and keeps the audit log and
organization settings. Schema migrations that rebuild a table also write a
backup first.

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

Browser end-to-end rehearsal of the whole workspace script, against a
disposable demo database (never `milaan.db`):

```bash
rm -f demo/sarcathon_demo.db demo/sarcathon_agent.db
DATABASE_PATH="$PWD/demo/sarcathon_demo.db" AGENT_DB_PATH="$PWD/demo/sarcathon_agent.db" \
  .venv/bin/python scripts/run_primary.py --port 8599 &
NODE_PATH=whatsapp_service/node_modules node scripts/demo_e2e.js \
  http://127.0.0.1:8599 . /tmp/milaan-shots
```

Last verified on 2026-10-24: **672 Python tests**, **75 Node tests**, **25/25
browser checks** (the browser run passes both with the WhatsApp Web bridge
live and with delivery switched off). Inbound WhatsApp intake is covered by
`test_inbound.py`, `test_inbound_web.py` and
`whatsapp_service/test/inbound*.test.js`; the pilot send restriction is pinned
across all three enforcement layers by `test_pilot_safety.py`.

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

**The QR is not visible.** Open **Settings → WhatsApp → Link device** (shown
only while the bridge waits for a scan). If the existing linked session is
valid, no QR is needed. Settings shows whether the bridge is linked, waiting
for a scan, off, or not running, and whether Cloud API delivery is configured.

**The agent pages are empty.** That is expected after the placeholder cleanup.
Seed a temporary database or message the agent when the inbound channel is
configured. Do not seed the real agent database with placeholders.

**A delivery is stuck or ambiguous.** Open the follow-up from **Supplier
Follow-ups**; its Deliveries and History sections show the stored evidence.
Unknown outcomes are preserved explicitly and can be resolved only with
verified provider evidence (`/api/deliveries/<id>/resolve`); the app never
guesses.

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
- Inbound WhatsApp documents cover tabular files only (CSV/XLSX/JSON).
  Photos, PDFs and voice notes are detected, logged and refused with the
  reason — they are **not** extracted or transcribed, because no OCR, PDF
  text-extraction or speech-to-text service is configured. Supplying
  `LLM_API_KEY` / `GEMINI_API_KEY` would be the prerequisite for that work;
  the agent subsystem's image extraction exists but is a separate pipeline
  from this bridge.
- The agent settings loader copies `.env` into the process environment
  lazily, with `setdefault`. An *unset* variable is therefore filled in from
  `.env` rather than staying unset: set a switch to `false` explicitly to
  turn it off, or run with `MILAAN_SKIP_DOTENV=1` for a clean environment.
- The only web interface is `agent/web/primary.html`, served by
  `milaan_web.py`. The read-only agent history adapter (`agent_views.py`)
  feeds `/api/state`; there is no separate agent page in the six-page UI.
- Reconciliation is deterministic and auditable; it does not query the live
  GST portal or verify registration status.
- The system does not run a background retry scheduler. Retries are explicit
  and bounded.
- The ZIP intentionally excludes `.env`, databases, WhatsApp auth/session
  state, `node_modules`, `.venv`, caches, and logs. Reconnect your local
  configuration after extraction.

## Production deployment

The local launchers run Flask's development server on `127.0.0.1` for a
single user. **Do not expose that server to a network.** For a shared or
public deployment:

1. Build the image (`Dockerfile`) or install `requirements-prod.txt`; it runs
   `gunicorn wsgi:app` (`wsgi.py` is the production entry point).
2. Set `APP_ENV=production`. The app then **refuses to start** unless
   `MILAAN_BASIC_AUTH_USER` and `MILAAN_BASIC_AUTH_PASSWORD_HASH` are set.
   Create the hash with
   `python -c "from werkzeug.security import generate_password_hash as g; print(g(input('Password: ')))"`.
3. Terminate HTTPS at a reverse proxy in front of gunicorn (Basic auth is only
   safe over TLS) and set `MILAAN_ALLOWED_HOSTS` to the public host name
   (a leading dot allows subdomains). Requests for other Host headers get 400.
4. Put `DATABASE_PATH`/`AGENT_DB_PATH` on persistent storage and schedule
   `scripts/backup_database.py`; SQLite suits a single small team, not
   concurrent multi-tenant use.

Built-in protections: Host allowlist (DNS rebinding), cross-site write
refusal (Origin / Sec-Fetch-Site), `X-Frame-Options: DENY`, 25 MB upload
limit, parameterised SQL, formula-safe CSV export, masked phone numbers and no
secrets in API responses or logs.

Still required before **public** production (not done here): a real hosting
target with HTTPS and DNS, an identity provider or managed credentials
rotation (Basic auth is a single shared login), monitoring/alerting, off-site
backups with restore drills, and WhatsApp Cloud API credentials plus a public
HTTPS webhook URL if live delivery and delivery receipts are wanted.

## Further documentation

- [MERGE_REPORT.md](MERGE_REPORT.md) — archived merge history (pre-consolidation)
- [DEMO.md](DEMO.md) — isolated synthetic walkthrough
- [SETUP_AGENT.md](SETUP_AGENT.md) — inbound agent setup
- [EVALUATION.md](EVALUATION.md) — evaluation methodology and results
- [INTEGRATION_REVIEW.md](INTEGRATION_REVIEW.md) — integration review
