"""
Vercel function /api/proxy — bring-your-own-agent bridge.

Forwards a chat turn to a team's OWN agent endpoint so their agent can drive
Catalyst's renderer. The client posts:

  POST { "endpoint": "https://their-api/agent", "body": { history, components, mode } }

Their endpoint must return { "reply": "...", "components": [ ...A2UI tree... ] }.
We normalize the response so the front-end treats it exactly like /api/chat.

Guards: https-only, blocks private/loopback/link-local hosts (SSRF), short timeout,
light per-IP rate limit. Dependency-free (stdlib only).
"""
import json
import time
import socket
import ipaddress
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

_MEM = {}


def _client_ip(headers):
    xff = headers.get("x-forwarded-for", "")
    return (xff.split(",")[0].strip() if xff else headers.get("x-real-ip", "")) or "unknown"


def _rate_ok(ip, per_min=20):
    t = time.time()
    arr = [x for x in _MEM.get(ip, []) if t - x < 60]
    if len(arr) >= per_min:
        _MEM[ip] = arr
        return False
    arr.append(t)
    _MEM[ip] = arr
    return True


def _blocked(host):
    """True if the host resolves to a private / loopback / reserved address (SSRF guard)."""
    try:
        for _, _, _, _, sa in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(sa[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return True
    except Exception:
        return True
    return False


class handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        if not _rate_ok(_client_ip(self.headers)):
            return self._send({"error": "Too many requests — slow down a moment."}, 429)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send({"error": "bad request"}, 400)

        ep = (data.get("endpoint") or "").strip()
        body = data.get("body") or {}
        u = urllib.parse.urlparse(ep)
        if u.scheme != "https" or not u.hostname:
            return self._send({"error": "Endpoint must be a valid https:// URL."}, 400)
        if _blocked(u.hostname):
            return self._send({"error": "That host isn't allowed."}, 400)

        try:
            req = urllib.request.Request(ep, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=45) as r:
                out = json.loads(r.read())
        except urllib.error.HTTPError as e:
            return self._send({"error": "Your agent returned HTTP %d." % e.code}, 502)
        except Exception as e:
            return self._send({"error": "Couldn't reach your agent: %s" % str(e)}, 502)

        if not isinstance(out, dict):
            return self._send({"error": "Your agent must return a JSON object with reply + components."}, 502)
        return self._send({
            "reply": out.get("reply", ""),
            "thinking": out.get("thinking", ""),
            "components": out.get("components", []),
            "usage": out.get("usage", {"input": 0, "output": 0, "total": 0}),
            "latency_ms": out.get("latency_ms", 0),
            "model": out.get("model", "your agent"),
            "provider": "byo",
            "tier": out.get("tier", "byo"),
            "searched": out.get("searched", 0),
        })
