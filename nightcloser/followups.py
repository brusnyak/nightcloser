"""Follow-up cadence for Nightcloser. Pure: due_followups() only RETURNS what is due and never sends.
The caller sends each item, then calls Closer.mark_nudged(session_id, text).

Rules (all must hold):
- the agent spoke last (customer silent), the session is not handed off, and no stop word was said
- at most MAX_NUDGES nudges in total
- nudge n is due NUDGE_AFTER[n] seconds after the agent's last non-nudge message
- now is inside the 24 h customer-service window opened by the customer's last message
Nudge 2 is due 20 h after the anchor so it still lands inside the 24 h window (owner-visible default; window opens at the customer's last message)."""
import zlib

from nightcloser.core import MAX_NUDGES, NUDGE_AFTER, NUDGES, STOP_RE, WINDOW_24H, norm


def _said_stop(s):
    return s.stopped or any(m["role"] == "user" and STOP_RE.search(norm(m["content"])) for m in s.history)


def due_followups(sessions, now):
    out = []
    for s in sessions:
        if s.handoff or _said_stop(s) or s.nudges_sent >= MAX_NUDGES:
            continue
        if s.last_speaker != "agent" or s.last_user_at is None or s.last_reply_at is None:
            continue
        if now >= s.last_user_at + WINDOW_24H:
            continue
        if now < s.last_reply_at + NUDGE_AFTER[s.nudges_sent]:
            continue
        variants = NUDGES[s.lang][s.nudges_sent]  # stage 1 (value + question) or stage 2 (call link + stop offer)
        text = variants[zlib.crc32(s.id.encode()) % len(variants)]
        out.append({"session_id": s.id, "nudge": s.nudges_sent + 1, "lang": s.lang, "text": text})
    return out
