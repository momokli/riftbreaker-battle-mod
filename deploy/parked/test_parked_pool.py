#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermetische Tests fuer deploy/parked/parked_pool.py (Issue #909).

Kein Docker, kein Netz, kein Spiel: Provisioner und Bridge sind Fakes, die
Uhr ist eine ``FakeClock``. Aufruf:

    cd deploy/parked && python3 -m unittest -v
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from parked_pool import BridgeError, ParkedError, ParkedPool, ParkedState


class FakeClock(object):
    """Monotone Fake-Uhr; nur explizites ``advance`` bewegt die Zeit."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeBridge(object):
    """Fake-Bridge: protokolliert Aufrufe, kann Fehler und Zustand simulieren."""

    def __init__(self, url: str, clock: FakeClock, handover_seconds: float = 0.0,
                 healthy: bool = True) -> None:
        self.url = url
        self.clock = clock
        self.handover_seconds = handover_seconds
        self.healthy = healthy
        self.calls = []
        self.fail_on = set()

    def _guard(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail_on:
            raise BridgeError("fake %s exploded" % name)

    def health_ok(self) -> bool:
        self.calls.append("health_ok")
        return self.healthy

    def pause_game(self):
        self._guard("pause_game")
        return {"ok": True}

    def resume_game(self):
        self._guard("resume_game")
        self.clock.advance(self.handover_seconds)
        return {"ok": True}

    def round_reset(self):
        self._guard("round_reset")
        return {"ok": True}

    def end_game(self, result=None):
        self._guard("end_game")
        return {"ok": True}

    def get_state(self):
        self._guard("get_state")
        return {"ok": True}


class FakeProvisioner(object):
    """Fake-Provisioner (#908): zaehlt Starts/Stops, keine echte Arbeit."""

    _BASE_PORT = 40000

    def __init__(self, env: str = "test") -> None:
        self.cfg = SimpleNamespace(env=env)
        self.starts = []
        self.stops = []

    def start(self, env=None, mode="solo", instance_id=None):
        self.starts.append((env or self.cfg.env, instance_id))
        port = self._BASE_PORT + len(self.starts)
        return {
            "instance": instance_id,
            "container": "riftbreaker-dedicated-%s-%s" % (env or self.cfg.env, instance_id),
            "running": True,
            "health": "healthy",
            "ports": {"bridge": port},
            "created": True,
        }

    def stop(self, instance_id=None, env=None):
        self.stops.append((env or self.cfg.env, instance_id))
        return {"instance": instance_id, "removed": {}}


class PoolHarness(unittest.TestCase):
    """Baut Pool + Fakes mit geteilter Uhr/Bridge zusammen."""

    def setUp(self) -> None:
        self.clock = FakeClock(start=1000.0)
        self.provisioner = FakeProvisioner()
        self.bridges = {}

        def factory(url):
            bridge = self.bridges.get(url)
            if bridge is None:
                bridge = FakeBridge(url, self.clock)
                self.bridges[url] = bridge
            return bridge

        self.factory = factory
        self.pool = ParkedPool(self.provisioner, factory, clock=self.clock, sleep=lambda _s: None)

    def bridge_for(self, entry) -> FakeBridge:
        return self.bridges[entry.bridge_url]


class WarmUpTests(PoolHarness):
    def test_warm_up_happy_path_parks_instance(self):
        entry = self.pool.warm_up(env="test", instance_id="r1")
        self.assertEqual(entry.state, ParkedState.PARKED)
        self.assertEqual(entry.parked_since, self.clock())
        self.assertEqual(entry.container, "riftbreaker-dedicated-test-r1")
        self.assertEqual(len(self.provisioner.starts), 1)
        self.assertIn("pause_game", self.bridge_for(entry).calls)
        self.assertEqual(entry.rounds, 0)

    def test_warm_up_is_idempotent(self):
        first = self.pool.warm_up(env="test", instance_id="r1")
        second = self.pool.warm_up(env="test", instance_id="r1")
        self.assertIs(first, second)
        self.assertEqual(len(self.provisioner.starts), 1)  # kein zweiter Start

    def test_warm_up_generates_deterministic_ids_but_distinct(self):
        a = self.pool.warm_up()
        b = self.pool.warm_up()
        self.assertNotEqual(a.instance_id, b.instance_id)
        self.assertEqual(len(self.provisioner.starts), 2)

    def test_warm_up_pause_failure_rolls_back_loudly(self):
        # Bridge kennt die URL erst nach start; pause_game soll knallen.
        original_factory = self.pool.bridge_factory

        def factory(url):
            bridge = original_factory(url)
            bridge.fail_on.add("pause_game")
            return bridge

        self.pool.bridge_factory = factory
        with self.assertRaises(ParkedError):
            self.pool.warm_up(env="test", instance_id="r1")
        self.assertEqual(len(self.provisioner.stops), 1)  # Rollback gestoppt
        entry = self.pool.status()[0]
        self.assertEqual(entry["state"], ParkedState.STOPPED.value)


class ClaimTests(PoolHarness):
    def test_claim_measures_handover_and_marks_claimed(self):
        entry = self.pool.warm_up(env="test", instance_id="r1")
        self.bridge_for(entry).handover_seconds = 0.13
        result = self.pool.claim(env="test", instance_id="r1")
        self.assertEqual(result["state"], ParkedState.CLAIMED.value)
        self.assertAlmostEqual(result["handover_seconds"], 0.13, places=9)
        self.assertIn("resume_game", self.bridge_for(entry).calls)
        self.assertEqual(self.pool.status()[0]["state"], ParkedState.CLAIMED.value)

    def test_claim_requires_parked_state(self):
        self.pool.warm_up(env="test", instance_id="r1")
        self.pool.claim(env="test", instance_id="r1")
        with self.assertRaises(ParkedError):
            self.pool.claim(env="test", instance_id="r1")

    def test_claim_unknown_instance_fails(self):
        with self.assertRaises(ParkedError):
            self.pool.claim(env="test", instance_id="nope")

    def test_claim_unhealthy_bridge_fails(self):
        entry = self.pool.warm_up(env="test", instance_id="r1")
        self.bridge_for(entry).healthy = False
        with self.assertRaises(ParkedError):
            self.pool.claim(env="test", instance_id="r1")
        self.assertEqual(self.pool.status()[0]["state"], ParkedState.PARKED.value)


class RecycleTests(PoolHarness):
    def test_recycle_keep_warm_returns_to_parked(self):
        entry = self.pool.warm_up(env="test", instance_id="r1")
        self.pool.claim(env="test", instance_id="r1")
        self.clock.advance(5.0)
        recycled = self.pool.recycle(env="test", instance_id="r1", keep_warm=True)
        self.assertEqual(recycled.state, ParkedState.PARKED)
        self.assertEqual(recycled.rounds, 1)
        self.assertEqual(recycled.parked_since, self.clock())
        calls = self.bridge_for(entry).calls
        for expected in ("end_game", "round_reset", "pause_game"):
            self.assertIn(expected, calls)
        self.assertEqual(self.provisioner.stops, [])  # warm geblieben

    def test_recycle_cold_stops_instance(self):
        self.pool.warm_up(env="test", instance_id="r1")
        self.pool.claim(env="test", instance_id="r1")
        recycled = self.pool.recycle(env="test", instance_id="r1", keep_warm=False)
        self.assertEqual(recycled.state, ParkedState.STOPPED)
        self.assertEqual(len(self.provisioner.stops), 1)

    def test_recycle_before_claim_fails(self):
        self.pool.warm_up(env="test", instance_id="r1")
        with self.assertRaises(ParkedError):
            self.pool.recycle(env="test", instance_id="r1")


class ReapTests(PoolHarness):
    def test_reap_stops_only_overdue_parked_instances(self):
        self.pool.warm_up(env="test", instance_id="old")
        self.clock.advance(30.0)
        self.pool.warm_up(env="test", instance_id="fresh")
        self.clock.advance(1.0)

        stopped = self.pool.reap(max_park_seconds=10.0)
        self.assertEqual([e.instance_id for e in stopped], ["old"])
        self.assertEqual(len(self.provisioner.stops), 1)
        states = {row["instance"]: row["state"] for row in self.pool.status()}
        self.assertEqual(states["old"], ParkedState.STOPPED.value)
        self.assertEqual(states["fresh"], ParkedState.PARKED.value)

    def test_reap_ignores_claimed_and_fresh(self):
        self.pool.warm_up(env="test", instance_id="r1")
        self.pool.claim(env="test", instance_id="r1")
        self.clock.advance(1000.0)
        self.assertEqual(self.pool.reap(max_park_seconds=1.0), [])
        self.assertEqual(self.provisioner.stops, [])


class StatusTests(PoolHarness):
    def test_status_reports_parked_seconds(self):
        self.pool.warm_up(env="test", instance_id="r1")
        self.clock.advance(7.5)
        row = self.pool.status()[0]
        self.assertEqual(row["instance"], "r1")
        self.assertEqual(row["env"], "test")
        self.assertEqual(row["state"], ParkedState.PARKED.value)
        self.assertAlmostEqual(row["parked_seconds"], 7.5, places=9)
        self.assertIn("bridge_url", row)

    def test_status_empty_pool(self):
        self.assertEqual(self.pool.status(), [])


if __name__ == "__main__":
    unittest.main()
