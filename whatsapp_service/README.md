# MILAAN WhatsApp Web service

A small Node.js service that exposes the unofficial
[`whatsapp-web.js`](https://github.com/wwebjs/whatsapp-web.js) client over a
**localhost-only HTTP API**. MILAAN uses it for two things: the labelled demo
test message, and — when `MILAAN_WA_LIVE_FOLLOWUPS=true` — real delivery of an
approved Supplier Follow-up to an **allowlisted** number. It is **not** part of
the Cloud API delivery pipeline and it never sends anything automatically.

## Safety boundaries

- Binds to `127.0.0.1` (or `localhost`/`::1`) only. The service refuses other
  bind addresses.
- Every data endpoint requires `Authorization: Bearer <MILAAN_WA_SERVICE_TOKEN>`.
- **Recipient allowlist.** `/send` refuses any number outside
  `MILAAN_WA_ALLOWED_RECIPIENTS` (falling back to `MILAAN_WA_TEST_RECIPIENT`)
  with `403 recipient_not_allowed`, before the WhatsApp client is touched. An
  empty allowlist blocks every send. The Python side enforces the same list
  independently, so a supplier number cannot be reached through this bridge
  from either side.
- The token and the linked-device session are never logged. Session
  credentials are written to `.wwebjs_auth/`, which is gitignored.
- One recipient per request; no bulk messaging and no automatic send.
- **Inbound is read-only.** The inbound handler queues documents from
  allowlisted senders and never sends, replies or acknowledges in WhatsApp, so
  an incoming message can never produce an outgoing one.
- Live follow-up delivery needs the live switch, a linked session and two
  explicit confirmations in the website for each message.
- No delivery/read claims: an accepted request is not proof the message was
  delivered or read.

> `whatsapp-web.js` drives WhatsApp Web through an unofficial browser
> automation path. Using it can violate WhatsApp's Terms of Service and may
> get a number limited or banned. Use a dedicated test number, not a business
> number you depend on.

## Requirements (macOS)

- macOS with Node.js **18 or newer** (tested with Node 22 and npm 10).
  Check with `node --version` and `node -p process.arch` (Apple Silicon should
  print `arm64`).
- `npm`, and roughly 500 MB of free disk space for the bundled Chromium.
- A phone with WhatsApp installed, used only as the linked test device.

Install Node with Homebrew if needed:

```sh
brew install node
```

## Install

```sh
cd whatsapp_service
npm install
```

`npm install` downloads a compatible Chromium through Puppeteer. If that step
fails behind a firewall, install the browser explicitly:

```sh
npx puppeteer browsers install chrome
```

## Run

Use the same 16+ character token for the service and the Python app. Generate
one with `openssl rand -hex 32`.

The simplest path is to put the shared settings in the repository-root
`.env` (gitignored), then start each process through its launcher:

```sh
cd whatsapp_service && npm ci && cd ..
cp .env.example .env    # set the values below in .env
./run_milaan.sh                        # starts the primary UI and this service when enabled
```

`scripts/run_webjs.py` loads `.env` once; variables already exported in the
shell still win. Running `npm start` directly remains supported but will not
read the repository-root `.env`.

Required `.env` values:

```sh
MILAAN_WA_ENABLED=true
MILAAN_WA_SERVICE_TOKEN="<openssl rand -hex 32>"
MILAAN_WA_TEST_RECIPIENT="9198XXXXXXXX"
MILAAN_WA_SERVICE_URL="http://127.0.0.1:8801"
MILAAN_WA_SERVICE_PORT=8801
MILAAN_WA_SERVICE_BIND=127.0.0.1
MILAAN_WA_HEADLESS=true
```

For real follow-up delivery, add:

```sh
MILAAN_WA_LIVE_FOLLOWUPS=true
MILAAN_WA_ALLOWED_RECIPIENTS="9198XXXXXXXX"   # every number that may be messaged
```

Open **Supplier follow-ups → Delivery** or **Settings**. While the service is
waiting for a link, a QR code appears. On the phone, open WhatsApp →
**Linked devices** → **Link a device** and scan it. The session is reused on
the next start until you log out.

To unlink and start fresh, stop the service and delete
`whatsapp_service/.wwebjs_auth/`.

## Demo send

1. Approve a follow-up draft in MILAAN.
2. Confirm the service status is `ready` in the delivery page.
3. Review the frozen approved text.
4. Tick **both** boxes: the test number consented, and explicit approval for
   this single send.
5. Press **Send demo to test recipient**.

The message goes only to `MILAAN_WA_TEST_RECIPIENT`. The supplier number is
never used by this demo, no `message_deliveries` row is created, and only a
non-secret `webjs_demo_send` audit event is recorded.

## Live follow-up send (real delivery)

With `MILAAN_WA_LIVE_FOLLOWUPS=true` and the recipient on the allowlist, the
website's Supplier Follow-ups page sends the approved message for real:

1. Set the draft's WhatsApp number to an allowlisted number **before**
   approving it — approval freezes the recipient.
2. Approve the draft.
3. Open it. The **Send a real WhatsApp message (WhatsApp Web)** panel shows
   the linked account and the masked recipient, or states exactly what is
   missing (bridge off, session not linked, recipient not allowlisted).
4. Tick both confirmations, press **Send WhatsApp message**, then confirm
   **Yes, send now**.

The result is a real `message_deliveries` row with provider
`whatsapp_web_js`, the status the bridge actually reported (`SENT`, `FAILED`
or `UNKNOWN`) and WhatsApp's own message ID. A `SENT` row means WhatsApp
accepted the message, not that it was read. One delivery is kept per
follow-up, and a WhatsApp Web delivery is never retried automatically.

## API

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | no | Liveness only |
| `GET` | `/status` | bearer | Connection state, masked linked number |
| `GET` | `/qr` | bearer | Current QR as a `data:image/png;base64,...` URL |
| `POST` | `/send` | bearer | `{ "to", "message", "idempotencyKey"? }` |
| `GET` | `/inbound` | bearer | Queued inbound documents, metadata only |
| `GET` | `/inbound/media?id=` | bearer | One entry including its base64 bytes |
| `POST` | `/inbound/ack?id=` | bearer | Drop an entry once MILAAN has it |

`/send` returns `403 recipient_not_allowed` for a number outside the
allowlist, `409 not_ready` until the phone is linked, `400` for invalid
input, `401` for a bad token, and `200 { "ok": true, "messageId": ... }` when
the client accepted the request.

## Inbound documents

Files sent **to** the linked account are queued for MILAAN to collect. The
bridge stays deliberately dumb: it classifies, downloads and queues, and
MILAAN decides which parser owns the file. The handler never replies, so an
incoming message cannot trigger an outgoing one.

- **Sender allowlist.** Only `MILAAN_WA_INBOUND_SENDERS` (falling back to the
  send allowlist, then the test recipient) is read. Any other number, every
  group chat and every broadcast is ignored without downloading anything. No
  configured sender means nothing is read.
- **Supported:** `.csv`, `.xlsx`, `.json`. Everything else is queued with a
  reason code and **no download**, so the dashboard can name the blocker:
  `UNSUPPORTED_PDF`, `UNSUPPORTED_LEGACY_XLS`, `UNSUPPORTED_IMAGE` (no OCR
  service), `UNSUPPORTED_AUDIO` (no speech-to-text service),
  `UNSUPPORTED_VIDEO`, `UNKNOWN_FILE_TYPE`.
- **Size cap.** `MILAAN_WA_INBOUND_MAX_BYTES` (default 5 MiB) is checked
  against the declared size before downloading and the real size after.
- **At-least-once, never twice.** An entry is acknowledged only after MILAAN
  commits its outcome, so a crash leaves the document queued; message ids are
  remembered after acknowledgement, so a WhatsApp re-delivery is not re-queued.
  The queue is bounded (50) and reports `dropped` rather than silently losing
  anything.
- File names are reduced to a base name before use; MILAAN stores the bytes
  under a content-hash name, never under the sender's.

## Tests

```sh
cd whatsapp_service
npm test        # pure logic/routing tests; no Chromium, network or WhatsApp
npm run check   # node --check on every source file
```

## Troubleshooting

- `MILAAN WhatsApp service not configured`: set `MILAAN_WA_SERVICE_TOKEN`.
- `Cannot find module 'whatsapp-web.js'`: run `npm install`.
- Chromium download failure: run `npx puppeteer browsers install chrome`.
- QR never appears: restart the service and check the terminal; delete
  `.wwebjs_auth/` if a stale session is stuck.
- `401 unauthorized` from MILAAN: the Python app and service tokens differ.
- `403 recipient_not_allowed`: the number is not in
  `MILAAN_WA_ALLOWED_RECIPIENTS` (or `MILAAN_WA_TEST_RECIPIENT`). Both
  processes must see the same list; restart the service after changing it.
- Message sent but no message id: `whatsapp-web.js` 1.34.7 looks the sent
  message up by `key._serialized`, which current WhatsApp Web keys no longer
  have. `src/sendConfirm.js` then reads the id back from WhatsApp Web's
  message store (newest outgoing message with the same text since the send
  began) and the log says so. If it still cannot find one, MILAAN records the
  outcome as unknown and never retries automatically.
