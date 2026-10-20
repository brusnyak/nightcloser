"""WhatsApp Cloud API webhook adapter. Transport only: parse inbound, verify signature, call core, send via injectable `send`.
Live use needs a Meta app (token, phone-number-id, app secret, verify token). Not wired to a real number."""
import hashlib, hmac, json, os, urllib.request


def verify_challenge(query, verify_token):
    """GET /webhook?hub.mode=subscribe&hub.verify_token=..&hub.challenge=.. -> (status, body)"""
    if query.get("hub.mode") == "subscribe" and hmac.compare_digest(query.get("hub.verify_token", ""), verify_token) and query.get("hub.challenge"):
        return 200, query["hub.challenge"]
    return 403, "forbidden"


def signature_ok(raw, header, app_secret):
    if not header or not header.startswith("sha256="):
        return False
    want = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header[7:])


def parse_inbound(payload):
    """-> list of (wa_id, text). Ignores statuses, non-text types (image/audio/..) get a flag text of None."""
    out = []
    for e in payload.get("entry", []):
        for ch in e.get("changes", []):
            v = ch.get("value", {})
            for m in v.get("messages", []) or []:
                text = m.get("text", {}).get("body") if m.get("type") == "text" else None
                out.append((m.get("from"), text, m.get("id")))
    return out


def graph_send(to, text, token=None, phone_number_id=None):
    r = urllib.request.Request(f"https://graph.facebook.com/v21.0/{phone_number_id}/messages", method="POST",
        data=json.dumps({"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    urllib.request.urlopen(r, timeout=15).read()


class Webhook:
    def __init__(self, bot, send, app_secret, verify_token):
        self.bot, self.send, self.secret, self.vt = bot, send, app_secret, verify_token
        self.seen = set()  # Meta retries: dedupe by message id (bounded)

    def get(self, query):
        return verify_challenge(query, self.vt)

    def post(self, raw, sig_header):
        if not signature_ok(raw, sig_header, self.secret):
            return 401, "bad signature"
        try:
            msgs = parse_inbound(json.loads(raw))
        except (ValueError, AttributeError):
            return 400, "bad payload"
        for wa_id, text, mid in msgs:
            if not wa_id or mid in self.seen:
                continue
            self.seen.add(mid)
            if len(self.seen) > 5000:
                self.seen.clear()
            if text is None:
                self.send(wa_id, "Zatial citam len textove spravy. Napiste prosim textom.")
                continue
            self.send(wa_id, self.bot.reply("wa:" + wa_id, text)["text"])
        return 200, "ok"  # always 200 quickly so Meta does not retry
