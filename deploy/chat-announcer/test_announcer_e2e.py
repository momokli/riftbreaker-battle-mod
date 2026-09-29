#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2E-Test fuer den Chat-Announcer (Issue #940).

Treibt den ECHTEN Sidecar-Prozess (:file:`announcer.py`) als Subprocess gegen
zwei lokale Fake-HTTP-Server (Attack-Cycle ``GET /status``, Bridge
``POST /send_chat``). Damit wird die Vorstufen-Unit-Suite (reiner Detektor gegen
synthetische Dicts) um den ganzen Weg erweitert: CLI -> urllib -> Loop ->
Bridge-POST.

Rein stdlib + hermetic (nur ``127.0.0.1``, kein Netz nach aussen), Fake-Server
via :mod:`http.server` in Threads.
"""

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ANNOUNCER = os.path.join(HERE, "announcer.py")

# Erwartete Chat-Zeilenfolge (style=short) fuer die skriptbare Statusfolge:
# warmup 305(=keine) -> 179/120/60/30/10 -> 0(GO) -> running 60/30/10 ->
# attack_index-Sprung (incoming) -> game_over.
EXPECTED = [
    "warmup 3:00",
    "warmup 2:00",
    "warmup 1:00",
    "warmup 0:30",
    "warmup 0:10",
    "GO",
    "next attack in 60s",
    "next attack in 30s",
    "next attack in 10s",
    "incoming [W1]x3 [W4]x1",
    "round over - HQ destroyed",
]


def scripted_statuses():
    """Skriptbare Statusfolge: warmup (Restzeit faellt) -> running (Attack ->
    GO-Edge -> naechste Attacken) -> Feuern (attack_index-Sprung, last_fire) ->
    game_over."""
    return [
        {"state": "warmup", "seconds_to_warmup_end": 305, "attack_index": 0},
        {"state": "warmup", "seconds_to_warmup_end": 179, "attack_index": 0},
        {"state": "warmup", "seconds_to_warmup_end": 120, "attack_index": 0},
        {"state": "warmup", "seconds_to_warmup_end": 60, "attack_index": 0},
        {"state": "warmup", "seconds_to_warmup_end": 30, "attack_index": 0},
        {"state": "warmup", "seconds_to_warmup_end": 10, "attack_index": 0},
        {"state": "warmup", "seconds_to_warmup_end": 0, "attack_index": 0},
        {"state": "running", "seconds_to_next_attack": 60, "attack_index": 1, "last_fire": None},
        {"state": "running", "seconds_to_next_attack": 30, "attack_index": 1, "last_fire": None},
        {"state": "running", "seconds_to_next_attack": 10, "attack_index": 1, "last_fire": None},
        {
            "state": "running",
            "seconds_to_next_attack": 999,
            "attack_index": 2,
            "last_fire": {
                "natural_level": 1,
                "natural_count": 3,
                "persona_levels": [],
                "sent_levels": [4],
                "t": 0.0,
            },
        },
        {"state": "game_over", "attack_index": 2},
    ]


class _ACHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # still
        pass

    def do_GET(self):
        if self.path.split("?")[0] != "/status":
            self.send_response(404)
            self.end_headers()
            return
        status = self.server.next_status()
        body = json.dumps(status).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeAttackCycle:
    """Fake-Attack-Cycle: liefert die Statusfolge; jeder Eintrag ``repeats``x,
    danach wird der letzte Eintrag ``hold``-mal wiederholt (fuer die
    No-Spam-Pruefung bei steady-state)."""

    def __init__(self, statuses, repeats=3):
        self._statuses = list(statuses)
        self._repeats = repeats
        self._i = 0
        self._lock = threading.Lock()
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ACHandler)
        self._httpd.next_status = self._next
        self._httpd.requests = 0
        self._thread = None
        self.port = self._httpd.server_address[1]

    def _next(self):
        with self._lock:
            self._httpd.requests += 1
            slot = self._i // self._repeats
            if slot >= len(self._statuses):
                return self._statuses[-1]
            self._i += 1
            return self._statuses[slot]

    @property
    def requests(self):
        return self._httpd.requests

    def start(self):
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()


class _BridgeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # still
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.split("?")[0] != "/send_chat":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            payload = {}
        fail = self.server.record(payload)
        if fail:
            self._json(500, {"ok": False, "error": "boom"})
        else:
            self._json(200, {"ok": True, "text": payload.get("text", ""), "sent": "true"})

    def do_GET(self):
        # Fallback-Endpunkt GET /attack_status (wird hier nicht gebraucht).
        self._json(200, {"state": "paused"})


class FakeBridge:
    """Fake-Bridge: nimmt ``POST /send_chat`` entgegen, protokolliert jeden
    Versuch (text/type/prefix in Reihenfolge) und antwortet
    ``{"ok":true,...,"sent":"true"}``. Mit ``fail_first=N`` antworten die ersten
    N Versuche mit HTTP 500."""

    def __init__(self, fail_first=0):
        self.attempts = []  # jede Anfrage (auch Fehlschlaege)
        self.received = []  # nur erfolgreich beantwortete (2xx)
        self._fail_first = fail_first
        self._lock = threading.Lock()
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _BridgeHandler)
        self._httpd.record = self._record
        self._thread = None
        self.port = self._httpd.server_address[1]

    def _record(self, payload):
        with self._lock:
            fail = len(self.attempts) < self._fail_first
            rec = {
                "text": payload.get("text", ""),
                "type": payload.get("type"),
                "prefix": payload.get("prefix"),
            }
            self.attempts.append(rec)
            if not fail:
                self.received.append(rec)
            return fail

    def start(self):
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()


def run_announcer(ac_port, br_port, out_path):
    """Startet den echten CLI-Prozess (echtes Senden, kein --dry-run)."""
    cmd = [
        sys.executable,
        ANNOUNCER,
        "--attack-cycle-url", "http://127.0.0.1:%d" % ac_port,
        "--bridge-url", "http://127.0.0.1:%d" % br_port,
        "--interval", "0.05",
        "--timeout", "2",
    ]
    fh = open(out_path, "w")
    proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT)
    return proc, fh


def stop_proc(proc, fh):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    fh.close()


def wait_until(pred, timeout=20.0, interval=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return False


class TestE2ESequence(unittest.TestCase):
    """Voller Weg: CLI-Prozess pollt Fake-Attack-Cycle und sendet ueber die
    Fake-Bridge; exakte Zeilenfolge, keine Doppelzeilen, kein Spam."""

    def test_exact_sequence_no_spam(self):
        ac = FakeAttackCycle(scripted_statuses(), repeats=3).start()
        br = FakeBridge().start()
        fd, out_path = tempfile.mkstemp(prefix="announcer-e2e-", suffix=".log")
        os.close(fd)
        proc, fh = run_announcer(ac.port, br.port, out_path)
        try:
            got_all = wait_until(lambda: len(br.received) >= len(EXPECTED))
            # Zusaetzliche Wartezeit: ein Spam-/Doppel-Bug wuerde jetzt weitere
            # Zeilen produzieren (steady game_over wird weiter gepollt).
            time.sleep(1.0)
            alive = proc.poll() is None
            lines = [r["text"] for r in br.received]
            requests = ac.requests
        finally:
            stop_proc(proc, fh)
            ac.stop()
            br.stop()

        self.assertTrue(got_all, "nicht alle erwarteten Zeilen empfangen: %r" % (lines,))
        self.assertTrue(alive, "Announcer-Prozess lebt nicht mehr")
        # Exakte Reihenfolge + Inhalt (style=short).
        self.assertEqual(lines, EXPECTED)
        # Keine Doppelzeilen.
        self.assertEqual(len(lines), len(set(lines)))
        # Kein 1-Hz-Spam: deutlich mehr Status-Polls als gesendete Zeilen.
        self.assertGreater(requests, len(EXPECTED) * 2)
        # Typen/Prefix: Ergebniszeile = announcement, Rest = system, Prefix leer.
        self.assertEqual(br.received[-1]["type"], "announcement")
        for rec in br.received[:-1]:
            self.assertEqual(rec["type"], "system")
        for rec in br.received:
            self.assertEqual(rec["prefix"], "")


class TestE2EBridgeFailure(unittest.TestCase):
    """Negativfall: Bridge liefert 500 -> Announcer laeuft weiter, loggt und
    sendet spaetere Zeilen trotzdem."""

    def test_bridge_500_keeps_running(self):
        ac = FakeAttackCycle(scripted_statuses(), repeats=3).start()
        br = FakeBridge(fail_first=3).start()
        fd, out_path = tempfile.mkstemp(prefix="announcer-e2e-neg-", suffix=".log")
        os.close(fd)
        proc, fh = run_announcer(ac.port, br.port, out_path)
        try:
            got_all = wait_until(lambda: len(br.attempts) >= len(EXPECTED))
            time.sleep(0.5)
            alive = proc.poll() is None
            attempts = list(br.attempts)
            received = [r["text"] for r in br.received]
            with open(out_path) as f:
                log = f.read()
        finally:
            stop_proc(proc, fh)
            ac.stop()
            br.stop()

        self.assertTrue(got_all, "nicht alle Sendeversuche gesehen: %r" % (_all_texts(br),))
        self.assertTrue(alive, "Announcer-Prozess ist nach den 500ern gestorben")
        # Alle 11 Zeilen wurden versucht (kein Drop), die ersten 3 scheitern.
        self.assertEqual([a["text"] for a in attempts], EXPECTED)
        # Spaetere Zeilen kommen trotzdem durch (2xx).
        self.assertEqual(received, EXPECTED[3:])
        # Fehler wird geloggt, Loop laeuft weiter.
        self.assertIn("send_chat HTTP 500", log)
        self.assertIn("weiter", log)


def _all_texts(br):
    return [a["text"] for a in br.attempts]


if __name__ == "__main__":
    unittest.main(verbosity=2)
