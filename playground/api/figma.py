"""
Vercel function /api/figma — the agent's Figma reader.

POST { "url": "<figma file url or key>" } -> returns the file's local Variables
(GET /v1/files/:key/variables/local). Needs a Figma token in the env:
  FIGMA_TOKEN  (Figma → Settings → Security → Personal access tokens)

Note: the Variables REST API is available on Figma Enterprise files. If unavailable,
the UI falls back to pasting the tokens JSON directly (no token needed).
"""
import json
import os
import re
import urllib.request
from http.server import BaseHTTPRequestHandler


def file_key(s):
    m = re.search(r"figma\.com/(?:file|design)/([A-Za-z0-9]+)", s or "")
    return m.group(1) if m else (s or "").strip()


class handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        tok = os.environ.get("FIGMA_TOKEN")
        if not tok:
            return self._send({"error": "FIGMA_TOKEN not set. Add it in Vercel env, "
                                        "or paste the tokens JSON instead."}, 400)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            key = file_key(body.get("url") or body.get("key"))
            req = urllib.request.Request(
                f"https://api.figma.com/v1/files/{key}/variables/local",
                headers={"X-Figma-Token": tok})
            with urllib.request.urlopen(req, timeout=30) as r:
                return self._send(json.loads(r.read()))
        except Exception as e:
            return self._send({"error": str(e)}, 400)
