#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Screenshots fuer Issue #1000 (Lobby: Queue-Button + Status).

Extrahiert die in `gns_probe.cpp` eingebettete `kUiHtml`-Lobby-UI (Slice
zwischen `R\"HTML(` und `)HTML\"`) und rendert sie in headless Chromium gegen
einen minimalen lokalen Mock der Steuer-API. Die Seite ist die **echte**
eingebettete UI (kein Nachbau); nur die Backends sind gemockt — inklusive
`GET /queue/status` (neu in #1000). Reines Stdlib-Script.

Aufruf (aus dem Repo-Wurzelverzeichnis):

    python3 docs/screenshots/1000/render.py

Erzeugt (je eine Phase der Labelkette, `--force-device-scale-factor=2`):
    01-queued.png        — IN QUEUE … (2 WARTEN) + "Queue verlassen"
    02-matched.png       — MATCH GEFUNDEN
    03-provisioning.png  — PROVISIONIERT
    04-ready.png         — LÄUFT
    05-lobby-full.png    — Gesamtansicht (Modi + Spielerauswahl + Cards)

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

TARGETS = [{"name": "parked-1", "endpoint": "127.0.0.1:32768"},
           {"name": "parked-2", "endpoint": "127.0.0.1:32769"}]


def _session(identity, name, state="held", **extra):
    s = {"name": name, "identity": identity, "state": state, "pinned": False,
         "ip": "10.0.0.5", "messages": 7, "age_seconds": 32, "held_seconds": 9,
         "connected": True, "soloMemberCount": 1, "soloMaxPlayers": 4,
         "soloMembers": [identity]}
    s.update(extra)
    return s


# Je Phase: (/sessions, /queue/status). Phase bevorzugt aus /queue/status (D3),
# /sessions.queuePhase nur Fallback — 02-matched nutzt genau diesen Fallback
# (Relay meldet Paarung, Dienst-Snapshot noch ohne Match-Eintrag).
PHASES = {
    "01-queued": (
        [_session("str:AB12", "momo", queuePhase="queued", queuePosition=1),
         _session("str:CD34", "gast", state="connected", queuePhase="queued",
                  queuePosition=2)],
        {"queued": 2, "matches": [],
         "queue": [{"identitaet": "str:AB12", "mode": "vs", "seq": 1,
                    "waiting_seconds": 3.1},
                   {"identitaet": "str:CD34", "mode": "vs", "seq": 2,
                    "waiting_seconds": 1.4}]}),
    "02-matched": (
        [_session("str:AB12", "momo", queuePhase="matched", matchId=1, vsWorld="A"),
         _session("str:CD34", "gast", state="connected", queuePhase="matched",
                  matchId=1, vsWorld="B")],
        {"queued": 0, "queue": [], "matches": []}),
    "03-provisioning": (
        [_session("str:AB12", "momo", queuePhase="matched", matchId=1, vsWorld="A"),
         _session("str:CD34", "gast", state="connected", queuePhase="matched",
                  matchId=1, vsWorld="B")],
        {"queued": 0, "queue": [],
         "matches": [{"match_id": 1, "state": "provisioning", "teams": [],
                      "participants": [
                          {"identitaet": "str:AB12", "world": "A",
                           "instance": None, "endpoint": None},
                          {"identitaet": "str:CD34", "world": "B",
                           "instance": None, "endpoint": None}]}]}),
    "04-ready": (
        [_session("str:AB12", "momo", queuePhase="ready", matchId=1, vsWorld="A"),
         _session("str:CD34", "gast", state="connected", queuePhase="ready",
                  matchId=1, vsWorld="B")],
        {"queued": 0, "queue": [],
         "matches": [{"match_id": 1, "state": "ready", "teams": [],
                      "participants": [
                          {"identitaet": "str:AB12", "world": "A",
                           "instance": "parked-1", "endpoint": "127.0.0.1:32768"},
                          {"identitaet": "str:CD34", "world": "B",
                           "instance": "parked-2", "endpoint": "127.0.0.1:32769"}]}]}),
    "05-lobby-full": (
        [_session("str:AB12", "momo", queuePhase="queued", queuePosition=1),
         _session("str:CD34", "gast", state="connected", queuePhase="ready",
                  matchId=1, vsWorld="B")],
        {"queued": 1, "queue": [{"identitaet": "str:AB12", "mode": "vs", "seq": 1,
                                 "waiting_seconds": 5.2}],
         "matches": [{"match_id": 1, "state": "ready", "teams": [],
                      "participants": [
                          {"identitaet": "str:CD34", "world": "B",
                           "instance": "parked-2", "endpoint": "127.0.0.1:32769"}]}]}),
}

# Fenstergroessen: 01-04 fokussieren die Cards, 05 die ganze Seite.
SIZES = {"01-queued": "900x760", "02-matched": "900x760",
         "03-provisioning": "900x760", "04-ready": "900x760",
         "05-lobby-full": "1280x1000"}


def extract_ui(text: str) -> str:
    m = re.search(r'R"HTML\((.*?)\)HTML"', text, re.S)
    if not m:
        raise SystemExit('kUiHtml nicht gefunden (R"HTML( ... )HTML")')
    return m.group(1)


def read_working() -> str:
    with open(SRC, "r", encoding="utf-8") as fh:
        return fh.read()


def open_diag(html: str) -> str:
    """Die eingebettete UI bleibt unveraendert; einzig der Diagnose-Bereich
    `<details class="diag">`, in dem die Session-Cards liegen, wird fuer den
    Screenshot aufgeklappt (sonst sind die Cards nicht sichtbar)."""
    return html.replace('<details class="diag">', '<details class="diag" open>')


def find_chrome() -> str:
    env = os.environ.get("CHROMIUM")
    if env and os.path.exists(env):
        return env
    cache = os.path.expanduser("~/.cache/ms-playwright")
    for name in sorted(os.listdir(cache), reverse=True) if os.path.isdir(cache) else []:
        if name.startswith("chromium-"):
            for sub in ("chrome-linux64", "chrome-linux"):
                cand = os.path.join(cache, name, sub, "chrome")
                if os.path.exists(cand):
                    return cand
    raise SystemExit("kein Chromium gefunden (CHROMIUM setzen)")


def make_handler(html, sessions, status):
    class MockHandler(BaseHTTPRequestHandler):
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
                self._send(200, html, "text/html; charset=utf-8")
            elif path == "/targets":
                self._send(200, json.dumps(TARGETS))
            elif path == "/sessions":
                self._send(200, json.dumps(sessions))
            elif path == "/queue/status":
                self._send(200, json.dumps(status))
            else:
                self._send(404, "{}")

        def do_POST(self):  # noqa: N802
            self._send(200, json.dumps({"ok": True}))

    return MockHandler


def render(html, sessions, status, out_png, chrome, size):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(html, sessions, status))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        subprocess.check_call([
            chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
            "--hide-scrollbars", "--force-device-scale-factor=2",
            "--window-size=" + size, "--virtual-time-budget=4000",
            "--screenshot=" + out_png, "http://127.0.0.1:%d/" % port],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        httpd.shutdown()
        httpd.server_close()
    print("geschrieben:", out_png)


def main() -> int:
    chrome = find_chrome()
    html = open_diag(extract_ui(read_working()))
    for phase, (sessions, status) in PHASES.items():
        render(html, sessions, status, os.path.join(HERE, phase + ".png"),
               chrome, SIZES[phase])
    return 0


if __name__ == "__main__":
    sys.exit(main())
