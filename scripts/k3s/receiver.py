"""Ephemeral webhook receiver for the bounded k3s alert delivery exercise."""

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        for alert in data["alerts"]:
            if alert["status"] == "firing":
                print(alert["labels"]["alertname"], int(time.time()), flush=True)
        self.send_response(200)
        self.end_headers()


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
