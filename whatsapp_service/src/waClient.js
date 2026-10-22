'use strict';

const QRCode = require('qrcode');

function createWhatsAppClient({ config, state }) {
  // Loaded lazily so the HTTP layer and its tests never require Chromium.
  const { Client, LocalAuth } = require('whatsapp-web.js');

  const client = new Client({
    authStrategy: new LocalAuth({ dataPath: config.sessionPath }),
    puppeteer: {
      headless: config.headless,
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
    },
  });

  client.on('qr', async (qr) => {
    try {
      const dataUrl = await QRCode.toDataURL(qr, { margin: 1, width: 320 });
      state.set('qr', { qr: dataUrl });
    } catch (error) {
      state.set('error', { error: 'QR_RENDER_FAILED' });
    }
  });

  client.on('authenticated', () => state.set('authenticating'));

  client.on('ready', () => {
    let phone = null;
    try {
      phone = client.info && client.info.wid ? client.info.wid.user : null;
    } catch (error) {
      phone = null;
    }
    state.set('ready', { phone });
  });

  client.on('auth_failure', () => state.set('error', { error: 'AUTH_FAILURE' }));
  client.on('disconnected', () =>
    state.set('disconnected', { error: 'DISCONNECTED' })
  );

  return {
    async initialize() {
      await client.initialize();
    },
    async sendText(phoneDigits, message) {
      if (state.snapshot().status !== 'ready') {
        const error = new Error('client is not ready');
        error.code = 'NOT_READY';
        throw error;
      }
      const chatId = `${phoneDigits}@c.us`;
      const sent = await client.sendMessage(chatId, message);
      const messageId = sent && sent.id
        ? (sent.id._serialized || sent.id.id || null)
        : null;
      if (!messageId) {
        const error = new Error('provider did not return a message id');
        error.code = 'MISSING_MESSAGE_ID';
        throw error;
      }
      return { messageId: String(messageId).slice(0, 200) };
    },
    async destroy() {
      try {
        await client.destroy();
      } catch (error) {
        // Best-effort shutdown; never surface client internals.
      }
    },
  };
}

module.exports = { createWhatsAppClient };
