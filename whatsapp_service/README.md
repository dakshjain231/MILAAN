# MILAAN WhatsApp Web demo service

A small Node.js service that exposes the unofficial
[`whatsapp-web.js`](https://github.com/wwebjs/whatsapp-web.js) client over a
**localhost-only HTTP API**. MILAAN uses it only for a clearly labelled demo:
preview one approved supplier follow-up, confirm consent, and send it to a
single configured test number. It is **not** part of the Cloud API delivery
pipeline and it never sends to suppliers automatically.

## Safety boundaries

- Binds to `127.0.0.1` (or `localhost`/`::1`) only. The service refuses other
  bind addresses.
- Every data endpoint requires `Authorization: Bearer <MILAAN_WA_SERVICE_TOKEN>`.
- The token and the linked-device session are never logged. Session
  credentials are written to `.wwebjs_auth/`, which is gitignored.
- One recipient per request; no bulk messaging and no automatic send.
- The Python app calls this service only from the explicit demo section after
  two confirmations, and only for the configured consented test recipient.
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

```sh
cd whatsapp_service
export MILAAN_WA_SERVICE_TOKEN="$(openssl rand -hex 32)"
export MILAAN_WA_SERVICE_PORT=8801
export MILAAN_WA_SERVICE_BIND=127.0.0.1
export MILAAN_WA_HEADLESS=true
npm start
```

In the terminal that runs Streamlit, export the same token plus the demo
settings:

```sh
export MILAAN_WA_ENABLED=true
export MILAAN_WA_SERVICE_TOKEN="<the same token>"
export MILAAN_WA_TEST_RECIPIENT="9198XXXXXXXX"
export MILAAN_WA_SERVICE_URL="http://127.0.0.1:8801"
.venv/bin/streamlit run app.py
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

## API

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | no | Liveness only |
| `GET` | `/status` | bearer | Connection state, masked linked number |
| `GET` | `/qr` | bearer | Current QR as a `data:image/png;base64,...` URL |
| `POST` | `/send` | bearer | `{ "to", "message", "idempotencyKey"? }` |

`/send` returns `409 not_ready` until the phone is linked, `400` for invalid
input, `401` for a bad token, and `200 { "ok": true, "messageId": ... }` when
the client accepted the request.

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
