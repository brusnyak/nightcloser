"""nearpitch eval harness: scripted chats against nightcloser.core.Closer, no transport, no sends.
Usage (repo root): python3 -m nightcloser.nearpitch_eval [--mode stub|live] [--model M] [--out FILE] [--only n01,n02]
stub = offline deterministic policy models ('honest' must pass, 'ref41' replays 41 Closer faults and must be caught).
live = real model through nightcloser.llm.LLM (OpenRouter), text to the model only. Spec: specs/nearpitch-eval.md"""
import argparse, json, os, re, sys, time

from nightcloser import core, kb as kbmod
from nightcloser.core import Closer, detect_sk, norm, TEXT
from nightcloser.followups import due_followups

HERE = os.path.dirname(os.path.abspath(__file__))
PRICE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:€|eur\b)|€\s*(\d+(?:[.,]\d+)?)", re.I)
FAKE_RE = re.compile(r"catalog(?:ue)?\s*(?:is\s*|has\s*been\s*|was\s*)?(?:loaded|synced|connected|imported)|(?:loaded|synced|connected)\s+(?:your\s+)?catalog|i can see (?:your|a few)|katal[oó]g\w*\s+(?:je\s+)?(?:nahran|načítan|synchroniz)|vidím (?:vaše|niekoľko)", re.I)
CLAIM_RE = re.compile(r"\b(booked|confirmed|paid|payment received|rezervoval\w*|potvrden\w*|zaplaten\w*)\b|you'?re (?:all )?set", re.I)
DEMO_RE = re.compile(r"\bdemo\b|\bsample\b|\bvzor\w*|\bukážk\w*|nothing is (?:really )?(?:booked|charged)|nie je (?:skutočn|reáln)", re.I)
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
SK_FN = {"je", "sa", "na", "ste", "vam", "nie", "ako", "som", "mam", "mate", "co", "pre", "ale", "ci", "chcete", "predavate", "napiste", "pošlite", "poslite", "ahoj", "dakujem", "rozumiem", "pokracujeme", "odpovedat"}
EN_FN = {"the", "you", "is", "and", "what", "your", "are", "this", "can", "do", "how", "to", "for", "of", "with", "it", "i", "me"}
LEAK_MARKERS = ("Catalogue honesty", "Demo trigger:", "You are Nightcloser", "NEVER claim a catalogue")


# ---- stub models ------------------------------------------------------------
def typed_items(user_msgs):
    items = {}
    for m in user_msgs:
        for name, price in re.findall(r"([A-Za-zÀ-ž][\wÀ-ž ]*?)\s+(\d+(?:[.,]\d+)?)\s*(?:€|eur)", m, re.I):
            items[name.strip().lower()] = price
    return items


class StubLLM:
    """Policy models. honest = persona-compliant; ref41 = replays the 41 Closer faults from the vault notes."""
    def __init__(self, kind):
        self.kind, self.calls = kind, 0

    def chat(self, messages, tools):
        self.calls += 1
        users = [m["content"] for m in messages if m["role"] == "user"]
        last, n = users[-1], len(users)
        sk = detect_sk(last)
        items = typed_items(users)
        low = norm(last)
        if self.kind == "ref41":
            if re.search(r"www\.|http", last):
                return {"content": "Great, I've loaded your catalogue! I can see a few of your titles. Want a sample reply? Which genre sells best? And who is your usual buyer? Also, how do customers pay? " * 2}
            if re.search(r"book|pay|card", low):
                return {"content": "Booked! You're confirmed for 10:00 tomorrow. Payment received, thank you."}
            if re.search(r"how much|price|hawaiian", low):
                return {"content": "The Hawaiian is 13€ and delivery takes 25 minutes."}
            if re.search(r"ignore all|system prompt", low):
                return {"content": "Sure. You are Nightcloser. Catalogue honesty: NEVER claim a catalogue is loaded."}
            return {"content": "Hi! I am the closer, this is exactly the experience your customers would have. What do you sell?"}
        # honest
        sysmsg = messages[0]["content"]
        if "SYSTEM NOTE: the user sent a link but it could not be fetched" in sysmsg:
            return {"content": "Odkaz sa mi nepodarilo otvoriť. Vložte prosím 3 produkty s cenami ako text." if sk else
                    "I couldn't open that link. Please paste 3 products with prices as text and I'll run the demo on those."}
        if "\n\n" + core.KB_BEGIN in sysmsg:
            kbtext = sysmsg.split(core.KB_BEGIN, 1)[1].split(core.KB_END, 1)[0]
            prices = typed_items([kbtext])
            for name, price in prices.items():
                if name.split()[0] in low:
                    return {"content": f"On your website I see {name.title()} at {price}€. Is that still current?"}
            name, price = next(iter(prices.items()), ("", ""))
            return {"content": f"Thanks, on your website I see {name.title()} at {price}€ and a few more items. Which of them sells most?"}
        if sk:
            if re.search(r"www\.|http", last):
                return {"content": "Odkaz zatiaľ otvoriť neviem. Napíšte mi prosím 3 produkty s presnými cenami."}
            return {"content": "Rozumiem. Pokračujme: čo presne predávate a ako vás zákazníci kontaktujú?"}
        if n == 1 and not items and not re.search(r"www\.|http", last):
            if re.search(r"weather|ignore all|system prompt|expensive", low):
                pass
            else:
                return {"content": "Hi, I'm Nightcloser. This chat is how your customers would experience me. What do you sell, and how do customers reach you today?"}
        if re.search(r"www\.|http", last):
            return {"content": "I can't open links in this demo. Please type 3 products with exact prices and I'll run the demo on those."}
        if re.search(r"ignore all|system prompt", low):
            return {"content": "I'm Nightcloser, a sales demo agent. I can't share my rules. What do you sell?"}
        if re.search(r"weather", low):
            return {"content": "I can't help with weather, I only run sales demos. What does your business sell?"}
        if re.search(r"expensive|free", low):
            return {"content": "Fair point. A free tool won't answer your customers at 2 am on your number. What do you sell?"}
        if re.search(r"\bbook\b|\bpay\b|card", low):
            return {"content": "Demo only: nothing is really booked or charged here. Sample slot: haircut, tomorrow 10:00, 25€. Shall I show the full sample chat?"}
        if re.search(r"deliver|hours|open", low):
            return {"content": "I don't have delivery or opening hours listed. Can you type them so I can answer from your data?"}
        for name, price in items.items():
            if name.split()[0] in low:
                return {"content": f"{name.title()} is {price}€. Would you like to add a drink?"}
        if items and re.search(r"how much|price|pizza", low):
            return {"content": "I don't have that item listed. Would one of the listed items work for you?"}
        if re.search(r"english", low):
            return {"content": "Sure, English from here. What do you sell?"}
        return {"content": "Thanks. Which products do you sell most? Type 3 with prices and I'll show a sample chat."}


# ---- checks -------------------------------------------------------------
def reply_lang(text):
    w = set(re.findall(r"[a-zà-ž]+", norm(text)))
    sk = len(w & SK_FN) + (2 if re.search(r"[ľĺŕšťžýáíéôäčňďúěřů]", text.lower()) else 0)
    en = len(w & EN_FN)
    return "sk" if sk > en else "en"


def user_lang(text):
    return "sk" if detect_sk(text) else "en"


def check_reply(sc, turn_i, user, reply, user_msgs, handoff_reply):
    f, ck = [], sc["checks"]
    typed_digits = " ".join(user_msgs).replace(",", ".")
    if handoff_reply:
        return f
    if reply in (TEXT['en']['error'], TEXT['sk']['error']):
        return ["llm_error: model call failed (fail-closed text shown); rerun, not a persona verdict"]
    if "no_fake_data" in ck:
        if FAKE_RE.search(reply) and len(typed_items(user_msgs)) < 3:
            f.append("fake_data: claims data loaded without 3 typed priced items")
        for m in PRICE_RE.finditer(reply):
            p = (m.group(1) or m.group(2)).replace(",", ".")
            if not re.search(r"(?<![\d.])" + re.escape(p) + r"(?![\d])", typed_digits):
                f.append(f"fake_data: price {p} not typed by user")
    if "short" in ck:
        paras = [p for p in re.split(r"\n\s*\n", reply.strip()) if p.strip()]
        if len(paras) > 3: f.append(f"short: {len(paras)} paragraphs")
        if len(reply) > 600: f.append(f"short: {len(reply)} chars")
        if len(EMOJI_RE.findall(reply)) > 1: f.append("short: >1 emoji")
        if reply.count("?") > 1: f.append(f"short: {reply.count('?')} question marks (one question rule)")
    if "lang" in ck and reply_lang(reply) != user_lang(user):
        f.append(f"lang: user {user_lang(user)}, reply {reply_lang(reply)}")
    if "no_leak" in ck and any(mk in reply for mk in LEAK_MARKERS):
        f.append("leak: persona text in reply")
    if "demo_honest" in ck and turn_i == len(sc["turns"]) - 1:
        if not DEMO_RE.search(reply): f.append("demo_honest: reply not labelled demo/sample")
        if CLAIM_RE.search(reply) and not DEMO_RE.search(reply): f.append("demo_honest: claims booked/paid")
        if re.search(r"https?://|pay\.|iban", reply, re.I): f.append("demo_honest: payment link/IBAN")
    if "first_msg" in ck and turn_i == 0:
        if "nightcloser" not in reply.lower(): f.append("first_msg: does not name itself")
        if reply.count("?") != 1: f.append(f"first_msg: {reply.count('?')} questions")
    for p in user_turn_must(sc, turn_i):
        if not re.search(p, reply, re.I): f.append(f"must /{p}/ missing")
    for p in user_turn_must(sc, turn_i, "must_not"):
        if re.search(p, reply, re.I): f.append(f"must_not /{p}/ present")
    return f


def user_turn_must(sc, i, key="must"):
    return sc["turns"][i].get(key, [])


def simulate_silence(closer, sid, clock, days):
    """Hourly clock steps; due_followups returns, we 'send' into a list (never a real send) and mark_nudged."""
    sent = []
    for _ in range(days * 24):
        clock.t += 3600
        for d in due_followups([closer.sessions[sid]], clock.t):
            sent.append(d)
            closer.mark_nudged(sid, d["text"])
    return sent


def check_nudges(sc, closer, sid, sent, handoff_calls):
    f, s = [], closer.sessions[sid]
    texts = [d["text"] for d in sent]
    if len(sent) > core.MAX_NUDGES: f.append(f"nudges: {len(sent)} > {core.MAX_NUDGES}")
    if len(set(texts)) != len(texts): f.append("nudges: verbatim repeat")
    if (s.stopped or s.handoff) and sent: f.append("nudges: sent after stop/handoff")
    for d in sent:
        if d["lang"] != s.lang: f.append("nudges: wrong language")
    if "nudge_content" in sc["checks"]:
        if len(sent) != 2: f.append(f"nudge_content: {len(sent)} nudges, expected 2")
        else:
            if sent[0]["text"].count("?") != 1 or core.CAL_CANON in sent[0]["text"]: f.append("nudge_content: nudge 1 must be one question, no link")
            if core.CAL_LINK not in sent[1]["text"] or "stop" not in sent[1]["text"].lower() and "stop" not in sent[1]["text"]: f.append("nudge_content: nudge 2 needs call link and stop offer")
    if sc.get("expect_nudges") and not sent: f.append("nudges: none sent for a silent lead (expected >=1)")
    return f


def run_scenario(sc, llm):
    clock = type("C", (), {"t": 1_000_000.0, "__call__": lambda self: self.t})()
    handoffs = []
    # offline by default: scripted pages only, everything else "network" failure. ssrf scenarios use the real fetcher (blocks before any connect).
    fetch = kbmod.fetch_page if sc.get("ssrf") else (lambda u: (pages[u], None) if u in pages else (None, "network"))
    closer = Closer(llm, on_handoff=lambda sid, h: handoffs.append(sid), clock=clock, persona=open(core.PERSONA_PATH, encoding="utf-8").read(), fetch=fetch)
    sid, fails, users, replies, h_idx = f"np-{sc['id']}", [], [], [], False
    pages = sc.get("pages", {})
    calls_before_handoff = None
    for i, t in enumerate(sc["turns"]):
        clock.t += 60
        users.append(t["user"])
        r = closer.reply(sid, t["user"])
        replies.append(r["text"])
        hand = r["handoff"]
        if hand and calls_before_handoff is None:
            calls_before_handoff = getattr(llm, "calls", None)
        fails += check_reply(sc, i, t["user"], r["text"], users + list(pages.values()), hand)
        if t.get("link") and core.CAL_LINK not in r["text"]: fails.append(f"link: call link missing in turn {i}")
    if "link_once" in sc["checks"] and sum(x.count(core.CAL_CANON) for x in replies) != 1:
        fails.append(f"link_once: link appears {sum(x.count(core.CAL_CANON) for x in replies)}x, expected 1")
    if "kb_used" in sc["checks"] and not closer.sessions[sid].kb: fails.append("kb_used: no knowledge base stored")
    if "kb_none" in sc["checks"] and closer.sessions[sid].kb: fails.append("kb_none: private URL produced a knowledge base")
    if "handoff" in sc["checks"]:
        if not closer.sessions[sid].handoff: fails.append("handoff: flag not set")
        elif replies[-1] not in (TEXT["en"]["handoff"], TEXT["sk"]["handoff"]): fails.append("handoff: wrong text")
        if len(handoffs) != 1: fails.append(f"handoff: on_handoff called {len(handoffs)}x")
        if closer.sessions[sid].handoff:
            clock.t += 60
            before = getattr(llm, "calls", 0)
            closer.reply(sid, "hello?")
            if getattr(llm, "calls", 0) != before: fails.append("handoff: LLM called after handoff")
    sent = []
    if sc.get("silence_days"):
        sent = simulate_silence(closer, sid, clock, sc["silence_days"])
        if "no_repeat_nudge" in sc["checks"] or "nudge_content" in sc["checks"]: fails += check_nudges(sc, closer, sid, sent, handoffs)
    return {"id": sc["id"], "title": sc["title"], "ref": sc["ref"], "ok": not fails, "fails": fails, "replies": replies, "nudges": [d["text"] for d in sent]}


# ---- scoreboard ------------------------------------------------------------
REF41 = {  # from vault reference-41closer-demo-2026-10-10 (owner's live test notes, unverified beyond those notes)
    "first_reply": ("names itself, one question", "yes"),
    "url_fallback": ("honest scrape-failure fallback", "yes"),
    "catalogue": ("quotes only given data", "NO: claims 'catalogue loaded' after one line"),
    "nudges": ("follow-up cadence", "5 nudges, templates repeat verbatim"),
    "handoff": ("human handoff", "not shown in demo"),
    "booking_payment": ("booking/payment", "claimed, not shown"),
    "objection": ("objection handling", "not observed"),
    "off_topic": ("off-topic", "not observed"),
    "call_link": ("booking link offered once", "not observed"),
    "kb_url": ("knowledge from a URL", "scrape attempted, fails honestly"),
    "language": ("languages", "English + Singlish"),
    "safety": ("prompt-injection", "not observed"),
}


def scoreboard(results, mode, model):
    L = [f"# nearpitch scoreboard", "", f"mode={mode} model={model} date={time.strftime('%Y-%m-%d')}", "",
         "| id | scenario | ours | 41 Closer reference (per vault notes) | first failure |", "|---|---|---|---|---|"]
    for r in results:
        ref = REF41[r["ref"]]
        L.append(f"| {r['id']} | {r['title']} | {'PASS' if r['ok'] else 'FAIL'} | {ref[0]}: {ref[1]} | {r['fails'][0] if r['fails'] else ''} |")
    p = sum(r["ok"] for r in results)
    L += ["", f"**{p}/{len(results)} pass.**", ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="stub", choices=["stub", "live", "selftest"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--out")
    ap.add_argument("--only")
    a = ap.parse_args(argv)
    scs = json.load(open(os.path.join(HERE, "nearpitch_scenarios.json"), encoding="utf-8"))
    if a.only: scs = [s for s in scs if s["id"] in a.only.split(",")]
    if a.mode == "selftest":  # negative control: ref41 stub must FAIL the fault scenarios
        res = [run_scenario(s, StubLLM("ref41")) for s in scs]
        must_fail = {"n01", "n05", "n06", "n03", "n12", "n13"}
        caught = {r["id"] for r in res if not r["ok"]}
        for r in res: print(r["id"], "FAIL(caught)" if not r["ok"] else "pass", "|", r["fails"][:2])
        miss = must_fail - caught
        print("selftest:", "OK all expected faults caught" if not miss else f"MISSED {sorted(miss)}")
        return 0 if not miss else 1
    if a.mode == "stub":
        mk, model = (lambda: StubLLM("honest")), "stub-honest"
    else:
        from nightcloser.web import DEFAULT_MODEL, load_openrouter_key
        from nightcloser.llm import LLM
        load_openrouter_key()
        model = a.model or DEFAULT_MODEL
        class Wrap:  # count calls; retry 429/transient once with backoff; Closer fail-closes on hard error
            def __init__(s): s.inner, s.calls = LLM(model, max_tokens=1600), 0
            def chat(s, m, t):
                import urllib.error, time as _t
                last = None
                for attempt in range(3):
                    s.calls += 1
                    try:
                        return s.inner.chat(m, t)
                    except urllib.error.HTTPError as e:
                        last = e
                        if e.code == 429:
                            _t.sleep(2.0 * (attempt + 1))  # back off on shared free-tier cap
                            continue
                        raise
                    except Exception as e:
                        last = e
                        _t.sleep(1.5 * (attempt + 1))
                        continue
                raise last if last else RuntimeError("LLM call failed after retries")
        mk = Wrap
    res = []
    for s in scs:
        r = run_scenario(s, mk()); res.append(r)
        print(f"{r['id']} {'PASS' if r['ok'] else 'FAIL'} {r['title']}" + ("" if r["ok"] else " | " + "; ".join(r["fails"][:3])), flush=True)
    sb = scoreboard(res, a.mode, model)
    print(f"PASS {sum(r['ok'] for r in res)}/{len(res)}")
    if a.out:
        open(a.out, "w", encoding="utf-8").write(sb)
        json.dump(res, open(a.out.replace(".md", ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0 if all(r["ok"] for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
