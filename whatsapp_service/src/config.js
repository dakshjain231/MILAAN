'use strict';

const path = require('node:path');
const { isPlausiblePhone, normalizePhone } = require('./validation');

const DEFAULT_PORT = 8801;
const MIN_TOKEN_LENGTH = 16;
const LOOPBACK_BINDS = new Set(['127.0.0.1', 'localhost', '::1']);

// Inbound attachment size cap. WhatsApp itself allows up to 100 MB, but the
// MILAAN importers only ever read small tabular files, and the bytes travel
// as base64 in a JSON body, so a tight ceiling keeps both sides predictable.
const DEFAULT_MAX_DOCUMENT_BYTES = 5 * 1024 * 1024;
const MAX_DOCUMENT_BYTES_CEILING = 25 * 1024 * 1024;

function parseBoolean(value, fallback) {
  if (value === undefined || value === null || value === '') {
    return fallback;
  }
  const normalized = String(value).trim().toLowerCase();
  if (['1', 'true', 'yes', 'on'].includes(normalized)) return true;
  if (['0', 'false', 'no', 'off'].includes(normalized)) return false;
  return fallback;
}

function isLoopbackBind(bind) {
  return LOOPBACK_BINDS.has(String(bind || '').trim().toLowerCase());
}

// Comma- or semicolon-separated; spaces inside an entry are kept, so
// "+91 88888 88888" stays one number.
function parseRecipients(raw) {
  return String(raw || '')
    .split(/[,;]/)
    .map((piece) => piece.trim())
    .filter(Boolean);
}

function isAllowedRecipient(config, value) {
  const digits = normalizePhone(value);
  if (!digits) return false;
  const allowed = (config && config.allowedRecipients) || [];
  return allowed.includes(digits);
}

function loadConfig(env = process.env, rootDir = path.join(__dirname, '..')) {
  const issues = [];
  const token = String(env.MILAAN_WA_SERVICE_TOKEN || '').trim();
  if (!token) {
    issues.push('MILAAN_WA_SERVICE_TOKEN is missing');
  } else if (token.length < MIN_TOKEN_LENGTH) {
    issues.push(
      `MILAAN_WA_SERVICE_TOKEN must be at least ${MIN_TOKEN_LENGTH} characters`
    );
  }

  const rawPort = env.MILAAN_WA_SERVICE_PORT;
  const port = rawPort === undefined || rawPort === ''
    ? DEFAULT_PORT
    : Number.parseInt(String(rawPort), 10);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    issues.push('MILAAN_WA_SERVICE_PORT must be an integer from 1 to 65535');
  }

  const bind = String(env.MILAAN_WA_SERVICE_BIND || '127.0.0.1').trim();
  if (!isLoopbackBind(bind)) {
    issues.push(
      'MILAAN_WA_SERVICE_BIND must be 127.0.0.1, localhost or ::1 '
        + '(this service is localhost-only)'
    );
  }

  const headless = parseBoolean(env.MILAAN_WA_HEADLESS, true);

  const testRecipient = String(env.MILAAN_WA_TEST_RECIPIENT || '').trim();
  if (testRecipient && !isPlausiblePhone(testRecipient)) {
    issues.push('MILAAN_WA_TEST_RECIPIENT must be a plausible phone number');
  }

  // Recipient allowlist. This service refuses /send for anything not listed,
  // independently of the caller, so a bug or a stray client in MILAAN can
  // never reach a supplier through this bridge. An unset allowlist falls back
  // to the test recipient; an empty one blocks every send (fail closed).
  const rawAllowed = parseRecipients(env.MILAAN_WA_ALLOWED_RECIPIENTS);
  if (rawAllowed.some((value) => !isPlausiblePhone(value))) {
    issues.push(
      'MILAAN_WA_ALLOWED_RECIPIENTS contains a number that is not plausible'
    );
  }
  const allowedSource = rawAllowed.length
    ? rawAllowed
    : (testRecipient ? [testRecipient] : []);
  const allowedRecipients = [
    ...new Set(
      allowedSource.filter(isPlausiblePhone).map((value) => normalizePhone(value))
    ),
  ];

  // Inbound intake. Senders default to the send allowlist, so the pilot
  // number is the only one whose attachments are read; an explicit
  // MILAAN_WA_INBOUND_SENDERS can narrow (never silently widen) that.
  const inboundEnabled = parseBoolean(env.MILAAN_WA_INBOUND_ENABLED, true);
  const rawInboundSenders = parseRecipients(env.MILAAN_WA_INBOUND_SENDERS);
  if (rawInboundSenders.some((value) => !isPlausiblePhone(value))) {
    issues.push(
      'MILAAN_WA_INBOUND_SENDERS contains a number that is not plausible'
    );
  }
  const inboundSenderSource = rawInboundSenders.length
    ? rawInboundSenders
    : allowedSource;
  const inboundSenders = [
    ...new Set(
      inboundSenderSource
        .filter(isPlausiblePhone)
        .map((value) => normalizePhone(value))
    ),
  ];
  if (inboundEnabled && !inboundSenders.length) {
    issues.push(
      'inbound intake is enabled but no sender is allowed '
        + '(MILAAN_WA_INBOUND_SENDERS, MILAAN_WA_ALLOWED_RECIPIENTS or '
        + 'MILAAN_WA_TEST_RECIPIENT)'
    );
  }

  const rawMaxBytes = env.MILAAN_WA_INBOUND_MAX_BYTES;
  let maxDocumentBytes = DEFAULT_MAX_DOCUMENT_BYTES;
  if (rawMaxBytes !== undefined && rawMaxBytes !== '') {
    maxDocumentBytes = Number.parseInt(String(rawMaxBytes), 10);
    if (
      !Number.isInteger(maxDocumentBytes)
      || maxDocumentBytes < 1
      || maxDocumentBytes > MAX_DOCUMENT_BYTES_CEILING
    ) {
      issues.push(
        'MILAAN_WA_INBOUND_MAX_BYTES must be an integer from 1 to '
          + MAX_DOCUMENT_BYTES_CEILING
      );
    }
  }

  const sessionPath = path.resolve(rootDir, '.wwebjs_auth');
  const cachePath = path.resolve(rootDir, '.wwebjs_cache');

  return {
    token,
    port,
    bind,
    headless,
    testRecipient: testRecipient || null,
    allowedRecipients,
    inboundEnabled,
    inboundSenders,
    maxDocumentBytes,
    sessionPath,
    cachePath,
    issues,
    ready: issues.length === 0,
  };
}

module.exports = {
  loadConfig,
  isLoopbackBind,
  isAllowedRecipient,
  parseBoolean,
  parseRecipients,
  DEFAULT_PORT,
  MIN_TOKEN_LENGTH,
  LOOPBACK_BINDS,
  DEFAULT_MAX_DOCUMENT_BYTES,
  MAX_DOCUMENT_BYTES_CEILING,
};
