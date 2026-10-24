'use strict';

// Inbound intake for the linked WhatsApp Web session.
//
// This module only *collects* what an allowlisted sender sent. It never
// sends, never replies and never touches the MILAAN database: MILAAN polls
// `GET /inbound` over loopback, fetches the bytes for one entry, imports it
// and then acknowledges it. Keeping the bridge dumb means a parser change in
// Python needs no change here, and a crash in Python cannot lose a document
// (an unacknowledged entry stays queued).
//
// What is NOT downloaded: anything the Python importers cannot read
// (PDF, .xls, photos, voice notes, video) and anything over the size cap.
// Those arrive as queue entries carrying only a reason, so the dashboard can
// name the precise blocker instead of silently dropping the message.

const crypto = require('node:crypto');
const { maskPhone, normalizePhone } = require('./validation');

// Extensions the MILAAN importers actually parse (invoice_import.py and
// gstr2b.py / gstr2b_portal.py). Keep in sync with inbound.py.
const SUPPORTED_EXTENSIONS = ['csv', 'xlsx', 'json'];

const DEFAULT_QUEUE_LIMIT = 50;
const MAX_FILE_NAME_LENGTH = 180;

// One reason code per rejection, with the operator-facing explanation. The
// wording names what the sender should do instead; MILAAN shows it verbatim.
const REASONS = {
  UNSUPPORTED_PDF:
    'PDF attachments are not parsed. Send the purchase register as CSV/XLSX, '
    + 'or the GSTR-2B as the portal JSON/Excel export.',
  UNSUPPORTED_LEGACY_XLS:
    'Legacy .xls files are not parsed. Save the file as .xlsx or CSV and send it again.',
  UNSUPPORTED_IMAGE:
    'Photos of invoices are not read on this channel: no vision/OCR service is '
    + 'configured for the WhatsApp Web bridge. Send a CSV/XLSX purchase register instead.',
  UNSUPPORTED_AUDIO:
    'Voice notes are not transcribed: no speech-to-text service is configured. '
    + 'Send the data as a CSV/XLSX file instead.',
  UNSUPPORTED_VIDEO: 'Video messages are never processed as accounting data.',
  UNSUPPORTED_EXTENSION:
    'This file type is not parsed. Supported attachments are CSV, XLSX and JSON.',
  UNKNOWN_FILE_TYPE:
    'The attachment has no recognisable file name or type, so it was not parsed. '
    + 'Resend it with a .csv, .xlsx or .json file name.',
  TEXT_MESSAGE: 'Text message recorded for the audit trail; it carries no file to import.',
  FILE_TOO_LARGE: 'The attachment is larger than the inbound size limit and was not downloaded.',
  DOWNLOAD_FAILED:
    'The attachment could not be downloaded from WhatsApp, so nothing was imported.',
};

function reasonDetail(code) {
  return REASONS[code] || 'This message was not imported.';
}

// Media types we recognise. Anything else is reported as its raw type.
const IMAGE_TYPES = new Set(['image', 'sticker']);
const AUDIO_TYPES = new Set(['audio', 'ptt', 'voice']);
const VIDEO_TYPES = new Set(['video', 'gif']);

function cleanFileName(value) {
  // Never trust a sender-supplied path: keep the base name only.
  const text = String(value || '').trim().replace(/\\/g, '/');
  const base = text.slice(text.lastIndexOf('/') + 1);
  return base.replace(/[\u0000-\u001f\u007f]/g, '').slice(0, MAX_FILE_NAME_LENGTH);
}

function extensionOf(fileName) {
  const name = cleanFileName(fileName);
  const dot = name.lastIndexOf('.');
  if (dot <= 0 || dot === name.length - 1) return '';
  return name.slice(dot + 1).toLowerCase();
}

function messageId(message) {
  const id = message && message.id;
  if (!id) return null;
  if (typeof id === 'string') return id;
  if (typeof id._serialized === 'string' && id._serialized) return id._serialized;
  if (typeof id.id === 'string' && id.id) return id.id;
  const text = typeof id.toString === 'function' ? String(id.toString()) : '';
  return text && text !== '[object Object]' ? text : null;
}

// Only a one-to-one chat with a real phone number counts. Groups (@g.us),
// broadcasts and channels are refused outright.
function senderDigits(message) {
  const from = String((message && message.from) || '');
  if (!from.endsWith('@c.us')) return null;
  const digits = normalizePhone(from.slice(0, -'@c.us'.length));
  return digits || null;
}

function unsupportedExtensionReason(extension) {
  if (extension === 'pdf') return 'UNSUPPORTED_PDF';
  if (extension === 'xls' || extension === 'xlsm') return 'UNSUPPORTED_LEGACY_XLS';
  return 'UNSUPPORTED_EXTENSION';
}

/**
 * Classify one incoming message without downloading anything.
 * Returns { kind, fileName, mimeType, extension, declaredSize, supported,
 *           reason, detail }.
 */
function describeInbound(message) {
  const data = (message && message._data) || {};
  const type = String((message && message.type) || '').toLowerCase();
  const mimeType = String(data.mimetype || (message && message.mimetype) || '')
    .split(';')[0]
    .trim()
    .toLowerCase() || null;
  const declaredSize = Number(data.size || data.filesize || (message && message.filesize)) || null;

  const describe = (kind, reason, fileName = null, extension = '') => ({
    kind,
    fileName,
    mimeType,
    extension,
    declaredSize,
    supported: reason === null,
    reason,
    detail: reason === null ? null : reasonDetail(reason),
  });

  if (!(message && message.hasMedia)) {
    return describe('text', 'TEXT_MESSAGE');
  }
  if (IMAGE_TYPES.has(type)) return describe('image', 'UNSUPPORTED_IMAGE');
  if (AUDIO_TYPES.has(type)) return describe('audio', 'UNSUPPORTED_AUDIO');
  if (VIDEO_TYPES.has(type)) return describe('video', 'UNSUPPORTED_VIDEO');

  // A document's name may live in _data.fileName, on the message, or (when a
  // client sends it as a caption) in the body. Try each, in that order.
  const candidates = [data.fileName, data.filename, message.filename, message.body];
  let fileName = '';
  let extension = '';
  for (const candidate of candidates) {
    const cleaned = cleanFileName(candidate);
    if (!cleaned) continue;
    const found = extensionOf(cleaned);
    if (found) {
      fileName = cleaned;
      extension = found;
      break;
    }
    if (!fileName) fileName = cleaned;
  }

  if (!extension) {
    return describe('document', 'UNKNOWN_FILE_TYPE', fileName || null);
  }
  if (!SUPPORTED_EXTENSIONS.includes(extension)) {
    return describe('document', unsupportedExtensionReason(extension), fileName, extension);
  }
  return describe('document', null, fileName, extension);
}

/**
 * Bounded FIFO of inbound entries, keyed by WhatsApp message id.
 *
 * A message id is remembered after it is acknowledged, so a re-delivery from
 * WhatsApp cannot queue the same document twice. When the queue is full the
 * oldest entry is dropped and counted: MILAAN surfaces `dropped` rather than
 * letting an overflow pass unnoticed.
 */
class InboundQueue {
  constructor({ limit = DEFAULT_QUEUE_LIMIT, seenLimit = 500 } = {}) {
    this.limit = Math.max(1, Number(limit) || DEFAULT_QUEUE_LIMIT);
    this.seenLimit = Math.max(this.limit, Number(seenLimit) || 500);
    this.entries = new Map();
    this.seen = new Set();
    this.dropped = 0;
  }

  add(entry) {
    const id = entry && entry.id;
    if (!id) return false;
    if (this.entries.has(id) || this.seen.has(id)) return false;
    this.entries.set(id, entry);
    this.seen.add(id);
    if (this.seen.size > this.seenLimit) {
      this.seen.delete(this.seen.values().next().value);
    }
    while (this.entries.size > this.limit) {
      const oldest = this.entries.keys().next().value;
      this.entries.delete(oldest);
      this.dropped += 1;
    }
    return true;
  }

  // Metadata only: the file bytes are fetched one entry at a time.
  list() {
    return [...this.entries.values()].map(({ data, ...meta }) => meta);
  }

  get(id) {
    return this.entries.get(id) || null;
  }

  ack(id) {
    return this.entries.delete(id);
  }

  get size() {
    return this.entries.size;
  }
}

/**
 * Build the `message` handler for the WhatsApp Web client.
 *
 * `download` is injected so the handler is testable without Chromium; in
 * production it is `(message) => message.downloadMedia()`.
 */
function createInboundHandler({ config, queue, download, logger = console.error, now = () => Date.now() }) {
  const allowed = new Set(
    (config.inboundSenders || config.allowedRecipients || [])
      .map((value) => normalizePhone(value))
      .filter(Boolean)
  );
  const maxBytes = Number(config.maxDocumentBytes) || 0;

  return async function onMessage(message) {
    if (!config.inboundEnabled) return null;

    const id = messageId(message);
    if (!id) return null;

    const digits = senderDigits(message);
    // Fail closed: an unlisted sender, a group or a channel is ignored
    // completely — no download, no queue entry, no record of its content.
    if (!digits || !allowed.has(digits)) return null;

    const described = describeInbound(message);
    let data = null;
    let reason = described.reason;
    let size = described.declaredSize;
    let sha256 = null;

    if (reason === null && maxBytes && described.declaredSize && described.declaredSize > maxBytes) {
      reason = 'FILE_TOO_LARGE';
    }

    if (reason === null) {
      try {
        const media = await download(message);
        const base64 = media && typeof media.data === 'string' ? media.data : '';
        if (!base64) throw new Error('no media payload');
        const bytes = Buffer.from(base64, 'base64');
        if (!bytes.length) throw new Error('empty media payload');
        if (maxBytes && bytes.length > maxBytes) {
          reason = 'FILE_TOO_LARGE';
          size = bytes.length;
        } else {
          data = base64;
          size = bytes.length;
          sha256 = crypto.createHash('sha256').update(bytes).digest('hex');
        }
      } catch (error) {
        reason = 'DOWNLOAD_FAILED';
        // Never log the sender's number or the file contents.
        logger(
          'MILAAN WhatsApp inbound: download failed for '
            + `${described.extension || 'attachment'}: `
            + String((error && error.message) || 'unknown error').slice(0, 200)
        );
      }
    }

    const entry = {
      id,
      fromMasked: maskPhone(digits),
      kind: described.kind,
      fileName: described.fileName,
      mimeType: described.mimeType,
      extension: described.extension || null,
      size: size || null,
      sha256,
      supported: reason === null,
      reason,
      detail: reason === null ? null : reasonDetail(reason),
      text: described.kind === 'text' ? String((message && message.body) || '').slice(0, 4096) : null,
      timestamp: Number(message && message.timestamp) || null,
      receivedAt: new Date(now()).toISOString(),
      data,
    };
    queue.add(entry);
    return entry;
  };
}

module.exports = {
  InboundQueue,
  describeInbound,
  createInboundHandler,
  cleanFileName,
  extensionOf,
  reasonDetail,
  SUPPORTED_EXTENSIONS,
  REASONS,
  DEFAULT_QUEUE_LIMIT,
};
