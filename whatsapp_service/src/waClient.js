'use strict';

const QRCode = require('qrcode');
const { sendAndConfirm } = require('./sendConfirm');
const { maskForLog } = require('./server');
const { createInboundHandler } = require('./inbound');

function createWhatsAppClient({ config, state, inboundQueue = null }) {
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

  // Inbound intake: queue documents from allowlisted senders for MILAAN to
  // poll. The handler never replies, so an incoming message can never
  // trigger an outgoing one.
  if (inboundQueue && config.inboundEnabled !== false) {
    const onMessage = createInboundHandler({
      config,
      queue: inboundQueue,
      download: (message) => message.downloadMedia(),
    });
    client.on('message', (message) => {
      Promise.resolve(onMessage(message)).catch((error) => {
        console.error(
          'MILAAN WhatsApp inbound: intake failed: '
            + maskForLog(error && error.name) + ': '
            + maskForLog(error && error.message)
        );
      });
    });
  }

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
      const { messageId, confirmedBy } = await sendAndConfirm(client, chatId, message, {
        onLookupError: (error) => console.error(
          `MILAAN WhatsApp send: message-store lookup failed: ${maskForLog(error && error.name)}: `
            + maskForLog(error && error.message)
        ),
      });
      return { messageId: String(messageId).slice(0, 200), confirmedBy };
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
