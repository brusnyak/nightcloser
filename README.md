# Nightcloser

A WhatsApp sales-agent demo you can test as the customer. It answers in seconds, asks one question at a time, quotes only the prices it was given, offers a booking link when the visitor shows intent, follows up twice when they go quiet, and hands over to a human when it should.

Live demo page: https://www.recareo.uk/demo/closer/ (one WhatsApp button, you play the customer).

## What is in here
| Path | What |
|---|---|
| `nightcloser/` | Python bot: `core.py` (sessions, guards, hand-off), `persona.md` (the rules), `kb.py` (reads a website you send), `followups.py` (follow-up schedule), `web.py` (HTTP endpoint), `wa_cloud.py` / `twilio_wa.py` (transports) |
| `bridge/` | Node service that links a WhatsApp number (Baileys linked device) to the bot, with pacing, hourly caps and a hard stop |
| `site/` | The landing page (static, no external requests) |
| `tests/`, `bridge/test/` | 50 Python tests, 24 Node tests |
| `nightcloser/nearpitch_eval.py` | Scripted-chat eval (25 cases, offline stub mode, optional live model mode) |

## Behaviour that is enforced in code, not only in the prompt
- Never states a price, contact detail or link the visitor did not type (or that is not on the website they sent), except the one booking link.
- One question per reply, short WhatsApp-sized messages, no claim that anything is booked or paid: a demo shows steps as labelled samples.
- Website reading is server-side with SSRF protection: http/https only, private/loopback/metadata addresses refused (also after redirects), 6 s timeout, 400 KB cap, text only, injected as untrusted text.
- Hand-off on refund, complaint, legal, medical or "talk to a person"; the bot stops selling.
- Follow-ups: at most 2, only in chats the visitor started, never after a stop word, never during an owner pause, within the 24 h window, with an hourly send cap and slots reserved for live replies.
- Sessions live in RAM with a 24 h TTL, message text is never logged.

## Run it
```
export OPENROUTER_API_KEY=...                 # free models work
export NIGHTCLOSER_DEMO_NUMBER=421900000000   # your test number (digits)
python3 -m nightcloser.web                    # 127.0.0.1:8096  POST /chat {session,text}  GET /health /followups
python3 -m pytest -q tests && python3 -m nightcloser.nearpitch_eval --mode stub
cd bridge && npm ci && node --test test/      # then see bridge/README.md to link a number
```
Config: `CLOSER_MODEL` (default `minimax/minimax-m3.1-flash:free`), `CLOSER_FALLBACK_MODEL`, `CLOSER_PORT`, `CLOSER_TRIGGER` (first message must contain it), `CLOSER_NUDGES=0` to switch follow-ups off. Edit `site/index.html` and replace the placeholder number `421000000000` in the WhatsApp links.

## Measured (2026-10-20, 5-turn chats on a small VM, free models)
Reply latency 1.3 to 3.9 s with the default model (a reasoning model before the change took 9 to 26 s). Bot process about 24 MB, bridge about 100 MB.

## Limits
- It is a demo line: nothing is stored as an order, booked or charged.
- The linked-device bridge is an unofficial WhatsApp client: use a dedicated number you can afford to lose, or the official Cloud API (`wa_cloud.py`).
- Free models rate-limit; the bot retries, falls back to a second model, and fails closed with a plain apology.
- Not yet tested against a live WhatsApp socket for follow-up delivery (tested with a fake clock).

MIT licensed. Built by Yegor Brusnyak.
