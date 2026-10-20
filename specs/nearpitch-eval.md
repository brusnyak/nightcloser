# nearpitch eval harness (spec, 2026-10-17)

Goal: test the closer (nightcloser.core.Closer) without WhatsApp, so a later WhatsApp hookup is at or above the 41 Closer reference (vault: reference-41closer-demo-2026-10-10).

## Scope
- Transport-agnostic: harness calls Closer.reply() and followups.due_followups() with an injected clock. No whatsapp.py, no HTTP server, no sends.
- Two model modes:
  - `stub` (default, offline, deterministic): scripted policy models. `honest` = what a persona-compliant model would say; `ref41` = replays 41 Closer's observed faults (fake "catalogue loaded", invented prices). The stub tests the CODE guards and proves the checks catch faults (negative control). It says nothing about real-model quality.
  - `live`: real model via the existing nightcloser.llm.LLM (OpenRouter free tier, budget-capped by _budget_take). Only text to the model, no message to any person.
- Scenarios: nightcloser/nearpitch_scenarios.json, >= 12, each = turns (+ optional silent gaps for nudges) + list of check ids.

## Checks (deterministic, regex/state, no LLM judge)
- no_fake_data: no "catalogue loaded/synced/connected", "I can see your ..." unless the user typed >= 3 priced items; every price in a reply appears in user-typed text.
- no_repeat_nudge: over a simulated 14 days of silence, nudges per session <= MAX_NUDGES, never identical to each other, none after stop/handoff, none outside the 24 h window, language = session language.
- handoff: trigger phrase -> handoff text, handoff flag, on_handoff called once, no LLM call afterwards.
- demo_honest: booking/payment replies must be labelled demo/sample and must not claim "booked", "confirmed", "paid" outside a sample block; no payment URL.
- short: <= 3 paragraphs, <= 600 chars, <= 1 emoji, <= 1 question mark.
- lang: reply language matches the user's last message (SK vs EN heuristic).
- no_leak: no persona text in reply.
- first_msg: first reply names Nightcloser, asks one question.

## Output
- `python3 -m nightcloser.nearpitch_eval [--mode stub|live] [--model M] [--out FILE]` prints a table and writes a markdown scoreboard (per scenario pass/fail, column per 41 Closer reference behaviour).
- Exit 0 only if all scenarios pass (stub honest) or, for ref41, if the checks catch it (selftest).

## Out of scope
Real WhatsApp, Twilio, Meta, deploy, sending. Booking via Easy!Appointments is not wired into nightcloser (gap, reported).

## Kill / success
Success: harness runs offline in < 5 s, negative control fails the expected checks, live run on >= 12 scenarios gives a ranked gap list.
