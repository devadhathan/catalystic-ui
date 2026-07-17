"""
Vercel function /api/keys — developer API-key management (session-authenticated).

Backed by the same Upstash/Vercel KV store as auth.py. Keys let a developer's
backend call /api/generate; usage is metered against a per-account credit balance
held server-side in KV (tamper-proof, unlike the playground's localStorage counter).

POST { action: "list" | "create" | "revoke", token, ... }
"""
import json
import os
import time
import secrets
import urllib.request
from http.server import BaseHTTPRequestHandler

DEFAULT_CREDITS = 50   # free credits seeded for a new account (shared with playground; see chat.FREE_CREDITS)


def _store():
    url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    tok = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    return url, tok


def enabled():
    url, tok = _store()
    return bool(url and tok)


def _cmd(*args):
    url, tok = _store()
    req = urllib.request.Request(url, data=json.dumps([str(a) for a in args]).encode(),
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()).get("result")


def session_email(token):
    if not token or not enabled():
        return None
    try:
        return _cmd("GET", "sess:" + token)
    except Exception:
        return None


def credits_of(email):
    v = _cmd("GET", "credits:" + email)
    if v is None:
        _cmd("SET", "credits:" + email, DEFAULT_CREDITS)
        return DEFAULT_CREDITS
    return int(v)


def handle(body):
    if not enabled():
        return {"error": "not configured"}
    email = session_email(body.get("token"))
    if not email:
        return {"error": "not signed in"}
    action = (body.get("action") or "").lower()

    if action == "list":
        raw = _cmd("SMEMBERS", "keys:" + email) or []
        keys = []
        for k in raw:
            meta = _cmd("GET", "keymeta:" + k)
            m = json.loads(meta) if meta else {"key": k}
            m["masked"] = m.get("key", "")[:10] + "…" + m.get("key", "")[-4:]
            m.pop("key", None)   # never re-expose the full key after creation
            keys.append(m)
        return {"keys": keys, "credits": credits_of(email)}

    if action == "create":
        key = "ck_" + secrets.token_urlsafe(24)
        _cmd("SET", "apikey:" + key, email)
        _cmd("SADD", "keys:" + email, key)
        meta = {"key": key, "label": (body.get("label") or "API key")[:40], "created": int(time.time())}
        _cmd("SET", "keymeta:" + key, json.dumps(meta))
        credits_of(email)
        return {"key": key, "label": meta["label"], "credits": credits_of(email)}   # full key shown ONCE

    if action == "revoke":
        want = body.get("masked") or body.get("key") or ""
        for k in (_cmd("SMEMBERS", "keys:" + email) or []):
            masked = k[:10] + "…" + k[-4:]
            if want and (want == masked or want == k):   # match by masked display value (full key is never re-listed)
                _cmd("DEL", "apikey:" + k)
                _cmd("SREM", "keys:" + email, k)
                _cmd("DEL", "keymeta:" + k)
        return {"ok": True}

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
            out = handle(body)
            self._send(out, 401 if out.get("error") == "not signed in" else 200)
        except Exception as e:
            self._send({"error": str(e)}, 500)
