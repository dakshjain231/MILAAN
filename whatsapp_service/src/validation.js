'use strict';

const MAX_MESSAGE_LENGTH = 4096;
const MAX_IDEMPOTENCY_KEY_LENGTH = 200;
const MIN_DIGITS = 6;
const MAX_DIGITS = 20;

function normalizePhone(value) {
  return String(value || '').replace(/[^\d]/g, '');
}

function isPlausiblePhone(value) {
  const digits = normalizePhone(value);
  return new RegExp(`^[1-9]\\d{${MIN_DIGITS - 1},${MAX_DIGITS - 1}}$`).test(
    digits
  );
}

function maskPhone(value) {
  const digits = normalizePhone(value);
  if (!digits) return null;
  if (digits.length <= 4) return '\u2026';
  return `\u2026${digits.slice(-4)}`;
}

function validateSend(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, error: 'invalid_body' };
  }
  const to = normalizePhone(body.to);
  if (!isPlausiblePhone(to)) {
    return { ok: false, error: 'invalid_recipient' };
  }
  if (typeof body.message !== 'string') {
    return { ok: false, error: 'invalid_message' };
  }
  const message = body.message;
  if (!message.trim()) {
    return { ok: false, error: 'empty_message' };
  }
  if (message.length > MAX_MESSAGE_LENGTH) {
    return { ok: false, error: 'message_too_long' };
  }
  let idempotencyKey = null;
  if (body.idempotencyKey !== undefined && body.idempotencyKey !== null) {
    if (typeof body.idempotencyKey !== 'string') {
      return { ok: false, error: 'invalid_idempotency_key' };
    }
    idempotencyKey = body.idempotencyKey.trim();
    if (idempotencyKey.length > MAX_IDEMPOTENCY_KEY_LENGTH) {
      return { ok: false, error: 'invalid_idempotency_key' };
    }
    if (!idempotencyKey) idempotencyKey = null;
  }
  return { ok: true, to, message, idempotencyKey };
}

module.exports = {
  normalizePhone,
  isPlausiblePhone,
  maskPhone,
  validateSend,
  MAX_MESSAGE_LENGTH,
  MAX_IDEMPOTENCY_KEY_LENGTH,
};
