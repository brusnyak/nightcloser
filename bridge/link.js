// One-time pairing: prints a QR, exits 0 once the owner's phone has linked this device.
// Run: node link.js   (scan from WhatsApp > Settings > Linked devices > Link a device)
import qrcode from 'qrcode-terminal';
import { DisconnectReason } from '@whiskeysockets/baileys';
import { startSocket } from './session.js';

const MAX_RETRIES = 8;
let retries = 0;

async function run() {
  const sock = await startSocket();
  sock.ev.on('connection.update', async (u) => {
    if (u.qr) {
      console.log('\nScan this QR with the owner phone (+421 900 000 000): WhatsApp > Linked devices > Link a device\n');
      qrcode.generate(u.qr, { small: true });
    }
    if (u.connection === 'open') {
      await new Promise((r) => setTimeout(r, 3000)); // let creds.update flush to disk
      console.log('LINKED. Credentials saved. You can now start closer-bridge.service.');
      process.exit(0);
    }
    if (u.connection === 'close') {
      const code = u.lastDisconnect?.error?.output?.statusCode;
      if (code === DisconnectReason.loggedOut) { console.error('Logged out or QR not scanned. Run node link.js again.'); process.exit(1); }
      // 515 restartRequired is normal right after pairing: reconnect with the saved creds.
      if (++retries > MAX_RETRIES) { console.error(`Gave up after ${MAX_RETRIES} reconnects (last code ${code}).`); process.exit(1); }
      setTimeout(run, 2000);
    }
  });
  sock.ev.on('creds.update', () => {});
}

run().catch((e) => { console.error('link failed:', e.name); process.exit(1); });
