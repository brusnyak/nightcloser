"""Test endpoint for Nightcloser: POST /chat {session,text} -> {text,handoff}; GET /health.
GET /followups -> {items:[{session,nudge,lang,text}]}; POST /nudged {session,text} -> mark_nudged.
Binds 127.0.0.1:8096 (CLOSER_PORT). Run: python3 -m nightcloser.web (from the repo root).
Model: CLOSER_MODEL, default minimax/minimax-m3.1-flash:free; CLOSER_FALLBACK_MODEL is tried when the primary raises. OPENROUTER_API_KEY from env or ~/.env (value never printed)."""
import json, os, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from nightcloser.llm import LLM
from nightcloser import twilio_wa
from nightcloser.core import Closer

DEFAULT_MODEL = "minimax/minimax-m3.1-flash:free"   # 2026-10-20 bench on the VM (5 turns): median 2.1 s vs 9.4 s for nemotron-3-super
FALLBACK_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"


def load_openrouter_key(path="~/.env"):
    if os.environ.get("OPENROUTER_API_KEY"):
        return
    try:
        for line in open(os.path.expanduser(path), encoding="utf-8"):
            k, sep, v = line.strip().removeprefix("export ").partition("=")
            if sep and k.strip() == "OPENROUTER_API_KEY":
                os.environ["OPENROUTER_API_KEY"] = v.strip().strip("\"'")
                return
    except OSError:
        pass


class FallbackLLM:
    """Primary, then fallback, then primary once more (1.5 s apart) (free models rate-limit and 5xx at random)."""
    def __init__(self, primary, fallback):
        self.primary, self.fallback = primary, fallback

    def chat(self, messages, tools):
        for llm in (self.primary, self.fallback, self.primary):
            try:
                m = llm.chat(messages, tools)
                if (m.get("content") or "").strip() or m.get("tool_calls"):
                    return m
            except Exception:
                pass
            time.sleep(1.0)  # error or empty content (reasoning ate max_tokens): next model
        raise RuntimeError("all models failed")


def write_handoff(session_id, last_messages):
    """Hand-over: save the last messages of this chat for the owner to read (only handed-over chats are stored)."""
    d = os.path.expanduser(os.environ.get("CLOSER_HANDOFF_DIR", "~/closer-handoffs"))
    os.makedirs(d, mode=0o700, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + session_id.replace(":", "_")[-16:] + ".txt"
    lines = ["%s: %s" % ("customer" if m["role"] == "user" else "bot", m["content"]) for m in last_messages[-20:]]
    fd = os.open(os.path.join(d, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n\n".join(lines) + "\n")


def handler(closer):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            b = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path.startswith("/health"):
                return self._send(200, {"ok": True, "bot": "nightcloser"})
            if self.path.startswith("/followups"):  # due nudges; the bridge sends them, then POSTs /nudged
                return self._send(200, {"items": closer.due()})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path.startswith("/nudged"):
                try:
                    n = int(self.headers.get("Content-Length", 0))
                    if n > 4000:
                        return self._send(413, {"error": "too large"})
                    d = json.loads(self.rfile.read(n))
                    sid, text = d.get("session"), d.get("text")
                    if not isinstance(sid, str) or not 8 <= len(sid) <= 64 or not isinstance(text, str):
                        return self._send(400, {"error": "bad request"})
                    return self._send(200, {"ok": closer.mark_nudged("web:" + sid, text)})
                except Exception:
                    return self._send(500, {"error": "error"})
            if self.path.startswith("/twilio"):
                n = int(self.headers.get("Content-Length", 0))
                if n > 8000:
                    return self._send(413, {"error": "too large"})
                code, xml = twilio_wa.handle(closer, self.rfile.read(n), self.headers.get("X-Twilio-Signature"),
                                             os.environ.get("TWILIO_WEBHOOK_URL", ""), os.environ.get("TWILIO_AUTH_TOKEN", ""))
                b = xml.encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            if not self.path.startswith("/chat"):
                return self._send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length", 0))
                if n > 4000:
                    return self._send(413, {"error": "too large"})
                d = json.loads(self.rfile.read(n))
                sid, text = d.get("session"), d.get("text")
                if not isinstance(sid, str) or not 8 <= len(sid) <= 64 or not isinstance(text, str):
                    return self._send(400, {"error": "bad request"})
                r = closer.reply("web:" + sid, text)
                return self._send(200, {"text": r["text"], "handoff": r["handoff"]})
            except Exception:
                return self._send(500, {"text": "Technical error. Try again.", "handoff": False})
    return H


if __name__ == "__main__":
    load_openrouter_key()
    fast = LLM(os.environ.get("CLOSER_MODEL", DEFAULT_MODEL), max_tokens=1200)
    fast.extra = {"reasoning": {"effort": "low"}}  # empty replies came from reasoning eating max_tokens
    closer = Closer(FallbackLLM(fast,
                                LLM(os.environ.get("CLOSER_FALLBACK_MODEL", FALLBACK_MODEL), max_tokens=1600)), on_handoff=write_handoff)  # reasoning model: 700 gave empty content (finish=length)
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("CLOSER_PORT", 8096))), handler(closer)).serve_forever()
