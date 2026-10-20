"""Run nightcloser/scenarios.json against a real model.
Usage (repo root): python3 -m nightcloser.eval_closer [model] [--retry]
Each scenario runs once. --retry re-runs failed scenarios once more. Stops with exit 3 on HTTP 429
(OpenRouter free tier: 1000 req/day shared across the account). Exit 0 all pass, 1 any failure."""
import json, os, re, sys, time, urllib.error
from nightcloser.core import Closer
from nightcloser.web import DEFAULT_MODEL, load_openrouter_key
from nightcloser.llm import LLM

HERE = os.path.dirname(os.path.abspath(__file__))


class MeteredLLM:
    def __init__(self, inner):
        self.inner, self.calls, self.limited = inner, 0, False

    def chat(self, messages, tools):
        self.calls += 1
        try:
            return self.inner.chat(messages, tools)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self.limited = True
            raise


def check(turn, reply):
    fails = [f"missing /{p}/" for p in turn.get("must", []) if not re.search(p, reply, re.I)]
    fails += [f"forbidden /{p}/" for p in turn.get("must_not", []) if re.search(p, reply, re.I)]
    return fails


def run_scenario(sc, model_llm):
    closer = Closer(model_llm)
    sid, lat, fails, last = f"eval-{sc['id']}-{int(time.time() * 1000)}", [], [], ""
    for turn in sc["turns"]:
        t0 = time.time()
        r = closer.reply(sid, turn["user"])
        lat.append(time.time() - t0)
        last = r["text"]
        if model_llm.limited:
            return {"id": sc["id"], "title": sc["title"], "ok": None, "lat": lat, "fails": ["HTTP 429"], "last": ""}
        fails += check(turn, r["text"])
    return {"id": sc["id"], "title": sc["title"], "ok": not fails, "lat": lat, "fails": fails, "last": last}


def main(argv):
    retry = "--retry" in argv
    args = [a for a in argv if a != "--retry"]
    model = args[0] if args else DEFAULT_MODEL
    load_openrouter_key()
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY missing (env or ~/.env)")
        return 2
    scenarios = json.load(open(os.path.join(HERE, "scenarios.json"), encoding="utf-8"))
    llm = MeteredLLM(LLM(model))
    results = []
    for sc in scenarios:
        res = run_scenario(sc, llm)
        if res["ok"] is None:
            print("STOP: HTTP 429 at", sc["id"], "after", llm.calls, "requests", flush=True)
            results.append(res)
            break
        results.append(res)
    if retry and not any(r["ok"] is None for r in results):
        for i, r in enumerate(results):
            if r["ok"] is False:
                again = run_scenario(next(s for s in scenarios if s["id"] == r["id"]), llm)
                results[i] = {**again, "title": r["title"] + " (retry)"}
                if again["ok"] is None:
                    break
    print(f"{'id':<5}{'result':<8}{'latency s (per turn)':<28}title / first failure")
    for r in results:
        res = "PASS" if r["ok"] else ("STOP" if r["ok"] is None else "FAIL")
        lat = " ".join(f"{x:.1f}" for x in r["lat"])
        note = "" if r["ok"] else (r["fails"][0] if r["fails"] else "")
        print(f"{r['id']:<5}{res:<8}{lat:<28}{r['title']}{' | ' + note if note else ''}")
    done = [r for r in results if r["ok"] is not None]
    print(f"PASS {sum(1 for r in done if r['ok'])}/{len(scenarios)}  model={model}  llm_requests={llm.calls}")
    if llm.limited:
        return 3
    return 0 if all(r["ok"] for r in done) and len(done) == len(scenarios) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
