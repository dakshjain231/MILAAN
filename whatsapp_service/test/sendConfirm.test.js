'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { pickSentMessageId, findInMessageStore, sendAndConfirm } = require('../src/sendConfirm');

const NOW = 1_800_000_000_000; // fixed clock (ms)
const T = Math.floor(NOW / 1000);
const BODY = 'MILAAN WhatsApp Web demo test.';

const msg = (id, extra = {}) => ({ id: { _serialized: id }, fromMe: true, body: BODY, timestamp: T, ...extra });

// Shape of a current WhatsApp Web MsgKey: no _serialized, serialized by toString().
class MsgKey {
  constructor(remote, id, fromMe = true) { this.remote = remote; this.id = id; this.fromMe = fromMe; }
  toString() { return `${this.fromMe}_${this.remote}_${this.id}_out`; }
}

function fakeClient({ sendResult, lookups }) {
  const calls = { send: 0, lookups: 0 };
  return {
    calls,
    async sendMessage() { calls.send += 1; return sendResult; },
    pupPage: {
      async evaluate(fn) {
        assert.equal(fn, findInMessageStore);
        const result = lookups[Math.min(calls.lookups, lookups.length - 1)];
        calls.lookups += 1;
        if (result instanceof Error) throw result;
        return result;
      },
    },
  };
}

const opts = (extra = {}) => ({ now: () => NOW, delayMs: 0, ...extra });

test('uses the id the client returns', async () => {
  const client = fakeClient({ sendResult: msg('true_91@c.us_A'), lookups: [[]] });
  assert.deepEqual(await sendAndConfirm(client, '91@c.us', BODY, opts()),
    { messageId: 'true_91@c.us_A', confirmedBy: 'send_result' });
  assert.equal(client.calls.lookups, 0);
});

test('reads the id from the message store when the client returns none', async () => {
  const client = fakeClient({ sendResult: undefined, lookups: [[msg('true_123@lid_B_out')]] });
  assert.deepEqual(await sendAndConfirm(client, '91@c.us', BODY, opts()),
    { messageId: 'true_123@lid_B_out', confirmedBy: 'message_store' });
  assert.equal(client.calls.send, 1); // never re-sends
});

test('a failing lookup is reported and retried, never re-sending', async () => {
  const seen = [];
  const client = fakeClient({ sendResult: undefined, lookups: [new Error('r'), [msg('late')]] });
  const result = await sendAndConfirm(client, '91@c.us', BODY, opts({ onLookupError: (e) => seen.push(e.message) }));
  assert.equal(result.messageId, 'late');
  assert.deepEqual(seen, ['r']);
  assert.equal(client.calls.send, 1);
});

test('older, received or different messages are never taken as this send', () => {
  const messages = [
    msg('old', { timestamp: T - 60 }),
    msg('incoming', { fromMe: false }),
    msg('other-text', { body: 'something else' }),
  ];
  assert.equal(pickSentMessageId(messages, BODY, T - 2), null);
  assert.equal(pickSentMessageId([...messages, msg('a'), msg('b', { timestamp: T + 1 })], BODY, T - 2), 'b');
});

test('stays unconfirmed (MISSING_MESSAGE_ID) when nothing is found, without re-sending', async () => {
  const client = fakeClient({ sendResult: undefined, lookups: [[msg('old', { timestamp: T - 60 })]] });
  await assert.rejects(sendAndConfirm(client, '91@c.us', BODY, opts({ attempts: 2 })),
    (error) => error.code === 'MISSING_MESSAGE_ID');
  assert.equal(client.calls.send, 1);
});

test('the in-page query serializes current WhatsApp keys via toString()', () => {
  const models = [
    { id: new MsgKey('123@lid', 'MINE'), body: BODY, t: T },
    { id: new MsgKey('123@lid', 'THEIRS', false), body: BODY, t: T },
    { id: new MsgKey('123@lid', 'OLDER'), body: BODY, t: T - 100 },
    { id: new MsgKey('123@lid', 'OTHER'), body: 'x', t: T },
    { id: { _serialized: 'legacy_key', fromMe: true }, body: BODY, t: T },
  ];
  global.window = { require: () => ({ Msg: { getModelsArray: () => models } }) };
  try {
    assert.deepEqual(findInMessageStore(BODY, T - 2).map((m) => m.id._serialized),
      ['true_123@lid_MINE_out', 'legacy_key']);
  } finally {
    delete global.window;
  }
});
