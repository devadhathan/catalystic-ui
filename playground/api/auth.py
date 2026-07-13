"""
Vercel function /api/auth — minimal email + password auth for the Catalyst UI beta.

Backed by an Upstash Redis / Vercel KV store over its REST API (stdlib only, no deps).
Provision it in Vercel (Storage → KV, or Marketplace → Upstash Redis); it injects
KV_REST_API_URL + KV_REST_API_TOKEN (or UPSTASH_REDIS_REST_URL/_TOKEN). Until those env
vars exist, auth reports "disabled" and the app stays open (no gate).

POST body: {"action": "status" | "signup" | "login" | "me" | "logout", ...}

Beta-grade: passwords are pbkdf2-hashed; sessions are opaque tokens with a TTL. Not
hardened for production (no email verification / login throttling yet).
"""
import json
import os
import re
import hashlib
import secrets
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

SESSION_TTL = 60 * 60 * 24 * 30  # 30 days
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")  # from Google Cloud Console (OAuth web client)


def _store():
    url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    tok = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    return url, tok


def enabled():
    url, tok = _store()
    return bool(url and tok)


def _cmd(*args):
    """Run one Redis command via the Upstash REST API (POST a JSON command array)."""
    url, tok = _store()
    req = urllib.request.Request(
        url, data=json.dumps([str(a) for a in args]).encode(),
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()).get("result")


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 120000).hex()


def _new_session(email):
    token = secrets.token_urlsafe(32)
    _cmd("SET", "sess:" + token, email, "EX", SESSION_TTL)
    return token


def session_email(token):
    if not token or not enabled():
        return None
    try:
        return _cmd("GET", "sess:" + token)
    except Exception:
        return None


def handle(body):
    action = (body.get("action") or "").lower()
    if action == "status":
        return {"enabled": enabled(), "googleClientId": GOOGLE_CLIENT_ID or ""}
    if not enabled():
        return {"error": "auth not configured"}

    if action == "google":
        if not GOOGLE_CLIENT_ID:
            return {"error": "Google sign-in isn't configured on the server."}
        cred = body.get("credential") or ""
        if not cred:
            return {"error": "Missing Google credential."}
        try:  # verify the ID token via Google's tokeninfo endpoint (no crypto deps)
            info = _get("https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote(cred, safe=""))
        except Exception:
            return {"error": "Couldn't verify your Google sign-in."}
        if info.get("aud") != GOOGLE_CLIENT_ID:
            return {"error": "This Google sign-in was issued for a different app."}
        if str(info.get("email_verified")).lower() not in ("true", "1"):
            return {"error": "Your Google email isn't verified."}
        email = (info.get("email") or "").strip().lower()
        if not email:
            return {"error": "No email returned from Google."}
        if not _cmd("GET", "user:" + email):
            _cmd("SET", "user:" + email, json.dumps({"google": True}))
        return {"token": _new_session(email), "email": email}

    if action == "signup":
        email = (body.get("email") or "").strip().lower()
        pw = body.get("password") or ""
        if not EMAIL_RE.match(email):
            return {"error": "Enter a valid email address."}
        if len(pw) < 8:
            return {"error": "Password must be at least 8 characters."}
        if _cmd("GET", "user:" + email):
            return {"error": "That email already has an account — sign in instead."}
        salt = os.urandom(16)
        _cmd("SET", "user:" + email, json.dumps({"salt": salt.hex(), "hash": _hash(pw, salt)}))
        return {"token": _new_session(email), "email": email}

    if action == "login":
        email = (body.get("email") or "").strip().lower()
        pw = body.get("password") or ""
        raw = _cmd("GET", "user:" + email)
        if not raw:
            return {"error": "No account for that email — create one."}
        rec = json.loads(raw)
        if _hash(pw, bytes.fromhex(rec["salt"])) != rec["hash"]:
            return {"error": "Wrong email or password."}
        return {"token": _new_session(email), "email": email}

    if action == "me":
        email = session_email(body.get("token"))
        return {"email": email} if email else {"error": "not signed in"}

    if action == "logout":
        t = body.get("token")
        if t:
            try:
                _cmd("DEL", "sess:" + t)
            except Exception:
                pass
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
