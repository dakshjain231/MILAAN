'use strict';

// Test harness, not part of the service. Starts the real HTTP server with a
// pre-seeded inbound queue and prints the port it bound, so the Python
// integration test can drive the actual wire protocol without Chromium, a
// WhatsApp session or any send path.
//
//   node tools/inboundHarness.js <token>
//
// Reads queue entries as JSON on argv[3] (optional).

const { createServer } = require('../src/server');
const { ConnectionState } = require('../src/state');
const { InboundQueue } = require('../src/inbound');

const token = process.argv[2];
const seed = process.argv[3] ? JSON.parse(process.argv[3]) : [];

const queue = new InboundQueue();
for (const entry of seed) queue.add(entry);

const state = new ConnectionState();
state.set('ready', { phone: '918888888888' });

const server = createServer({
  config: { token, allowedRecipients: ['918888888888'], inboundEnabled: true },
  state,
  // No adapter: a send attempt would throw NOT_READY. Nothing calls it.
  inboundQueue: queue,
  logger: () => {},
});

server.listen(0, '127.0.0.1', () => {
  process.stdout.write(`PORT=${server.address().port}\n`);
});

process.on('SIGTERM', () => server.close(() => process.exit(0)));
