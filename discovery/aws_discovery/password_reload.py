"""Sidecar: reload an atomically projected password file; acknowledge a random generation, never its contents."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import threading
import time
import urllib.request

generation = ""


def watch(directory=Path("/etc/redis-credentials")):
    global generation
    while True:
        try:
            snapshot = (directory / "..data").resolve()
            candidate = (snapshot / "generation").read_text().strip()
            json.loads((snapshot / "passwords.json").read_text())
            if candidate != generation:
                request = urllib.request.Request("http://127.0.0.1:9121/-/reload", method="POST", data=b"")
                with urllib.request.urlopen(request, timeout=5) as response:
                    if response.status == 200 and (directory / "generation").read_text().strip() == candidate:
                        generation = candidate
        except Exception:
            # Do not log file contents, HTTP bodies or exception representations.
            pass
        time.sleep(1)


class Ready(BaseHTTPRequestHandler):
    def do_GET(self):
        body = generation.encode()
        self.send_response(200 if generation else 503)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=watch, daemon=True).start()
    HTTPServer(("0.0.0.0", 9122), Ready).serve_forever()
