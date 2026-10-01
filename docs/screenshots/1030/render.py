#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Screenshots fuer Issue #1030 (Lobby-Rematch: "Rematch"-Button in der Referee-Zeile).

Extrahiert die in `gns_probe.cpp` eingebettete `kUiHtml`-Lobby-UI (Slice
zwischen `R\"HTML(` und `)HTML\"`) und rendert sie in headless Chromium gegen
einen minimalen lokalen Mock der Steuer-API. Die Seite ist die **echte**
eingebettete UI (kein Nachbau); nur die Backends sind gemockt — inklusive
`GET /referee/state` mit `phase=finished` und `GET /sessions` mit `matchId`
(Voraussetzung fuer den Rematch-Button in `card()`).

Aufruf (aus dem Repo-Wurzelverzeichnis):

    python3 docs/screenshots/1030/render.py

Erzeugt (`--force-device-scale-factor=2`):
    00-referee-finished-before.png — Basis (`origin/main`): Referee-Zeile im
                                     Zustand `finished`, **ohne** Rematch-Button
    01-referee-finished-rematch.png— Arbeitsbaum (dieser PR): dieselbe Zeile mit
                                     aktivem **Rematch**-Button

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
REL_SRC = "tools/gns-proxy/gns_probe.cpp"
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


# Zustand nach Match-Ende: Referee `phase=finished` (StateView serialisiert
# Phasen lowercase, siehe tournament/src/state.rs `Phase::as_str`). Beide
# Spieler kennen ihre Match-ID aus /sessions (`matchId`) -> `card()` zeigt den
# Rematch-Button (`REF_SNAP.phase === "finished" && refMid`).
REF_FINISHED = {"phase": "finished", "winner": "A", "both_ready": True,
                "teams": {"A": {"ready": True, "player": "str:AB12"},
                          "B": {"ready": True, "player": "str:CD34"}}}

SESSIONS = [
    _session("str:AB12", "momo", vsWorld="A", matchId=7),
    _session("str:CD34", "gast", state="connected", vsWorld="B", matchId=7),
]

SIZE = "900x1020"


def extract_ui(text: str) -> str:
    m = re.search(r'R"HTML\((.*?)\)HTML"', text, re.S)
    if not m:
        raise SystemExit('kUiHtml nicht gefunden (R"HTML( ... )HTML")')
    return m.group(1)


def read_baseline_ui() -> str:
    """kUiHtml aus origin/main (Basis-Stand, ohne Rematch-Button)."""
    for cmd in (["clanker-git", "show", "origin/main:" + REL_SRC],
                ["git", "show", "origin/main:" + REL_SRC]):
        try:
            out = subprocess.check_output(cmd, cwd=REPO, stderr=subprocess.DEVNULL)
            return extract_ui(out.decode("utf-8"))
        except (OSError, subprocess.CalledProcessError):
            continue
    raise SystemExit("Basis-UI nicht lesbar (clanker-git/git show origin/main:...")


def open_diag(html: str) -> str:
    """Nur den Diagnose-Bereich aufklappen, in dem die Session-Cards liegen."""
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


def make_handler(html, referee):
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
            elif path == "/targets":
                self._send(200, json.dumps(TARGETS))
            elif path == "/sessions":
                self._send(200, json.dumps(SESSIONS))
            elif path == "/queue/status":
                self._send(200, json.dumps({"queued": 0, "queue": [], "matches": []}))
            elif path == "/referee/state":
                self._send(200, json.dumps(referee))
            else:
                self._send(404, "{}")

        def do_POST(self):  # noqa: N802
            self._send(200, json.dumps({"ok": True}))

    return MockHandler


def render(html, referee, out_png, chrome):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(html, referee))
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
        after = open_diag(extract_ui(fh.read()))
    before = open_diag(read_baseline_ui())
    render(before, REF_FINISHED, os.path.join(HERE, "00-referee-finished-before.png"), chrome)
    render(after, REF_FINISHED, os.path.join(HERE, "01-referee-finished-rematch.png"), chrome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
