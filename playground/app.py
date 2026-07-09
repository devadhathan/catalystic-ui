#!/usr/bin/env python3
"""
Catalyst — conversational generative-UI playground.

  export OPENAI_API_KEY=...
  python3 playground/app.py            # open http://localhost:8000

Chat with the model; it composes a shadcn-style screen as a small component tree
(not HTML/CSS/JS). Follow-up messages refine the current screen. Powered by
OpenAI gpt-4o-mini to keep per-turn cost and latency low; each turn returns the
reply, a short "thinking" rationale, the component JSON, token usage and latency.

The component vocabulary is a compact fixed list (not a big schema dump) — that is
the main lever keeping token use small.
"""
import json
import os
import re
import time
import http.server
import socketserver
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAY = ROOT / "playground"
WEB = PLAY / "web"

# Two tiers per provider. Simple requests use the FAST model (cheap, low latency);
# complex/dashboard requests use the SMART model. All overridable via env.
# No Sonnet / Opus. Anthropic uses Haiku for both tiers; OpenAI uses mini/4o.
OPENAI_FAST = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_SMART = os.environ.get("OPENAI_SMART_MODEL", "gpt-4o")
ANTHROPIC_FAST = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")
ANTHROPIC_SMART = os.environ.get("ANTHROPIC_SMART_MODEL", "claude-haiku-4-5")

# Signals that a request wants a rich, multi-section UI (-> smart tier).
_COMPLEX = re.compile(
    r"\b(dashboard|analytics|chart|graph|plot|metric|kpi|report|overview|"
    r"table|stats?|trend|breakdown|pie|donut|bar chart|line chart|admin|panel|grid)\b",
    re.I)
_RICH_NODES = re.compile(r'"(BarChart|LineChart|Donut|PieChart|Table|Metric|Grid|Screen)"')


def classify(history, components):
    """Pick a tier from the latest message + whether the current screen is already rich."""
    last = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    if _COMPLEX.search(last):
        return "complex"
    # follow-ups on an already-rich screen stay complex for consistency
    if components and _RICH_NODES.search(json.dumps(components)):
        return "complex"
    return "simple"


def _supports_effort(model):
    return any(m in model for m in ("sonnet-4-6", "opus-4", "fable"))
PORT = int(os.environ.get("PORT", "8000"))


def pick_provider():
    """PROVIDER env wins; otherwise auto-detect from whichever key is present."""
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

PRESETS = [
    {"title": "Book appointment", "instruction": "A screen to book a salon appointment: pick a service, choose date and time, enter name and phone, confirm."},
    {"title": "Gen-Z budget", "instruction": "A playful monthly budget: show income, sliders for food, going out, subscriptions and savings, and money left. Save button."},
    {"title": "Login", "instruction": "A clean sign-in card: email, password, a primary sign-in button and a small 'forgot password' link."},
    {"title": "Feedback", "instruction": "A feedback form: a rating, a comment textarea, and a submit button."},
]

# Compact vocabulary — small on purpose (keeps input tokens low). Children may be
# nested inline OR referenced by id string.
SYSTEM = """You are a generative-UI engine. Given a chat, you design a small SCREEN as a \
tree of components and return it as JSON. You never write HTML, CSS, or JavaScript.

Respond with ONLY a JSON object of this exact shape:
{
  "reply": "one short sentence to the user about what you built or changed",
  "thinking": "ONE short sentence on the layout choice — keep it brief",
  "components": [ <one or more root nodes> ]
}

A node is: {"id": "unique-string", "component": "<Name>", ...props, "children": [<nodes or id strings>]}.
Allowed component names and their props (use ONLY these):
Shell / Layout:
- Screen     { theme?: "light|dark", width?: "sm|lg", children }   root wrapper; theme DEFAULTS to light
- Card       { children }
- Stack      { children, gap?: "sm|md|lg" }         vertical
- Row        { children, gap?, justify?: "start|center|between|end", align?: "start|center|end", wrap?: bool }  horizontal
- Grid       { children, cols?: int }               use for KPI card rows
- Separator  { }
Typography:
- Text       { text, variant?: "title|subtitle|body|muted|label" }
- Link       { text, href? }
Inputs:
- Input      { label?, placeholder?, value?, type?: "text|email|password|number|tel" }
- Textarea   { label?, placeholder?, value? }
- DatePicker { label?, value? }
- Select     { label?, options: [string...], value?, variant?: "filter", prefix? }   filter variant = dropdown pill (prefix like "Group by")
- RadioGroup { label?, options: [string...], value? }
- Slider     { label?, min?, max?, value? }
- Checkbox   { label, checked?: bool }
- Switch     { label, checked?: bool }
- Toggle     { label, pressed?: bool }
- Button     { label, variant?: "default|secondary|outline|ghost|destructive", size?: "sm|md|lg" }
- IconButton { icon: "download|filter|plus|search|more|settings|calendar|bell", label? }
Display:
- Badge      { label, tone?: "default|secondary|success|warning|destructive" }
- Avatar     { url?, fallback? }
- Image      { url, alt? }
- Metric     { label, value, description?, delta?, deltaTone?: "up|down", chart?: <a chart node> }   KPI/stat card
- Progress   { value: 0-100, label? }
- Skeleton   { lines?: int }
- Tooltip    { label, tip }
Feedback:
- Alert      { title, description?, tone?: "default|destructive|success|warning" }
Composite:
- Tabs       { tabs: [ { label, children: [nodes] } ] }
- Accordion  { items: [ { title, children: [nodes] } ] }
- Table      { columns: [string...], rows: [[cell...]...] }
Charts (inline SVG with axes, gridlines, ticks and legend — just give data):
- BarChart   simple: { title?, format?: "currency|number|percent", data: [ { label, value } ] }
             multi-series/stacked: { title?, subtitle?, format?, stacked?: bool, labels: [string...], series: [ { name, color?, data: [num...] } ] }
- LineChart  same shapes as BarChart (series supported)
- Donut      { title?, format?, center?, data: [ { label, value } ] }

INTERACTIVITY (make a filter actually change the data):
- Give the controlling Select a "stateKey" (e.g. "department") plus an initial "value".
- For any value that should change with that filter, replace the literal with a binding:
    {"bind": {"key": "department", "map": {"All": <val>, "Engineering": <val>, "Sales": <val>}}}
  Provide a map entry for EVERY option (include "All"). The renderer shows map[current selection]
  and re-renders instantly when the user changes the filter — no extra request.
- Bindable props: Metric value/description/delta, Text text, Badge label, and a chart's "data"
  or "series" (map each option to that option's own data array).
- CHART DATA: the `labels` array length MUST equal the number of data points in each series
  (e.g. 7 day labels for 7 daily values). labels are the x-axis (days/months), NOT the series names.
- WHEN THE SCREEN HAS FILTERS: the PRIMARY filter MUST have a stateKey, and you MUST bind the
  chart's data/series to that same key too — not only the metric cards — so the CHART visibly
  changes when the filter changes. Give every filter Select real options (no decorative filters).
  Bind the metrics AND at least one chart to the primary filter.
- Example — a department-driven metric:
    {"component":"Metric","label":"Total Cost",
     "value":{"bind":{"key":"department","map":{"All":"$124,560","Engineering":"$48,200","Sales":"$31,000"}}}}
  and the controlling filter:
    {"component":"Select","variant":"filter","prefix":"Department","stateKey":"department",
     "value":"All","options":["All","Engineering","Sales"]}
- Only add bindings when the screen has a filter meant to drive the data. Keep maps small and realistic.

Rules:
- MATCH THE STRUCTURE TYPE to the request: a form request -> a form; a dashboard request ->
  a dashboard. Do NOT add charts, KPI grids, tabs, or filter bars unless the request is
  analytical. A form must not become a dashboard.
- BE PREDICTIVE AND HELPFUL within that structure — this is domain-agnostic and applies to
  EVERY request (booking, payments, recipes, health, scheduling, budgeting, anything). It is
  what separates a great generative UI from a bare form. Always:
    • PREFILL fields with concrete values parsed from the message — whatever the domain: dates,
      amounts, names, locations, quantities, items. The screen should look like it already
      understood the request, not an empty template.
    • Add a short subtitle under the title.
    • When there are several inputs, group them under labeled section headers (Text variant "subtitle").
    • Add ONE brief info Alert with genuinely useful, domain-appropriate context when it helps —
      factual and short, never filler. Match it to the task: a money transfer notes the fee/arrival
      time; a recipe notes servings/cook time; an appointment notes what to bring; a budget notes
      what's left. Use your world knowledge of THAT domain.
    • Add a short summary line of the parsed request, a clear primary Button, and a sensible
      secondary action when natural.
    • Enrich, don't sprawl: one focused screen, genuinely useful — never empty, never a dashboard
      unless asked.
- Prefer nesting children INLINE (as node objects) — simpler and fewer tokens.
- For a DASHBOARD: wrap in Screen, put a Row of Select variant:"filter" pills (+ an IconButton
  download) at top, a Grid (cols 3-4) of Metric cards, then a Card holding a chart.
- THEME: default to light. Only set Screen theme:"dark" when the user explicitly asks for dark mode.
- Use series+stacked+format:"currency" on BarChart when showing money broken down by category over time.
- Use realistic literal values. Keep the tree focused and tidy; do not over-build.
- If the user asks to CHANGE the current screen (given below), modify it minimally rather
  than rebuilding from scratch.
- Keep "thinking" short. Output nothing except the JSON object."""


def _complete(provider, model, system, messages, json_mode=False, effort=None):
    """One model call. Returns (text, usage_dict, latency_ms)."""
    t0 = time.perf_counter()
    if provider == "openai":
        from openai import OpenAI
        client = OpenAI()
        kw = {"response_format": {"type": "json_object"}} if json_mode else {}
        resp = client.chat.completions.create(
            model=model, messages=[{"role": "system", "content": system}] + messages,
            temperature=0.4, max_tokens=4000, **kw,
        )
        text = resp.choices[0].message.content
        u = resp.usage
        usage = {"input": u.prompt_tokens, "output": u.completion_tokens, "total": u.total_tokens}
    else:  # anthropic (no native JSON mode; we rely on the prompt + repair)
        import anthropic
        client = anthropic.Anthropic()
        kw = {}
        if effort and _supports_effort(model):
            kw["output_config"] = {"effort": effort}  # low effort = far less thinking, faster
        msg = client.messages.create(model=model, max_tokens=4000,
                                     system=system, messages=messages, **kw)
        text = "".join(b.text for b in msg.content if b.type == "text")
        u = msg.usage
        usage = {"input": u.input_tokens, "output": u.output_tokens,
                 "total": u.input_tokens + u.output_tokens}
    return text, usage, int((time.perf_counter() - t0) * 1000)


def chat(history, components):
    provider = pick_provider()
    tier = classify(history, components)
    if provider == "openai":
        model, fast, effort = (OPENAI_SMART if tier == "complex" else OPENAI_FAST), OPENAI_FAST, None
    else:
        model = ANTHROPIC_SMART if tier == "complex" else ANTHROPIC_FAST
        fast = ANTHROPIC_FAST
        effort = "low" if tier == "complex" else None  # keep the smart model fast

    current = ("\nCurrent screen (modify if the user requests a change):\n"
               + json.dumps(components)) if components else ""
    system = SYSTEM + current
    hist = history[-8:]

    text, usage, latency = _complete(provider, model, system, hist, json_mode=True, effort=effort)
    try:
        data = parse_json_object(text)
    except Exception:
        # One-shot repair on the FAST model — mechanical, cheap, quick.
        fix, u2, lat2 = _complete(
            provider, fast,
            "Return ONLY the corrected, valid JSON object with keys reply, thinking, "
            "components. No prose, no code fences.",
            [{"role": "user", "content": text}], json_mode=True)
        data = parse_json_object(fix)
        usage = {k: usage[k] + u2[k] for k in usage}
        latency += lat2

    return {
        "reply": data.get("reply", ""),
        "thinking": data.get("thinking", ""),
        "components": data.get("components", []),
        "usage": usage, "latency_ms": latency,
        "model": model, "provider": provider, "tier": tier,
    }


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, obj, code=200, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            return self._send((WEB / "index.html").read_bytes(), ctype="text/html")
        if p == "/renderer.js":
            return self._send((WEB / "renderer.js").read_bytes(),
                              ctype="application/javascript")
        if p == "/polaris-bundle.js":
            return self._send((WEB / "polaris-bundle.js").read_bytes(),
                              ctype="application/javascript")
        if p == "/polaris-bundle.css":
            return self._send((WEB / "polaris-bundle.css").read_bytes(), ctype="text/css")
        if p in ("/logo.svg", "/empty.svg"):
            return self._send((WEB / p.lstrip("/")).read_bytes(), ctype="image/svg+xml")
        if p == "/presets.json":
            return self._send((WEB / "presets.json").read_bytes())
        if p == "/api/presets":
            return self._send(PRESETS)
        return self._send({"error": "not found"}, 404)

    def do_POST(self):
        if self.path.split("?")[0] == "/api/chat":
            need = "OPENAI_API_KEY" if pick_provider() == "openai" else "ANTHROPIC_API_KEY"
            if not os.environ.get(need):
                return self._send({"error": f"{need} not set"}, 400)
            length = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                return self._send({"error": "bad request body"}, 400)
            history = body.get("history") or []
            if not history:
                return self._send({"error": "empty message"}, 400)
            try:
                return self._send(chat(history, body.get("components")))
            except Exception as e:
                return self._send({"error": str(e)}, 500)
        return self._send({"error": "not found"}, 404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    prov = pick_provider()
    fast = OPENAI_FAST if prov == "openai" else ANTHROPIC_FAST
    smart = OPENAI_SMART if prov == "openai" else ANTHROPIC_SMART
    print(f"Catalyst playground on http://localhost:{PORT}  (provider={prov}, "
          f"fast={fast}, smart={smart})")
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        httpd.serve_forever()
