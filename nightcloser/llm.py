"""Minimal OpenAI-compatible chat client (OpenRouter by default, NVIDIA NIM with a nim: prefix) with an hourly request cap."""
import json, os, time, urllib.request


class BudgetExceeded(Exception):
    """Hourly LLM request cap hit (shared free quota guard); never retried."""


def _env_value(name, path="~/.env"):
    """Value from the process env or ~/.env (never printed)."""
    if os.environ.get(name):
        return os.environ[name]
    try:
        for line in open(os.path.expanduser(path), encoding="utf-8"):
            k, sep, v = line.strip().removeprefix("export ").partition("=")
            if sep and k.strip() == name:
                return v.strip().strip("\"'")
    except OSError:
        pass
    return ""


def _budget_take(cap=None, path=None):
    """Sliding 1 h window counter in a small JSON file (one per machine). Cap via env OR_HOURLY_CAP (default 300); 0 = off."""
    cap = int(os.environ.get("OR_HOURLY_CAP", "300")) if cap is None else cap
    if cap <= 0:
        return
    f = os.path.expanduser(path or os.environ.get("OR_BUDGET_FILE", "~/.cache/or_budget.json"))
    os.makedirs(os.path.dirname(f), exist_ok=True)
    now = time.time()
    try:
        ts = [t for t in json.load(open(f)) if now - t < 3600]
    except (OSError, ValueError):
        ts = []
    if len(ts) >= cap:
        raise BudgetExceeded(f"{len(ts)} requests in the last hour (cap {cap})")
    ts.append(now)
    tmp = f + ".tmp"
    json.dump(ts, open(tmp, "w"))
    os.replace(tmp, f)


class LLM:
    """OpenAI-compatible chat client (OpenRouter by default)."""
    def __init__(self, model, url="https://openrouter.ai/api/v1/chat/completions", key=None, max_tokens=700):
        self.model, self.url, self.max_tokens = model, url, max_tokens
        self.extra = {}  # optional request-body fields, e.g. {"reasoning": {"effort": "low"}}
        self.key = key or os.environ.get("OPENROUTER_API_KEY", "")
        if model.startswith("nim:"):  # NVIDIA NIM (OpenAI-compatible), key NVIDIA_API_KEY_1; its own rate limits, so no OpenRouter budget
            self.model, self.url = model[4:], "https://integrate.api.nvidia.com/v1/chat/completions"
            self.key = key or _env_value("NVIDIA_API_KEY_1")

    def chat(self, messages, tools):
        if not self.url.startswith("https://integrate.api.nvidia.com"):
            _budget_take()
        body = {"model": self.model, "messages": messages, "tools": tools, "temperature": 0.2, "max_tokens": self.max_tokens, **self.extra}
        r = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                   headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=60) as f:
            d = json.loads(f.read())
        return d["choices"][0]["message"]
