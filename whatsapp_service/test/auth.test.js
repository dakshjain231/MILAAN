'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { tokenMatches, extractToken } = require('../src/auth');

test('tokenMatches accepts identical tokens only', () => {
  assert.equal(tokenMatches('abcdefghijklmnop', 'abcdefghijklmnop'), true);
  assert.equal(tokenMatches('abcdefghijklmnop', 'abcdefghijklmnoX'), false);
  assert.equal(tokenMatches('short', 'a-much-longer-token'), false);
  assert.equal(tokenMatches(null, 'abcdefghijklmnop'), false);
  assert.equal(tokenMatches('abcdefghijklmnop', ''), false);
});

test('extractToken supports bearer and direct headers', () => {
  assert.equal(
    extractToken({ authorization: 'Bearer secret-token' }),
    'secret-token'
  );
  assert.equal(
    extractToken({ 'x-milaan-service-token': 'direct-token' }),
    'direct-token'
  );
  assert.equal(extractToken({}), null);
  assert.equal(extractToken({ authorization: 'Basic x' }), null);
});
