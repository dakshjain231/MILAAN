'use strict';

const http = require('node:http');
const { tokenMatches, extractToken } = require('./auth');
const { validateSend } = require('./validation');
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

function createServer({ config, state, adapter, logger = console.error }) {
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
      if (connectionState.snapshot().status !== 'ready') {
        return sendJson(res, 409, { ok: false, error: 'not_ready' });
      }
      try {
        const result = await clientAdapter.sendText(parsed.to, parsed.message);
        if (!result || typeof result.messageId !== 'string' || !result.messageId) {
          return sendJson(res, 502, { ok: false, error: 'missing_message_id' });
        }
        return sendJson(res, 200, {
          ok: true,
          messageId: result.messageId.slice(0, 200),
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
