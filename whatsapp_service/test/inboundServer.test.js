'use strict';

// The loopback HTTP surface MILAAN polls for inbound documents.

const test = require('node:test');
const assert = require('node:assert/strict');
const { createServer } = require('../src/server');
const { ConnectionState } = require('../src/state');
const { InboundQueue } = require('../src/inbound');
const { loadConfig } = require('../src/config');

const TOKEN = 'test-service-token-1234';
const BASE_ENV = {
  MILAAN_WA_SERVICE_TOKEN: TOKEN,
  MILAAN_WA_TEST_RECIPIENT: '+918888888888',
};

async function start(t, { queue } = {}) {
  const state = new ConnectionState();
  state.set('ready', { phone: '918888888888' });
  const server = createServer({
    config: { token: TOKEN, allowedRecipients: ['918888888888'] },
    state,
    adapter: { async sendText() { throw new Error('tests never send'); } },
    inboundQueue: queue || new InboundQueue(),
    logger: () => {},
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  return `http://127.0.0.1:${server.address().port}`;
}

const auth = (token = TOKEN) => ({ Authorization: `Bearer ${token}` });

function sampleEntry(overrides = {}) {
  return {
    id: 'false_918888888888@c.us_AAA',
    fromMasked: '…8888',
    kind: 'document',
    fileName: 'invoices.csv',
    extension: 'csv',
    mimeType: 'text/csv',
    size: 8,
    sha256: 'abc123',
    supported: true,
    reason: null,
    detail: null,
    data: 'YSxiCjEsMgo=',
    ...overrides,
  };
}

// --- config ----------------------------------------------------------------

test('inbound intake is on by default and reuses the send allowlist', () => {
  const config = loadConfig({ ...BASE_ENV });
  assert.equal(config.ready, true);
  assert.equal(config.inboundEnabled, true);
  assert.deepEqual(config.inboundSenders, ['918888888888']);
});

test('inbound senders can be narrowed independently of the send allowlist', () => {
  const config = loadConfig({
    ...BASE_ENV,
    MILAAN_WA_ALLOWED_RECIPIENTS: '+918888888888',
    MILAAN_WA_INBOUND_SENDERS: '+91 88888 88888',
  });
  assert.deepEqual(config.inboundSenders, ['918888888888']);
});

test('inbound intake can be switched off', () => {
  const config = loadConfig({ ...BASE_ENV, MILAAN_WA_INBOUND_ENABLED: 'false' });
  assert.equal(config.inboundEnabled, false);
});

test('an implausible inbound sender is a configuration error', () => {
  const config = loadConfig({ ...BASE_ENV, MILAAN_WA_INBOUND_SENDERS: 'not-a-number' });
  assert.equal(config.ready, false);
  assert.match(config.issues.join(' '), /MILAAN_WA_INBOUND_SENDERS/);
});

test('the inbound size cap has a default and a ceiling', () => {
  assert.equal(loadConfig({ ...BASE_ENV }).maxDocumentBytes, 5 * 1024 * 1024);
  assert.equal(
    loadConfig({ ...BASE_ENV, MILAAN_WA_INBOUND_MAX_BYTES: '1024' }).maxDocumentBytes,
    1024
  );
  const tooBig = loadConfig({ ...BASE_ENV, MILAAN_WA_INBOUND_MAX_BYTES: '99999999' });
  assert.equal(tooBig.ready, false);
  assert.match(tooBig.issues.join(' '), /MILAAN_WA_INBOUND_MAX_BYTES/);
});

// --- GET /inbound ----------------------------------------------------------

test('the inbound list requires the shared token', async (t) => {
  const base = await start(t);
  assert.equal((await fetch(`${base}/inbound`)).status, 401);
  assert.equal((await fetch(`${base}/inbound`, { headers: auth('wrong-token-0000') })).status, 401);
});

test('the inbound list reports queued entries without their bytes', async (t) => {
  const queue = new InboundQueue();
  queue.add(sampleEntry());
  const base = await start(t, { queue });
  const payload = await (await fetch(`${base}/inbound`, { headers: auth() })).json();
  assert.equal(payload.ok, true);
  assert.equal(payload.queued, 1);
  assert.equal(payload.documents.length, 1);
  assert.equal(payload.documents[0].fileName, 'invoices.csv');
  assert.equal(payload.documents[0].data, undefined);
});

test('the inbound list reports dropped entries so an overflow is visible', async (t) => {
  const queue = new InboundQueue({ limit: 1 });
  queue.add(sampleEntry({ id: 'A' }));
  queue.add(sampleEntry({ id: 'B' }));
  const base = await start(t, { queue });
  const payload = await (await fetch(`${base}/inbound`, { headers: auth() })).json();
  assert.equal(payload.dropped, 1);
});

test('an unsupported entry is listed with its reason', async (t) => {
  const queue = new InboundQueue();
  queue.add(sampleEntry({
    id: 'P', fileName: 'bill.pdf', extension: 'pdf', supported: false,
    reason: 'UNSUPPORTED_PDF', detail: 'PDF attachments are not parsed.', data: null,
  }));
  const base = await start(t, { queue });
  const payload = await (await fetch(`${base}/inbound`, { headers: auth() })).json();
  assert.equal(payload.documents[0].supported, false);
  assert.equal(payload.documents[0].reason, 'UNSUPPORTED_PDF');
});

// --- GET /inbound/media ----------------------------------------------------

test('media is served for one entry by id', async (t) => {
  const queue = new InboundQueue();
  queue.add(sampleEntry());
  const base = await start(t, { queue });
  const url = `${base}/inbound/media?id=${encodeURIComponent(sampleEntry().id)}`;
  const payload = await (await fetch(url, { headers: auth() })).json();
  assert.equal(payload.ok, true);
  assert.equal(payload.document.data, 'YSxiCjEsMgo=');
  assert.equal(payload.document.fileName, 'invoices.csv');
});

test('media requires the shared token', async (t) => {
  const queue = new InboundQueue();
  queue.add(sampleEntry());
  const base = await start(t, { queue });
  const url = `${base}/inbound/media?id=${encodeURIComponent(sampleEntry().id)}`;
  assert.equal((await fetch(url)).status, 401);
});

test('media for an unknown id is a 404, not an empty success', async (t) => {
  const base = await start(t);
  const response = await fetch(`${base}/inbound/media?id=nope`, { headers: auth() });
  assert.equal(response.status, 404);
  assert.equal((await response.json()).error, 'not_found');
});

test('media with no id is a bad request', async (t) => {
  const base = await start(t);
  assert.equal((await fetch(`${base}/inbound/media`, { headers: auth() })).status, 400);
});

// --- POST /inbound/ack -----------------------------------------------------

test('acknowledging removes the entry from the queue', async (t) => {
  const queue = new InboundQueue();
  queue.add(sampleEntry());
  const base = await start(t, { queue });
  const url = `${base}/inbound/ack?id=${encodeURIComponent(sampleEntry().id)}`;
  const payload = await (await fetch(url, { method: 'POST', headers: auth() })).json();
  assert.equal(payload.acknowledged, true);
  assert.equal(queue.size, 0);
});

test('acknowledging an unknown id answers honestly instead of failing', async (t) => {
  const base = await start(t);
  const response = await fetch(`${base}/inbound/ack?id=nope`, { method: 'POST', headers: auth() });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).acknowledged, false);
});

test('acknowledging requires the shared token', async (t) => {
  const queue = new InboundQueue();
  queue.add(sampleEntry());
  const base = await start(t, { queue });
  const url = `${base}/inbound/ack?id=${encodeURIComponent(sampleEntry().id)}`;
  assert.equal((await fetch(url, { method: 'POST' })).status, 401);
  assert.equal(queue.size, 1);
});

// --- isolation from the send path -----------------------------------------

test('inbound endpoints work with no queue configured and never 500', async (t) => {
  const server = createServer({
    config: { token: TOKEN, allowedRecipients: [] },
    state: new ConnectionState(),
    logger: () => {},
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const base = `http://127.0.0.1:${server.address().port}`;
  const payload = await (await fetch(`${base}/inbound`, { headers: auth() })).json();
  assert.equal(payload.ok, true);
  assert.deepEqual(payload.documents, []);
});

test('the inbound surface never exposes the service token', async (t) => {
  const queue = new InboundQueue();
  queue.add(sampleEntry());
  const base = await start(t, { queue });
  const list = await (await fetch(`${base}/inbound`, { headers: auth() })).text();
  assert.equal(list.includes(TOKEN), false);
});
