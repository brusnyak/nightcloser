// Policy tests: socket, bot and file I/O are mocked. No network, real clock never used.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  ChatStore, FailBreaker, HourlyCap, CHAT_GAP_MS, HOUR_MS, createInbound,
  isDirectChat, messageText, sessionId, TYPING_BASE_MS, PER_CHAR_MS,
} from '../lib.js';

const START = 1_800_000_000_000;

function rig({ botReply = { text: 'hi there', handoff: false }, botImpl, cap, breaker, due = [], nudgedImpl } = {}) {
  let t = START;
  const sleeps = [];
  const sent = [];
  const presence = [];
  const handoffLines = [];
  const halts = [];
  const bot = [];
  const store = new ChatStore();
  const posted = [];
  const dueBox = { items: due };
  const inbound = createInbound({
    trigger: 'Nightcloser',
    store,
    now: () => t,
    sleep: async (ms) => { sleeps.push(ms); t += ms; },
    rand: () => 0,
    cap: cap ?? new HourlyCap({ now: () => t }),
    breaker: breaker ?? new FailBreaker(),
    io: {
      presence: async (jid, type) => { presence.push([jid, type]); },
      send: async (jid, text) => { sent.push({ jid, text, at: t }); },
    },
    bot: async (body) => {
      bot.push(body);
      if (botImpl) return botImpl(body);
      return botReply;
    },
    followups: async () => dueBox.items,
    nudged: async (b) => { if (nudgedImpl) await nudgedImpl(b); posted.push(b); },
    handoffWrite: async (line) => { handoffLines.push(line); },
    onHalt: (reason) => halts.push(reason),
  });
  const msg = (jid, text, { fromMe = false, age = 0, group = false } = {}) => ({
    key: { remoteJid: jid, fromMe, id: `${Math.random()}` },
    message: { conversation: text },
    messageTimestamp: Math.floor((t - age * 1000) / 1000),
  });
  return { dueBox, posted, inbound, store, sent, presence, sleeps, bot, halts, handoffLines, msg, advance: (ms) => { t += ms; } };
}

const JID = '421900000001@s.whatsapp.net';
const OTHER = '421900000002@s.whatsapp.net';

test('sessionId is a stable 16-hex hash that does not contain the jid', () => {
  const id = sessionId(JID);
  assert.match(id, /^[0-9a-f]{16}$/);
  assert.equal(id, sessionId(JID));
  assert.ok(!id.includes('421900'));
});

test('only 1:1 chats qualify (groups, status, broadcast, newsletters rejected)', () => {
  assert.equal(isDirectChat(JID), true);
  assert.equal(isDirectChat('123@lid'), true);
  assert.equal(isDirectChat('120363@g.us'), false);
  assert.equal(isDirectChat('status@broadcast'), false);
  assert.equal(isDirectChat('12345@newsletter'), false);
  assert.equal(isDirectChat(undefined), false);
});

test('messageText reads conversation, extended text and ephemeral wrappers; null otherwise', () => {
  assert.equal(messageText({ message: { conversation: 'a' } }), 'a');
  assert.equal(messageText({ message: { extendedTextMessage: { text: 'b' } } }), 'b');
  assert.equal(messageText({ message: { ephemeralMessage: { message: { conversation: 'c' } } } }), 'c');
  assert.equal(messageText({ message: { imageMessage: {} } }), null);
});

test('chat with no trigger in first inbound is ignored forever, no bot call', async () => {
  const r = rig();
  await r.inbound.handle(r.msg(JID, 'hello, is this the bakery?'));
  await r.inbound.handle(r.msg(JID, 'Nightcloser are you there'));
  assert.equal(r.bot.length, 0);
  assert.equal(r.sent.length, 0);
  assert.equal(r.store.get(JID), 'ignored');
});

test('trigger in first inbound activates the chat and replies once', async () => {
  const r = rig();
  await r.inbound.handle(r.msg(JID, 'hey Nightcloser, price?'));
  assert.equal(r.bot.length, 1);
  assert.deepEqual(r.bot[0], { session: sessionId(JID), text: 'hey Nightcloser, price?' });
  assert.equal(r.sent.length, 1);
  assert.equal(r.sent[0].text, 'hi there');
  assert.deepEqual(r.presence, [[JID, 'composing'], [JID, 'paused']]);
});

test('trigger match is case-insensitive', async () => {
  const r = rig();
  await r.inbound.handle(r.msg(JID, 'NIGHTCLOSER'));
  assert.equal(r.sent.length, 1);
});

test('typing delay is 2 s base + 40 ms per reply char', async () => {
  const reply = 'x'.repeat(100);
  const r = rig({ botReply: { text: reply, handoff: false } });
  await r.inbound.handle(r.msg(JID, 'Nightcloser'));
  assert.equal(r.sleeps.length, 1);
  assert.equal(r.sleeps[0], TYPING_BASE_MS + PER_CHAR_MS * 100);
});

test('inbound older than 60 s is ignored and does not burn the first-message slot', async () => {
  const r = rig();
  await r.inbound.handle(r.msg(JID, 'Nightcloser old', { age: 120 }));
  assert.equal(r.bot.length, 0);
  assert.equal(r.store.get(JID), undefined);
  await r.inbound.handle(r.msg(JID, 'Nightcloser fresh'));
  assert.equal(r.sent.length, 1);
});

test('group and status messages never reach the bot', async () => {
  const r = rig();
  await r.inbound.handle(r.msg('120363@g.us', 'Nightcloser'));
  await r.inbound.handle(r.msg('status@broadcast', 'Nightcloser'));
  assert.equal(r.bot.length, 0);
  assert.equal(r.sent.length, 0);
});

test('owner fromMe normal messages are ignored (owner can chat from phone)', async () => {
  const r = rig();
  await r.inbound.handle(r.msg(JID, 'Nightcloser', { fromMe: true }));
  assert.equal(r.bot.length, 0);
  assert.equal(r.store.get(JID), undefined);
});

test('/pause from owner stops replies; /resume only resumes a paused chat', async () => {
  const r = rig();
  await r.inbound.handle(r.msg(JID, 'Nightcloser start'));
  assert.equal(r.sent.length, 1);
  await r.inbound.handle(r.msg(JID, '/pause', { fromMe: true }));
  await r.inbound.handle(r.msg(JID, 'are you there?'));
  assert.equal(r.bot.length, 1, 'paused chat gets no bot call');
  await r.inbound.handle(r.msg(JID, '/resume', { fromMe: true }));
  await r.inbound.handle(r.msg(JID, 'back again'));
  assert.equal(r.bot.length, 2);
  // /resume on a never-triggered chat must not activate it
  await r.inbound.handle(r.msg(OTHER, '/resume', { fromMe: true }));
  assert.equal(r.store.get(OTHER), undefined, '/resume alone never activates');
  await r.inbound.handle(r.msg(OTHER, 'hello, not the trigger'));
  assert.equal(r.store.get(OTHER), 'ignored');
  assert.equal(r.bot.length, 2);
});

test('pacing: consecutive replies to one chat are at least 2 s apart', async () => {
  const r = rig();
  await Promise.all([
    r.inbound.handle(r.msg(JID, 'Nightcloser one')),
    r.inbound.handle(r.msg(JID, 'two')),
  ]);
  assert.equal(r.sent.length, 2);
  assert.ok(r.sent[1].at - r.sent[0].at >= CHAT_GAP_MS);
});

test('hourly cap: 200 sends allowed per hour, the 201st hard-stops', async () => {
  let t = START;
  const cap = new HourlyCap({ max: 3, now: () => t });
  const r = rig({ cap });
  await r.inbound.handle(r.msg(JID, 'Nightcloser'));
  await r.inbound.handle(r.msg(OTHER, 'Nightcloser'));
  await r.inbound.handle(r.msg('421900000003@s.whatsapp.net', 'Nightcloser'));
  assert.equal(r.sent.length, 3);
  await r.inbound.handle(r.msg('421900000004@s.whatsapp.net', 'Nightcloser'));
  assert.equal(r.sent.length, 3, 'capped send refused');
  assert.deepEqual(r.halts, ['hourly_cap']);
  assert.equal(r.inbound.halted(), true);
  // after the rolling hour, a fresh cap has slots again
  assert.equal(new HourlyCap({ max: 3, now: () => t + HOUR_MS }).take(), true);
});

test('hourly cap window slides: old sends free slots after 1 h', () => {
  let t = START;
  const cap = new HourlyCap({ max: 1, now: () => t });
  assert.equal(cap.take(), true);
  assert.equal(cap.take(), false);
  t += HOUR_MS;
  assert.equal(cap.take(), true);
});

test('handoff: reply is sent, hash-only line written, no further bot calls in that chat', async () => {
  const r = rig({ botReply: { text: 'Passing you to a human.', handoff: true } });
  await r.inbound.handle(r.msg(JID, 'Nightcloser I want a refund'));
  assert.equal(r.sent.length, 1);
  assert.equal(r.handoffLines.length, 1);
  assert.match(r.handoffLines[0], /^\d{4}-\d{2}-\d{2}T[\d:.]+Z [0-9a-f]{16}\n$/);
  assert.ok(!r.handoffLines[0].includes('refund'), 'no message text in handoff log');
  await r.inbound.handle(r.msg(JID, 'hello?'));
  assert.equal(r.bot.length, 1);
  assert.equal(r.store.get(JID), 'handoff');
});

test('bot failure: no send, counted; 5 consecutive failures hard-stop', async () => {
  const r = rig({ botImpl: async () => { throw new TypeError('down'); } });
  for (let i = 0; i < 4; i++) await r.inbound.handle(r.msg(JID, 'Nightcloser'));
  assert.equal(r.sent.length, 0);
  assert.deepEqual(r.halts, []);
  await r.inbound.handle(r.msg(JID, 'again'));
  assert.deepEqual(r.halts, ['bot_failures']);
  const calls = r.bot.length;
  await r.inbound.handle(r.msg(OTHER, 'Nightcloser'));
  assert.equal(r.bot.length, calls, 'nothing runs after halt');
});

test('bot success resets the failure counter', async () => {
  let n = 0;
  const r = rig({
    botImpl: async () => {
      n += 1;
      if (n % 2 === 1) throw new Error('flaky');
      return { text: 'ok', handoff: false };
    },
  });
  for (let i = 0; i < 10; i++) await r.inbound.handle(r.msg(JID, 'Nightcloser'));
  assert.deepEqual(r.halts, []);
});

// ---- follow-up tick (fake clock: sleep advances t) ------------------------------------
const item = (jid, nudge = 1, text = 'Want me to run it on your products?') => ({ session: sessionId(jid), nudge, text });

test('nudge: sent to an active chat after typing delay, then POST /nudged, once only', async () => {
  const r = rig({ due: [item(JID)] });
  await r.inbound.handle(r.msg(JID, 'Nightcloser hi'));
  r.advance(3 * HOUR_MS);
  assert.equal(await r.inbound.nudgeTick(), 1);
  assert.equal(r.sent.length, 2);
  assert.equal(r.sent[1].text, 'Want me to run it on your products?');
  assert.deepEqual(r.posted, [{ session: sessionId(JID), text: 'Want me to run it on your products?' }]);
  assert.ok(r.presence.some(([, t]) => t === 'composing'));
  assert.equal(await r.inbound.nudgeTick(), 0, 'same nudge never re-sent in this process');
  assert.equal(r.sent.length, 2);
});

test('nudge: never to ignored, paused, handoff or unknown chats; never starts a chat', async () => {
  const r = rig({ due: [item(JID), item(OTHER), item('421900000003@s.whatsapp.net'), item('421900000009@s.whatsapp.net')] });
  r.store.set(JID, 'ignored');
  r.store.set(OTHER, 'paused');
  r.store.set('421900000003@s.whatsapp.net', 'handoff');
  assert.equal(await r.inbound.nudgeTick(), 0);
  assert.equal(r.sent.length, 0);
  assert.equal(r.posted.length, 0);
});

test('nudge: skipped when the visitor wrote after the tick began, or the owner pauses during typing', async () => {
  const r = rig({ due: [item(JID)] });
  await r.inbound.handle(r.msg(JID, 'Nightcloser hi'));
  r.advance(1000);
  const tick = r.inbound.nudgeTick();
  r.store.set(JID, 'paused');            // owner /pause lands while the nudge is typing
  assert.equal(await tick, 0);
  assert.equal(r.sent.length, 1);
  r.store.set(JID, 'active');
  r.advance(10);
  r.dueBox.items = [item(JID, 2)];
  const tick2 = r.inbound.nudgeTick();
  await r.inbound.handle(r.msg(JID, 'wait I have a question'));   // visitor writes mid-tick
  assert.equal(await tick2, 0);
  assert.equal(r.sent.filter((s) => s.text.startsWith('Want')).length, 0);
});

test('nudge: hourly cap reserve keeps slots for live replies; no halt', async () => {
  let t = START;
  const cap = new HourlyCap({ max: 22, now: () => t });
  const r = rig({ cap, due: [item(JID)] });
  await r.inbound.handle(r.msg(JID, 'Nightcloser hi'));      // 1 used, 21 remain
  assert.equal(await r.inbound.nudgeTick(), 1);               // 21 > 20 reserve: allowed, 20 remain
  r.dueBox.items = [item(JID, 2)];
  assert.equal(await r.inbound.nudgeTick(), 0);               // 20 <= reserve: refused
  assert.deepEqual(r.halts, []);
  assert.equal(r.inbound.halted(), false);
});

test('nudge: failed POST /nudged is retried next tick without re-sending', async () => {
  let fail = true;
  const r = rig({ due: [item(JID)], nudgedImpl: async () => { if (fail) throw new Error('down'); } });
  await r.inbound.handle(r.msg(JID, 'Nightcloser hi'));
  assert.equal(await r.inbound.nudgeTick(), 1);
  assert.equal(r.posted.length, 0);
  fail = false;
  assert.equal(await r.inbound.nudgeTick(), 0);
  assert.equal(r.sent.length, 2, 'not re-sent');
  assert.equal(r.posted.length, 1, 'post retried');
});

test('nudge: serialized behind a reply in the same chat, max 5 per tick, bad items ignored', async () => {
  const jids = Array.from({ length: 7 }, (_, i) => `42190000010${i}@s.whatsapp.net`);
  const r = rig({ due: [...jids.map((j) => item(j)), null, { session: 5, text: 'x' }, { session: 'zz', text: '' }] });
  for (const j of jids) await r.inbound.handle(r.msg(j, 'Nightcloser hi'));
  r.advance(4_000);
  assert.equal(await r.inbound.nudgeTick(), 5);
  assert.equal(r.sent.length, 7 + 5);
});

test('nudge: tick is a no-op without a followups source and after halt', async () => {
  const bare = createInbound({ trigger: 'N', io: {}, store: new ChatStore(), bot: async () => ({ text: 'x' }), cap: new HourlyCap(), breaker: new FailBreaker(), handoffWrite: async () => {}, onHalt: () => {} });
  assert.equal(await bare.nudgeTick(), 0);
  const r = rig({ due: [item(JID)], botImpl: async () => { throw new Error('x'); } });
  for (let i = 0; i < 5; i++) await r.inbound.handle(r.msg(JID, 'Nightcloser'));
  assert.equal(await r.inbound.nudgeTick(), 0);
});
