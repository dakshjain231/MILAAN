'use strict';

// Configuration tests for the recipient allowlist. Nothing here starts the
// WhatsApp client or sends a message.

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  loadConfig,
  isAllowedRecipient,
  parseRecipients,
} = require('../src/config');

const TOKEN = 'test-service-token-1234';

function env(overrides = {}) {
  return { MILAAN_WA_SERVICE_TOKEN: TOKEN, ...overrides };
}

test('the allowlist falls back to the test recipient', () => {
  const config = loadConfig(env({ MILAAN_WA_TEST_RECIPIENT: '+91 88888 88888' }));
  assert.equal(config.ready, true);
  assert.deepEqual(config.allowedRecipients, ['918888888888']);
  assert.equal(isAllowedRecipient(config, '+91-88888-88888'), true);
  assert.equal(isAllowedRecipient(config, '918111222333'), false);
});

test('an explicit allowlist replaces the test recipient', () => {
  const config = loadConfig(
    env({
      MILAAN_WA_TEST_RECIPIENT: '919999999999',
      MILAAN_WA_ALLOWED_RECIPIENTS: '+918888888888, 918888888888',
    })
  );
  assert.deepEqual(config.allowedRecipients, ['918888888888']);
  assert.equal(isAllowedRecipient(config, '919999999999'), false);
});

test('an implausible allowlist entry is a configuration issue', () => {
  const config = loadConfig(env({ MILAAN_WA_ALLOWED_RECIPIENTS: '12' }));
  assert.equal(config.ready, false);
  assert.ok(
    config.issues.some((issue) => issue.includes('MILAAN_WA_ALLOWED_RECIPIENTS'))
  );
  assert.deepEqual(config.allowedRecipients, []);
});

test('no configured recipient means an empty allowlist', () => {
  const config = loadConfig(env());
  assert.deepEqual(config.allowedRecipients, []);
  assert.equal(isAllowedRecipient(config, '918888888888'), false);
  assert.equal(isAllowedRecipient(config, ''), false);
});

test('recipient lists split on commas and semicolons, keeping inner spaces', () => {
  assert.deepEqual(parseRecipients(' 1, 2 ;3  '), ['1', '2', '3']);
  assert.deepEqual(parseRecipients('+91 88888 88888'), ['+91 88888 88888']);
  assert.deepEqual(parseRecipients(''), []);
  assert.deepEqual(parseRecipients(undefined), []);
});
