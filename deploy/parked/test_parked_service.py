#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermetische Tests fuer deploy/parked/parked_service.py (Issue #928).

Kein Docker, kein Netz (ausser einem echten ``ThreadingHTTPServer`` auf
``127.0.0.1:0``), kein Spiel: Provisioner/Bridge sind Fakes, die Uhr ist eine
``FakeClock``. Muster aus ``test_parked_pool.py`` und
``deploy/server-control/test_server_control.py``. Aufruf:

    cd deploy/parked && python3 -m unittest -v
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace

from parked_pool import ParkedPool, ParkedState
from parked_service import (
    ParkedConfigError,
    ParkedController,
    ParkedServiceConfig,
    build_provisioner,
    build_server,
)


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

    def __init__(self, url: str, clock: FakeClock, handover_seconds: float = 0.0,
                 healthy: bool = True) -> None:
        self.url = url
        self.clock = clock
        self.handover_seconds = handover_seconds
        self.healthy = healthy
        self.calls = []
        self.paused = False

    def health_ok(self) -> bool:
        self.calls.append("health_ok")
        return self.healthy

    def pause_game(self):
        self.calls.append("pause_game")
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


class FakeProvisioner(object):
    """Fake-Provisioner (#908): zaehlt Starts/Stops, keine echte Arbeit."""

    _BASE_PORT = 40000

    def __init__(self, env: str = "test") -> None:
        self.cfg = SimpleNamespace(env=env)
        self.starts = []
        self.stops = []
        self.fail_start = False
        # GNS-UDP-Host-Endpoint, den `status` liefert (simuliert `docker port`
        # 6321/udp). Tests duerfen ihn aendern (Container-Neustart) oder auf None
        # setzen (Mapping weg).
        self.gns = "127.0.0.1:%d" % (self._BASE_PORT + 1001)

    def start(self, env=None, mode="solo", instance_id=None):
        if self.fail_start:
            raise RuntimeError("fake start %s exploded" % instance_id)
        self.starts.append((env or self.cfg.env, instance_id))
        port = self._BASE_PORT + len(self.starts)
        return {
            "instance": instance_id,
            "container": "riftbreaker-dedicated-%s-%s" % (env or self.cfg.env, instance_id),
            "running": True,
            "ports": {"bridge": port, "gns": "127.0.0.1:%d" % (port + 1000)},
            "created": True,
        }

    def status(self, instance_id=None, env=None):
        # Erste Instanz: bridge=40001, gns=41001 (siehe start). Der GNS-UDP-Port
        # kommt hier AUS DEM MAPPING (simuliert `docker port` 6321/udp).
        return {
            "running": True,
            "health": "healthy",
            "ports": {"bridge": self._BASE_PORT + 1, "gns": self.gns},
            "container": "riftbreaker-dedicated-%s-%s" % (env or self.cfg.env, instance_id),
        }

    def stop(self, instance_id=None, env=None):
        self.stops.append((env or self.cfg.env, instance_id))
        return {"instance": instance_id, "removed": {}}


def make_pool(clock, provisioner, bridges):
    def factory(url):
        bridge = bridges.get(url)
        if bridge is None:
            bridge = FakeBridge(url, clock)
            bridges[url] = bridge
        return bridge

    return ParkedPool(provisioner, factory, clock=clock, sleep=lambda _s: None)


def make_controller(clock, provisioner, bridges, pool_size=1, **kwargs):
    pool = make_pool(clock, provisioner, bridges)
    controller = ParkedController(
        pool,
        pool_size=pool_size,
        max_park_seconds=kwargs.pop("max_park_seconds", 900.0),
        reap_interval=kwargs.pop("reap_interval", 30.0),
        env=kwargs.pop("env", "test"),
        prefix=kwargs.pop("prefix", "parked"),
        clock=clock,
        sleep=lambda _s: None,
        **kwargs,
    )
    return controller


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = ParkedServiceConfig.from_env({})
        self.assertEqual(cfg.env, "test")
        self.assertEqual(cfg.bind, "127.0.0.1")
        self.assertEqual(cfg.port, 8092)
        self.assertEqual(cfg.pool_size, 1)
        self.assertEqual(cfg.max_park_seconds, 900.0)
        self.assertEqual(cfg.reap_interval, 30.0)
        self.assertEqual(cfg.instance_prefix, "parked")
        self.assertEqual(cfg.token, "")

    def test_env_values(self):
        cfg = ParkedServiceConfig.from_env(
            {
                "PARKED_ENV": "staging",
                "PARKED_BIND": "127.0.0.2",
                "PARKED_PORT": "8095",
                "PARKED_POOL_SIZE": "3",
                "PARKED_MAX_PARK_SECONDS": "120",
                "PARKED_REAP_INTERVAL": "5",
                "PARKED_INSTANCE_PREFIX": "warm",
                "PARKED_TOKEN": "  s3cr3t  ",
                "PARKED_LOG_LEVEL": "DEBUG",
            }
        )
        self.assertEqual(cfg.env, "staging")
        self.assertEqual(cfg.bind, "127.0.0.2")
        self.assertEqual(cfg.port, 8095)
        self.assertEqual(cfg.pool_size, 3)
        self.assertEqual(cfg.max_park_seconds, 120.0)
        self.assertEqual(cfg.reap_interval, 5.0)
        self.assertEqual(cfg.instance_prefix, "warm")
        self.assertEqual(cfg.token, "s3cr3t")
        self.assertEqual(cfg.log_level, "DEBUG")

    def test_env_falls_back_to_provisioner_env(self):
        cfg = ParkedServiceConfig.from_env({"PROVISIONER_ENV": "prod"})
        self.assertEqual(cfg.env, "prod")

    def test_invalid_pool_size_raises(self):
        with self.assertRaises(ParkedConfigError):
            ParkedServiceConfig.from_env({"PARKED_POOL_SIZE": "0"})
        with self.assertRaises(ParkedConfigError):
            ParkedServiceConfig.from_env({"PARKED_POOL_SIZE": "abc"})

    def test_invalid_max_park_seconds_raises(self):
        with self.assertRaises(ParkedConfigError):
            ParkedServiceConfig.from_env({"PARKED_MAX_PARK_SECONDS": "-1"})

    def test_invalid_reap_interval_raises(self):
        with self.assertRaises(ParkedConfigError):
            ParkedServiceConfig.from_env({"PARKED_REAP_INTERVAL": "NaN"})

    def test_invalid_port_raises(self):
        with self.assertRaises(ParkedConfigError):
            ParkedServiceConfig.from_env({"PARKED_PORT": "0"})

    def test_missing_provisioner_image_raises(self):
        cfg = ParkedServiceConfig.from_env({})
        with self.assertRaises(ParkedConfigError):
            build_provisioner(cfg, env={})


class ControllerFillTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(1000.0)
        self.provisioner = FakeProvisioner()
        self.bridges = {}

    def test_maintain_fills_pool_to_size(self):
        controller = make_controller(self.clock, self.provisioner, self.bridges, pool_size=2)
        controller.maintain_once()
        counters = controller.counters()
        self.assertEqual(counters["parked"], 2)
        self.assertEqual(counters["total"], 2)
        self.assertEqual(len(self.provisioner.starts), 2)

    def test_maintain_is_idempotent(self):
        controller = make_controller(self.clock, self.provisioner, self.bridges, pool_size=1)
        controller.maintain_once()
        controller.maintain_once()
        self.assertEqual(len(self.provisioner.starts), 1)

    def test_warm_failure_backs_off_and_counts(self):
        controller = make_controller(self.clock, self.provisioner, self.bridges, pool_size=1)
        self.provisioner.fail_start = True
        controller.maintain_once()
        self.assertEqual(controller.warm_failures, 1)
        self.assertEqual(controller.counters()["parked"], 0)

        # Backoff aktiv: der naechste sofortige Lauf versucht es NICHT erneut.
        controller.maintain_once()
        self.assertEqual(controller.warm_failures, 1)

        self.clock.advance(1.0)  # erster Backoff (1.0 s) abgelaufen
        controller.maintain_once()
        self.assertEqual(controller.warm_failures, 2)

        self.provisioner.fail_start = False
        self.clock.advance(10.0)
        controller.maintain_once()
        self.assertEqual(controller.counters()["parked"], 1)
        self.assertEqual(controller.warm_failures, 2)

    def test_counters_invariant_warming_stopped(self):
        controller = make_controller(self.clock, self.provisioner, self.bridges, pool_size=1)
        self.provisioner.fail_start = True
        controller.maintain_once()
        counters = controller.counters()
        state_sum = (
            counters["parked"]
            + counters["claimed"]
            + counters["warming"]
            + counters["recycling"]
            + counters["stopped"]
        )
        self.assertEqual(state_sum, counters["total"])
        self.assertEqual(counters["stopped"], 1)


class ControllerReapTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(1000.0)
        self.provisioner = FakeProvisioner()
        self.bridges = {}

    def test_reap_due_stops_overdue_parked(self):
        controller = make_controller(
            self.clock, self.provisioner, self.bridges, pool_size=1,
            max_park_seconds=10.0, reap_interval=5.0,
        )
        controller.maintain_once()
        self.assertFalse(controller.reap_due())
        self.clock.advance(20.0)
        self.assertTrue(controller.reap_due())
        controller.maintain_once()
        counters = controller.counters()
        self.assertEqual(counters["parked"], 0)
        self.assertEqual(counters["stopped"], 1)
        self.assertEqual(len(self.provisioner.stops), 1)

    def test_reap_ignores_claimed(self):
        controller = make_controller(
            self.clock, self.provisioner, self.bridges, pool_size=1,
            max_park_seconds=10.0, reap_interval=5.0,
        )
        controller.maintain_once()
        claimed = controller.claim()
        self.clock.advance(1000.0)
        controller.maintain_once()
        self.assertIn(claimed["instance"], [r["instance"] for r in controller._rows()])
        # CLAIMED bleibt unberuehrt (kein stop der laufenden Instanz).
        self.assertEqual(self.provisioner.stops, [])


class ControllerClaimRecycleTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(1000.0)
        self.provisioner = FakeProvisioner()
        self.bridges = {}

    def test_claim_fifo_oldest(self):
        controller = make_controller(self.clock, self.provisioner, self.bridges, pool_size=1)
        controller.pool.warm_up(env="test", instance_id="old")
        self.clock.advance(30.0)
        controller.pool.warm_up(env="test", instance_id="fresh")
        result = controller.claim()
        self.assertEqual(result["instance"], "old")
        self.assertEqual(controller.claims, 1)
        self.assertIsNotNone(controller.counters()["handover_last_seconds"])

    def test_claim_none_parked_raises_409(self):
        controller = make_controller(self.clock, self.provisioner, self.bridges, pool_size=1)
        with self.assertRaises(Exception) as ctx:
            controller.claim()
        self.assertEqual(getattr(ctx.exception, "status", None), 409)
        self.assertEqual(getattr(ctx.exception, "reason", None), "none_parked")

    def test_recycle_returns_to_parked(self):
        controller = make_controller(self.clock, self.provisioner, self.bridges, pool_size=1)
        controller.maintain_once()
        claimed = controller.claim()
        result = controller.recycle(instance_id=claimed["instance"], keep_warm=True)
        self.assertEqual(result["state"], ParkedState.PARKED.value)
        self.assertEqual(controller.counters()["parked"], 1)
        self.assertEqual(controller.recycles, 1)

    def test_double_claim_same_instance_409(self):
        controller = make_controller(self.clock, self.provisioner, self.bridges, pool_size=1)
        entry = controller.pool.warm_up(env="test", instance_id="r1")
        controller.claim(instance_id="r1")
        with self.assertRaises(Exception) as ctx:
            controller.claim(instance_id="r1")
        self.assertEqual(getattr(ctx.exception, "status", None), 409)
        # kein zweites resume_game (Doppel-Claim lehnt VOR der Bridge ab)
        self.assertEqual(self.bridges[entry.bridge_url].calls.count("resume_game"), 1)


class HttpHarness(unittest.TestCase):
    """Echter ``ThreadingHTTPServer`` auf ``127.0.0.1:0``, Requests per urllib."""

    token = ""

    def setUp(self):
        self.clock = FakeClock(1000.0)
        self.provisioner = FakeProvisioner()
        self.bridges = {}
        self.pool = make_pool(self.clock, self.provisioner, self.bridges)
        self.controller = ParkedController(
            self.pool, pool_size=1, max_park_seconds=900.0, reap_interval=9999.0,
            env="test", prefix="parked", clock=self.clock, sleep=lambda _s: None,
        )
        self.config = ParkedServiceConfig(
            env="test", bind="127.0.0.1", port=0, token=self.token
        )
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
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path), data=data, method=method
        )
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
        self.assertEqual(body["env"], "test")

    def test_status_has_counters_and_entries(self):
        self.controller.maintain_once()
        status, body = self.get("/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["counters"]["parked"], 1)
        self.assertEqual(len(body["entries"]), 1)
        self.assertEqual(body["entries"][0]["state"], "parked")

    def test_claim_ok_with_handover(self):
        self.controller.maintain_once()
        entry = self.pool.status()[0]
        self.bridges[entry["bridge_url"]].handover_seconds = 0.13
        status, body = self.post("/claim", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["state"], "claimed")
        self.assertAlmostEqual(body["handover_seconds"], 0.13, places=9)
        # Issue #929: die Claim-Antwort traegt den GNS-UDP-Endpoint (Relay-Ziel).
        self.assertEqual(body["gns_endpoint"], "127.0.0.1:41001")

    def test_claim_empty_pool_409(self):
        status, body = self.post("/claim", {})
        self.assertEqual(status, 409)
        self.assertFalse(body["ok"])
        self.assertEqual(body["reason"], "none_parked")

    def test_claim_resume_false_keeps_world_paused(self):
        # #931: POST /claim {"resume": false} reicht durch -> Welt bleibt pausiert.
        self.controller.maintain_once()
        entry = self.pool.status()[0]
        bridge = self.bridges[entry["bridge_url"]]
        status, body = self.post("/claim", {"resume": False})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "claimed")
        self.assertFalse(body["resumed"])
        self.assertNotIn("resume_game", bridge.calls)
        self.assertTrue(bridge.paused)

    def test_claim_resume_default_true(self):
        self.controller.maintain_once()
        status, body = self.post("/claim", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["resumed"])

    def test_claim_non_bool_resume_400(self):
        # `resume` kein JSON-Boolean -> 400 bad_request (kein Truthy-Koerzieren).
        self.controller.maintain_once()
        status, body = self.post("/claim", {"resume": 1})
        self.assertEqual(status, 400)
        self.assertEqual(body["reason"], "bad_request")

    def test_status_entry_carries_gns_endpoint(self):
        # Issue #929 (durchgaengig): provisioner ports.gns -> ParkedEntry -> /status.
        self.controller.maintain_once()
        status, body = self.get("/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["entries"][0]["gns_endpoint"], "127.0.0.1:41001")

    def test_claim_gns_endpoint_fresh_after_restart(self):
        # Nach einem Container-Neustart wechselt der Host-UDP-Port: /claim liest
        # ihn FRISCH vom Provisioner (nicht den warm_up-Cache) und liefert ihn als
        # Relay-Ziel in der HTTP-Antwort.
        self.controller.maintain_once()
        self.provisioner.gns = "127.0.0.1:55999"
        status, body = self.post("/claim", {})
        self.assertEqual(status, 200)
        self.assertEqual(body["gns_endpoint"], "127.0.0.1:55999")
        # /status spiegelt denselben frischen Endpoint (Quelle fuer Abnahme).
        _s, snapshot = self.get("/status")
        self.assertEqual(snapshot["entries"][0]["gns_endpoint"], "127.0.0.1:55999")

    def test_claim_without_gns_mapping_is_none(self):
        # Fehlt das 6321/udp-Mapping, ist gns_endpoint None — kein Crash, die
        # uebrigen Claim-Felder bleiben intakt.
        self.controller.maintain_once()
        self.provisioner.gns = None
        status, body = self.post("/claim", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIsNone(body["gns_endpoint"])
        self.assertIn("bridge_url", body)

    def test_recycle_cold_releases_gns_endpoint(self):
        # Ein kalter Recycle (Stop) entfernt den Endpoint wieder (toter Port).
        self.controller.maintain_once()
        _s, claimed = self.post("/claim", {})
        status, body = self.post(
            "/recycle", {"instance_id": claimed["instance"], "keep_warm": False}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "stopped")
        _s, snapshot = self.get("/status")
        stopped = [r for r in snapshot["entries"] if r["state"] == "stopped"]
        self.assertEqual(len(stopped), 1)
        self.assertIsNone(stopped[0]["gns_endpoint"])

    def test_double_claim_409(self):
        self.controller.maintain_once()
        status, first = self.post("/claim", {})
        self.assertEqual(status, 200)
        status, second = self.post("/claim", {"instance_id": first["instance"]})
        self.assertEqual(status, 409)
        self.assertEqual(second["reason"], "not_claimable")

    def test_recycle_returns_to_parked(self):
        self.controller.maintain_once()
        _status, claimed = self.post("/claim", {})
        status, body = self.post("/recycle", {"instance_id": claimed["instance"], "keep_warm": True})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "parked")
        self.assertEqual(body["rounds"], 1)
        _status, snapshot = self.get("/status")
        self.assertEqual(snapshot["counters"]["parked"], 1)
        self.assertEqual(snapshot["counters"]["claimed"], 0)

    def test_recycle_cold_stops(self):
        self.controller.maintain_once()
        _status, claimed = self.post("/claim", {})
        status, body = self.post("/recycle", {"instance_id": claimed["instance"], "keep_warm": False})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "stopped")

    def test_recycle_non_bool_keep_warm_400(self):
        self.controller.maintain_once()
        _status, claimed = self.post("/claim", {})
        status, body = self.post("/recycle", {"instance_id": claimed["instance"], "keep_warm": "false"})
        self.assertEqual(status, 400)
        self.assertEqual(body["reason"], "bad_request")

    def test_reap_endpoint(self):
        self.controller.maintain_once()
        self.clock.advance(20.0)
        status, body = self.post("/reap", {"max_park_seconds": 10})
        self.assertEqual(status, 200)
        self.assertEqual(len(body["stopped"]), 1)

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
            "http://127.0.0.1:%d/claim" % self.port, data=b"{not json",
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=5)
            self.fail("erwartet HTTP 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)


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

    def test_claim_bridge_unhealthy_503(self):
        self.controller.maintain_once()
        entry = self.pool.status()[0]
        self.bridges[entry["bridge_url"]].healthy = False
        status, body = self.post("/claim", {}, token="s3cr3t-token")
        self.assertEqual(status, 503)
        self.assertEqual(body["reason"], "bridge_unhealthy")


if __name__ == "__main__":
    unittest.main()
