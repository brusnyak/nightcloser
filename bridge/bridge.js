// Nightcloser WhatsApp linked-device bridge. Run: node bridge.js (after link.js has paired the device).
import { appendFile, mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { DisconnectReason } from '@whiskeysockets/baileys';
import { AUTH_DIR, BRIDGE_DIR, startSocket } from './session.js';
import { BOT_TIMEOUT_MS, NUDGE_TICK_MS, ChatStore, FailBreaker, HourlyCap, createInbound } from './lib.js';

const TRIGGER = process.env.CLOSER_TRIGGER || 'Nightcloser';
const BOT_URL = process.env.CLOSER_URL || 'http://127.0.0.1:8096/chat';
const BASE_URL = new URL(BOT_URL);
const NUDGES_ON = process.env.CLOSER_NUDGES !== '0';   // CLOSER_NUDGES=0 turns the follow-up tick off
const HANDOFF_LOG = path.join(BRIDGE_DIR, 'handoff.log');
const STATE_FILE = path.join(BRIDGE_DIR, 'state.json');
const RECONNECT_MS = 5_000;
const MAX_CONNECT_FAILURES = 10;

const log = (event, fields = {}) => console.log(JSON.stringify({ t: new Date().toISOString(), event, ...fields }));

async function loadState() {
  try { return JSON.parse(await readFile(STATE_FILE, 'utf8')); } catch { return {}; }
}

async function saveState(obj) {
  const tmp = `${STATE_FILE}.tmp`;
  await writeFile(tmp, JSON.stringify(obj), { mode: 0o600 });
  await rename(tmp, STATE_FILE);
}

async function bot(body) {
  const res = await fetch(BOT_URL, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(BOT_TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`bot http ${res.status}`);
  return res.json();
}

async function followups() {
  const res = await fetch(new URL('/followups', BASE_URL), { signal: AbortSignal.timeout(10_000) });
  if (!res.ok) throw new Error(`followups http ${res.status}`);
  return (await res.json()).items;
}

async function nudged(body) {
  const res = await fetch(new URL('/nudged', BASE_URL), {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(10_000),
  });
  if (!res.ok) throw new Error(`nudged http ${res.status}`);
}

await mkdir(BRIDGE_DIR, { recursive: true, mode: 0o700 });
const store = new ChatStore(await loadState(), (obj) => {
  saveState(obj).catch((e) => log('state_save_fail', { err: e.name }));
});

let sock = null;
const io = {
  presence: (jid, type) => sock.sendPresenceUpdate(type, jid),
  send: (jid, text) => sock.sendMessage(jid, { text }),
};

const inbound = createInbound({
  trigger: TRIGGER,
  io,
  store,
  bot,
  cap: new HourlyCap(),
  breaker: new FailBreaker(),
  followups: NUDGES_ON ? followups : null,
  nudged,
  handoffWrite: (line) => appendFile(HANDOFF_LOG, line, { mode: 0o600 }),
  onHalt: (reason) => {
    // Hard stop. Exit 0 so systemd (Restart=on-failure) does not restart it. Restart manually after review.
    log('hard_stop', { reason });
    process.exit(0);
  },
  log,
});

let failures = 0;

async function connect() {
  const s = await startSocket();
  sock = s;
  s.ev.on('connection.update', (u) => {
    if (u.connection === 'open') { failures = 0; log('open', { auth: path.basename(AUTH_DIR) }); return; }
    if (u.connection !== 'close') return;
    const code = u.lastDisconnect?.error?.output?.statusCode;
    if (code === DisconnectReason.loggedOut) {
      log('logged_out', { note: 'relink with: node link.js' });
      process.exit(0);
    }
    failures += 1;
    if (failures > MAX_CONNECT_FAILURES) { log('connect_giveup', { code }); process.exit(1); }
    log('reconnect', { code, n: failures });
    setTimeout(() => connect().catch((e) => { log('connect_error', { err: e.name }); process.exit(1); }), RECONNECT_MS);
  });
  s.ev.on('messages.upsert', ({ messages, type }) => {
    if (type !== 'notify') return; // ignore history/append batches
    for (const m of messages) {
      inbound.handle(m).catch((e) => log('error', { err: e?.name ?? 'Error' }));
    }
  });
}

if (NUDGES_ON) setInterval(() => inbound.nudgeTick().catch((e) => log('error', { err: e?.name ?? 'Error' })), NUDGE_TICK_MS);
log('start', { nudges: NUDGES_ON, trigger_len: TRIGGER.length, bot: new URL(BOT_URL).host });
await connect();
