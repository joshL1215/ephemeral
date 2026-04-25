"""Local ingest server for testing the ephemeral SDK."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import sys


class IngestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            events = data.get("events", [])
            print(f"\n[server] received {len(events)} event(s):", flush=True)
            for ev in events:
                print(json.dumps(ev, indent=2), flush=True)
        except Exception as e:
            print(f"[server] parse error: {e}\nraw: {body}", flush=True)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, fmt, *args):
        pass  # suppress default access logs


if __name__ == "__main__":
    server = HTTPServer(("localhost", 8000), IngestHandler)
    print("listening on http://localhost:8000", flush=True)
    server.serve_forever()
