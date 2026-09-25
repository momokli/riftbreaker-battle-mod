#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermetische Tests fuer deploy/capsule/capsule_service.py (Issue #931).

Kein Netz (ausser einem echten ``ThreadingHTTPServer`` auf ``127.0.0.1:0``),
kein Spiel/Docker: die Kapsel-Clients sind Fakes. Aufruf:

    cd deploy/capsule && python3 -m unittest -v
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from capsule_flow import CapsuleCoordinator
from capsule_service import (
    CapsuleConfigError,
    CapsuleServiceConfig,
    build_server,
)
from test_capsule_flow import FakeBridge, FakeClock, FakeCycle, FakeParked


def make_coordinator():
    clock = FakeClock()
    parked = FakeParked()
    bridges = {}
    cycles = {}

    def bridge_factory(url):
        bridge = bridges.get(url)
        if bridge is None:
            bridge = FakeBridge(url)
            bridges[url] = bridge
        return bridge

    def cycle_factory(env):
        cycle = cycles.get(env)
        if cycle is None:
            cycle = FakeCycle(env)
            cycles[env] = cycle
        return cycle

    coord = CapsuleCoordinator(parked, cycle_factory, bridge_factory, clock=clock, env="test")
    return coord, parked, bridges, cycles


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = CapsuleServiceConfig.from_env({})
        self.assertEqual(cfg.env, "dev")
        self.assertEqual(cfg.bind, "127.0.0.1")
        self.assertEqual(cfg.port, 8093)
        self.assertEqual(cfg.token, "")
        self.assertEqual(cfg.timeout, 5.0)

    def test_env_values(self):
        cfg = CapsuleServiceConfig.from_env(
            {
                "CAPSULE_ENV": "staging",
                "CAPSULE_BIND": "127.0.0.2",
                "CAPSULE_PORT": "8096",
                "CAPSULE_TOKEN": "  s3cr3t  ",
                "CAPSULE_PARKED_URL": "http://127.0.0.1:8097",
                "CAPSULE_PARKED_TOKEN": "ptok",
                "CAPSULE_CYCLE_URL": "http://127.0.0.1:9202",
                "CAPSULE_TIMEOUT": "2.5",
            }
        )
        self.assertEqual(cfg.env, "staging")
        self.assertEqual(cfg.bind, "127.0.0.2")
        self.assertEqual(cfg.port, 8096)
        self.assertEqual(cfg.token, "s3cr3t")
        self.assertEqual(cfg.parked_url, "http://127.0.0.1:8097")
        self.assertEqual(cfg.parked_token, "ptok")
        self.assertEqual(cfg.cycle_url, "http://127.0.0.1:9202")
        self.assertEqual(cfg.timeout, 2.5)

    def test_invalid_port_raises(self):
        with self.assertRaises(CapsuleConfigError):
            CapsuleServiceConfig.from_env({"CAPSULE_PORT": "0"})
        with self.assertRaises(CapsuleConfigError):
            CapsuleServiceConfig.from_env({"CAPSULE_PORT": "abc"})

    def test_invalid_timeout_raises(self):
        with self.assertRaises(CapsuleConfigError):
            CapsuleServiceConfig.from_env({"CAPSULE_TIMEOUT": "-1"})
        with self.assertRaises(CapsuleConfigError):
            CapsuleServiceConfig.from_env({"CAPSULE_TIMEOUT": "NaN"})


class HttpHarness(unittest.TestCase):
    token = ""

    def setUp(self):
        self.coord, self.parked, self.bridges, self.cycles = make_coordinator()
        self.config = CapsuleServiceConfig(
            env="test", bind="127.0.0.1", port=0, token=self.token
        )
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

    def test_status_idle(self):
        status, body = self.get("/capsule/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["phase"], "idle")

    def test_open_then_status_claimed(self):
        status, body = self.post("/capsule/open", {"identitaet": "str:AB12"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["phase"], "claimed")
        self.assertEqual(body["gns_endpoint"], "127.0.0.1:41001")
        # Kein resume_game -> Welt bleibt pausiert.
        self.assertTrue(self.bridges["http://127.0.0.1:40001"].paused)
        self.assertFalse(self.parked.claims[0]["resume"])

    def test_open_twice_409(self):
        self.post("/capsule/open", {})
        status, body = self.post("/capsule/open", {})
        self.assertEqual(status, 409)
        self.assertEqual(body["reason"], "already_open")

    def test_ready_warmup(self):
        self.post("/capsule/open", {})
        status, body = self.post("/capsule/ready", {})
        self.assertEqual(status, 200)
        self.assertEqual(body["phase"], "warmup")
        self.assertEqual(self.cycles["test"].state, "warmup")

    def test_ready_without_open_409(self):
        status, body = self.post("/capsule/ready", {})
        self.assertEqual(status, 409)
        self.assertEqual(body["reason"], "no_instance")

    def test_finish_parks_and_counts_round(self):
        self.post("/capsule/open", {})
        self.post("/capsule/ready", {})
        status, body = self.post("/capsule/finish", {"result": "win"})
        self.assertEqual(status, 200)
        self.assertEqual(body["phase"], "parked")
        self.assertEqual(body["round"], 1)
        self.assertEqual(self.parked.recycles[0]["result"], "win")

    def test_finish_without_result(self):
        self.post("/capsule/open", {})
        self.post("/capsule/ready", {})
        status, body = self.post("/capsule/finish", {})
        self.assertEqual(status, 200)
        self.assertIsNone(self.parked.recycles[0]["result"])

    def test_finish_bad_result_400(self):
        self.post("/capsule/open", {})
        status, body = self.post("/capsule/finish", {"result": "draw"})
        self.assertEqual(status, 400)
        self.assertEqual(body["reason"], "bad_request")

    def test_auto_releases_override(self):
        self.post("/capsule/open", {})
        status, body = self.post("/capsule/auto", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.bridges["http://127.0.0.1:40001"].pause_auto, -1)

    def test_unknown_route_404(self):
        status, body = self.get("/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["reason"], "not_found")

    def test_method_not_allowed_405(self):
        status, body = self.post("/health", {})
        self.assertEqual(status, 405)
        self.assertEqual(body["reason"], "method_not_allowed")

    def test_bad_body_400(self):
        status, body = self.post("/capsule/open", raw=b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(body["reason"], "bad_request")

    def test_backend_unreachable_503(self):
        self.parked.fail_claim = RuntimeError("connection refused")
        status, body = self.post("/capsule/open", {})
        self.assertEqual(status, 503)


class AuthTests(HttpHarness):
    token = "s3cr3t-token"

    def test_missing_token_401(self):
        status, body = self.get("/health")
        self.assertEqual(status, 401)
        self.assertEqual(body["reason"], "unauthorized")

    def test_wrong_token_401(self):
        status, body = self.get("/health", token="nope")
        self.assertEqual(status, 401)

    def test_correct_token_ok(self):
        status, body = self.get("/health", token="s3cr3t-token")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_open_requires_token(self):
        status, body = self.post("/capsule/open", {}, token="s3cr3t-token")
        self.assertEqual(status, 200)


class FullChainHttpTests(HttpHarness):
    """KERN-NACHWEIS ueber die HTTP-Schicht: eine ganze Runde, hermetisch."""

    def test_one_full_round_over_http(self):
        # open -> claimed/paused
        status, opened = self.post("/capsule/open", {"identitaet": "str:AB12"})
        self.assertEqual(status, 200)
        self.assertEqual(opened["phase"], "claimed")

        # ready -> warmup
        status, ready = self.post("/capsule/ready", {})
        self.assertEqual(status, 200)
        self.assertEqual(ready["phase"], "warmup")

        # Cycle laeuft weiter -> running
        self.cycles["test"].to_running()
        _s, snap = self.get("/capsule/status")
        self.assertEqual(snap["phase"], "running")

        # HQ-Tod -> game_over sichtbar
        self.cycles["test"].to_game_over()
        _s, snap = self.get("/capsule/status")
        self.assertEqual(snap["phase"], "game_over")

        # finish(win) -> parked
        status, done = self.post("/capsule/finish", {"result": "win"})
        self.assertEqual(status, 200)
        self.assertEqual(done["phase"], "parked")
        self.assertEqual(done["round"], 1)

        # auto -> Override frei
        status, _auto = self.post("/capsule/auto", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.bridges["http://127.0.0.1:40001"].pause_auto, -1)


if __name__ == "__main__":
    unittest.main()