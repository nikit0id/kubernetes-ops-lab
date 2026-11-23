"""Report allocated blocks of the lab's local-path volume, read-only."""

import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        usage = subprocess.run(["du", "-s", "-B1", "/claim"], capture_output=True)
        if usage.returncode:
            self.send_error(503)
            return
        used = int(usage.stdout.split()[0])
        labels = '{persistentvolumeclaim="data-portfolio-postgres-0"}'
        metric = f"portfolio_pvc_used_bytes{labels} {used}\n"
        self.send_response(200)
        self.end_headers()
        self.wfile.write(metric.encode())


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9403), Handler).serve_forever()
