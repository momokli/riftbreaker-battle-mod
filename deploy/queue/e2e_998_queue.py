#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2E-Beweis fuer Issue #998 (Casual-Queue end-to-end) — Tester-Stage.

Treibt den VOLLEN HTTP-Pfad ueber den ECHTEN Queue-Dienst:

    queue_service HTTP  POST /queue/join {identitaet, mode:"vs"}
      -> QueueCoordinator (real) -> QueueCore (real, FIFO-Pairing A/B)
      -> ProvisionerClient HTTP  POST /start {env, mode, instance_id, world}
         -> Stub-Provisioner (recordet; liefert je Welt einen eigenen GNS-Endpoint)
      -> RefereeClient HTTP      POST /lobby {player, world}
         -> Stub-Referee (recordet)
      -> Match-Record (real, mit assignments/instance/endpoint)

Nur die Docker-/Spiel-Ebene ist ersetzt (kein Docker, kein Spiel); der
HTTP-Pfad, das Pairing, die A/B-Zuordnung und der Match-Record sind echt.
Kein Warm-Pool, kein MMR.

Aufruf (Exit 0 = alle ACs belegt; schreibt Rohbelege nach evidence/):

    cd deploy/queue && TMPDIR=/dev/shm python3 e2e_998_queue.py
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
            # Deterministischer, aber pro Welt VERSCHIEDENER GNS-Endpoint.
            port = 40000 + (0 if world == "A" else 1)
            self.server.starts.append(payload)
            _record("STUB provisioner POST /start %s -> gns=127.0.0.1:%d"
                    % (json.dumps(payload), port))
            self._json(200, {"instance": instance, "running": True, "created": True,
                             "ports": {"gns": "127.0.0.1:%d" % port}})
        elif self.path == "/stop":
            self.server.stops.append(payload)
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

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        payload = json.loads(raw) if raw.strip() else {}
        if self.path == "/lobby":
            self.server.lobbies.append(payload)
            _record("STUB referee POST /lobby %s" % json.dumps(payload))
            self._json(200, {"ok": True})
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
        self.prov_port = _serve(self.prov_server)
        case.addCleanup(self.prov_server.shutdown)
        case.addCleanup(self.prov_server.server_close)

        self.ref_server = ThreadingHTTPServer(("127.0.0.1", 0), _RefereeStub)
        self.ref_server.daemon_threads = True
        self.ref_server.lobbies = []
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


class QueueE2E(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="e2e998-state-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.state_dir, True))
        _record("=== E2E #998 run (state_dir=%s) ===" % self.state_dir)

    def test_acceptance_two_players_pair_into_one_match(self):
        st = Stack(self, self.state_dir)

        # (1) Zwei Identitaeten, Modus vs, POST /queue/join.
        status_a, a = st.post("/queue/join", {"identitaet": "str:aa", "mode": "vs"})
        self.assertEqual(status_a, 200, a)
        self.assertEqual(a["status"], "queued", a)
        status_b, b = st.post("/queue/join", {"identitaet": "str:bb", "mode": "vs"})
        self.assertEqual(status_b, 200, b)

        # (2) genau EINE Paarung (Match m1), A <-> Welt A, B <-> Welt B.
        self.assertEqual(b["status"], "matched", b)
        match = b["match"]
        self.assertEqual(match["match_id"], 1, match)
        by_world = {x["world"]: x["identitaet"] for x in match["assignments"]}
        self.assertEqual(by_world, {"A": "str:aa", "B": "str:bb"}, match)

        # (3) beide Welten KALT provisioniert (zwei frische Instanzen, kein Pool).
        self.assertEqual(len(st.prov_server.starts), 2, st.prov_server.starts)
        worlds = sorted(s["world"] for s in st.prov_server.starts)
        self.assertEqual(worlds, ["A", "B"], st.prov_server.starts)
        instances = sorted(s["instance_id"] for s in st.prov_server.starts)
        self.assertEqual(instances, ["queue-1-a", "queue-1-b"], instances)

        # (4) Referee erhaelt genau ein /lobby A UND ein /lobby B.
        lobbies = st.ref_server.lobbies
        self.assertEqual(len(lobbies), 2, lobbies)
        lobby_by_world = {l["world"]: l["player"] for l in lobbies}
        self.assertEqual(lobby_by_world, {"A": "str:aa", "B": "str:bb"}, lobbies)

        # (5) beide Identitaeten auf VERSCHIEDENE GNS-Endpoints gepinnt.
        endpoints = {x["identitaet"]: x["target"] for x in match["assignments"]}
        self.assertEqual(len(set(endpoints.values())), 2, endpoints)
        self.assertTrue(all(endpoints.values()), endpoints)

        # (6) EIN Match-Datensatz mit beiden Teilnehmern; finish traegt Ergebnis nach.
        _s, snap = st.get("/queue/status")
        self.assertEqual(snap["matches_count"], 1, snap)
        record = snap["matches"][0]
        self.assertEqual(len(record["participants"]), 2, record)
        _s, done = st.post("/queue/finish", {"match_id": 1, "result": "winnerA"})
        self.assertEqual(done["match"]["state"], "finished", done)
        self.assertEqual(done["match"]["result"], "winnerA", done)
        # Kaltes Cleanup: beide Instanzen gestoppt.
        self.assertEqual(len(st.prov_server.stops), 2, st.prov_server.stops)


def _write_evidence() -> str:
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    path = os.path.join(EVIDENCE_DIR, "998-e2e-queue.txt")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("# E2E-Beweis Issue #998 — zwei Spieler -> ein VS-Match (A/B)\n")
        handle.write("# Rohprotokoll des Harness-Laufs (queue_service HTTP + Stubs)\n\n")
        handle.write("\n".join(TRANSCRIPT))
        handle.write("\n")
    return path


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(QueueE2E)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    path = _write_evidence()
    print("Rohbeleg: %s" % path)
    sys.exit(0 if result.wasSuccessful() else 1)