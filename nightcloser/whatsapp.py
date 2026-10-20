"""Nightcloser WhatsApp adapter: thin wrapper over nightcloser.wa_cloud (signature check, verify challenge,
parsing, dedupe). Transport is still undecided: nothing here is wired to a number, and nothing is sent
unless build() is called with real WA_* env vars.
CLOSER_TRIGGER (optional): a chat is served only if its FIRST message contains the phrase (accent-insensitive).
Chats that open without it are ignored for good, so a personal number can share the same webhook."""
import os

from nightcloser.wa_cloud import Webhook, graph_send
from nightcloser.core import norm


class TriggerGate:
    def __init__(self, closer, trigger=None):
        self.closer = closer
        self.trigger = norm(trigger) if trigger else None
        self.allowed, self.blocked = set(), set()  # session ids ("wa:<number>")

    def reply(self, sid, text):
        if self.trigger:
            if sid in self.blocked:
                return {"text": None, "handoff": False}
            if sid not in self.allowed:
                if self.trigger in norm(text or ""):
                    self.allowed.add(sid)
                else:
                    self.blocked.add(sid)  # ponytail: unbounded set; fine for one demo number
                    return {"text": None, "handoff": False}
        return self.closer.reply(sid, text)


def build(closer, env=os.environ, send=None):
    """Webhook for Nightcloser. send(to, text) defaults to the Cloud API; None text is never sent."""
    gate = TriggerGate(closer, env.get("CLOSER_TRIGGER"))
    if send is None:
        def send(to, text):
            graph_send(to, text, token=env["WA_TOKEN"], phone_number_id=env["WA_PHONE_ID"])

    def guarded_send(to, text):
        if text is None:
            return
        if gate.trigger and "wa:" + to not in gate.allowed:  # also covers non-text notices in undecided chats
            return
        send(to, text)

    return Webhook(gate, guarded_send, env["WA_APP_SECRET"], env["WA_VERIFY_TOKEN"])
