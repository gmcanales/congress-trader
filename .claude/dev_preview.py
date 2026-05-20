"""Dev-only preview server: serves the frontend and proxies /api to the backend.
Not part of the deployed app — production uses the nginx container.
"""
import http.server
import socketserver
import urllib.request
import urllib.error

PORT = 3000
FRONTEND_DIR = "frontend"
BACKEND = "http://localhost:8000"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def _proxy(self, body=None):
        url = BACKEND + self.path
        req = urllib.request.Request(url, data=body, method=self.command)
        ct = self.headers.get("Content-Type")
        if ct:
            req.add_header("Content-Type", ct)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.end_headers()
                self.wfile.write(resp.read())
        except urllib.error.HTTPError as exc:
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(exc.read())
        except Exception as exc:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(str(exc).encode())

    def do_GET(self):
        if self.path.startswith("/api/") or self.path == "/health":
            self._proxy()
        else:
            super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self._proxy(self.rfile.read(length) if length else None)


socketserver.ThreadingTCPServer.allow_reuse_address = True
with socketserver.ThreadingTCPServer(("", PORT), Handler) as httpd:
    print(f"dev preview on http://localhost:{PORT} (proxying /api -> {BACKEND})")
    httpd.serve_forever()
