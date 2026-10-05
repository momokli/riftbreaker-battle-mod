#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Screenshots fuer Issue #1031 (Oeffentliche Match-View, `GET /match`).

Extrahiert die in `gns_probe.cpp` eingebettete read-only Match-View
(`kMatchHtml`, der `R\"HTML(`-Block, der den Sentinel `MATCH-VIEW BEGIN`
enthaelt) und rendert sie in headless Chromium gegen einen minimalen lokalen
Mock des Relay-Backends. Die Seite ist die **echte** eingebettete UI (kein
Nachbau); nur die Backends sind gemockt:
  * `GET /`                 -> das extrahierte HTML,
  * `GET /referee/state`    -> je Zustand ein synthetischer State (oder 503
                               `referee_unconfigured` fuer den Offline-Fall),
  * `GET /referee/events`   -> Cursor-Feed `{events, last_seq}`.

Aufruf (aus dem Repo-Wurzelverzeichnis):

    python3 docs/screenshots/1031/render.py

Erzeugt (`--force-device-scale-factor=2`, Fenster 1100x900):
    00-lobby.png    — Phase lobby, beide Welten verbunden, noch nicht ready
    01-ready.png    — Phase ready, beide Teams ready, Winner offen
    02-running.png  — Phase running, Runde 2, HQ-Werte fallen
    03-paused.png   — Phase running + `paused:true` (Statusleiste "paused")
    04-offline.png  — `/referee/state` antwortet 503 (referee_unconfigured)

Der Chromium-Pfad wird ueber CHROMIUM (Env) oder den Playwright-Cache gesucht.
Mock-Werte sind rein synthetisch (keine Tokens, keine echten Serverdaten).
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

SIZE = "1100x900"


def _feed(*events):
    return [{"seq": s, "ts": t, "kind": k, "world": w, "msg": m}
            for (s, t, k, w, m) in events]


FEED = _feed(
    (1, "12:00:01", "round_start", "*", "Runde 2 gestartet"),
    (2, "12:00:05", "send", "A", "natural 3 -> B"),
    (3, "12:00:06", "hq", "B", "HQ -32"),
    (4, "12:00:12", "wave", "B", "Welle 3"),
    (5, "12:00:19", "send", "B", "self 5 -> A"),
)


def _team(player, ready, hq_hp, score, wave, pending_sends):
    return {"player": player, "ready": ready, "hq_hp": hq_hp, "score": score,
            "wave": wave, "pending_sends": pending_sends}


STATES = {
    "00-lobby": {
        "match_id": "rift-1", "mode": "vs", "phase": "lobby", "round": 0,
        "winner": None, "paused": False,
        "teams": {"A": _team("str:AB12", False, 100.0, 0, 0, 0),
                  "B": _team("str:CD34", False, 100.0, 0, 0, 0)},
        "feed": [],
    },
    "01-ready": {
        "match_id": "rift-1", "mode": "vs", "phase": "ready", "round": 0,
        "winner": None, "paused": False,
        "teams": {"A": _team("str:AB12", True, 100.0, 0, 0, 0),
                  "B": _team("str:CD34", True, 100.0, 0, 0, 0)},
        "feed": _feed((1, "12:00:01", "ready", "A", "Welt A ready"),
                      (2, "12:00:03", "ready", "B", "Welt B ready")),
    },
    "02-running": {
        "match_id": "rift-1", "mode": "vs", "phase": "running", "round": 2,
        "winner": None, "paused": False,
        "teams": {"A": _team("str:AB12", True, 100.0, 3, 3, 2),
                  "B": _team("str:CD34", True, 68.4, 1, 3, 1)},
        "feed": FEED,
    },
    "03-paused": {
        "match_id": "rift-1", "mode": "vs", "phase": "running", "round": 2,
        "winner": None, "paused": True,
        "teams": {"A": _team("str:AB12", True, 92.1, 3, 3, 0),
                  "B": _team("str:CD34", True, 68.4, 1, 3, 0)},
        "feed": FEED,
    },
    "04-offline": {"__http__": 503,
                   "body": {"ok": False, "reason": "referee_unconfigured",
                            "retry": False}},
}

EVENTS = {"events": FEED, "last_seq": 5}


def extract_match_html(text: str) -> str:
    """Den Raw-String nehmen, der den `MATCH-VIEW BEGIN`-Sentinel enthaelt
    (gns_probe.cpp hat mehrere `R\"HTML( ... )HTML\"`-Bloecke)."""
    for m in re.finditer(r'R"HTML\((.*?)\)HTML"', text, re.S):
        block = m.group(1)
        if "MATCH-VIEW BEGIN" in block:
            return block
    raise SystemExit('kMatchHtml nicht gefunden (R"HTML( ... )HTML" mit Sentinel)')


def find_chrome() -> str:
    env = os.environ.get("CHROMIUM")
    if env and os.path.exists(env):
        return env
    cache = os.path.expanduser("~/.cache/ms-playwright")
    for name in (sorted(os.listdir(cache), reverse=True)
                 if os.path.isdir(cache) else []):
        if name.startswith("chromium-"):
            for sub in ("chrome-linux64", "chrome-linux"):
                cand = os.path.join(cache, name, sub, "chrome")
                if os.path.exists(cand):
                    return cand
    raise SystemExit("kein Chromium gefunden (CHROMIUM setzen)")


def make_handler(html, state):
    offline = isinstance(state, dict) and "__http__" in state

    class MockHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D401 - still
            pass

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
                self._send(200, html, "text/html; charset=utf-8")
            elif path == "/referee/state":
                if offline:
                    self._send(state["__http__"], json.dumps(state["body"]))
                else:
                    self._send(200, json.dumps(state))
            elif path == "/referee/events":
                self._send(200, json.dumps(EVENTS))
            else:
                self._send(404, "{}")

    return MockHandler


def render(html, state, out_png, chrome):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(html, state))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        subprocess.check_call([
            chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
            "--hide-scrollbars", "--force-device-scale-factor=2",
            "--window-size=" + SIZE, "--virtual-time-budget=6000",
            "--screenshot=" + out_png, "http://127.0.0.1:%d/" % port],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        httpd.shutdown()
        httpd.server_close()
    print("geschrieben:", out_png)


def main() -> int:
    chrome = find_chrome()
    with open(SRC, encoding="utf-8") as fh:
        html = extract_match_html(fh.read())
    for name, state in STATES.items():
        render(html, state, os.path.join(HERE, name + ".png"), chrome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
