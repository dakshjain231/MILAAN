'use strict';

// Why this exists: whatsapp-web.js 1.34.7 sends a message, then looks it up
// with Msg.get(newMsgKey._serialized). Current WhatsApp Web message keys have
// no `_serialized` property (their serialized form comes from toString(),
// e.g. "true_<chat>@lid_<id>_out"), so that lookup returns nothing and
// sendMessage() resolves to undefined even though the message was sent.
//
// When that happens, read WhatsApp Web's own message store: the newest
// outgoing message with exactly this text, created after the send started,
// and use its key. The send is never repeated. If no such message is found,
// the outcome stays unconfirmed (reported as unknown, never as failed, and
// never retried automatically).

function idOf(message) {
  if (!message || !message.id) return null;
  return message.id._serialized || message.id.id || null;
}

function pickSentMessageId(messages, body, sinceSeconds) {
  const matches = (messages || []).filter(
    (m) => m && m.fromMe && m.body === body
      && Number(m.timestamp) >= sinceSeconds && idOf(m)
  );
  if (!matches.length) return null;
  matches.sort((a, b) => Number(b.timestamp) - Number(a.timestamp));
  return idOf(matches[0]);
}

// Runs inside the WhatsApp Web page (puppeteer evaluate); returns plain data.
function findInMessageStore(body, sinceSeconds) {
  const keyOf = (key) => {
    if (!key) return null;
    if (typeof key._serialized === 'string' && key._serialized) return key._serialized;
    const text = typeof key.toString === 'function' ? String(key.toString()) : '';
    return text && text !== '[object Object]' ? text : null;
  };
  return window.require('WAWebCollections').Msg.getModelsArray()
    .filter((m) => m && m.id && m.id.fromMe && m.body === body && Number(m.t) >= sinceSeconds)
    .map((m) => ({ id: { _serialized: keyOf(m.id) }, fromMe: true, body: m.body, timestamp: m.t }));
}

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function sendAndConfirm(client, chatId, body, options = {}) {
  const now = options.now || Date.now;
  const attempts = options.attempts || 3;
  const delayMs = options.delayMs === undefined ? 1000 : options.delayMs;
  const onLookupError = options.onLookupError || (() => {});
  const sinceSeconds = Math.floor(now() / 1000) - 2;

  const sent = await client.sendMessage(chatId, body);
  const direct = idOf(sent);
  if (direct) return { messageId: direct, confirmedBy: 'send_result' };

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (delayMs) await pause(delayMs);
    try {
      const messages = await client.pupPage.evaluate(findInMessageStore, body, sinceSeconds);
      const found = pickSentMessageId(messages, body, sinceSeconds);
      if (found) return { messageId: found, confirmedBy: 'message_store' };
    } catch (error) {
      onLookupError(error);
    }
  }
  const error = new Error('provider did not return a message id');
  error.code = 'MISSING_MESSAGE_ID';
  throw error;
}

module.exports = { idOf, pickSentMessageId, findInMessageStore, sendAndConfirm };
