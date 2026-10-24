'use strict';

const { loadConfig } = require('./config');
const { ConnectionState } = require('./state');
const { createServer } = require('./server');
const { InboundQueue } = require('./inbound');

async function main() {
  const config = loadConfig();
  if (!config.ready) {
    console.error(
      'MILAAN WhatsApp service not configured: ' + config.issues.join('; ')
    );
    process.exitCode = 2;
    return;
  }

  let createWhatsAppClient;
  try {
    ({ createWhatsAppClient } = require('./waClient'));
  } catch (error) {
    console.error(
      'MILAAN WhatsApp service dependencies are missing. '
        + 'Run: cd whatsapp_service && npm install'
    );
    process.exitCode = 2;
    return;
  }

  const state = new ConnectionState();
  const inboundQueue = config.inboundEnabled ? new InboundQueue() : null;
  let adapter;
  try {
    adapter = createWhatsAppClient({ config, state, inboundQueue });
  } catch (error) {
    console.error(
      'MILAAN WhatsApp service could not create the client. '
        + 'Run: cd whatsapp_service && npm install'
    );
    process.exitCode = 2;
    return;
  }
  const server = createServer({ config, state, adapter, inboundQueue });

  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(config.port, config.bind, resolve);
  });
  console.log(
    `MILAAN WhatsApp Web service listening on `
      + `http://${config.bind}:${config.port} (localhost only)`
  );
  console.log('Open MILAAN Settings or the delivery tab to scan the QR code.');

  const shutdown = async () => {
    console.log('Stopping MILAAN WhatsApp Web service...');
    server.close();
    await adapter.destroy();
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);

  try {
    await adapter.initialize();
  } catch (error) {
    console.error(
      'MILAAN WhatsApp service could not initialize the client: '
        + (error && error.code ? error.code : 'initialize_failed')
    );
  }
}

main().catch((error) => {
  console.error(
    'MILAAN WhatsApp service failed to start: '
      + (error && error.code ? error.code : 'start_failed')
  );
  process.exitCode = 1;
});
