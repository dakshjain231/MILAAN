'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  normalizePhone,
  isPlausiblePhone,
  maskPhone,
  validateSend,
  MAX_MESSAGE_LENGTH,
} = require('../src/validation');

test('normalizePhone keeps digits only', () => {
  assert.equal(normalizePhone('+91 99999-99999'), '919999999999');
});

test('isPlausiblePhone rejects short and leading-zero numbers', () => {
  assert.equal(isPlausiblePhone('9999999999'), true);
  assert.equal(isPlausiblePhone('12345'), false);
  assert.equal(isPlausiblePhone('0000000000'), false);
});

test('maskPhone exposes only the last four digits', () => {
  assert.equal(maskPhone('919999999999'), '\u20269999');
  assert.equal(maskPhone(''), null);
});

test('validateSend accepts a normal message', () => {
  const parsed = validateSend({
    to: '+91 99999 99999',
    message: 'Hello',
    idempotencyKey: 'abc',
  });
  assert.equal(parsed.ok, true);
  assert.equal(parsed.to, '919999999999');
  assert.equal(parsed.message, 'Hello');
  assert.equal(parsed.idempotencyKey, 'abc');
});

test('validateSend rejects bad input', () => {
  assert.equal(validateSend(null).ok, false);
  assert.equal(validateSend({ to: '12', message: 'x' }).error, 'invalid_recipient');
  assert.equal(validateSend({ to: '9999999999', message: '' }).error, 'empty_message');
  assert.equal(
    validateSend({
      to: '9999999999',
      message: 'x'.repeat(MAX_MESSAGE_LENGTH + 1),
    }).error,
    'message_too_long'
  );
  assert.equal(
    validateSend({ to: '9999999999', message: 'x', idempotencyKey: 5 }).error,
    'invalid_idempotency_key'
  );
});
