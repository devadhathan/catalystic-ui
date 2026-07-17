"""
Vercel function /api/admin — owner-only account management for the beta.

DISABLED unless an ADMIN_TOKEN env var is set. Every action requires that exact
token (constant-time compared), so with no token the endpoint does nothing.

POST body:
  { "action": "config" }                       -> { "enabled": bool }   (no token needed)
  { "action": "list",   "token": "..." }        -> { "users": [{email, kind, credits}] }
  { "action": "delete", "token": "...", "email": "..." }
                                                -> { "ok": true, "deleted": "<email>" }

"Delete" removes the account record, its credit balance, its API keys, and any
pending sign-up — the user can no longer sign in. (Opaque session tokens simply
stop resolving to a usable account.)
"""
import os
import json
import secrets
import urllib.request
from http.server import BaseHTTPRequestHandler


def _store():
    url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    tok = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    return url, tok


def _kv_on():
    url, tok = _store()
    return bool(url and tok)


def _cmd(*args):
    url, tok = _store()
    req = urllib.request.Request(
        url, data=json.dumps([str(a) for a in args]).encode(),
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()).get("result")


def _scan(match):
    """Return every key matching a glob pattern (handles SCAN cursor paging)."""
    cursor, keys = "0", []
    while True:
        out = _cmd("SCAN", cursor, "MATCH", match, "COUNT", "500")
        cursor, batch = out[0], out[1]
        keys += batch or []
        if str(cursor) == "0":
            break
    return keys


def _admin_token():
    return os.environ.get("ADMIN_TOKEN")


def _authed(body):
    want = _admin_token()
    got = body.get("token") or ""
    return bool(want) and secrets.compare_digest(str(got), str(want))


def handle(body):
    action = (body.get("action") or "").lower()
    if action == "config":
        return {"enabled": bool(_admin_token()) and _kv_on()}
    if not _admin_token():
        return {"error": "admin not configured"}
    if not _authed(body):
        return {"error": "unauthorized"}
    if not _kv_on():
        return {"error": "datastore not configured"}

    if action == "list":
        users = []
        for k in _scan("user:*"):
            email = k[len("user:"):]
            raw = _cmd("GET", k)
            try:
                rec = json.loads(raw) if raw else {}
            except Exception:
                rec = {}
            cr = _cmd("GET", "credits:" + email)
            users.append({
                "email": email,
                "kind": "google" if rec.get("google") else "email",
                "credits": int(cr) if cr is not None else None,
            })
        users.sort(key=lambda u: u["email"])
        return {"users": users, "count": len(users)}

    if action == "delete":
        email = (body.get("email") or "").strip().lower()
        if not email:
            return {"error": "provide an email to delete"}
        if not _cmd("GET", "user:" + email):
            return {"error": "no account for that email"}
        # remove the account's API keys (+ their reverse lookups) then the account itself
        for key in (_cmd("SMEMBERS", "keys:" + email) or []):
            _cmd("DEL", "apikey:" + key)
            _cmd("DEL", "keymeta:" + key)
        _cmd("DEL", "keys:" + email)
        _cmd("DEL", "credits:" + email)
        _cmd("DEL", "pending:" + email)
        _cmd("DEL", "user:" + email)
        return {"ok": True, "deleted": email}

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
            code = 401 if out.get("error") in ("unauthorized", "admin not configured") else 200
            self._send(out, code)
        except Exception as e:
            self._send({"error": str(e)}, 500)
