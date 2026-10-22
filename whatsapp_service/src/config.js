'use strict';

const path = require('node:path');
const { isPlausiblePhone } = require('./validation');

const DEFAULT_PORT = 8801;
const MIN_TOKEN_LENGTH = 16;
const LOOPBACK_BINDS = new Set(['127.0.0.1', 'localhost', '::1']);

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

  const sessionPath = path.resolve(rootDir, '.wwebjs_auth');
  const cachePath = path.resolve(rootDir, '.wwebjs_cache');

  return {
    token,
    port,
    bind,
    headless,
    testRecipient: testRecipient || null,
    sessionPath,
    cachePath,
    issues,
    ready: issues.length === 0,
  };
}

module.exports = {
  loadConfig,
  isLoopbackBind,
  parseBoolean,
  DEFAULT_PORT,
  MIN_TOKEN_LENGTH,
  LOOPBACK_BINDS,
};
