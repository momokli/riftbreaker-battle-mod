#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Screenshots fuer Issue #1025 (Lobby: Ready/GO — kontextabhaengiger READY).

Extrahiert die in `gns_probe.cpp` eingebettete `kUiHtml`-Lobby-UI (Slice
zwischen `R\"HTML(` und `)HTML\"`) und rendert sie in headless Chromium gegen
einen minimalen lokalen Mock der Steuer-API. Die Seite ist die **echte**
eingebettete UI (kein Nachbau); nur die Backends sind gemockt.

Fuer den Vorher/Nachher-Vergleich wird die UI einmal aus dem Arbeitsbaum
(Nachher, Branch `feature/1025-lobby-ready-referee`) und einmal aus dem Blob
`origin/main:tools/gns-proxy/gns_probe.cpp` (Vorher) gezogen. Der Blob wird
read-only via `clanker-git show` (Fallback: `git show`) gelesen.

Der **READY-Tooltip** ist ein natives `title`-Attribut; native Tooltips werden
von headless-Chromium-Screenshots nicht erfasst. Deshalb liest die Demo den
**realen** `title`-Wert des READY-Buttons aus dem gerenderten DOM und zeigt ihn
als Diagnose-Banner ueber der Karte an (kein nachgebauter Text — der Wert kommt
aus der realen Branch-Funktion `card()`). Der **Hinweistext** wird ueber den
echten Klickpfad `ready(rbtn, c, s.vsWorld)` gegen den Mock ausgeloest (reale
`hint()`-Funktion).

Aufruf (aus dem Repo-Wurzelverzeichnis):

    python3 docs/screenshots/1025/render.py

Der Chromium-Pfad wird ueber `CHROMIUM` (Env) oder den Playwright-Cache gesucht.
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

TARGETS = [{"name": "parked-1", "endpoint": "127.0.0.1:32768"}]


def _session(identity, name, state="held", **extra):
    s = {"name": name, "identity": identity, "state": state, "pinned": False,
         "ip": "10.0.0.5", "messages": 7, "age_seconds": 32, "held_seconds": 9,
         "connected": True, "soloMemberCount": 1, "soloMaxPlayers": 4,
         "soloMembers": [identity]}
    s.update(extra)
    return s


SESSIONS_SOLO = [_session("str:AB12", "momo")]
SESSIONS_VS = [_session("str:AB12", "momo", vsWorld="A")]

REF_RUNNING = {"phase": "Running", "winner": None, "both_ready": True}

# Vorbild aus #1024; hier ohne Referee-Zeile relevant, aber /referee/state wird
# von loadSessions weiterhin abgefragt -> konfiguriert (Running).
#
# name -> {src, sessions, ready, referee}
#   src      "after" (Arbeitsbaum) | "before" (origin/main-Blob)
#   ready    (http_status, body)   Antwort des Mocks auf POST /ready
#   referee  dict | None           /referee/state (None -> 503 unconfigured)
STATES = {
    "00-before-solo-ready": {
        "src": "before", "sessions": SESSIONS_SOLO,
        "ready": (200, {"ok": True}), "referee": REF_RUNNING},
    "01-after-solo-ready": {
        "src": "after", "sessions": SESSIONS_SOLO,
        "ready": (200, {"ok": True}), "referee": REF_RUNNING},
    "02-before-vs-ready": {
        "src": "before", "sessions": SESSIONS_VS,
        "ready": (200, {"ok": True}), "referee": REF_RUNNING},
    "03-after-vs-ready": {
        "src": "after", "sessions": SESSIONS_VS,
        "ready": (200, {"ok": True}), "referee": REF_RUNNING},
    "04-after-vs-referee-unconfigured": {
        "src": "after", "sessions": SESSIONS_VS,
        "ready": (503, {"ok": False, "reason": "referee_unconfigured"}),
        "referee": None},
    "05-after-vs-referee-unreachable": {
        "src": "after", "sessions": SESSIONS_VS,
        "ready": (502, {"ok": False, "reason": "referee_unreachable"}),
        "referee": REF_RUNNING},
    "06-after-vs-bad-world": {
        "src": "after", "sessions": SESSIONS_VS,
        "ready": (400, {"ok": False, "reason": "bad_request"}),
        "referee": REF_RUNNING},
}

SIZE = "900x1020"

DEMO = r"""
// DEMO (#1025): reale title/ready-Pfade sichtbar machen (kein Nachbau).
(function () {
  function rd(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  (async function () {
    for (var i = 0; i < 300; i++) {
      if (document.querySelector('#grid .card button')) break;
      await rd(20);
    }
    await rd(40);
    var cards = document.querySelectorAll('#grid .card');
    var cardEl = null, rbtn = null;
    for (var ci = 0; ci < cards.length && !rbtn; ci++) {
      var bs = cards[ci].querySelectorAll('button');
      for (var bi = 0; bi < bs.length; bi++) {
        if (bs[bi].textContent.trim() === 'READY') { cardEl = cards[ci]; rbtn = bs[bi]; break; }
      }
    }
    // Realen title-Wert aus dem DOM zeigen (headless-Screenshot zeigt sonst
    // keine nativen Tooltips).
    var main = document.querySelector('main');
    var b = document.createElement('div');
    b.style.cssText = 'position:sticky;top:0;z-index:99;margin:0 0 14px;padding:10px 14px;'
      + 'border:1px solid #4ea1ff;border-radius:10px;background:#10233a;color:#e7eaee;'
      + 'font:13px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;';
    b.textContent = 'DEMO #1025 - READY-Button title (reales title-Attribut der Karte):'
      + '\n' + (rbtn ? rbtn.title : '(kein READY-Button gefunden)');
    main.insertBefore(b, main.firstChild);
    if (rbtn) {
      loadSessions = function () {};  // Auto-Re-Render stoppen -> Hinweis haelt.
      rbtn.click();                   // echter Klickpfad ready(rbtn, c, s.vsWorld)
      // Auf das reale hint()-Ergebnis warten (fetch gegen den Mock).
      var ht = null;
      for (var j = 0; j < 300; j++) {
        ht = cardEl.querySelector('.hint');
        if (ht) break;
        await rd(20);
      }
      b.textContent += '\nErgebnis-Hinweis (reale hint()-Ausgabe): '
        + (ht ? ht.textContent : '(kein Hinweis)');
      await rd(400);
    }
  })();
})();
"""


def extract_ui(text: str) -> str:
    m = re.search(r'R"HTML\((.*?)\)HTML"', text, re.S)
    if not m:
        raise SystemExit('kUiHtml nicht gefunden (R"HTML( ... )HTML")')
    return m.group(1)


def open_diag(html: str) -> str:
    """Nur den Diagnose-Bereich (Session-Cards) fuer den Screenshot aufklappen."""
    return html.replace('<details class="diag">', '<details class="diag" open>')


def inject_demo(html: str) -> str:
    return html.replace("\nsetInterval(loadSessions, 1500);",
                        "\nsetInterval(loadSessions, 1500);\n" + DEMO)


def read_source(which: str) -> str:
    if which == "after":
        with open(SRC, encoding="utf-8") as fh:
            return fh.read()
    # before: origin/main-Blob read-only via clanker-git (Fallback git).
    for tool in ("clanker-git", "git"):
        try:
            out = subprocess.check_output(
                [tool, "-C", REPO, "show", "origin/main:" + REL_SRC],
                stderr=subprocess.DEVNULL)
            return out.decode("utf-8")
        except (OSError, subprocess.CalledProcessError):
            continue
    raise SystemExit("origin/main-Blob nicht lesbar (clanker-git/git show fehlgeschlagen)")


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


def make_handler(html, sessions, ready, referee):
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
                self._send(200, json.dumps(sessions))
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
            path = self.path.split("?", 1)[0]
            if path == "/ready":
                code, body = ready
                self._send(code, json.dumps(body))
            else:
                self._send(200, json.dumps({"ok": True}))

    return MockHandler


def render(html, sessions, ready, referee, out_png, chrome):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(html, sessions, ready, referee))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        subprocess.check_call([
            chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
            "--hide-scrollbars", "--force-device-scale-factor=2",
            "--run-all-compositor-stages-before-draw",
            "--window-size=" + SIZE, "--virtual-time-budget=8000",
            "--screenshot=" + out_png, "http://127.0.0.1:%d/" % port],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        httpd.shutdown()
        httpd.server_close()
    print("geschrieben:", out_png)


def main() -> int:
    chrome = find_chrome()
    cache = {}
    for name, cfg in STATES.items():
        which = cfg["src"]
        if which not in cache:
            cache[which] = open_diag(extract_ui(read_source(which)))
        html = inject_demo(cache[which])
        render(html, cfg["sessions"], cfg["ready"], cfg["referee"],
               os.path.join(HERE, name + ".png"), chrome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
