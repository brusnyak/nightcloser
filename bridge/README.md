# Nightcloser WhatsApp bridge (linked device)

Baileys linked-device client for the owner's number. Inbound 1:1 text goes to `nightcloser.web` (`POST http://127.0.0.1:8096/chat`), the reply goes back to the same chat.

Files: `lib.js` (policy: filter, pacing, caps, handoff; pure), `session.js` (socket factory), `bridge.js` (service), `link.js` (one-time QR pairing), `deploy.sh` (rsync to VM), `systemd/` (units), `test/` (node --test, socket mocked).

## Behaviour (what the code enforces)
- Replies only to inbound 1:1 chats (`@s.whatsapp.net`, `@lid`). Groups, status, broadcast, newsletters: never.
- Never starts a chat. A chat becomes `active` only when its FIRST inbound text contains `CLOSER_TRIGGER` (default `Nightcloser`, case-insensitive). Any other first message marks the chat `ignored` forever.
- Inbound older than 60 s is skipped (no history replay). Owner's own messages (`fromMe`) are ignored, except `/pause` and `/resume` typed in a chat.
- Per chat: serialized, >= 2 s between replies, typing presence `composing` for 2-5 s + 40 ms per reply char, then `paused`, then send.
- Hard cap: 200 outbound per rolling hour. Hit = log `hard_stop`, process exits 0 (systemd does not restart it).
- Bot failure: 5 consecutive HTTP/parse failures = hard stop.
- Handoff: if the bot returns `handoff: true`, its reply is sent once, `~/closer-bridge/handoff.log` gets `<ISO time> <session hash>` (no text), and the chat is `handoff` (no more bot replies).
- Logs: JSON lines with event names, session hashes and counts. Message text is never logged.

## Files on the VM (`your-server`)
- `~/closer-bridge/` (mode 700): bridge code, `node_modules`.
- `~/closer-bridge/auth/` (mode 700): Baileys credentials. Never copy it off the VM.
- `~/closer-bridge/state.json` (mode 600): chat states, keyed by JID, needed to resume after restart.
- `~/closer-bridge/handoff.log`.
- `~/nightcloser/`: bot package (copied from `outpost/nightcloser`), runs with `python3 -m nightcloser.web` from `~/`.
- `~/booking/bot.env`: `OPENROUTER_API_KEY` (not printed anywhere).

## Deploy (safe, does not link or start the bridge)
```
nightcloser/bridge/deploy.sh          # rsync + npm ci + units + restart closer-bot only
ssh your-server 'curl -s http://127.0.0.1:8096/health'   # {"ok": true, "bot": "nightcloser"}
```

## Link the owner's number (owner runs this, once)
1. In Claude Code, type `! ssh -t your-server 'cd ~/closer-bridge && node link.js'` (or run it in a terminal).
2. On the phone (+421 900 000 000): WhatsApp > Settings > Linked devices > Link a device. Scan the QR in the terminal. A new QR appears about every 20 s if the first one expires.
3. Wait for `LINKED. Credentials saved.` (exit 0). Then start the bridge:
   `ssh your-server 'systemctl --user start closer-bridge.service'`
4. Check: `ssh your-server 'systemctl --user is-active closer-bridge.service; journalctl --user -u closer-bridge -n 20 --no-pager'` shows `open`, not message text.

Re-link if the phone unlinks the device: run step 1 again. Its `logged_out` event means the bridge is stopped on purpose.

## Pause / stop
- Pause one chat: owner types `/pause` in that chat from the phone. `/resume` restarts it.
- Stop everything: `ssh your-server 'systemctl --user stop closer-bridge.service'`.
- Revoke: phone > Linked devices > remove the device (also kills the session).
- Stop also the bot if needed: `ssh your-server 'systemctl --user stop closer-bot.service'`.

## Tests (no network)
```
cd nightcloser/bridge && npm ci && node --test test/
```
Expected: `# pass 17`, `# fail 0`.

## Ban risk (accepted by the owner, reduced in code)
Unofficial client: WhatsApp can ban the number at any time. The code limits volume and avoids unsolicited contact, but cannot make it safe. Keep the cap low, check for `handoff`/`hard_stop` events daily, and do not run bulk or cold sends from this number.
