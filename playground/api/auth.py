"""
Vercel function /api/auth — minimal email + password auth for the Catalyst UI beta.

Backed by an Upstash Redis / Vercel KV store over its REST API (stdlib only, no deps).
Provision it in Vercel (Storage → KV, or Marketplace → Upstash Redis); it injects
KV_REST_API_URL + KV_REST_API_TOKEN (or UPSTASH_REDIS_REST_URL/_TOKEN). Until those env
vars exist, auth reports "disabled" and the app stays open (no gate).

POST body: {"action": "status" | "signup" | "verify" | "resend" | "login" | "me" | "logout", ...}

Sign-up is two-step when RESEND_API_KEY is set: "signup" emails a 6-digit code and holds
the account as pending:<email>; "verify" checks the code and creates the account. Without
an email provider it falls back to immediate (unverified) sign-up.

Beta-grade: passwords are pbkdf2-hashed; sessions are opaque tokens with a TTL.
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
SIGNUP_LIMIT = 20                # successful sign-ups allowed per IP per hour (spam guard)
SEND_COOLDOWN = 45               # seconds between transactional emails to the SAME address (anti-bombing)
EMAIL_DAILY_CAP = int(os.environ.get("EMAIL_DAILY_CAP", "200"))  # global daily send ceiling (cost/quota safety valve)
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


def _send_gate(email):
    """Rate-limit transactional email: a per-address cooldown (stops inbox-bombing a victim) plus a
    global daily ceiling (caps cost/quota if a distributed attack tries to burn the Resend budget).
    Returns None if allowed, else a short user-facing message. Fails OPEN on KV errors."""
    import time as _t
    try:
        if _cmd("GET", "sendcd:" + email):
            return "Please wait a moment before requesting another email."
        day = "sendday:" + _t.strftime("%Y%m%d", _t.gmtime())
        n = int(_cmd("INCR", day) or 1)
        if n == 1:
            _cmd("EXPIRE", day, 172800)   # keep the counter ~2 days
        if n > EMAIL_DAILY_CAP:
            return "We've hit today's email limit — please try again later."
        _cmd("SET", "sendcd:" + email, "1", "EX", SEND_COOLDOWN)
        return None
    except Exception:
        return None   # never block a legitimate user because the rate store hiccuped


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def _send_email(to, subject, html):
    """Send an email via the Resend HTTPS API. Returns True on success (False if no key / send fails).

    On failure it logs the real Resend status + body to stderr (visible in Vercel function logs) so
    the common causes are diagnosable — most often: (1) the API key was rotated/revoked → 401, or
    (2) sending from the shared onboarding@resend.dev test address, which can ONLY deliver to the
    Resend account owner's own email → 403 for every other recipient. Fix (2) by verifying a domain
    in Resend and setting RESEND_FROM to an address on it.
    """
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        import sys
        print("[auth] email skipped: RESEND_API_KEY not set", file=sys.stderr)
        return False
    import sys
    default_from = "Catalystic UI <onboarding@resend.dev>"
    configured = os.environ.get("RESEND_FROM") or default_from
    # Try the configured sender first; if it fails (e.g. RESEND_FROM points at an unverified domain),
    # fall back once to Resend's shared onboarding sender so owner-bound mail (feedback, owner signup)
    # still goes out. To email arbitrary recipients, verify a domain and set RESEND_FROM to it.
    froms = [configured] if configured == default_from else [configured, default_from]
    last = ""
    for frm in froms:
        payload = json.dumps({"from": frm, "to": [to], "subject": subject, "html": html}).encode()
        req = urllib.request.Request(
            "https://api.resend.com/emails", data=payload,
            # A real User-Agent is required: Resend is behind Cloudflare, which blocks the default
            # "Python-urllib" client signature with error 1010. Send a normal UA + Accept.
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                     "Accept": "application/json",
                     "User-Agent": "Mozilla/5.0 (compatible; CatalysticUI/1.0; +https://catalysticui.space)"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                if 200 <= r.status < 300:
                    return True
                last = "status %d" % r.status
        except Exception as e:
            try:
                last = e.read().decode()[:500]   # urllib.error.HTTPError carries the response body
            except Exception:
                last = str(e)
            print("[auth] Resend send failed from=%r to=%r: %s" % (frm, to, last), file=sys.stderr)
    return False


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _send_code_email(to, code):
    """Send a 6-digit verification code. Returns True on success."""
    html = (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:440px;'
        'margin:0 auto;padding:28px 8px;color:#0b0f14">'
        '<div style="font-weight:700;font-size:17px;letter-spacing:-.02em">Catalystic UI</div>'
        '<p style="font-size:15px;color:#54606b;margin:18px 0 8px">Confirm your email to finish creating your account.</p>'
        '<div style="font-size:34px;font-weight:700;letter-spacing:.32em;background:#f4f5f7;border:1px solid #e6e8ec;'
        'border-radius:12px;padding:18px 0;text-align:center;margin:10px 0 14px">' + code + '</div>'
        '<p style="font-size:13px;color:#8a94a0;line-height:1.5">This code expires in 15 minutes. '
        "If you didn't request it, you can ignore this email.</p></div>"
    )
    return _send_email(to, code + " is your Catalystic UI verification code", html)


def _send_reset_email(to, code):
    """Send a 6-digit password-reset code. Returns True on success."""
    html = (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:440px;'
        'margin:0 auto;padding:28px 8px;color:#0b0f14">'
        '<div style="font-weight:700;font-size:17px;letter-spacing:-.02em">Catalystic UI</div>'
        '<p style="font-size:15px;color:#54606b;margin:18px 0 8px">Use this code to reset your password.</p>'
        '<div style="font-size:34px;font-weight:700;letter-spacing:.32em;background:#f4f5f7;border:1px solid #e6e8ec;'
        'border-radius:12px;padding:18px 0;text-align:center;margin:10px 0 14px">' + code + '</div>'
        '<p style="font-size:13px;color:#8a94a0;line-height:1.5">This code expires in 15 minutes. '
        "If you didn't request a password reset, you can safely ignore this email — your password won't change.</p></div>"
    )
    return _send_email(to, code + " is your Catalystic UI password reset code", html)


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
        rec = {"salt": salt.hex(), "hash": _hash(pw, salt)}
        # If an email provider is configured, hold the account as "pending" and email a 6-digit code.
        # The user MUST enter that code (the "verify" action) before the account is created — no bypass.
        if os.environ.get("RESEND_API_KEY"):
            gate = _send_gate(email)
            if gate:
                return {"error": gate}
            code = "%06d" % secrets.randbelow(1000000)
            pending_rec = dict(rec, code=code, tries=0)
            _cmd("SET", "pending:" + email, json.dumps(pending_rec), "EX", 900)  # 15 min
            if not _send_code_email(email, code):
                _cmd("DEL", "pending:" + email)   # don't strand a pending record we couldn't deliver
                return {"error": "Couldn't send the verification email — try again in a moment."}
            return {"pending": True, "email": email}
        # No email provider configured → create the account immediately (legacy, unverified).
        _cmd("SET", "user:" + email, json.dumps(rec))
        return {"token": _new_session(email), "email": email}

    if action == "verify":
        email = (body.get("email") or "").strip().lower()
        code = (body.get("code") or "").strip()
        raw = _cmd("GET", "pending:" + email)
        if not raw:
            return {"error": "This code expired — start sign-up again."}
        rec = json.loads(raw)
        if rec.get("tries", 0) >= 6:
            _cmd("DEL", "pending:" + email)
            return {"error": "Too many attempts — start sign-up again."}
        if not code or code != rec.get("code"):
            rec["tries"] = rec.get("tries", 0) + 1
            _cmd("SET", "pending:" + email, json.dumps(rec), "KEEPTTL")
            return {"error": "Incorrect code — check the 6 digits and try again."}
        if _cmd("GET", "user:" + email):
            _cmd("DEL", "pending:" + email)
            return {"error": "That email already has an account — sign in instead."}
        _cmd("SET", "user:" + email, json.dumps({"salt": rec["salt"], "hash": rec["hash"]}))
        _cmd("DEL", "pending:" + email)
        return {"token": _new_session(email), "email": email}

    if action == "resend":
        email = (body.get("email") or "").strip().lower()
        raw = _cmd("GET", "pending:" + email)
        if not raw:
            return {"error": "Start sign-up again."}
        gate = _send_gate(email)
        if gate:
            return {"error": gate}
        rec = json.loads(raw)
        code = "%06d" % secrets.randbelow(1000000)
        rec["code"] = code
        rec["tries"] = 0
        _cmd("SET", "pending:" + email, json.dumps(rec), "EX", 900)
        if not _send_code_email(email, code):
            return {"error": "Couldn't resend the email — try again."}
        return {"pending": True, "email": email}

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

    if action == "forgot":
        email = (body.get("email") or "").strip().lower()
        if not EMAIL_RE.match(email):
            return {"error": "Enter a valid email address."}
        # Only email a code if the account exists AND we're not rate-limited, but ALWAYS report success
        # (no account enumeration; a rate-limited request just doesn't resend — the recent code still works).
        if _cmd("GET", "user:" + email) and not _send_gate(email):
            code = "%06d" % secrets.randbelow(1000000)
            _cmd("SET", "reset:" + email, json.dumps({"code": code, "tries": 0}), "EX", 900)  # 15 min
            _send_reset_email(email, code)
        return {"ok": True, "email": email}

    if action == "reset":
        email = (body.get("email") or "").strip().lower()
        code = (body.get("code") or "").strip()
        pw = body.get("password") or ""
        if len(pw) < 8:
            return {"error": "Password must be at least 8 characters."}
        raw = _cmd("GET", "reset:" + email)
        if not raw:
            return {"error": "This reset code expired — request a new one."}
        rec = json.loads(raw)
        if rec.get("tries", 0) >= 6:
            _cmd("DEL", "reset:" + email)
            return {"error": "Too many attempts — request a new code."}
        if not code or code != rec.get("code"):
            rec["tries"] = rec.get("tries", 0) + 1
            _cmd("SET", "reset:" + email, json.dumps(rec), "KEEPTTL")
            return {"error": "Incorrect code — check the 6 digits and try again."}
        uraw = _cmd("GET", "user:" + email)
        if not uraw:
            _cmd("DEL", "reset:" + email)
            return {"error": "No account for that email."}
        urec = json.loads(uraw)
        salt = os.urandom(16)
        urec["salt"] = salt.hex(); urec["hash"] = _hash(pw, salt)
        urec.pop("google", None)   # the account now has a password
        _cmd("SET", "user:" + email, json.dumps(urec))
        _cmd("DEL", "reset:" + email)
        return {"token": _new_session(email), "email": email}   # signed in with the new password

    if action == "me":
        email = session_email(body.get("token"))
        if not email:
            return {"error": "not signed in"}
        try:
            c = _cmd("GET", "credits:" + email)
            credits = int(c) if c is not None else 50   # matches chat.FREE_CREDITS / keys.DEFAULT_CREDITS
        except Exception:
            credits = None
        # granted = the TOTAL ever allotted (free seed + purchases). Tracked server-side so "usage"
        # (granted - remaining) is stable across logout/login. Backfill it once for existing accounts.
        granted = None
        try:
            g = _cmd("GET", "granted:" + email)
            if g is None:
                granted = max(50, credits if isinstance(credits, int) else 50)
                _cmd("SET", "granted:" + email, granted)
            else:
                granted = int(g)
        except Exception:
            granted = None
        return {"email": email, "credits": credits, "granted": granted}

    if action == "logout":
        t = body.get("token")
        if t:
            try:
                _cmd("DEL", "sess:" + t)
            except Exception:
                pass
        return {"ok": True}

    if action == "feedback":
        msg = (body.get("message") or "").strip()
        if not msg:
            return {"error": "Write a little feedback first."}
        msg = msg[:4000]
        who = session_email(body.get("token")) or (body.get("email") or "").strip().lower() or "anonymous"
        to = os.environ.get("FEEDBACK_TO") or "devadhathanmd18@gmail.com"
        html = (
            '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:520px;color:#0b0f14">'
            '<div style="font-weight:700;font-size:16px">New Catalystic UI feedback</div>'
            '<p style="font-size:13px;color:#54606b;margin:6px 0 14px">From: <b>' + _esc(who) + '</b></p>'
            '<div style="white-space:pre-wrap;font-size:14px;line-height:1.5;background:#f6f7f9;border:1px solid #e6e8ec;'
            'border-radius:10px;padding:14px">' + _esc(msg) + '</div></div>'
        )
        # Persist to KV first so feedback is NEVER lost, even if email delivery is misconfigured
        # (e.g. a restricted RESEND_API_KEY). Email is best-effort on top of the durable store.
        stored = False
        try:
            entry = json.dumps({"from": who, "message": msg, "at": int(__import__("time").time())})
            _cmd("LPUSH", "feedback:log", entry)
            _cmd("LTRIM", "feedback:log", 0, 499)   # keep the latest 500
            stored = True
        except Exception:
            pass
        emailed = _send_email(to, "Catalystic UI feedback — " + who, html)
        if stored or emailed:
            return {"ok": True}
        return {"error": "Couldn't send your feedback — try again in a moment."}

    return {"error": "unknown action"}


class handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _ip(self):
        xff = self.headers.get("x-forwarded-for", "")
        return (xff.split(",")[0].strip() if xff else self.headers.get("x-real-ip", "")) or "unknown"

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send({"error": "bad request"}, 400)
        # throttle account creation per IP (blocks scripted spam signups that would burn the budget).
        # Only SUCCESSFUL signups count — failed email sends, invalid inputs, and "already exists"
        # must NOT burn a slot (otherwise ordinary retries lock a real user out).
        action = (body.get("action") or "").lower()
        ip_key = "signup:" + self._ip()
        if action == "signup" and enabled():
            try:
                if int(_cmd("GET", ip_key) or 0) >= SIGNUP_LIMIT:
                    return self._send({"error": "Too many sign-ups from your network — try again later."}, 429)
            except Exception:
                pass
        try:
            out = handle(body)
            # count only a signup that actually did something (emailed a code or created an account)
            if action == "signup" and enabled() and (out.get("pending") or out.get("token")):
                try:
                    if int(_cmd("INCR", ip_key)) == 1:
                        _cmd("EXPIRE", ip_key, 3600)
                except Exception:
                    pass
            self._send(out, 401 if out.get("error") == "not signed in" else 200)
        except Exception as e:
            self._send({"error": str(e)}, 500)
