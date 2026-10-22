'use strict';

const crypto = require('node:crypto');

function tokenDigest(value) {
  return crypto.createHash('sha256').update(String(value), 'utf8').digest();
}

function tokenMatches(provided, expected) {
  if (typeof provided !== 'string' || typeof expected !== 'string') {
    return false;
  }
  if (!provided || !expected) return false;
  return crypto.timingSafeEqual(tokenDigest(provided), tokenDigest(expected));
}

function extractToken(headers) {
  const authorization = headers && headers.authorization;
  if (typeof authorization === 'string') {
    const match = /^Bearer\s+(.+)$/i.exec(authorization.trim());
    if (match) return match[1].trim();
  }
  const direct = headers && headers['x-milaan-service-token'];
  if (typeof direct === 'string' && direct.trim()) return direct.trim();
  return null;
}

module.exports = { tokenMatches, extractToken };
