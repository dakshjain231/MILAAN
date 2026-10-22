'use strict';

const { maskPhone } = require('./validation');

const STATUSES = new Set([
  'disconnected',
  'qr',
  'authenticating',
  'ready',
  'error',
]);

class ConnectionState {
  constructor(now = () => Date.now()) {
    this.now = now;
    this.status = 'disconnected';
    this.qr = null;
    this.phone = null;
    this.error = null;
    this.since = this.now();
  }

  set(status, patch = {}) {
    if (!STATUSES.has(status)) {
      throw new Error(`unsupported connection status ${status}`);
    }
    this.status = status;
    this.since = this.now();
    if (status === 'qr') {
      this.qr = typeof patch.qr === 'string' ? patch.qr : this.qr;
    } else {
      this.qr = null;
    }
    this.phone = Object.prototype.hasOwnProperty.call(patch, 'phone')
      ? patch.phone
      : this.phone;
    this.error = Object.prototype.hasOwnProperty.call(patch, 'error')
      ? patch.error
      : null;
    return this.snapshot();
  }

  qrDataUrl() {
    return this.qr;
  }

  snapshot() {
    return {
      status: this.status,
      ready: this.status === 'ready',
      qrAvailable: Boolean(this.qr),
      phoneMasked: maskPhone(this.phone),
      since: new Date(this.since).toISOString(),
      error: this.error || null,
    };
  }
}

module.exports = { ConnectionState, STATUSES };
