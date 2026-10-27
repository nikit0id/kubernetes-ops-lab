"""Small HTTP service for a local Kubernetes lab, not a production server."""

import json
import os
import signal
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            status, body = (
                200,
                {
                    "service": "ops-lab",
                    "version": os.environ.get("APP_VERSION", "1.0.0"),
                    "hostname": socket.gethostname(),
                },
            )
        elif path in ("/healthz", "/readyz"):
            # No external dependencies: ready as soon as the server can respond.
            status, body = 200, {"status": "ok"}
        else:
            status, body = 404, {"error": "not_found"}
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        print(json.dumps({"event": "http", "message": fmt % args}), flush=True)


def make_server(host="0.0.0.0", port=8080):
    return ThreadingHTTPServer((host, port), Handler)


def main():
    server = make_server(port=int(os.environ.get("PORT", "8080")))

    def stop(signum, frame):
        # shutdown must run outside serve_forever's thread to avoid a deadlock.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({"event": "started", "port": server.server_port}), flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
