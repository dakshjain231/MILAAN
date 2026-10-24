'use strict';

const http = require('node:http');
const { tokenMatches, extractToken } = require('./auth');
const { validateSend } = require('./validation');
const { isAllowedRecipient } = require('./config');
const { ConnectionState } = require('./state');

const MAX_BODY_BYTES = 64 * 1024;

function sendJson(res, status, payload) {
  const body = Buffer.from(JSON.stringify(payload));
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': body.length,
    'Cache-Control': 'no-store',
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        const error = new Error('payload too large');
        error.code = 'TOO_LARGE';
        reject(error);
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

// Long digit runs (phone numbers, chat ids) are masked before logging.
function maskForLog(text) {
  return String(text || '')
    .replace(/\d{6,}/g, (digits) => `••••${digits.slice(-4)}`)
    .slice(0, 300);
}

function createServer({
  config,
  state,
  adapter,
  inboundQueue = null,
  logger = console.error,
}) {
  const connectionState = state || new ConnectionState();
  const clientAdapter = adapter || {
    async sendText() {
      const error = new Error('no client adapter configured');
      error.code = 'NOT_READY';
      throw error;
    },
  };

  const server = http.createServer(async (req, res) => {
    const requestUrl = new URL(req.url || '/', 'http://127.0.0.1');

    if (req.method === 'GET' && requestUrl.pathname === '/health') {
      return sendJson(res, 200, {
        ok: true,
        service: 'milaan-whatsapp-web',
        version: 1,
      });
    }

    const authorized = tokenMatches(extractToken(req.headers), config.token);

    if (req.method === 'GET' && requestUrl.pathname === '/status') {
      if (!authorized) return sendJson(res, 401, { ok: false, error: 'unauthorized' });
      return sendJson(res, 200, { ok: true, ...connectionState.snapshot() });
    }

    if (req.method === 'GET' && requestUrl.pathname === '/qr') {
      if (!authorized) return sendJson(res, 401, { ok: false, error: 'unauthorized' });
      const snapshot = connectionState.snapshot();
      return sendJson(res, 200, {
        ok: true,
        status: snapshot.status,
        qr: snapshot.qrAvailable ? connectionState.qrDataUrl() : null,
      });
    }

    // --- inbound intake (read + acknowledge only; never sends) -----------
    // MILAAN polls these: /inbound lists metadata, /inbound/media returns the
    // bytes for one entry, /inbound/ack drops it once MILAAN has committed
    // the import. An entry that is never acknowledged stays queued, so a
    // crash on the MILAAN side cannot lose a document.

    if (req.method === 'GET' && requestUrl.pathname === '/inbound') {
      if (!authorized) return sendJson(res, 401, { ok: false, error: 'unauthorized' });
      if (!inboundQueue) {
        return sendJson(res, 200, {
          ok: true, documents: [], queued: 0, dropped: 0, enabled: false,
        });
      }
      return sendJson(res, 200, {
        ok: true,
        documents: inboundQueue.list(),
        queued: inboundQueue.size,
        dropped: inboundQueue.dropped,
        enabled: config.inboundEnabled !== false,
      });
    }

    if (req.method === 'GET' && requestUrl.pathname === '/inbound/media') {
      if (!authorized) return sendJson(res, 401, { ok: false, error: 'unauthorized' });
      const id = requestUrl.searchParams.get('id');
      if (!id) return sendJson(res, 400, { ok: false, error: 'missing_id' });
      const entry = inboundQueue ? inboundQueue.get(id) : null;
      if (!entry) return sendJson(res, 404, { ok: false, error: 'not_found' });
      return sendJson(res, 200, { ok: true, document: entry });
    }

    if (req.method === 'POST' && requestUrl.pathname === '/inbound/ack') {
      if (!authorized) return sendJson(res, 401, { ok: false, error: 'unauthorized' });
      const id = requestUrl.searchParams.get('id');
      if (!id) return sendJson(res, 400, { ok: false, error: 'missing_id' });
      const acknowledged = inboundQueue ? inboundQueue.ack(id) : false;
      return sendJson(res, 200, { ok: true, acknowledged });
    }

    if (req.method === 'POST' && requestUrl.pathname === '/send') {
      if (!authorized) return sendJson(res, 401, { ok: false, error: 'unauthorized' });
      let raw;
      try {
        raw = await readBody(req);
      } catch (error) {
        const tooLarge = error && error.code === 'TOO_LARGE';
        return sendJson(res, tooLarge ? 413 : 400, {
          ok: false,
          error: tooLarge ? 'payload_too_large' : 'bad_request',
        });
      }
      let body;
      try {
        body = JSON.parse(raw);
      } catch (error) {
        return sendJson(res, 400, { ok: false, error: 'invalid_json' });
      }
      const parsed = validateSend(body);
      if (!parsed.ok) return sendJson(res, 400, { ok: false, error: parsed.error });
      // Allowlist gate, independent of the caller: an unlisted recipient is
      // refused here before the WhatsApp client is touched. An empty
      // allowlist blocks every send.
      if (!isAllowedRecipient(config, parsed.to)) {
        return sendJson(res, 403, { ok: false, error: 'recipient_not_allowed' });
      }
      if (connectionState.snapshot().status !== 'ready') {
        return sendJson(res, 409, { ok: false, error: 'not_ready' });
      }
      try {
        const result = await clientAdapter.sendText(parsed.to, parsed.message);
        if (!result || typeof result.messageId !== 'string' || !result.messageId) {
          return sendJson(res, 502, { ok: false, error: 'missing_message_id' });
        }
        if (result.confirmedBy === 'message_store') {
          logger('MILAAN WhatsApp send: message id read from the WhatsApp Web message store (client returned none)');
        }
        return sendJson(res, 200, {
          ok: true,
          messageId: result.messageId.slice(0, 200),
          confirmedBy: result.confirmedBy || 'send_result',
        });
      } catch (error) {
        if (error && error.code === 'NOT_READY') {
          return sendJson(res, 409, { ok: false, error: 'not_ready' });
        }
        // Keep the cause diagnosable in the service log (never the message
        // text, the token or an unmasked number); the HTTP reply stays generic.
        logger(
          `MILAAN WhatsApp send failed: ${maskForLog(error && error.name)}: `
            + maskForLog(error && error.message)
        );
        // The client may have handed the message to WhatsApp before failing
        // (e.g. no message id returned), so the outcome is not "failed".
        return sendJson(res, 502, { ok: false, error: 'send_outcome_unknown' });
      }
    }

    return sendJson(res, 404, { ok: false, error: 'not_found' });
  });

  server.on('clientError', (error, socket) => {
    try {
      socket.end('HTTP/1.1 400 Bad Request\r\n\r\n');
    } catch (ignored) {
      // The socket may already be gone.
    }
  });

  return server;
}

module.exports = { createServer, maskForLog, MAX_BODY_BYTES };
