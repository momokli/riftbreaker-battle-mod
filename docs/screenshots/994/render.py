#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Screenshots fuer Issue #994 (Lobby Main-Screen + Status-Badge).

Extrahiert die in `gns_probe.cpp` eingebettete `kUiHtml`-Steuer-UI (Slice
zwischen `R\"HTML(` und `)HTML\"`) und rendert sie in headless Chromium gegen
einen minimalen lokalen Mock der Steuer-API. Reines Stdlib-Script, keine
Build-Targets (additiv im docs/-Baum, Muster docs/screenshots/930).

Aufruf (aus dem Repo-Wurzelverzeichnis):

    python3 docs/screenshots/994/render.py

Erzeugt:
    01-lobby-before.png  — Layout aus origin/main (9ba5ca3, Session-Liste zuerst)
    02-lobby-after.png   — Main-Screen (Modi-Kacheln + Status-Badge)

Der Chromium-Pfad wird ueber CHROMIUM (Env) oder den Playwright-Cache gesucht.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SRC = os.path.join(REPO, "tools", "gns-proxy", "gns_probe.cpp")
HERE = os.path.dirname(os.path.abspath(__file__))
BASE_REF = "9ba5ca3"  # origin/main vor #994 (Baseline)

# Mock-Sessions: decken die vier Phasen des Status-Badges ab (Diagnose) und
# liefern der Main-Screen-Kachel ein Ziel.
SESSIONS = [
    {"name": "momo", "identity": "str:AB12", "state": "held", "pinned": True,
     "ip": "10.0.0.5", "messages": 12, "age_seconds": 41, "held_seconds": 9,
     "connected": True, "soloPhase": "running", "soloInstance": "parked-1",
     "soloMemberCount": 1, "soloMaxPlayers": 4, "soloMembers": ["str:AB12"],
     "target": "127.0.0.1:32768"},
    {"name": "gast", "identity": "str:CD34", "state": "connected", "pinned": False,
     "ip": "10.0.0.6", "messages": 3, "age_seconds": 18, "connected": True,
     "soloPhase": "underway", "soloInstance": "parked-2",
     "soloMemberCount": 1, "soloMaxPlayers": 4, "soloMembers": ["str:CD34"]},
    {"name": "neu", "identity": "str:EF56", "state": "waiting", "pinned": False,
     "ip": "10.0.0.7", "messages": 0, "age_seconds": 4, "held_seconds": 4,
     "connected": False, "soloPhase": "provisioned", "soloInstance": "parked-3",
     "soloMemberCount": 1, "soloMaxPlayers": 4, "soloMembers": ["str:EF56"]},
]
TARGETS = [{"name": "parked-1", "endpoint": "127.0.0.1:32768"},
           {"name": "parked-2", "endpoint": "127.0.0.1:32769"}]


def extract_ui(text: str) -> str:
    m = re.search(r'R"HTML\((.*?)\)HTML"', text, re.S)
    if not m:
        raise SystemExit("kUiHtml nicht gefunden (R\"HTML( ... )HTML\")")
    return m.group(1)


def read_working() -> str:
    with open(SRC, "r", encoding="utf-8") as fh:
        return fh.read()


def read_baseline() -> str:
    out = subprocess.check_output(["git", "show", "%s:tools/gns-proxy/gns_probe.cpp" % BASE_REF],
                                  cwd=REPO)
    return out.decode("utf-8")


def find_chrome() -> str:
    env = os.environ.get("CHROMIUM")
    if env and os.path.exists(env):
        return env
    cache = os.path.expanduser("~/.cache/ms-playwright")
    for name in sorted(os.listdir(cache), reverse=True) if os.path.isdir(cache) else []:
        if name.startswith("chromium-"):
            cand = os.path.join(cache, name, "chrome-linux64", "chrome")
            if os.path.exists(cand):
                return cand
            cand = os.path.join(cache, name, "chrome-linux", "chrome")
            if os.path.exists(cand):
                return cand
    raise SystemExit("kein Chromium gefunden (CHROMIUM setzen)")


class MockHandler(BaseHTTPRequestHandler):
    html = ""
    quiet = False

    def log_message(self, *args):  # noqa: D401 - still
        if not self.quiet:
            sys.stderr.write("mock: " + (args[0] % args[1:]) + "\n")

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, self.html, "text/html; charset=utf-8")
        elif path == "/targets":
            self._send(200, json.dumps(TARGETS))
        elif path == "/sessions":
            self._send(200, json.dumps(SESSIONS))
        else:
            self._send(404, "{}")

    def do_POST(self):  # noqa: N802
        self._send(200, json.dumps({"ok": True, "identitaet": "str:AB12",
                                    "target": "127.0.0.1:32768", "instance": "parked-1"}))


def render(html: str, out_png: str, chrome: str) -> None:
    handler = type("H", (MockHandler,), {"html": html})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        cmd = [chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
               "--hide-scrollbars", "--force-device-scale-factor=2",
               "--window-size=1280,860", "--virtual-time-budget=4000",
               "--screenshot=" + out_png,
               "http://127.0.0.1:%d/" % port]
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        httpd.shutdown()
        httpd.server_close()
    print("geschrieben:", out_png)


def main() -> int:
    chrome = find_chrome()
    render(extract_ui(read_baseline()), os.path.join(HERE, "01-lobby-before.png"), chrome)
    render(extract_ui(read_working()), os.path.join(HERE, "02-lobby-after.png"), chrome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
