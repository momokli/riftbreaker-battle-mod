#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Screenshots fuer Issue #1024 (Lobby: Referee-Bruecke + READY (Referee)).

Extrahiert die in `gns_probe.cpp` eingebettete `kUiHtml`-Lobby-UI (Slice
zwischen `R\"HTML(` und `)HTML\"`) und rendert sie in headless Chromium gegen
einen minimalen lokalen Mock der Steuer-API. Die Seite ist die **echte**
eingebettete UI (kein Nachbau); nur die Backends sind gemockt — inklusive
`GET /referee/state` (neu in #1024). Reines Stdlib-Script.

Aufruf (aus dem Repo-Wurzelverzeichnis):

    python3 docs/screenshots/1024/render.py

Erzeugt (`--force-device-scale-factor=2`):
    01-referee-running.png      — konfiguriert: Badge "Ref: laeuft" + "beide ready"
                                  + Welt-<select> + aktiver READY (Referee)-Button
    02-referee-finished.png     — konfiguriert: Badge "Ref: beendet" + "Sieger A"
                                  + aktiver READY (Referee)-Button
    03-referee-unconfigured.png — 503 referee_unconfigured: deaktivierter
                                  READY-Button + sichtbarer Hinweis

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


# Konfigurierter Referee-Zustand (GET /referee/state). Synthetisch.
REF_RUNNING = {"phase": "Running", "winner": None, "both_ready": True,
               "teams": {"A": {"ready": True, "player": "str:AB12"},
                         "B": {"ready": True, "player": "str:CD34"}}}
REF_FINISHED = {"phase": "Finished", "winner": "A", "both_ready": True,
                "teams": {"A": {"ready": True, "player": "str:AB12"},
                          "B": {"ready": True, "player": "str:CD34"}}}

SESSIONS = [
    _session("str:AB12", "momo", vsWorld="A"),
    _session("str:CD34", "gast", state="connected", vsWorld="B"),
]

# name -> (referee_state | None, injiziere sichtbaren Hinweis?)
STATES = {
    "01-referee-running": (REF_RUNNING, False),
    "02-referee-finished": (REF_FINISHED, False),
    "03-referee-unconfigured": (None, True),
}

SIZE = "900x1020"


def extract_ui(text: str) -> str:
    m = re.search(r'R"HTML\((.*?)\)HTML"', text, re.S)
    if not m:
        raise SystemExit('kUiHtml nicht gefunden (R"HTML( ... )HTML")')
    return m.group(1)


def open_diag(html: str) -> str:
    """Die eingebettete UI bleibt unveraendert; einzig der Diagnose-Bereich
    `<details class="diag">`, in dem die Session-Cards liegen, wird fuer den
    Screenshot aufgeklappt (sonst sind die Cards nicht sichtbar)."""
    return html.replace('<details class="diag">', '<details class="diag" open>')


def inject_hint(html: str) -> str:
    """Zeigt fuer den unkonfigurierten Fall den realen `hint()`-Pfad: die
    Karte bekommt (wie nach einem Referee-Fehler) den sichtbaren Hinweis. Nur
    die echte Branch-Funktion `hint()` wird aufgerufen — kein nachgebautes
    Markup. Der 150-ms-Takt haelt den Hinweis gegen das 1500-ms-Re-Render."""
    demo = (
        "\n// DEMO (#1024): realen hint()-Pfad fuer den unkonfigurierten Fall zeigen.\n"
        "// Haengt sich nach jedem echten loadSessions-Render an (kein Nachbau).\n"
        "(function () {\n"
        "  var _orig = loadSessions;\n"
        "  loadSessions = async function () {\n"
        "    await _orig();\n"
        "    var c = document.querySelector('#grid .card');\n"
        "    if (c) hint(c, 'Referee-Dienst nicht konfiguriert "
        "(503 referee_unconfigured)');\n"
        "  };\n"
        "})();\n"
    )
    return html.replace("\nsetInterval(loadSessions, 1500);",
                        demo + "\nsetInterval(loadSessions, 1500);")


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
                if referee is None:
                    self._send(503, json.dumps({"reason": "referee_unconfigured"}))
                else:
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
        base = open_diag(extract_ui(fh.read()))
    for name, (referee, want_hint) in STATES.items():
        html = inject_hint(base) if want_hint else base
        render(html, referee, os.path.join(HERE, name + ".png"), chrome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
