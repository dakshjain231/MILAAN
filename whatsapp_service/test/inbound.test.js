'use strict';

// Inbound document intake. The bridge only queues what an allowlisted
// sender sent; MILAAN polls the queue over loopback and acknowledges each
// entry. Nothing here ever sends a message.

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  InboundQueue,
  describeInbound,
  createInboundHandler,
  SUPPORTED_EXTENSIONS,
} = require('../src/inbound');

const SENDER = '918888888888';
const OTHER = '919812345678';

function makeConfig(overrides = {}) {
  return {
    token: 'test-service-token-1234',
    allowedRecipients: [SENDER],
    inboundEnabled: true,
    inboundSenders: [SENDER],
    maxDocumentBytes: 1024,
    ...overrides,
  };
}

function makeMessage(overrides = {}) {
  return {
    id: { _serialized: 'false_918888888888@c.us_AAA' },
    from: `${SENDER}@c.us`,
    type: 'document',
    hasMedia: true,
    timestamp: 1_700_000_000,
    body: '',
    _data: { fileName: 'invoices.csv', mimetype: 'text/csv', size: 12 },
    ...overrides,
  };
}

// --- describeInbound -------------------------------------------------------

test('a CSV document is described as supported', () => {
  const described = describeInbound(makeMessage());
  assert.equal(described.kind, 'document');
  assert.equal(described.supported, true);
  assert.equal(described.fileName, 'invoices.csv');
  assert.equal(described.extension, 'csv');
});

test('every supported extension is accepted as a document', () => {
  for (const extension of SUPPORTED_EXTENSIONS) {
    const described = describeInbound(
      makeMessage({ _data: { fileName: `data.${extension}`, mimetype: 'application/octet-stream' } })
    );
    assert.equal(described.supported, true, `${extension} should be supported`);
  }
});

test('a PDF document is unsupported and says why', () => {
  const described = describeInbound(
    makeMessage({ _data: { fileName: 'bill.pdf', mimetype: 'application/pdf' } })
  );
  assert.equal(described.supported, false);
  assert.equal(described.reason, 'UNSUPPORTED_PDF');
  assert.match(described.detail, /PDF/i);
});

test('a legacy .xls document is unsupported and says why', () => {
  const described = describeInbound(
    makeMessage({ _data: { fileName: 'old.xls', mimetype: 'application/vnd.ms-excel' } })
  );
  assert.equal(described.supported, false);
  assert.equal(described.reason, 'UNSUPPORTED_LEGACY_XLS');
});

test('a photo is unsupported and reported as an image', () => {
  const described = describeInbound(
    makeMessage({ type: 'image', _data: { mimetype: 'image/jpeg' } })
  );
  assert.equal(described.kind, 'image');
  assert.equal(described.supported, false);
  assert.equal(described.reason, 'UNSUPPORTED_IMAGE');
});

test('a voice note is unsupported and reported as audio', () => {
  const described = describeInbound(
    makeMessage({ type: 'ptt', _data: { mimetype: 'audio/ogg; codecs=opus' } })
  );
  assert.equal(described.kind, 'audio');
  assert.equal(described.supported, false);
  assert.equal(described.reason, 'UNSUPPORTED_AUDIO');
});

test('a text message is described as text and carries no media', () => {
  const described = describeInbound(
    makeMessage({ type: 'chat', hasMedia: false, body: 'sent the file', _data: {} })
  );
  assert.equal(described.kind, 'text');
  assert.equal(described.supported, false);
  assert.equal(described.reason, 'TEXT_MESSAGE');
});

test('a document whose name is only in the caption is still recognised', () => {
  const described = describeInbound(
    makeMessage({ body: 'gstr2b.json', _data: { mimetype: 'application/json' } })
  );
  assert.equal(described.extension, 'json');
  assert.equal(described.supported, true);
});

test('a document with no usable name is unsupported rather than guessed', () => {
  const described = describeInbound(
    makeMessage({ body: '', _data: { mimetype: 'application/octet-stream' } })
  );
  assert.equal(described.supported, false);
  assert.equal(described.reason, 'UNKNOWN_FILE_TYPE');
});

// --- InboundQueue ----------------------------------------------------------

test('the queue keeps one entry per WhatsApp message id', () => {
  const queue = new InboundQueue();
  assert.equal(queue.add({ id: 'A', fileName: 'a.csv' }), true);
  assert.equal(queue.add({ id: 'A', fileName: 'a.csv' }), false);
  assert.equal(queue.list().length, 1);
});

test('acknowledging an entry removes it and is idempotent', () => {
  const queue = new InboundQueue();
  queue.add({ id: 'A' });
  assert.equal(queue.ack('A'), true);
  assert.equal(queue.list().length, 0);
  assert.equal(queue.ack('A'), false);
});

test('an acknowledged message id is not queued again', () => {
  const queue = new InboundQueue();
  queue.add({ id: 'A' });
  queue.ack('A');
  assert.equal(queue.add({ id: 'A' }), false, 're-delivery must not re-queue');
});

test('the queue is bounded and counts what it dropped', () => {
  const queue = new InboundQueue({ limit: 2 });
  queue.add({ id: 'A' });
  queue.add({ id: 'B' });
  queue.add({ id: 'C' });
  assert.deepEqual(queue.list().map((e) => e.id), ['B', 'C']);
  assert.equal(queue.dropped, 1);
});

test('listing never exposes the file bytes', () => {
  const queue = new InboundQueue();
  queue.add({ id: 'A', fileName: 'a.csv', data: 'c3VwZXItc2VjcmV0' });
  const [entry] = queue.list();
  assert.equal(entry.data, undefined);
  assert.equal(queue.get('A').data, 'c3VwZXItc2VjcmV0');
});

// --- createInboundHandler --------------------------------------------------

function makeHandler(options = {}) {
  const queue = options.queue || new InboundQueue();
  const downloads = [];
  const handler = createInboundHandler({
    config: makeConfig(options.config),
    queue,
    logger: () => {},
    download: options.download
      || (async (message) => {
        downloads.push(message);
        return { data: Buffer.from('a,b\n1,2\n').toString('base64'), mimetype: 'text/csv', filename: 'invoices.csv' };
      }),
  });
  return { handler, queue, downloads };
}

test('a document from the allowlisted sender is queued with its bytes', async () => {
  const { handler, queue } = makeHandler();
  await handler(makeMessage());
  const [entry] = queue.list();
  assert.equal(entry.supported, true);
  assert.equal(entry.fileName, 'invoices.csv');
  assert.equal(entry.fromMasked, '…8888');
  assert.equal(queue.get(entry.id).data, Buffer.from('a,b\n1,2\n').toString('base64'));
});

test('a message from any other number is ignored entirely', async () => {
  const { handler, queue, downloads } = makeHandler();
  await handler(makeMessage({ from: `${OTHER}@c.us`, id: { _serialized: 'false_other_BBB' } }));
  assert.equal(queue.list().length, 0);
  assert.equal(downloads.length, 0, 'media from an unlisted sender must not be downloaded');
});

test('a group message is ignored even if it quotes an allowlisted number', async () => {
  const { handler, queue } = makeHandler();
  await handler(makeMessage({ from: '919812345678-1600000000@g.us' }));
  assert.equal(queue.list().length, 0);
});

test('inbound intake disabled queues nothing', async () => {
  const { handler, queue, downloads } = makeHandler({ config: { inboundEnabled: false } });
  await handler(makeMessage());
  assert.equal(queue.list().length, 0);
  assert.equal(downloads.length, 0);
});

test('an unsupported attachment is queued as evidence but never downloaded', async () => {
  const { handler, queue, downloads } = makeHandler();
  await handler(makeMessage({ _data: { fileName: 'bill.pdf', mimetype: 'application/pdf' } }));
  const [entry] = queue.list();
  assert.equal(entry.supported, false);
  assert.equal(entry.reason, 'UNSUPPORTED_PDF');
  assert.equal(downloads.length, 0);
  assert.equal(queue.get(entry.id).data, null);
});

test('an attachment over the size cap is recorded as too large, not imported', async () => {
  const { handler, queue } = makeHandler({
    config: { maxDocumentBytes: 4 },
    download: async () => ({ data: Buffer.alloc(64).toString('base64'), mimetype: 'text/csv' }),
  });
  await handler(makeMessage());
  const [entry] = queue.list();
  assert.equal(entry.supported, false);
  assert.equal(entry.reason, 'FILE_TOO_LARGE');
  assert.equal(queue.get(entry.id).data, null);
});

test('a declared size over the cap is refused before downloading', async () => {
  const { handler, queue, downloads } = makeHandler({
    config: { maxDocumentBytes: 4 },
  });
  await handler(makeMessage({ _data: { fileName: 'big.csv', mimetype: 'text/csv', size: 99999 } }));
  assert.equal(queue.list()[0].reason, 'FILE_TOO_LARGE');
  assert.equal(downloads.length, 0);
});

test('a failed download is recorded as a failure, never as an import', async () => {
  const { handler, queue } = makeHandler({
    download: async () => {
      throw new Error('decryption failed');
    },
  });
  await handler(makeMessage());
  const [entry] = queue.list();
  assert.equal(entry.supported, false);
  assert.equal(entry.reason, 'DOWNLOAD_FAILED');
});

test('an empty download is recorded as a failure', async () => {
  const { handler, queue } = makeHandler({ download: async () => null });
  await handler(makeMessage());
  assert.equal(queue.list()[0].reason, 'DOWNLOAD_FAILED');
});

// The WhatsApp message id embeds the chat id, and MILAAN needs it verbatim
// to acknowledge the entry, so it is kept as-is (exactly like
// message_deliveries.provider_message_id). What must never appear is a
// readable sender field: the only phone exposure is the masked form.
test('a queued entry exposes the sender only in masked form', async () => {
  const { handler, queue } = makeHandler();
  await handler(makeMessage());
  const [entry] = queue.list();
  assert.equal(entry.fromMasked, '…8888');
  assert.equal(entry.from, undefined);
  assert.equal(entry.author, undefined);
  const withoutId = { ...entry, id: null };
  assert.equal(JSON.stringify(withoutId).includes(SENDER), false);
});
