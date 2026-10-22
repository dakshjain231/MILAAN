'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { createServer } = require('../src/server');
const { ConnectionState } = require('../src/state');

const TOKEN = 'test-service-token-1234';

function makeConfig() {
  return { token: TOKEN };
}

async function start(t, options = {}) {
  const state = options.state || new ConnectionState();
  const adapter = options.adapter || {
    async sendText() {
      return { messageId: 'wamid.test.1' };
    },
  };
  const server = createServer({ config: makeConfig(), state, adapter });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const { port } = server.address();
  return { base: `http://127.0.0.1:${port}`, state };
}

function authHeaders(token = TOKEN) {
  return { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
}

test('health is public', async (t) => {
  const { base } = await start(t);
  const response = await fetch(`${base}/health`);
  assert.equal(response.status, 200);
  assert.equal((await response.json()).service, 'milaan-whatsapp-web');
});

test('status and qr require the shared token', async (t) => {
  const { base } = await start(t);
  assert.equal((await fetch(`${base}/status`)).status, 401);
  assert.equal((await fetch(`${base}/qr`)).status, 401);
  const status = await fetch(`${base}/status`, { headers: authHeaders('wrong-token-0000') });
  assert.equal(status.status, 401);
});

test('status reports connection state and never the token', async (t) => {
  const state = new ConnectionState();
  state.set('ready', { phone: '919999999999' });
  const { base } = await start(t, { state });
  const response = await fetch(`${base}/status`, { headers: authHeaders() });
  const payload = await response.json();
  assert.equal(response.status, 200);
  assert.equal(payload.ready, true);
  assert.equal(payload.phoneMasked, '\u20269999');
  assert.equal(JSON.stringify(payload).includes(TOKEN), false);
});

test('qr is served only while connecting', async (t) => {
  const state = new ConnectionState();
  state.set('qr', { qr: 'data:image/png;base64,abc' });
  const { base } = await start(t, { state });
  const qr = await (await fetch(`${base}/qr`, { headers: authHeaders() })).json();
  assert.equal(qr.qr, 'data:image/png;base64,abc');
  state.set('ready', { phone: '919999999999' });
  const cleared = await (await fetch(`${base}/qr`, { headers: authHeaders() })).json();
  assert.equal(cleared.qr, null);
});

test('send rejects when not connected', async (t) => {
  const { base } = await start(t);
  const response = await fetch(`${base}/send`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ to: '9999999999', message: 'Hello' }),
  });
  assert.equal(response.status, 409);
  assert.equal((await response.json()).error, 'not_ready');
});

test('send validates input before calling the client', async (t) => {
  const state = new ConnectionState();
  state.set('ready', { phone: '919999999999' });
  let called = false;
  const { base } = await start(t, {
    state,
    adapter: { async sendText() { called = true; return { messageId: 'x' }; } },
  });
  const response = await fetch(`${base}/send`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ to: '12', message: 'Hello' }),
  });
  assert.equal(response.status, 400);
  assert.equal(called, false);
});

test('send returns the provider message id when ready', async (t) => {
  const state = new ConnectionState();
  state.set('ready', { phone: '919999999999' });
  const calls = [];
  const { base } = await start(t, {
    state,
    adapter: {
      async sendText(to, message) {
        calls.push({ to, message });
        return { messageId: 'wamid.test.42' };
      },
    },
  });
  const response = await fetch(`${base}/send`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ to: '+91 99999 99999', message: 'Hello' }),
  });
  const payload = await response.json();
  assert.equal(response.status, 200);
  assert.equal(payload.messageId, 'wamid.test.42');
  assert.deepEqual(calls, [{ to: '919999999999', message: 'Hello' }]);
});

test('send does not leak client errors', async (t) => {
  const state = new ConnectionState();
  state.set('ready', { phone: '919999999999' });
  const { base } = await start(t, {
    state,
    adapter: {
      async sendText() {
        const error = new Error('internal secret details');
        throw error;
      },
    },
  });
  const response = await fetch(`${base}/send`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ to: '9999999999', message: 'Hello' }),
  });
  const text = await response.text();
  assert.equal(response.status, 502);
  assert.equal(text.includes('internal secret details'), false);
});

test('unknown routes return 404', async (t) => {
  const { base } = await start(t);
  assert.equal((await fetch(`${base}/nope`)).status, 404);
});
