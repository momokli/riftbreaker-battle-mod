#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2E-Beweis fuer Issue #1030 (Rematch ueber die Lobby) — Tester-Stage.

Treibt den VOLLEN HTTP-Pfad ueber den ECHTEN Queue-Dienst (nur Docker/Spiel
ersetzt, wie e2e_998_queue.py):

    POST /queue/join    x2  -> Match m (A/B, zwei KALT-Welten)
    POST /queue/finish      -> Ergebnis + Kalt-Stop beider Welten
    POST /queue/rematch     -> Alt-Stop VOR Neu-Start, Referee-Reset VOR
                               Provisionierung, danach zwei FRISCHE Welten
                               derselben Paarung (neue match_id/Endpoints)
    POST /queue/rematch x2  -> idempotent (kein zweiter Start)

Aufruf (Exit 0 = alle ACs belegt; schreibt Rohbelege nach evidence/):

    cd deploy/queue && TMPDIR=/dev/shm python3 e2e_1030_rematch.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from queue_service import (  # noqa: E402
    QueueServiceConfig,
    build_coordinator,
    build_server,
)

EVIDENCE_DIR = os.path.join(HERE, "evidence")
TRANSCRIPT: list = []


def _record(line: str) -> None:
    TRANSCRIPT.append(line)
    print(line)


# --- Stub-Backends (echter HTTP-Pfad, nur Docker/Spiel ersetzt) -------------


class _ProvisionerStub(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return json.loads(raw) if raw.strip() else {}

    def do_POST(self):  # noqa: N802
        payload = self._read()
        if self.path == "/start":
            instance = payload.get("instance_id")
            world = payload.get("world")
            # #1030: eindeutiger GNS-Endpoint je Instanz (Alt != Neu belegbar).
            self.server._port += 1
            self.server.starts.append(payload)
            self.server.events.append(("start", instance))
            _record("STUB provisioner POST /start %s -> gns=127.0.0.1:%d"
                    % (json.dumps(payload), self.server._port))
            self._json(200, {"instance": instance, "running": True, "created": True,
                             "ports": {"gns": "127.0.0.1:%d" % self.server._port}})
        elif self.path == "/stop":
            self.server.stops.append(payload)
            self.server.events.append(("stop", payload.get("instance_id")))
            _record("STUB provisioner POST /stop %s" % json.dumps(payload))
            self._json(200, {"instance": payload.get("instance_id"), "removed": True})
        else:
            self._json(404, {"ok": False})


class _RefereeStub(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/state":
            payload = getattr(self.server, "state_payload", None) or {}
            _record("STUB referee GET /state -> %s" % json.dumps(payload))
            self._json(200, payload)
        else:
            self._json(404, {"ok": False})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        payload = json.loads(raw) if raw.strip() else {}
        if self.path == "/lobby":
            self.server.lobbies.append(payload)
            self.server.calls.append(("lobby", payload))
            _record("STUB referee POST /lobby %s" % json.dumps(payload))
            self._json(200, {"ok": True})
        elif self.path == "/ready":
            self.server.readies.append(payload)
            self.server.calls.append(("ready", payload))
            _record("STUB referee POST /ready %s" % json.dumps(payload))
            self._json(200, {"ok": True})
        elif self.path == "/rematch":
            # #1030: Reset in die Lobby (Spieler bleiben registriert).
            self.server.rematches += 1
            self.server.calls.append(("rematch", payload))
            _record("STUB referee POST /rematch -> phase=lobby rematches=%d"
                    % self.server.rematches)
            self._json(200, {"phase": "lobby", "rematches": self.server.rematches})
        else:
            self._json(404, {"ok": False})


def _serve(server: ThreadingHTTPServer) -> int:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server.server_address[1]


class Stack(object):
    def __init__(self, case, state_dir: str) -> None:
        self.prov_server = ThreadingHTTPServer(("127.0.0.1", 0), _ProvisionerStub)
        self.prov_server.daemon_threads = True
        self.prov_server.starts = []
        self.prov_server.stops = []
        self.prov_server.events = []  # #1030: globale Start-/Stop-Reihenfolge
        self.prov_server._port = 40000
        self.prov_port = _serve(self.prov_server)
        case.addCleanup(self.prov_server.shutdown)
        case.addCleanup(self.prov_server.server_close)

        self.ref_server = ThreadingHTTPServer(("127.0.0.1", 0), _RefereeStub)
        self.ref_server.daemon_threads = True
        self.ref_server.lobbies = []
        self.ref_server.readies = []
        self.ref_server.calls = []
        self.ref_server.rematches = 0
        self.ref_server.state_payload = None
        self.ref_port = _serve(self.ref_server)
        case.addCleanup(self.ref_server.shutdown)
        case.addCleanup(self.ref_server.server_close)

        self.config = QueueServiceConfig(
            env="e2e", bind="127.0.0.1", port=0, token="",
            provisioner_url="http://127.0.0.1:%d" % self.prov_port,
            referee_url="http://127.0.0.1:%d" % self.ref_port,
            state_dir=state_dir,
        )
        self.coordinator = build_coordinator(self.config)
        self.httpd = build_server(self.config, self.coordinator)
        self.port = _serve(self.httpd)
        case.addCleanup(self.httpd.shutdown)
        case.addCleanup(self.httpd.server_close)

    def post(self, path, payload):
        return _call("POST", "http://127.0.0.1:%d%s" % (self.port, path), payload)

    def get(self, path):
        return _call("GET", "http://127.0.0.1:%d%s" % (self.port, path))


def _call(method, url, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            _record("HTTP %s %s -> %d %s" % (method, url, resp.status, json.dumps(body)))
            return resp.status, body
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode("utf-8"))
        _record("HTTP %s %s -> %d %s" % (method, url, exc.code, json.dumps(body)))
        return exc.code, body


class RematchE2E(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="e2e1030-state-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.state_dir, True))
        _record("=== E2E #1030 run (state_dir=%s) ===" % self.state_dir)

    def _matched(self, st):
        _s, a = st.post("/queue/join", {"identitaet": "str:aa", "mode": "vs"})
        self.assertEqual(a["status"], "queued", a)
        _s, b = st.post("/queue/join", {"identitaet": "str:bb", "mode": "vs"})
        self.assertEqual(b["status"], "matched", b)
        return b["match"]

    def test_acceptance_rematch_same_pairing_fresh_worlds(self):
        st = Stack(self, self.state_dir)
        match = self._matched(st)
        mid = match["match_id"]
        self.assertEqual(mid, 1, match)
        _s, done = st.post("/queue/finish", {"match_id": mid, "result": "winnerA"})
        self.assertEqual(done["match"]["state"], "finished", done)

        # (1) Rematch per match_id.
        status, out = st.post("/queue/rematch", {"match_id": mid})
        self.assertEqual(status, 200, out)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["rematch_of"], mid, out)
        self.assertFalse(out["idempotent"], out)
        new = out["match"]
        self.assertEqual(new["match_id"], mid + 1, new)
        self.assertEqual(new["rematch_of"], mid, new)
        self.assertEqual(new["state"], "ready", new)

        # (2) Gleiche zwei Spieler + gleiche A/B-Weltzuordnung.
        by_world = {a["world"]: a["identitaet"] for a in new["assignments"]}
        self.assertEqual(by_world, {"A": "str:aa", "B": "str:bb"}, new)

        # (3) Zwei FRISCHE Kalt-Welten (neue Instanz-IDs), neue Endpoints.
        new_instances = sorted(s["instance_id"] for s in st.prov_server.starts)[-2:]
        self.assertEqual(new_instances, ["queue-2-a", "queue-2-b"], new_instances)
        old_eps = {a["target"] for a in match["assignments"]}
        new_eps = {a["target"] for a in new["assignments"]}
        self.assertTrue(new_eps and not (new_eps & old_eps), (old_eps, new_eps))

        # (4) Anti-Zombie: Alt-Stop VOR Neu-Start.
        events = st.prov_server.events
        for world in ("a", "b"):
            stop_old = events.index(("stop", "queue-1-%s" % world))
            start_new = events.index(("start", "queue-2-%s" % world))
            self.assertLess(stop_old, start_new, events)

        # (5) Referee-Folge: rematch VOR den neuen Lobby/Ready-Aufrufen.
        calls = [kind for kind, _ in st.ref_server.calls]
        self.assertEqual(calls[:4], ["lobby", "lobby", "ready", "ready"], calls)
        self.assertEqual(calls[4], "rematch", calls)
        self.assertEqual(calls[5:], ["lobby", "lobby", "ready", "ready"], calls)
        # Neue match_id wird per /lobby echoisiert (Referee-Vertrag).
        self.assertEqual([lobby["match_id"] for lobby in st.ref_server.lobbies[-2:]],
                         [mid + 1, mid + 1])

        # (6) Idempotenter Zweitaufruf: kein zweiter Start, gleiche neue match_id.
        starts_before = len(st.prov_server.starts)
        status2, out2 = st.post("/queue/rematch", {"match_id": mid})
        self.assertEqual(status2, 200, out2)
        self.assertTrue(out2["idempotent"], out2)
        self.assertEqual(out2["match"]["match_id"], mid + 1, out2)
        self.assertEqual(len(st.prov_server.starts), starts_before, st.prov_server.starts)
        self.assertEqual(st.ref_server.rematches, 1, st.ref_server.rematches)

    def test_acceptance_rematch_by_identitaet_and_refusal(self):
        st = Stack(self, self.state_dir)
        match = self._matched(st)
        mid = match["match_id"]

        # Noch nicht finished -> 409 match_not_finished, kein neuer Start.
        status, refuse = st.post("/queue/rematch", {"match_id": mid})
        self.assertEqual(status, 409, refuse)
        self.assertEqual(refuse["reason"], "match_not_finished", refuse)

        _s, _done = st.post("/queue/finish", {"match_id": mid, "result": "draw"})
        # Komfort-Pfad per Identitaet.
        status2, out = st.post("/queue/rematch", {"identitaet": "str:aa"})
        self.assertEqual(status2, 200, out)
        self.assertEqual(out["rematch_of"], mid, out)
        self.assertEqual(out["match"]["match_id"], mid + 1, out)


def _write_evidence() -> str:
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    path = os.path.join(EVIDENCE_DIR, "1030-e2e-rematch.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("# E2E-Beweis Issue #1030 — Lobby-Rematch (gleiche Paarung, frische A/B-Welten)\n")
        handle.write("# Rohprotokoll des Harness-Laufs (queue_service HTTP + Stubs)\n\n")
        handle.write("\n".join(TRANSCRIPT))
        handle.write("\n")
    return path


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(RematchE2E)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    path = _write_evidence()
    print("Rohbeleg: %s" % path)
    sys.exit(0 if result.wasSuccessful() else 1)
