"""Twilio WhatsApp (sandbox or sender) webhook for Nightcloser. Stdlib only.
POST form (From, Body) -> X-Twilio-Signature check -> Closer.reply -> TwiML. Nothing is sent from here: Twilio delivers the TwiML reply.
Env: TWILIO_AUTH_TOKEN (signature key), TWILIO_WEBHOOK_URL (exact public URL configured in Twilio, used in the signature)."""
import base64, hashlib, hmac
from urllib.parse import parse_qsl
from xml.sax.saxutils import escape


def signature(url, params, token):
    base = url + "".join(k + v for k, v in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), base.encode(), hashlib.sha1).digest()).decode()


def handle(closer, body_bytes, sig, url, token):
    """Returns (status, twiml). 403 on a bad signature, 200 otherwise (empty <Response/> when nothing to say)."""
    params = parse_qsl(body_bytes.decode("utf-8", "replace"), keep_blank_values=True)
    if not token or not hmac.compare_digest(signature(url, params, token), sig or ""):
        return 403, ""
    d = dict(params)
    sender, text = d.get("From", ""), d.get("Body", "")
    if not sender.startswith("whatsapp:") or not text:
        return 200, "<Response/>"
    r = closer.reply("wa:" + sender[9:], text)
    msg = r.get("text")
    return 200, f"<Response><Message>{escape(msg)}</Message></Response>" if msg else "<Response/>"
