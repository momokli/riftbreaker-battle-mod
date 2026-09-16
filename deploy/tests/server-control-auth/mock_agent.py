#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mock des Server-Control-Agenten fuer den Auth-Test (Issue #454).

Bildet die EINE Eigenschaft nach, auf die es hier ankommt: JEDER Request braucht
``Authorization: Bearer <token>`` (wie ``deploy/server-control/server_control.py``,
``hmac.compare_digest``). Alles andere (docker, config.cfg) ist fuer den
Caddy-Auth-Test irrelevant.

Zusaetzlich schreibt er jede gesehene Authorization-Zeile nach ``--seen`` (JSONL).
Damit ist BELEGBAR, welchen Header Caddy dem Agenten schickt — genau das
beweist, dass der Bearer von Caddy injiziert wird und der Browser ihn nie sieht.

Aufruf: mock_agent.py <port> <token> <seen-file>
"""

import hmac
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1])
TOKEN = sys.argv[2]
SEEN = sys.argv[3]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _record(self):
        with open(SEEN, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "path": self.path,
                        "method": self.command,
                        "authorization": self.headers.get("Authorization", ""),
                    }
                )
                + "\n"
            )

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        self._record()
        header = self.headers.get("Authorization", "")
        expected = "Bearer " + TOKEN
        if not hmac.compare_digest(header.encode(), expected.encode()):
            self._send(401, {"error": "unauthorized"})
            return
        self._send(200, {"state": "running", "health": "ok", "path": self.path})

    do_GET = _handle
    do_POST = _handle

    def log_message(self, *args):  # kein Zugriffslog auf stderr
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
