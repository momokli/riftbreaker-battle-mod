#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermetische Tests fuer deploy/compose/warm/warm_service.py (Issue #1093).

Kein Docker, kein Netz (ausser einem echten ``ThreadingHTTPServer`` auf
``127.0.0.1:0``), kein Spiel: die Bridge ist ein ``FakeBridge``, die Uhr eine
``FakeClock``. Muster aus ``deploy/parked/test_parked_service.py``. Aufruf
(vom Repo-Root):

    python3 -m unittest deploy/compose/warm/test_warm_service.py -v
"""

from __future__ import annotations

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from warm_service import (  # noqa: E402
    BridgeError,
    WarmConfigError,
    WarmController,
    WarmServiceConfig,
    build_server,
)

BRIDGE_URL = "http://dedicated:9001"
GNS_ENDPOINT = "127.0.0.1:6322"


class FakeClock(object):
    """Monotone Fake-Uhr; nur explizites ``advance`` bewegt die Zeit."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeBridge(object):
    """Fake-Bridge: protokolliert Aufrufe, kein Netz."""

    def __init__(self, url: str, clock: FakeClock, handover_seconds: float = 0.0, healthy: bool = True) -> None:
        self.url = url
        self.clock = clock
        self.handover_seconds = handover_seconds
        self.healthy = healthy
        self.calls = []
        self.paused = False
        self.fail_pause = False

    def health_ok(self) -> bool:
        self.calls.append("health_ok")
        return self.healthy

    def pause_game(self):
        self.calls.append("pause_game")
        if self.fail_pause:
            raise BridgeError("fake pause_game exploded")
        self.paused = True
        return {"ok": True}

    def resume_game(self):
        self.calls.append("resume_game")
        self.paused = False
        self.clock.advance(self.handover_seconds)
        return {"ok": True}

    def round_reset(self):
        self.calls.append("round_reset")
        return {"ok": True}

    def end_game(self, result=None):
        self.calls.append("end_game")
        return {"ok": True}


def make_controller(config: WarmServiceConfig, bridge: FakeBridge, clock: FakeClock) -> WarmController:
    return WarmController(config, bridge_factory=lambda _url: bridge, clock=clock)


# ---------------------------------------------------------------------------
# Config (WARM_*, fail-closed)
# ---------------------------------------------------------------------------


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        config = WarmServiceConfig.from_env({})
        self.assertEqual(config.env, "local")
        self.assertEqual(config.bind, "0.0.0.0")
        self.assertEqual(config.port, 9201)
        self.assertEqual(config.bridge_url, "http://dedicated:9001")
        self.assertEqual(config.gns_endpoint, "127.0.0.1:6322")
        self.assertEqual(config.instance, "solo")
        self.assertEqual(config.token, "")
        self.assertTrue(config.park_on_start)

    def test_env_values(self):
        config = WarmServiceConfig.from_env(
            {
                "WARM_ENV": "dev",
                "WARM_BIND": "127.0.0.1",
                "WARM_PORT": "9301",
                "WARM_BRIDGE_URL": "http://host:9001/",
                "WARM_GNS_ENDPOINT": "10.0.0.1:6322",
                "WARM_INSTANCE": "solo-a",
                "WARM_TOKEN": "s3cr3t",
                "WARM_PARK_ON_START": "false",
            }
        )
        self.assertEqual(config.env, "dev")
        self.assertEqual(config.bind, "127.0.0.1")
        self.assertEqual(config.port, 9301)
        self.assertEqual(config.bridge_url, "http://host:9001/")
        self.assertEqual(config.gns_endpoint, "10.0.0.1:6322")
        self.assertEqual(config.instance, "solo-a")
        self.assertEqual(config.token, "s3cr3t")
        self.assertFalse(config.park_on_start)

    def test_invalid_port_raises(self):
        with self.assertRaises(WarmConfigError):
            WarmServiceConfig.from_env({"WARM_PORT": "nope"})
        with self.assertRaises(WarmConfigError):
            WarmServiceConfig.from_env({"WARM_PORT": "0"})

    def test_invalid_park_on_start_raises(self):
        with self.assertRaises(WarmConfigError):
            WarmServiceConfig.from_env({"WARM_PARK_ON_START": "maybe"})


# ---------------------------------------------------------------------------
# Start-Park (WARM_PARK_ON_START, fail-loud)
# ---------------------------------------------------------------------------


class ParkOnStartTests(unittest.TestCase):
    def _config(self, park_on_start: bool) -> WarmServiceConfig:
        return WarmServiceConfig(
            env="local",
            instance="solo",
            bridge_url=BRIDGE_URL,
            gns_endpoint=GNS_ENDPOINT,
            park_on_start=park_on_start,
            token="",
        )

    def test_start_parks_world(self):
        bridge = FakeBridge(BRIDGE_URL, FakeClock())
        controller = make_controller(self._config(True), bridge, FakeClock())
        controller.start()
        self.assertEqual(bridge.calls, ["pause_game"])
        self.assertTrue(bridge.paused)
        self.assertEqual(controller.status()["state"], "parked")

    def test_start_noop_when_disabled(self):
        bridge = FakeBridge(BRIDGE_URL, FakeClock())
        controller = make_controller(self._config(False), bridge, FakeClock())
        controller.start()
        self.assertEqual(bridge.calls, [])
        self.assertEqual(controller.status()["state"], "parked")

    def test_start_fail_loud(self):
        bridge = FakeBridge(BRIDGE_URL, FakeClock())
        bridge.fail_pause = True
        controller = make_controller(self._config(True), bridge, FakeClock())
        with self.assertRaises(BridgeError):
            controller.start()


# ---------------------------------------------------------------------------
# HTTP-Harness: echter ThreadingHTTPServer auf 127.0.0.1:0
# ---------------------------------------------------------------------------


class HttpHarness(unittest.TestCase):
    token = ""

    def setUp(self):
        self.clock = FakeClock(1000.0)
        self.bridge = FakeBridge(BRIDGE_URL, self.clock)
        self.config = WarmServiceConfig(
            env="local",
            bind="127.0.0.1",
            port=0,
            bridge_url=BRIDGE_URL,
            gns_endpoint=GNS_ENDPOINT,
            instance="solo",
            token=self.token,
            park_on_start=False,
        )
        self.controller = make_controller(self.config, self.bridge, self.clock)
        self.httpd = build_server(self.config, self.controller)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2.0)

    def _call(self, method, path, payload=None, token=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, method=method)
        if payload is not None:
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

    def post(self, path, payload=None, token=None):
        return self._call("POST", path, payload, token=token)


class HttpTests(HttpHarness):
    def test_health(self):
        status, body = self.get("/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["env"], "local")

    def test_status_parked(self):
        status, body = self.get("/status")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["instance"], "solo")
        self.assertEqual(body["env"], "local")
        self.assertEqual(body["state"], "parked")
        self.assertEqual(body["gns_endpoint"], GNS_ENDPOINT)
        self.assertEqual(body["bridge_url"], BRIDGE_URL)

    def test_claim_returns_endpoints(self):
        status, body = self.post("/claim", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["state"], "claimed")
        self.assertEqual(body["instance"], "solo")
        self.assertEqual(body["env"], "local")
        self.assertTrue(body["resumed"])
        # Die Kapsel (ParkedServiceClient/open) braucht genau diese beiden Felder.
        self.assertEqual(body["bridge_url"], BRIDGE_URL)
        self.assertEqual(body["gns_endpoint"], GNS_ENDPOINT)
        self.assertIn("resume_game", self.bridge.calls)
        self.assertFalse(self.bridge.paused)

    def test_claim_resume_false_keeps_world_paused(self):
        # Solo-Kapsel: POST /claim {"resume": false} -> Welt bleibt pausiert.
        # Precondition: der Start-Park (WARM_PARK_ON_START) hat die Welt angehalten.
        self.bridge.paused = True
        self.bridge.calls.clear()
        status, body = self.post("/claim", {"resume": False})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "claimed")
        self.assertFalse(body["resumed"])
        self.assertNotIn("resume_game", self.bridge.calls)
        self.assertTrue(self.bridge.paused)

    def test_claim_non_bool_resume_400(self):
        status, body = self.post("/claim", {"resume": 1})
        self.assertEqual(status, 400)
        self.assertEqual(body["reason"], "bad_request")

    def test_claim_when_claimed_409(self):
        status, _first = self.post("/claim", {})
        self.assertEqual(status, 200)
        status, body = self.post("/claim", {})
        self.assertEqual(status, 409)
        self.assertFalse(body["ok"])
        self.assertEqual(body["reason"], "none_parked")

    def test_claim_unknown_instance_409(self):
        status, body = self.post("/claim", {"instance_id": "other"})
        self.assertEqual(status, 409)
        self.assertEqual(body["reason"], "unknown_instance")

    def test_claim_bridge_unhealthy_503(self):
        self.bridge.healthy = False
        status, body = self.post("/claim", {})
        self.assertEqual(status, 503)
        self.assertEqual(body["reason"], "bridge_unhealthy")

    def test_recycle_returns_to_parked(self):
        _s, claimed = self.post("/claim", {"resume": False})
        self.bridge.calls.clear()
        status, body = self.post("/recycle", {"instance_id": claimed["instance"], "keep_warm": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["state"], "parked")
        self.assertEqual(body["rounds"], 1)
        # Recycle puffert die Welt: round_reset + pause_game (keine laufende Welt).
        self.assertIn("round_reset", self.bridge.calls)
        self.assertIn("pause_game", self.bridge.calls)
        self.assertTrue(self.bridge.paused)
        # ... und danach ist die Instanz wieder claimbar.
        status, again = self.post("/claim", {})
        self.assertEqual(status, 200)
        self.assertEqual(again["state"], "claimed")

    def test_recycle_with_result_calls_end_game(self):
        self.post("/claim", {"resume": False})
        self.bridge.calls.clear()
        status, _body = self.post("/recycle", {"result": "win"})
        self.assertEqual(status, 200)
        self.assertIn("end_game", self.bridge.calls)

    def test_recycle_without_result_skips_end_game(self):
        self.post("/claim", {"resume": False})
        self.bridge.calls.clear()
        status, _body = self.post("/recycle", {})
        self.assertEqual(status, 200)
        self.assertNotIn("end_game", self.bridge.calls)

    def test_recycle_before_claim_409(self):
        status, body = self.post("/recycle", {})
        self.assertEqual(status, 409)
        self.assertEqual(body["reason"], "not_recyclable")

    def test_recycle_non_bool_keep_warm_400(self):
        self.post("/claim", {})
        status, body = self.post("/recycle", {"keep_warm": "false"})
        self.assertEqual(status, 400)
        self.assertEqual(body["reason"], "bad_request")

    def test_recycle_cold_not_supported_409(self):
        # Eine statische Instanz kann nicht gestoppt werden: keep_warm=false ist
        # fail-loud (kein stiller No-Op).
        self.post("/claim", {})
        status, body = self.post("/recycle", {"keep_warm": False})
        self.assertEqual(status, 409)
        self.assertEqual(body["reason"], "cold_not_supported")

    def test_unknown_route_404(self):
        status, body = self.get("/nope")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])
        self.assertEqual(body["reason"], "not_found")

    def test_method_not_allowed_405(self):
        status, body = self.post("/health", {})
        self.assertEqual(status, 405)
        self.assertEqual(body["reason"], "method_not_allowed")

    def test_bad_body_400(self):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/claim" % self.port,
            data=b"{not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(ctx.exception.code, 400)


class AuthTests(HttpHarness):
    token = "s3cr3t-token"

    def test_missing_token_401(self):
        status, body = self.get("/health")
        self.assertEqual(status, 401)
        self.assertEqual(body["reason"], "unauthorized")

    def test_wrong_token_401(self):
        status, _body = self.get("/status", token="nope")
        self.assertEqual(status, 401)

    def test_correct_token_ok(self):
        status, body = self.get("/health", token="s3cr3t-token")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_claim_with_token_ok(self):
        status, body = self.post("/claim", {}, token="s3cr3t-token")
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "claimed")


if __name__ == "__main__":
    unittest.main()
