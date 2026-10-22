"""A stand-in for a restarted Reef on 127.0.0.1: /api/health and /api/diagnostics (reef commit, yt-dlp version)
behind Reef's own Basic-auth rule (werkzeug's header parsing, the password compared like app.require_password).
Used by the updater and update.sh health-check tests; nothing leaves loopback."""
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from werkzeug.datastructures import Authorization


class FakeReef:
    def __init__(self, password=None, reef="abc1234", ytdlp="2026.10.01"):
        self.password, self.reef, self.ytdlp = password, reef, ytdlp
        self.redirect = None  # a URL: /api/diagnostics answers 302 there (after the auth check)
        self.seen = []  # (path, Authorization header or None) per request
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                header = self.headers.get("Authorization")
                fake.seen.append((self.path, header))
                if fake.password is not None:
                    auth = Authorization.from_header(header)
                    if not (auth and hmac.compare_digest((auth.password or "").encode(), fake.password.encode())):
                        return self._send(401, {"error": "Password required"})
                if self.path == "/api/health":
                    return self._send(200, {"reef": "ok"})
                if self.path == "/api/diagnostics" and fake.redirect:
                    self.send_response(302)
                    self.send_header("Location", fake.redirect)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
                if self.path == "/api/diagnostics":
                    return self._send(200, {"version": {"reef": fake.reef, "ytdlp": fake.ytdlp}})
                self._send(404, {})

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def headers(self):
        return {h for _, h in self.seen}
