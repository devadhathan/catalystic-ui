"""
Vercel serverless function for /api/chat.

Dependency-free (stdlib urllib only) — calls the Anthropic Messages API or the
OpenAI Chat Completions API directly, so the function stays tiny and cold-starts
fast. Mirrors the generation logic in ../app.py.

Set as Vercel environment variables (never commit these):
  ANTHROPIC_API_KEY   and/or   OPENAI_API_KEY
  PROVIDER            (optional: "anthropic" | "openai"; auto-detected otherwise)
  ANTHROPIC_MODEL / ANTHROPIC_SMART_MODEL / OPENAI_MODEL / OPENAI_SMART_MODEL (optional)
"""
import json
import os
import re
import time
import urllib.request
from http.server import BaseHTTPRequestHandler

OPENAI_FAST = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_SMART = os.environ.get("OPENAI_SMART_MODEL", "gpt-4o")
ANTHROPIC_FAST = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")
ANTHROPIC_SMART = os.environ.get("ANTHROPIC_SMART_MODEL", "claude-haiku-4-5")

_COMPLEX = re.compile(
    r"\b(dashboard|analytics|chart|graph|plot|metric|kpi|report|overview|"
    r"table|stats?|trend|breakdown|pie|donut|bar chart|line chart|admin|panel|grid)\b", re.I)
_RICH = re.compile(r'"(BarChart|LineChart|Donut|PieChart|Table|Metric|Grid|Screen)"')

SYSTEM = """You are a generative-UI engine. Given a chat, you design a small SCREEN as a \
tree of components and return it as JSON. You never write HTML, CSS, or JavaScript.

Respond with ONLY a JSON object of this exact shape:
{
  "reply": "one short sentence to the user about what you built or changed",
  "thinking": "ONE short sentence on the layout choice — keep it brief",
  "components": [ <one or more root nodes> ]
}

A node is: {"id": "unique-string", "component": "<Name>", ...props, "children": [<nodes or id strings>]}.
Allowed components: Screen, Card, Stack, Row, Grid, Separator, Text, Link, Input, Textarea,
DatePicker, Select (variant "filter", prefix, stateKey), RadioGroup, Slider, Checkbox, Switch,
Toggle, Button, IconButton (icon), Badge, Avatar, Image, Metric, Progress, Skeleton, Tooltip,
Alert, Tabs, Accordion, Table, BarChart, LineChart, Donut.

- MATCH THE STRUCTURE TYPE to the request: a form request -> a form; a dashboard -> a dashboard.
- BE PREDICTIVE AND HELPFUL: prefill fields with values parsed from the message; add a subtitle;
  group inputs under labeled section headers (Text variant "subtitle"); add ONE brief, domain-
  appropriate info Alert; add a summary line and a clear primary Button.
- CHART DATA: the labels array length MUST equal the number of data points in each series
  (e.g. 7 day labels for 7 daily values). labels are the x-axis, NOT the series names.
- INTERACTIVITY: give a controlling Select a "stateKey" + initial "value"; bind changing values with
  {"bind": {"key": "<stateKey>", "map": {"<option>": <value>, ...}}}. When a dashboard has filters,
  the primary filter MUST have a stateKey and you MUST bind the chart data to it too.
- THEME: default light. Only Screen theme "dark" if the user explicitly asks for dark.
- Prefer nesting children INLINE. Output nothing except the JSON object."""


def pick_provider():
    p = (os.environ.get("PROVIDER") or "").lower()
    if p in ("openai", "anthropic"):
        return p
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "openai"


def parse_json_object(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1].removeprefix("json").strip().rstrip("`").strip()
    if not text.startswith("{"):
        text = text[text.find("{"): text.rfind("}") + 1]
    return json.loads(text)


def classify(history, components):
    last = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    if _COMPLEX.search(last):
        return "complex"
    if components and _RICH.search(json.dumps(components)):
        return "complex"
    return "simple"


def _post(url, headers, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _call(provider, model, system, messages):
    if provider == "openai":
        data = _post("https://api.openai.com/v1/chat/completions",
                     {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"},
                     {"model": model, "messages": [{"role": "system", "content": system}] + messages,
                      "response_format": {"type": "json_object"}, "temperature": 0.4, "max_tokens": 4000})
        text = data["choices"][0]["message"]["content"]
        u = data["usage"]
        return text, {"input": u["prompt_tokens"], "output": u["completion_tokens"], "total": u["total_tokens"]}
    data = _post("https://api.anthropic.com/v1/messages",
                 {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
                 {"model": model, "max_tokens": 4000, "system": system, "messages": messages})
    text = "".join(b["text"] for b in data["content"] if b.get("type") == "text")
    u = data["usage"]
    return text, {"input": u["input_tokens"], "output": u["output_tokens"], "total": u["input_tokens"] + u["output_tokens"]}


def chat(history, components):
    provider = pick_provider()
    tier = classify(history, components)
    if provider == "openai":
        model, fast = (OPENAI_SMART if tier == "complex" else OPENAI_FAST), OPENAI_FAST
    else:
        model, fast = (ANTHROPIC_SMART if tier == "complex" else ANTHROPIC_FAST), ANTHROPIC_FAST
    current = ("\nCurrent screen (modify if the user requests a change):\n" + json.dumps(components)) if components else ""
    system = SYSTEM + current
    hist = history[-8:]

    t0 = time.perf_counter()
    text, usage = _call(provider, model, system, hist)
    try:
        data = parse_json_object(text)
    except Exception:
        fix, u2 = _call(provider, fast,
                        "Return ONLY the corrected, valid JSON object with keys reply, thinking, components. No prose.",
                        [{"role": "user", "content": text}])
        data = parse_json_object(fix)
        usage = {k: usage[k] + u2[k] for k in usage}
    return {"reply": data.get("reply", ""), "thinking": data.get("thinking", ""),
            "components": data.get("components", []), "usage": usage,
            "latency_ms": int((time.perf_counter() - t0) * 1000), "model": model, "provider": provider, "tier": tier}


class handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        need = "OPENAI_API_KEY" if pick_provider() == "openai" else "ANTHROPIC_API_KEY"
        if not os.environ.get(need):
            return self._send({"error": f"{need} not set"}, 400)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._send({"error": "bad request body"}, 400)
        if not body.get("history"):
            return self._send({"error": "empty message"}, 400)
        try:
            self._send(chat(body["history"], body.get("components")))
        except Exception as e:
            self._send({"error": str(e)}, 500)
