"""
Vercel function /api/checkout — real credit purchases via Stripe Checkout.

Dependency-free (stdlib urllib only) — talks to the Stripe REST API directly.
Set STRIPE_SECRET_KEY in Vercel env to enable it; until then the endpoint reports
"disabled" and the frontend falls back to the instant demo top-up.

POST body:
  { "action": "config" }                       -> { "enabled": bool }
  { "action": "create", "pack": "200", "origin": "https://…" }
                                                -> { "url": "https://checkout.stripe.com/…" }
  { "action": "verify", "session_id": "cs_…" }  -> { "paid": bool, "credits": int }

Credit packs are defined SERVER-SIDE (never trust a client-sent price/amount).
"""
import os
import json
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

# pack id -> (credits granted, price in the SMALLEST currency unit — pence, since CURRENCY is GBP).
# The UI cards show £ prices, so Stripe must charge GBP too (previously it charged USD → mismatch).
# Economics: 1 credit = 1 fast generation (~£0.0012 API cost); quality generation = 5 credits.
# Sold at ~1p/credit (starter) → healthy margin over raw cost, covering Stripe fees + quality use.
# Bigger packs give more credits per £ (the "Save %" bonus).
CURRENCY = "gbp"
PACKS = {
    "starter": (500,   500),   # 500 credits · £5.00   (1.0p/credit)
    "plus":    (1800, 1500),   # 1,800 credits · £15.00 (0.83p/credit — Save ~20%)
    "pro":     (4000, 3000),   # 4,000 credits · £30.00 (0.75p/credit — Save ~33%)
}

STRIPE_API = "https://api.stripe.com/v1"


# ---- KV (shared credit balance with chat.py / keys.py) ----
def _kv():
    return (os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL"),
            os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN"))


def _kv_on():
    u, t = _kv()
    return bool(u and t)


def _kv_cmd(*args):
    u, t = _kv()
    req = urllib.request.Request(u, data=json.dumps([str(a) for a in args]).encode(),
                                 headers={"Authorization": "Bearer " + t, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()).get("result")


def _email(token):
    if not token or not _kv_on():
        return None
    try:
        return _kv_cmd("GET", "sess:" + token)
    except Exception:
        return None


def _key():
    return os.environ.get("STRIPE_SECRET_KEY")


def enabled():
    return bool(_key())


def _stripe(method, path, form=None):
    """One Stripe REST call. GET when form is None, else POST form-urlencoded."""
    data = urllib.parse.urlencode(form, doseq=True).encode() if form is not None else None
    req = urllib.request.Request(
        STRIPE_API + path, data=data,
        headers={"Authorization": "Bearer " + _key(),
                 "Content-Type": "application/x-www-form-urlencoded"},
        method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _safe_origin(origin):
    """Only allow http(s) origins we build redirect URLs from (no open-redirect surprises)."""
    if not origin:
        return None
    p = urllib.parse.urlparse(origin)
    if p.scheme in ("http", "https") and p.netloc:
        return p.scheme + "://" + p.netloc
    return None


def handle(body):
    action = (body.get("action") or "").lower()
    if action == "config":
        return {"enabled": enabled()}
    if not enabled():
        return {"error": "payments not configured"}

    if action == "create":
        pack = str(body.get("pack") or "")
        if pack not in PACKS:
            return {"error": "unknown pack"}
        credits, amount = PACKS[pack]
        origin = _safe_origin(body.get("origin")) or ""
        success = origin + "/?checkout=success&session_id={CHECKOUT_SESSION_ID}"
        cancel = origin + "/?checkout=cancel"
        form = {
            "mode": "payment",
            "success_url": success,
            "cancel_url": cancel,
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": CURRENCY,
            "line_items[0][price_data][unit_amount]": amount,
            "line_items[0][price_data][product_data][name]": f"{credits} Catalyst credits",
            "metadata[credits]": credits,
            "metadata[pack]": pack,
        }
        sess = _stripe("POST", "/checkout/sessions", form)
        if sess.get("error"):
            return {"error": sess["error"].get("message", "Stripe error")}
        return {"url": sess.get("url"), "id": sess.get("id")}

    if action == "verify":
        sid = body.get("session_id") or ""
        if not sid:
            return {"error": "missing session_id"}
        sess = _stripe("GET", "/checkout/sessions/" + urllib.parse.quote(sid, safe=""))
        if sess.get("error"):
            return {"error": sess["error"].get("message", "Stripe error")}
        paid = sess.get("payment_status") == "paid"
        credits = int((sess.get("metadata") or {}).get("credits") or 0)
        out = {"paid": paid, "credits": credits if paid else 0}
        # credit the SERVER-SIDE balance for the signed-in user (once per session — replay-safe)
        if paid and credits and _kv_on():
            email = _email(body.get("token"))
            if email:
                try:
                    if _kv_cmd("SET", "paid:" + sid, "1", "NX", "EX", 2592000):   # first time only
                        bal = _kv_cmd("INCRBY", "credits:" + email, credits)
                        out["credits_remaining"] = int(bal)
                    else:
                        cv = _kv_cmd("GET", "credits:" + email)
                        out["credits_remaining"] = int(cv) if cv is not None else None
                except Exception:
                    pass
        return out

    return {"error": "unknown action"}


class handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send({"error": "bad request"}, 400)
        try:
            self._send(handle(body))
        except Exception as e:
            self._send({"error": str(e)}, 500)
