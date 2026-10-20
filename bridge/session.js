// Baileys socket factory shared by link.js and bridge.js. Auth state lives in CLOSER_BRIDGE_DIR/auth (mode 700).
import { mkdir } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import makeWASocket, { useMultiFileAuthState } from '@whiskeysockets/baileys';

export const BRIDGE_DIR = process.env.CLOSER_BRIDGE_DIR || path.join(os.homedir(), 'closer-bridge');
export const AUTH_DIR = path.join(BRIDGE_DIR, 'auth');

export async function startSocket() {
  await mkdir(AUTH_DIR, { recursive: true, mode: 0o700 });
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const sock = makeWASocket({
    auth: state,
    syncFullHistory: false,      // no history replay
    markOnlineOnConnect: false,  // do not show the bot as "online" on the owner's phone
  });
  sock.ev.on('creds.update', saveCreds);
  return sock;
}
