'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { ConnectionState } = require('../src/state');

test('state moves through qr to ready and clears the QR', () => {
  let tick = 0;
  const state = new ConnectionState(() => (tick += 1));
  state.set('qr', { qr: 'data:image/png;base64,abc' });
  assert.equal(state.snapshot().qrAvailable, true);
  state.set('authenticating');
  assert.equal(state.qrDataUrl(), null);
  state.set('ready', { phone: '919999999999' });
  const snapshot = state.snapshot();
  assert.equal(snapshot.ready, true);
  assert.equal(snapshot.phoneMasked, '\u20269999');
  assert.equal(snapshot.qrAvailable, false);
});

test('state rejects unknown status', () => {
  const state = new ConnectionState();
  assert.throws(() => state.set('banana'));
});
