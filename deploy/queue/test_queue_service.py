#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermetische Tests fuer deploy/queue/queue_service.py (Issue #998, US3).

Kein Netz (ausser einem echten ``ThreadingHTTPServer`` auf ``127.0.0.1:0``),
kein Spiel/Docker: Provisioner und Referee sind Fakes. Aufruf:

    cd deploy/queue && python3 -m unittest -v
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from queue_core import QueueCore
from queue_flow import QueueCoordinator
from queue_service import (
    QueueConfigError,
    QueueServiceConfig,
    build_server,
)
from test_queue_flow import FakeClock, FakeProvisioner, FakeReferee


def make_coordinator():
    clock = FakeClock()
    provisioner = FakeProvisioner()
    referee = FakeReferee()
    coord = QueueCoordinator(QueueCore(clock=clock), provisioner, referee,
                             clock=clock, env="test")
    return coord, provisioner, referee


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = QueueServiceConfig.from_env({})
        self.assertEqual(cfg.env, "dev")
        self.assertEqual(cfg.bind, "127.0.0.1")
        self.assertEqual(cfg.port, 9221)
        self.assertEqual(cfg.token, "")
        self.assertEqual(cfg.timeout, 5.0)
        self.assertEqual(cfg.team_size, 1)
        self.assertFalse(cfg.allow_teams)
        self.assertEqual(cfg.reconcile_interval_s, 5.0)  # #1028 Default

    def test_reconcile_interval_env(self):
        # #1028: Intervall steuerbar; 0 = aus (erlaubt), negativ = Fehler.
        cfg = QueueServiceConfig.from_env({"QUEUE_RECONCILE_INTERVAL_S": "0"})
        self.assertEqual(cfg.reconcile_interval_s, 0.0)
        cfg = QueueServiceConfig.from_env({"QUEUE_RECONCILE_INTERVAL_S": "2.5"})
        self.assertEqual(cfg.reconcile_interval_s, 2.5)
        with self.assertRaises(QueueConfigError):
            QueueServiceConfig.from_env({"QUEUE_RECONCILE_INTERVAL_S": "-1"})
        with self.assertRaises(QueueConfigError):
            QueueServiceConfig.from_env({"QUEUE_RECONCILE_INTERVAL_S": "abc"})

    def test_env_values(self):
        cfg = QueueServiceConfig.from_env({
            "QUEUE_ENV": "prod",
            "QUEUE_PORT": "9222",
            "QUEUE_TOKEN": "  s3cr3t  ",
            "QUEUE_PROVISIONER_URL": "http://127.0.0.1:8094",
            "QUEUE_REFEREE_URL": "http://127.0.0.1:8080",
            "QUEUE_STATE_DIR": "/tmp/q",
            "QUEUE_TIMEOUT": "2.5",
            "QUEUE_ALLOW_TEAMS": "on",
            "QUEUE_RECONCILE_INTERVAL_S": "1.5",
        })
        self.assertEqual(cfg.env, "prod")
        self.assertEqual(cfg.port, 9222)
        self.assertEqual(cfg.token, "s3cr3t")
        self.assertEqual(cfg.state_dir, "/tmp/q")
        self.assertEqual(cfg.timeout, 2.5)
        self.assertTrue(cfg.allow_teams)
        self.assertEqual(cfg.reconcile_interval_s, 1.5)

    def test_invalid_port_raises(self):
        with self.assertRaises(QueueConfigError):
            QueueServiceConfig.from_env({"QUEUE_PORT": "0"})
        with self.assertRaises(QueueConfigError):
            QueueServiceConfig.from_env({"QUEUE_PORT": "abc"})

    def test_invalid_timeout_raises(self):
        with self.assertRaises(QueueConfigError):
            QueueServiceConfig.from_env({"QUEUE_TIMEOUT": "-1"})


class HttpHarness(unittest.TestCase):
    token = ""

    def setUp(self):
        self.coord, self.provisioner, self.referee = make_coordinator()
        self.config = QueueServiceConfig(env="test", bind="127.0.0.1", port=0,
                                         token=self.token)
        self.httpd = build_server(self.config, self.coord)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2.0)

    def _call(self, method, path, payload=None, token=None, raw=None):
        data = raw if raw is not None else (
            json.dumps(payload).encode("utf-8") if payload is not None else None
        )
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path), data=data, method=method
        )
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if token is not None:
            request.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def get(self, path, token=None):
        return self._call("GET", path, token=token)

    def post(self, path, payload=None, token=None, raw=None):
        return self._call("POST", path, payload, token=token, raw=raw)


class ApiTests(HttpHarness):
    def test_health(self):
        status, body = self.get("/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["env"], "test")

    def test_status_empty(self):
        status, body = self.get("/queue/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["queued"], 0)
        self.assertEqual(body["matches"], [])

    def test_join_first_pending(self):
        status, body = self.post("/queue/join", {"identitaet": "str:aa", "mode": "vs"})
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "queued")
        self.assertEqual(body["position"], 1)

    def test_join_second_matched(self):
        self.post("/queue/join", {"identitaet": "str:aa", "mode": "vs"})
        status, body = self.post("/queue/join", {"identitaet": "str:bb", "mode": "vs"})
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "matched")
        match = body["match"]
        self.assertEqual(len(match["assignments"]), 2)
        by_world = {a["world"]: a["identitaet"] for a in match["assignments"]}
        self.assertEqual(by_world["A"], "str:aa")
        self.assertEqual(by_world["B"], "str:bb")
        targets = [a["target"] for a in match["assignments"]]
        self.assertEqual(len(set(targets)), 2)
        self.assertTrue(all(targets))
        # Referee-Lobby fuer beide.
        self.assertEqual(len(self.referee.lobbies), 2)

    def test_join_bad_mode_400(self):
        status, body = self.post("/queue/join", {"identitaet": "str:aa", "mode": "solo"})
        self.assertEqual(status, 400)
        self.assertEqual(body["reason"], "bad_mode")

    def test_join_missing_identitaet_400(self):
        status, body = self.post("/queue/join", {"mode": "vs"})
        self.assertEqual(status, 400)
        self.assertEqual(body["reason"], "bad_request")

    def test_join_team_size_gate_400(self):
        status, body = self.post("/queue/join", {"identitaet": "str:aa", "mode": "vs",
                                                 "team_size": 2})
        self.assertEqual(status, 400)
        self.assertEqual(body["reason"], "unsupported_team_size")

    def test_leave_pending(self):
        self.post("/queue/join", {"identitaet": "str:aa", "mode": "vs"})
        status, body = self.post("/queue/leave", {"identitaet": "str:aa"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_leave_unknown_409(self):
        status, body = self.post("/queue/leave", {"identitaet": "str:zz"})
        self.assertEqual(status, 409)
        self.assertEqual(body["reason"], "not_queued")

    def test_finish_match(self):
        self.post("/queue/join", {"identitaet": "str:aa", "mode": "vs"})
        _s, matched = self.post("/queue/join", {"identitaet": "str:bb", "mode": "vs"})
        match_id = matched["match"]["match_id"]
        status, body = self.post("/queue/finish", {"match_id": match_id, "result": "winnerA"})
        self.assertEqual(status, 200)
        self.assertEqual(body["match"]["state"], "finished")
        self.assertEqual(body["match"]["result"], "winnerA")
        self.assertEqual(len(self.provisioner.stops), 2)

    def test_finish_unknown_409(self):
        status, body = self.post("/queue/finish", {"match_id": 999})
        self.assertEqual(status, 409)
        self.assertEqual(body["reason"], "unknown_match")

    def test_finish_bad_result_400(self):
        self.post("/queue/join", {"identitaet": "str:aa", "mode": "vs"})
        _s, matched = self.post("/queue/join", {"identitaet": "str:bb", "mode": "vs"})
        status, body = self.post("/queue/finish",
                                 {"match_id": matched["match"]["match_id"], "result": "nope"})
        self.assertEqual(status, 400)
        self.assertEqual(body["reason"], "bad_result")

    def test_unknown_route_404(self):
        status, body = self.get("/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["reason"], "not_found")

    def test_method_not_allowed_405(self):
        status, body = self.post("/health", {})
        self.assertEqual(status, 405)
        self.assertEqual(body["reason"], "method_not_allowed")

    def test_bad_body_400(self):
        status, body = self.post("/queue/join", raw=b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(body["reason"], "bad_request")

    def test_backend_unreachable_503(self):
        self.post("/queue/join", {"identitaet": "str:aa", "mode": "vs"})
        self.provisioner.fail_on_world = "A"
        status, body = self.post("/queue/join", {"identitaet": "str:bb", "mode": "vs"})
        self.assertEqual(status, 503)
        self.assertEqual(body["reason"], "provision_failed")

    def test_status_shows_match(self):
        self.post("/queue/join", {"identitaet": "str:aa", "mode": "vs"})
        self.post("/queue/join", {"identitaet": "str:bb", "mode": "vs"})
        status, body = self.get("/queue/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["matches_count"], 1)
        self.assertEqual(len(body["matches"][0]["participants"]), 2)


class AuthTests(HttpHarness):
    token = "s3cr3t-token"

    def test_missing_token_401(self):
        status, body = self.get("/health")
        self.assertEqual(status, 401)
        self.assertEqual(body["reason"], "unauthorized")

    def test_wrong_token_401(self):
        status, _body = self.get("/health", token="nope")
        self.assertEqual(status, 401)

    def test_correct_token_ok(self):
        status, body = self.get("/health", token="s3cr3t-token")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_join_requires_token(self):
        status, _body = self.post("/queue/join",
                                  {"identitaet": "str:aa", "mode": "vs"},
                                  token="s3cr3t-token")
        self.assertEqual(status, 200)


class FullChainHttpTests(HttpHarness):
    """KERN-NACHWEIS ueber die HTTP-Schicht: zwei Spieler -> ein Match A/B."""

    def test_two_players_pair_over_http(self):
        _s, a = self.post("/queue/join", {"identitaet": "str:aa", "mode": "vs"})
        self.assertEqual(a["status"], "queued")
        _s, b = self.post("/queue/join", {"identitaet": "str:bb", "mode": "vs"})
        self.assertEqual(b["status"], "matched")
        match = b["match"]
        by_world = {x["world"]: x["identitaet"] for x in match["assignments"]}
        self.assertEqual(by_world["A"], "str:aa")
        self.assertEqual(by_world["B"], "str:bb")
        # Genau zwei Kalt-Provisionierungen.
        self.assertEqual(len(self.provisioner.starts), 2)
        # Status zeigt beide Teilnehmer.
        _s, snap = self.get("/queue/status")
        self.assertEqual(len(snap["matches"][0]["participants"]), 2)
        # Finish -> finished + Cleanup.
        _s, done = self.post("/queue/finish", {"match_id": match["match_id"],
                                               "result": "winnerB"})
        self.assertEqual(done["match"]["state"], "finished")
        self.assertEqual(len(self.provisioner.stops), 2)


if __name__ == "__main__":
    unittest.main()
