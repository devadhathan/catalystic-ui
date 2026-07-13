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
import hashlib
import urllib.request
from http.server import BaseHTTPRequestHandler


# ---- beta auth gate (active only once a KV/Upstash datastore is configured) ----
def _kv():
    url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    tok = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    return url, tok


def _auth_enabled():
    url, tok = _kv()
    return bool(url and tok)


def _kv_cmd(*args):
    url, tok = _kv()
    req = urllib.request.Request(url, data=json.dumps([str(a) for a in args]).encode(),
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()).get("result")


def _session_email(token):
    if not token or not _auth_enabled():
        return None
    try:
        return _kv_cmd("GET", "sess:" + token)
    except Exception:
        return None


# ---- rate limiting / spend cap (durable via KV; best-effort in-memory otherwise) ----
_MEM = {}


def _client_ip(headers):
    xff = headers.get("x-forwarded-for", "")
    return (xff.split(",")[0].strip() if xff else headers.get("x-real-ip", "")) or "unknown"


def _rate_ok(ip):
    """Returns (ok, reason). Caps requests per IP/minute and globally per day."""
    per_min = int(os.environ.get("RATE_PER_MIN", "12"))
    daily = int(os.environ.get("DAILY_CAP", "250"))
    now = int(time.time())
    if _auth_enabled():  # durable, cross-instance via Upstash
        try:
            mkey = "rl:%s:%d" % (ip, now // 60)
            n = int(_kv_cmd("INCR", mkey))
            if n == 1:
                _kv_cmd("EXPIRE", mkey, 120)
            if n > per_min:
                return False, "rate"
            dkey = "rl:day:%d" % (now // 86400)
            g = int(_kv_cmd("INCR", dkey))
            if g == 1:
                _kv_cmd("EXPIRE", dkey, 86400)
            if g > daily:
                return False, "daily"
            return True, ""
        except Exception:
            pass
    t = time.time()  # in-memory sliding window (per warm instance)
    arr = [x for x in _MEM.get(ip, []) if t - x < 60]
    if len(arr) >= per_min:
        _MEM[ip] = arr
        return False, "rate"
    arr.append(t)
    _MEM[ip] = arr
    return True, ""


# ---- generation cache: identical requests skip the LLM and return instantly ----
# Keeps the model OUT of the hot path once a screen has been generated once.
_CACHE = {}


def _cache_key(history, components, mode, agent_prompt, model_sel=None, web_pref=None):
    payload = json.dumps({"h": history[-6:], "c": components, "m": mode, "p": agent_prompt or "",
                          "md": model_sel or "auto", "ws": web_pref},
                         sort_keys=True, default=str)
    return "gen:" + hashlib.sha256(payload.encode()).hexdigest()[:40]


def _cache_get(key):
    if _auth_enabled():   # durable, shared across instances via Upstash
        try:
            v = _kv_cmd("GET", key)
            return json.loads(v) if v else None
        except Exception:
            pass
    return _CACHE.get(key)


def _cache_set(key, val):
    ttl = int(os.environ.get("CACHE_TTL", "3600"))
    if _auth_enabled():
        try:
            _kv_cmd("SET", key, json.dumps(val), "EX", ttl)
            return
        except Exception:
            pass
    _CACHE[key] = val


OPENAI_FAST = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_SMART = os.environ.get("OPENAI_SMART_MODEL", "gpt-4.1")
ANTHROPIC_FAST = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5")
ANTHROPIC_SMART = os.environ.get("ANTHROPIC_SMART_MODEL", "claude-haiku-4-5")


def _available_models():
    """Model options the UI can offer — only providers whose key is actually configured.
    'auto' always leads (the tier heuristic picks fast/smart per turn)."""
    out = [{"id": "auto", "label": "Auto (recommended)"}]
    if os.environ.get("ANTHROPIC_API_KEY"):
        out.append({"id": "anthropic:fast", "label": ANTHROPIC_FAST + " · fast"})
        if ANTHROPIC_SMART != ANTHROPIC_FAST:
            out.append({"id": "anthropic:smart", "label": ANTHROPIC_SMART + " · quality"})
    if os.environ.get("OPENAI_API_KEY"):
        out.append({"id": "openai:fast", "label": OPENAI_FAST + " · fast"})
        if OPENAI_SMART != OPENAI_FAST:
            out.append({"id": "openai:smart", "label": OPENAI_SMART + " · quality"})
    return out


def _resolve_model(sel):
    """Map a UI model id ('anthropic:smart') to (provider, model). Returns (None, None) for auto/unknown."""
    if not sel or sel == "auto":
        return None, None
    prov, _, tier = sel.partition(":")
    if prov == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic", (ANTHROPIC_SMART if tier == "smart" else ANTHROPIC_FAST)
    if prov == "openai" and os.environ.get("OPENAI_API_KEY"):
        return "openai", (OPENAI_SMART if tier == "smart" else OPENAI_FAST)
    return None, None

_COMPLEX = re.compile(
    r"\b(dashboard|analytics|chart|graph|plot|metric|kpi|report|overview|"
    r"table|stats?|trend|breakdown|pie|donut|bar chart|line chart|admin|panel|grid)\b", re.I)
_RICH = re.compile(r'"(BarChart|LineChart|Donut|PieChart|Table|Metric|Grid|Screen)"')

SYSTEM = """You are a generative-UI engine. Given a chat, you design a small SCREEN as a \
tree of components and return it as JSON. You never write HTML, CSS, or JavaScript.

Respond with ONLY a JSON object of this exact shape:
{
  "reply": "your message to the user — natural and conversational",
  "thinking": "ONE short sentence on your choice — keep it brief",
  "components": [ <root nodes, OR an empty array [] when no screen is needed> ]
}

Talk with the user like an assistant. Only build a UI when it genuinely helps. If the user is
greeting you, chatting, asking or answering a question, or you just need to reply in words, set
"components": [] and put everything in "reply" — do NOT invent a screen for every message. Build a
component tree only when the user actually needs one (a form, menu, dashboard, summary, confirmation).
If you use a tool first (e.g. web search), your FINAL message must STILL be only that JSON object — no
prose before or after it.

A node is: {"id": "unique-string", "component": "<Name>", ...props, "children": [<nodes or id strings>]}.
Put visible TEXT in props, never as a children string: Text uses "text", Button/Badge use "label",
Alert uses "title"/"description". `children` is ONLY for nested component nodes.
The MAIN action button (submit/save/confirm/continue) MUST use variant "default" (the prominent dark
button). Use "secondary"/"outline"/"ghost" only for secondary or cancel actions.
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
- Map        { lat, lon, zoom?, label? }         a LIVE interactive map centered on lat/lon (use for tracking / locations)
- LocationRequest { label? }                     a button; when the user taps it, their device location is captured and sent back to you
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


_TAP_RE = re.compile(r"^(select|add|remove|checkout|pay|confirm|order|book|choose|use my)\b", re.I)


def _is_mechanical(history, components):
    """A tap on an existing screen (Add/Select/Checkout/Pay/a slot) — cheap, no web search."""
    last = (next((m["content"] for m in reversed(history) if m.get("role") == "user"), "") or "").strip()
    if _TAP_RE.match(last):
        return True
    if components and len(last.split()) <= 4:  # short reply while a screen is already up
        return True
    return False


def _trim_components(node):
    """Shrink the on-screen tree we replay as context: drop image urls, clip long text."""
    if isinstance(node, list):
        return [_trim_components(x) for x in node]
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in ("url", "alt"):
                continue
            if k in ("description", "text") and isinstance(v, str) and len(v) > 60:
                out[k] = v[:60] + "…"
            else:
                out[k] = _trim_components(v)
        return out
    return node


def _post(url, headers, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _call(provider, model, system, messages, web_search=False):
    """Returns (text, usage, searches). Both providers can search the web when web_search=True."""
    if provider == "openai":
        auth = {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"}
        if web_search:
            # chat-completions has no web search — use the Responses API + web_search tool
            # NB: OpenAI forbids JSON mode together with web search — rely on the prompt + repair
            data = _post("https://api.openai.com/v1/responses", auth,
                         {"model": model, "instructions": system, "input": messages,
                          "tools": [{"type": "web_search"}], "temperature": 0.4, "max_output_tokens": 4000})
            text = "".join(c.get("text", "") for it in data.get("output", []) if it.get("type") == "message"
                           for c in it.get("content", []) if c.get("type") == "output_text")
            searches = sum(1 for it in data.get("output", []) if it.get("type") == "web_search_call")
            u = data.get("usage", {}) or {}
            return text, {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
                          "total": u.get("input_tokens", 0) + u.get("output_tokens", 0)}, searches
        data = _post("https://api.openai.com/v1/chat/completions", auth,
                     {"model": model, "messages": [{"role": "system", "content": system}] + messages,
                      "response_format": {"type": "json_object"}, "temperature": 0.4, "max_tokens": 4000})
        text = data["choices"][0]["message"]["content"]
        u = data["usage"]
        return text, {"input": u["prompt_tokens"], "output": u["completion_tokens"], "total": u["total_tokens"]}, 0
    # mark the (static) system as a cached block — Anthropic reuses it across turns (~90% cheaper)
    payload = {"model": model, "max_tokens": 4000, "messages": messages,
               "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]}
    if web_search:
        payload["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]
    data = _post("https://api.anthropic.com/v1/messages",
                 {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
                 payload)
    text = "".join(b["text"] for b in data["content"] if b.get("type") == "text")
    searches = sum(1 for b in data["content"] if b.get("type") == "web_search_tool_result")
    u = data["usage"]
    return text, {"input": u["input_tokens"], "output": u["output_tokens"], "total": u["input_tokens"] + u["output_tokens"]}, searches


_AGENT_CORE = (
    "You are a concierge agent inside a live product DEMO. You role-play helping the user order / book "
    "things as a realistic, hands-on simulation. This is a sandbox — no real payment is charged and no "
    "real order is placed — so you must NEVER disclaim that you are an AI or that you 'can't actually "
    "order, eat, or transact'. Just play the concierge convincingly and carry the task through to a "
    "confirmation. You are AUTONOMOUS and goal-driven — figure out the next best action yourself. If you "
    "need the user's location or preferences, ASK for them; speak in the first person to the user.\n"
    "- HARD RULE: never output phrases like 'I'm an AI', 'I'm Claude', 'I can't actually', 'not actually', "
    "or apologise for limitations. The user already knows this is a demo — there is nothing to disclaim; "
    "just continue the task.\n"
    "- When you web_search you now HAVE the information — present it as YOUR OWN findings, e.g. 'Here are a "
    "few great spots I found near you', and render them as options. Search results are NOT messages from "
    "the user: never say 'thanks for sharing' or 'I appreciate these options'. Stay fully in the concierge role.\n"
    "- Keep your REPLY to ONE short concierge sentence (e.g. 'Here are the best pizza places I found in "
    "Edinburgh:'). Put the actual options in components as Cards — do NOT list them in the reply text.\n"
    "- Build option screens using ONLY these component names: Screen, Card, Stack, Row, Text, Badge, Button, "
    "Image, Separator. Each option = a Card with a Text (variant 'title') name, a Text (variant 'muted') "
    "detail / price line, and a Button (label 'Select'). Never invent other component names.\n"
    "- Do NOT invent photo URLs for specific dishes or items — a wrong photo destroys trust, so a wrong "
    "photo is WORSE than none. Only add an Image when you have a genuinely correct URL; otherwise omit "
    "images and make clean text cards (name, one-line description, price, action Button).\n"
    "- FOR ORDERING: each menu item Card must have a clear PRICE and an 'Add' Button. The APP then handles the "
    "cart, checkout, payment and delivery tracking IN CODE when the user taps Add / Checkout / Pay — you do NOT "
    "render those screens; your job ends at the menu. Only step back in if the user types a new free-text request.\n"
    "- Treat menus / prices as a representative SAMPLE (this is a demo); don't claim they are the venue's "
    "exact live prices. Ask at most ONE short question when you truly need info; infer the rest.\n"
    "- When the user's message is a tapped choice (e.g. 'Select — Civerinos', or just 'Civerinos'), it means "
    "they CHOSE that option — acknowledge briefly and MOVE TO THE NEXT STEP (show that place's menu, the "
    "time slots, or the order summary). Never react by asking what YOU like, and never disclaim.\n"
    "  RIGHT: user taps 'Select — Civerinos' -> reply 'Great choice! Here's the Civerinos menu:' and render "
    "the menu Cards.  WRONG: 'I'm an AI, so I don't eat pizza…'.\n"
    "- You MUST render that next screen as tappable Cards — never end a selection step with only a text "
    "question. If exact menu items or prices aren't in your search, show a few typical popular dishes for "
    "that place as Cards anyway (this is a demo). Only ask in plain text when you genuinely need info the "
    "user alone can give (like their address).\n"
    "- Read the WHOLE chat history and build context. Track what the user has already told you "
    "(location, preferences, prior choices) and NEVER re-ask something you already know.\n"
    "- When you are missing something you need to reach the goal, ASK a short, specific question and "
    "reply in words with components: [] — don't render a screen just to ask. Ask only what you still "
    "need, one or two things at a time; infer the rest.\n"
    "- Use web_search to get REAL, current, location-relevant information. Prefer real names, real "
    "options and real prices from your search results over anything invented.\n"
    "- Render a screen (components) only when it genuinely helps: to present real options, a cart / "
    "summary, or a confirmation. Otherwise just talk.\n"
    "- When you show choices, lay the option Cards in ONE top-level Row so they scroll SIDEWAYS as a "
    "rail. Cards / Buttons are tap-to-select — the user's next message is whatever they tapped, so "
    "interpret it in context and advance toward the goal.\n"
    "- Any payment or final booking is ALWAYS human-confirmed via an explicit Button — never automatic.")
AGENT_CONTEXT = {
    "food": ("\n\nAGENT MODE — food ordering. GOAL: get the user a meal ordered (as a demo).\n" + _AGENT_CORE +
             "\nFull flow, ONE screen per step:\n"
             "  1. Confirm their location + what they're craving. To get their location, render a "
             "LocationRequest component so they can share their real device location in one tap (or just ask).\n"
             "  2. web_search real nearby restaurants (think Deliveroo / Uber Eats) and show 3-5 as image "
             "Cards (photo, name, cuisine, rating, 'Select').\n"
             "  3. On select, show that place's menu as dish image Cards (photo, dish name, short "
             "description, price, 'Add').\n"
             "  4. As dishes are added, show a cart / order-summary Card (line items, total) with a "
             "'Checkout' Button.\n"
             "  5. On checkout, show a PAYMENT screen: Input fields for card number, expiry, CVC and name on "
             "card (demo), plus a 'Pay £<total>' Button.\n"
             "  6. After the user taps Pay, show an order-confirmed Card AND a live DELIVERY-TRACKING Card: a "
             "live Map component (Map {lat, lon, zoom: 14, label: 'Your driver'}) centered on the delivery area "
             "(use approximate coordinates for the location), plus the driver's name + vehicle + ETA and a Progress bar.\n"
             "DEMO: no real order, charge, restaurant or driver is contacted — never claim otherwise; just "
             "present the simulated experience convincingly."),
    "booking": ("\n\nAGENT MODE — appointment booking. GOAL: get the user's appointment booked (as a demo).\n" + _AGENT_CORE +
                "\nFull flow: (1) confirm the service, location and preferred time (ask if unknown), "
                "(2) web_search real local providers (salons, clinics, studios) and show them as image Cards "
                "(photo, name, service, rating, 'Select'), (3) on select, show available time-slot Buttons, "
                "(4) show a booking-summary Card, (5) finish at a human-confirmed 'Confirm booking', then a "
                "confirmation Card with the provider details and a live Map component "
                "(Map {lat, lon, zoom: 15, label: 'the provider name'}) of the provider location. "
                "DEMO only — nothing is really booked."),
}

# Phase-1 research prompt: gathers real data with web search, returned as a plain brief.
RESEARCH_SYS = (
    "You are the research arm of a concierge agent. Using web search when it helps, return a SHORT factual "
    "brief for the user's LATEST request: real venue / dish / provider names, prices, ratings, addresses and "
    "approximate lat,lon coordinates. Plain-text bullet points, no preamble, no JSON. If the latest turn needs "
    "no lookup (e.g. a checkout, payment, or confirmation step), reply exactly 'no research needed'.")


def chat(history, components, mode="chat", agent_prompt=None, model_sel=None, web_pref=None):
    provider = pick_provider()
    mechanical = _is_mechanical(history, components)
    tier = classify(history, components)
    if mode in AGENT_CONTEXT:
        tier = "simple" if mechanical else "complex"   # a tap doesn't need the big model
    use_search = (mode in AGENT_CONTEXT) and not mechanical   # taps skip web search
    if web_pref is False:            # user turned the Web-search connector OFF in the UI
        use_search = False
    if provider == "openai":
        model, fast = (OPENAI_SMART if tier == "complex" else OPENAI_FAST), OPENAI_FAST
    else:
        model, fast = (ANTHROPIC_SMART if tier == "complex" else ANTHROPIC_FAST), ANTHROPIC_FAST
    # explicit model preference from the UI overrides the automatic tier/provider pick
    ov_provider, ov_model = _resolve_model(model_sel)
    if ov_model:
        provider, model = ov_provider, ov_model
        fast = OPENAI_FAST if provider == "openai" else ANTHROPIC_FAST
    # replay only a TRIMMED view of the current screen (drop image urls + long text) to cut input tokens
    current = ("\nCurrent screen (modify if the user requests a change):\n"
               + json.dumps(_trim_components(components))) if components else ""
    # agent_prompt (if the user tweaked it in the UI) overrides the built-in agent context
    ctx = agent_prompt if (agent_prompt and agent_prompt.strip()) else AGENT_CONTEXT.get(mode, "")
    hist = history[-8:]

    # Cache: an identical request skips the LLM entirely (0ms, 0 tokens).
    ckey = _cache_key(history, components, mode, agent_prompt, model_sel, web_pref)
    hit = _cache_get(ckey)
    if hit is not None:
        out = dict(hit)
        out["cached"] = True
        out["latency_ms"] = 0
        out["usage"] = {"input": 0, "output": 0, "total": 0}
        return out
    t0 = time.perf_counter()

    # PHASE 1 (agents only): research real data with web search. Kept separate from the UI
    # build because OpenAI forbids web search + JSON mode together — so search here, build next.
    searches, brief = 0, ""
    u1 = {"input": 0, "output": 0, "total": 0}
    tr0 = time.perf_counter()
    if use_search:
        try:
            brief, u1, searches = _call(provider, fast, RESEARCH_SYS, hist, web_search=True)
        except Exception:
            brief, u1 = "", {"input": 0, "output": 0, "total": 0}
    research_ms = int((time.perf_counter() - tr0) * 1000) if use_search else 0
    brief_ctx = ("\n\nResearch brief — real data to put in this screen:\n" + brief) \
        if brief.strip() and "no research needed" not in brief.lower() else ""
    # Static system stays IDENTICAL across turns → cacheable prefix (OpenAI auto-caches it,
    # Anthropic caches it via cache_control). The per-turn context rides as a leading message.
    static_system = SYSTEM + ctx
    dyn = (brief_ctx + current).strip()
    msgs = ([{"role": "user", "content": "[Current context]\n" + dyn}] + hist) if dyn else hist

    # PHASE 2: build the screen in JSON mode (no web search) → reliable components.
    tb0 = time.perf_counter()
    repaired = False
    text, u2, _ = _call(provider, model, static_system, msgs, web_search=False)
    try:
        data = parse_json_object(text)
    except Exception:
        repaired = True
        fix, u3, _ = _call(provider, fast,
                           "Return ONLY the corrected, valid JSON object with keys reply, thinking, components. No prose.",
                           [{"role": "user", "content": text}])
        data = parse_json_object(fix)
        u2 = {k: u2[k] + u3[k] for k in u2}
    build_ms = int((time.perf_counter() - tb0) * 1000)
    usage = {k: u1[k] + u2[k] for k in u2}
    result = {"reply": data.get("reply", ""), "thinking": data.get("thinking", ""),
              "components": data.get("components", []), "usage": usage, "searched": searches,
              "latency_ms": int((time.perf_counter() - t0) * 1000), "model": model,
              "research_ms": research_ms, "build_ms": build_ms, "repaired": repaired,
              "provider": provider, "tier": tier, "cached": False}
    _cache_set(ckey, result)
    return result


class handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # serve the default agent system prompts + available models so the UI can show / reset them
        return self._send({"prompts": AGENT_CONTEXT, "models": _available_models()})

    def do_POST(self):
        need = "OPENAI_API_KEY" if pick_provider() == "openai" else "ANTHROPIC_API_KEY"
        if not os.environ.get(need):
            return self._send({"error": f"{need} not set"}, 400)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._send({"error": "bad request body"}, 400)
        ok, why = _rate_ok(_client_ip(self.headers))   # protect the API budget
        if not ok:
            return self._send({"error": "Daily demo limit reached — please come back tomorrow." if why == "daily"
                               else "Too many requests — give it a few seconds and try again."}, 429)
        if _auth_enabled():  # require a valid beta account once the datastore is set up
            auth = self.headers.get("Authorization", "")
            token = auth[7:] if auth.startswith("Bearer ") else body.get("token")
            if not _session_email(token):
                return self._send({"error": "Please sign in to use Catalyst UI."}, 401)
        if not body.get("history"):
            return self._send({"error": "empty message"}, 400)
        try:
            self._send(chat(body["history"], body.get("components"),
                            body.get("mode") or "chat", body.get("agentPrompt"),
                            body.get("model"), body.get("webSearch")))
        except Exception as e:
            self._send({"error": str(e)}, 500)
