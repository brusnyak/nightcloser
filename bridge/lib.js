// Pure policy for the Nightcloser bridge: filter, pacing, caps, handoff. No sockets, no network, no real clock.
// Never log message text. Logs carry session hashes and counts only.
import { createHash } from 'node:crypto';

export const HOUR_MS = 3_600_000;
export const MAX_PER_HOUR = 200;        // hard stop on outbound messages
export const CHAT_GAP_MS = 2_000;       // max 1 reply per chat per 2 s
export const MAX_AGE_S = 60;            // ignore inbound older than this (no history replay)
export const TYPING_BASE_MS = 800;      // composing presence: 0.8-2 s + 10 ms per reply char (was 2-5 s + 40 ms: a 400-char reply waited 20 s)
export const TYPING_JITTER_MS = 1_200;
export const PER_CHAR_MS = 10;
export const BOT_FAIL_LIMIT = 5;        // consecutive bot HTTP failures -> hard stop
export const BOT_TIMEOUT_MS = 30_000;
export const NUDGE_TICK_MS = 60_000;     // bridge.js polls /followups at this interval
export const NUDGE_RESERVE = 20;         // nudges only while this many hourly-cap slots stay free for live replies
export const NUDGE_MAX_PER_TICK = 5;

export const sessionId = (jid) => createHash('sha1').update(jid).digest('hex').slice(0, 16);

// 1:1 chats only. Groups (@g.us), status/broadcast, newsletters are rejected.
export function isDirectChat(jid) {
  return typeof jid === 'string' && (jid.endsWith('@s.whatsapp.net') || jid.endsWith('@lid'));
}

export function messageText(msg) {
  let m = msg?.message;
  if (!m) return null;
  m = m.ephemeralMessage?.message ?? m.viewOnceMessageV2?.message ?? m.viewOnceMessage?.message ?? m;
  const t = m.conversation ?? m.extendedTextMessage?.text ?? null;
  return typeof t === 'string' ? t : null;
}

export function ageSeconds(msg, nowMs) {
  const ts = msg?.messageTimestamp;
  const s = ts && typeof ts === 'object' && typeof ts.toNumber === 'function' ? ts.toNumber() : Number(ts);
  return Number.isFinite(s) ? nowMs / 1000 - s : Infinity;
}

export const hasTrigger = (text, trigger) => text.toLowerCase().includes(trigger.toLowerCase());

// Rolling 1 h window on outbound sends. take() reserves a slot or returns false.
export class HourlyCap {
  constructor({ max = MAX_PER_HOUR, windowMs = HOUR_MS, now = Date.now } = {}) {
    Object.assign(this, { max, windowMs, now, stamps: [] });
  }
  take() {
    const t = this.now();
    this.stamps = this.stamps.filter((s) => t - s < this.windowMs);
    if (this.stamps.length >= this.max) return false;
    this.stamps.push(t);
    return true;
  }
  remaining() {
    const t = this.now();
    this.stamps = this.stamps.filter((s) => t - s < this.windowMs);
    return this.max - this.stamps.length;
  }
}

// Returns true once `limit` consecutive failures have happened.
export class FailBreaker {
  constructor(limit = BOT_FAIL_LIMIT) { Object.assign(this, { limit, n: 0 }); }
  ok() { this.n = 0; }
  fail() { this.n += 1; return this.n >= this.limit; }
}

// Chat state per 1:1 JID: undefined (never seen) | 'ignored' | 'active' | 'paused' | 'handoff'.
export class ChatStore {
  constructor(initial = {}, onChange = () => {}) {
    this.map = new Map(Object.entries(initial));
    this.onChange = onChange;
  }
  get(jid) { return this.map.get(jid); }
  jids() { return [...this.map.keys()]; }
  set(jid, state) { this.map.set(jid, state); this.onChange(Object.fromEntries(this.map)); }
}

/**
 * io:       { presence(jid, 'composing'|'paused'), send(jid, text) }   bound to the live socket
 * bot:      async ({session, text}) => {text, handoff}                 POST to nightcloser.web
 * handoffWrite: async (line) => void                                    appends to handoff.log
 * sleep, now, rand, onHalt, log: injected for tests
 */
export function createInbound({
  trigger, io, store, bot, cap, breaker, handoffWrite, onHalt,
  now = Date.now, sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
  rand = Math.random, log = () => {},
  followups = null, nudged = async () => {},   // followups: async () => [{session,nudge,text}]; nudged: async ({session,text})
}) {
  let halted = false;
  const lastSent = new Map();
  const chains = new Map();
  const lastInbound = new Map();   // jid -> ms of the visitor's latest message (a nudge never goes out over a fresh message)
  const nudgeDone = new Set();     // `${session}:${nudge}` sent in this process
  const unposted = [];             // sent nudges whose POST /nudged failed: retried each tick, never re-sent

  function halt(reason) {
    if (halted) return;
    halted = true;
    log('halt', { reason });
    onHalt(reason);
  }

  async function callBot(jid, text) {
    try {
      const r = await bot({ session: sessionId(jid), text });
      if (typeof r?.text !== 'string') throw new TypeError('bad bot reply');
      breaker.ok();
      return r;
    } catch (e) {
      log('bot_fail', { sid: sessionId(jid), err: e?.name ?? 'Error' });
      if (breaker.fail()) halt('bot_failures');
      return null;
    }
  }

  async function replyNow(jid, text) {
    if (halted || store.get(jid) !== 'active') return;
    const r = await callBot(jid, text);
    if (!r || !r.text || halted || store.get(jid) !== 'active') return;

    const gap = now() - (lastSent.get(jid) ?? -Infinity);
    if (gap < CHAT_GAP_MS) await sleep(CHAT_GAP_MS - gap);

    await io.presence(jid, 'composing');
    await sleep(TYPING_BASE_MS + rand() * TYPING_JITTER_MS + PER_CHAR_MS * [...r.text].length);
    await io.presence(jid, 'paused');
    if (halted || store.get(jid) !== 'active') return; // owner paused during typing

    if (!cap.take()) return halt('hourly_cap');
    await io.send(jid, r.text);
    lastSent.set(jid, now());
    log('sent', { sid: sessionId(jid) });

    if (r.handoff) {
      store.set(jid, 'handoff');
      await handoffWrite(`${new Date(now()).toISOString()} ${sessionId(jid)}\n`);
      log('handoff', { sid: sessionId(jid) });
    }
  }

  function reply(jid, text) {
    const job = (chains.get(jid) ?? Promise.resolve())
      .then(() => replyNow(jid, text))
      .catch((e) => log('error', { sid: sessionId(jid), err: e?.name ?? 'Error' }));
    chains.set(jid, job);
    return job;
  }

  async function nudgeNow(jid, item, tickStart) {
    const ok = () => !halted && store.get(jid) === 'active' && (lastInbound.get(jid) ?? -Infinity) < tickStart;
    if (!ok()) return false;
    if (cap.remaining() <= NUDGE_RESERVE) { log('nudge_skip', { sid: item.session, why: 'cap_reserve' }); return false; }
    const gap = now() - (lastSent.get(jid) ?? -Infinity);
    if (gap < CHAT_GAP_MS) await sleep(CHAT_GAP_MS - gap);
    await io.presence(jid, 'composing');
    await sleep(TYPING_BASE_MS + rand() * TYPING_JITTER_MS + PER_CHAR_MS * [...item.text].length);
    await io.presence(jid, 'paused');
    if (!ok()) return false;                      // visitor wrote, owner paused or handed off while typing
    if (!cap.take()) { log('nudge_skip', { sid: item.session, why: 'cap' }); return false; }
    await io.send(jid, item.text);
    lastSent.set(jid, now());
    nudgeDone.add(`${item.session}:${item.nudge}`);
    log('nudge_sent', { sid: item.session, n: item.nudge });
    return true;
  }

  // Called by bridge.js every NUDGE_TICK_MS. Sends due follow-ups to chats that are 'active' only; never starts a chat.
  async function nudgeTick() {
    if (halted || !followups) return 0;
    const tickStart = now();
    for (let i = unposted.length; i > 0; i--) {
      const p = unposted.shift();
      try { await nudged(p); } catch { unposted.push(p); }
    }
    let items;
    try { items = await followups(); } catch (e) { log('followups_fail', { err: e?.name ?? 'Error' }); return 0; }
    if (!Array.isArray(items)) return 0;
    const byId = new Map(store.jids().filter((j) => isDirectChat(j) && store.get(j) === 'active').map((j) => [sessionId(j), j]));
    let sent = 0;
    for (const it of items) {
      if (sent >= NUDGE_MAX_PER_TICK || halted) break;
      if (typeof it?.session !== 'string' || typeof it?.text !== 'string' || !it.text) continue;
      const jid = byId.get(it.session);
      if (!jid || nudgeDone.has(`${it.session}:${it.nudge}`)) continue;
      const job = (chains.get(jid) ?? Promise.resolve())
        .then(() => nudgeNow(jid, it, tickStart))
        .catch((e) => { log('error', { sid: it.session, err: e?.name ?? 'Error' }); return false; });
      chains.set(jid, job);
      if (!(await job)) continue;
      sent += 1;
      const rec = { session: it.session, text: it.text };
      try { await nudged(rec); } catch (e) { unposted.push(rec); log('nudged_post_fail', { sid: it.session }); }
    }
    return sent;
  }

  async function handle(m) {
    if (halted) return;
    const jid = m?.key?.remoteJid;
    if (!isDirectChat(jid)) return;
    const text = messageText(m);
    const sid = sessionId(jid);

    if (m.key.fromMe) {
      if (text === '/pause') store.set(jid, 'paused');
      else if (text === '/resume' && store.get(jid) === 'paused') store.set(jid, 'active');
      else return;
      log('owner_cmd', { sid, cmd: text });
      return;
    }

    if (text === null) return;
    if (ageSeconds(m, now()) > MAX_AGE_S) return;

    const state = store.get(jid);
    if (state === undefined) {
      if (!hasTrigger(text, trigger)) { store.set(jid, 'ignored'); return; }
      store.set(jid, 'active');
    } else if (state !== 'active') {
      return;
    }
    lastInbound.set(jid, now());
    await reply(jid, text);
  }

  return { handle, nudgeTick, halted: () => halted };
}
