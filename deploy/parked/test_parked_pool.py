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
    """Fake-Bridge: protokolliert Aufrufe, modelliert einen Welt-Tick.

    Die Bridge fuehrt einen **simulierten Welt-Tick** (``world_tick`` in
    Sekunden Weltzeit). Er waechst NUR, solange die Welt NICHT pausiert ist:
    ``pause_game`` setzt ``paused=True``, ``resume_game`` ``paused=False``.
    ``get_state`` liefert den Fingerprint und rueckt den Tick nur vor, wenn
    nicht pausiert — damit ist ``get_state`` die echte Invarianten-Quelle.
    ``pause_noop=True`` simuliert eine kaputte (No-op-)``pause_game`` fuer den
    Red-before-green-Beleg.
    """

    def __init__(self, url: str, clock: FakeClock, handover_seconds: float = 0.0,
                 healthy: bool = True, pause_noop: bool = False) -> None:
        self.url = url
        self.clock = clock
        self.handover_seconds = handover_seconds
        self.healthy = healthy
        self.pause_noop = pause_noop
        self.calls = []
        self.fail_on = set()
        self.end_game_calls = []
        self.paused = False
        self.world_tick = 0.0
        self._last_tick_at = clock()

    def _guard(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail_on:
            raise BridgeError("fake %s exploded" % name)

    def health_ok(self) -> bool:
        self.calls.append("health_ok")
        return self.healthy

    def pause_game(self):
        self._guard("pause_game")
        if not self.pause_noop:
            self.paused = True
        return {"ok": True, "game_paused": self.paused, "pause_want": 1 if self.paused else 0}

    def resume_game(self):
        self._guard("resume_game")
        self.paused = False
        self.clock.advance(self.handover_seconds)
        return {"ok": True, "game_paused": self.paused, "pause_want": 0}

    def round_reset(self):
        self._guard("round_reset")
        return {"ok": True}

    def end_game(self, result=None):
        self._guard("end_game")
        self.end_game_calls.append(result)
        return {"ok": True}

    def get_state(self):
        self._guard("get_state")
        now = self.clock()
        if not self.paused:
            self.world_tick += now - self._last_tick_at
        self._last_tick_at = now
        return {
            "ok": True,
            "game_paused": self.paused,
            "pause_want": 1 if self.paused else 0,
            "world_tick": self.world_tick,
        }


class FakeProvisioner(object):
    """Fake-Provisioner (#908): zaehlt Starts/Stops, keine echte Arbeit."""

    _BASE_PORT = 40000

    def __init__(self, env: str = "test") -> None:
        self.cfg = SimpleNamespace(env=env)
        self.starts = []
        self.stops = []
        self.fail_stop = set()
        # instance_id -> (bridge_port, gns_endpoint); status liest hieraus.
        self._ports = {}

    def _gns_for(self, instance_id):
        known = self._ports.get(instance_id)
        if known is None:
            return None
        return known[1]

    def start(self, env=None, mode="solo", instance_id=None):
        self.starts.append((env or self.cfg.env, instance_id))
        port = self._BASE_PORT + len(self.starts)
        # Issue #929: der GNS-UDP-Host-Port kommt aus dem 6321/udp-Mapping.
        gns = "127.0.0.1:%d" % (port + 1000)
        self._ports[instance_id] = (port, gns)
        return {
            "instance": instance_id,
            "container": "riftbreaker-dedicated-%s-%s" % (env or self.cfg.env, instance_id),
            "running": True,
            "health": "healthy",
            "ports": {"bridge": port, "gns": gns},
            "created": True,
        }

    def status(self, instance_id=None, env=None):
        known = self._ports.get(instance_id)
        port, gns = known if known else (self._BASE_PORT, None)
        return {
            "running": True,
            "health": "healthy",
            "ports": {"bridge": port, "gns": gns},
            "container": "riftbreaker-dedicated-%s-%s" % (env or self.cfg.env, instance_id),
        }

    def set_gns_endpoint(self, instance_id, endpoint):
        """Testhilfe: den GNS-UDP-Port einer Instanz nachtraeglich aendern
        (Container-Neustart) bzw. entfernen (endpoint=None)."""
        known = self._ports.get(instance_id)
        bridge = known[0] if known else self._BASE_PORT
        self._ports[instance_id] = (bridge, endpoint)

    def stop(self, instance_id=None, env=None):
        self.stops.append((env or self.cfg.env, instance_id))
        if instance_id in self.fail_stop:
            raise RuntimeError("fake stop %s exploded" % instance_id)
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

    def test_warm_up_records_gns_endpoint(self):
        # Issue #929: der GNS-UDP-Endpoint aus dem start-Ergebnis wird gespeichert.
        entry = self.pool.warm_up(env="test", instance_id="r1")
        self.assertEqual(entry.gns_endpoint, "127.0.0.1:41001")

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

    def test_claim_returns_gns_endpoint(self):
        # Issue #929: die Claim-Antwort traegt den GNS-UDP-Endpoint der Instanz.
        self.pool.warm_up(env="test", instance_id="r1")
        result = self.pool.claim(env="test", instance_id="r1")
        self.assertEqual(result["gns_endpoint"], "127.0.0.1:41001")

    def test_claim_reads_gns_endpoint_fresh(self):
        # Nach einem Container-Neustart wechselt der Host-UDP-Port -> claim liest
        # ihn FRISCH ueber provisioner.status (nicht den warm_up-Cache).
        self.pool.warm_up(env="test", instance_id="r1")
        self.provisioner.set_gns_endpoint("r1", "127.0.0.1:55555")
        result = self.pool.claim(env="test", instance_id="r1")
        self.assertEqual(result["gns_endpoint"], "127.0.0.1:55555")

    def test_claim_without_gns_mapping_is_none(self):
        self.pool.warm_up(env="test", instance_id="r1")
        self.provisioner.set_gns_endpoint("r1", None)
        result = self.pool.claim(env="test", instance_id="r1")
        self.assertIsNone(result["gns_endpoint"])

    def test_claim_survives_provisioner_without_status(self):
        # Provisioner ohne ``status`` -> claim faellt auf den warm_up-Wert zurueck.
        self.provisioner.status = None
        self.pool.warm_up(env="test", instance_id="r1")
        result = self.pool.claim(env="test", instance_id="r1")
        self.assertEqual(result["gns_endpoint"], "127.0.0.1:41001")


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
        for expected in ("round_reset", "pause_game"):
            self.assertIn(expected, calls)
        self.assertEqual(self.provisioner.stops, [])  # warm geblieben

    def test_recycle_without_result_skips_end_game(self):
        # Red-before-green: die Bridge verlangt ein Pflicht-`result`
        # (pipe_bridge.c handle_end_game); `end_game(None)` ist immer HTTP 400
        # `invalid_request`. Ohne Ergebnis darf recycle `end_game` NICHT rufen —
        # `round_reset` + `pause_game` ist der gueltige, weltunabhaengige Pfad.
        entry = self.pool.warm_up(env="test", instance_id="r1")
        self.pool.claim(env="test", instance_id="r1")
        recycled = self.pool.recycle(env="test", instance_id="r1", keep_warm=True)
        calls = self.bridge_for(entry).calls
        self.assertNotIn("end_game", calls)
        self.assertIn("round_reset", calls)
        self.assertIn("pause_game", calls)
        self.assertEqual(recycled.state, ParkedState.PARKED)
        self.assertEqual(recycled.rounds, 1)
        self.assertEqual(self.provisioner.stops, [])

    def test_recycle_with_result_calls_end_game(self):
        entry = self.pool.warm_up(env="test", instance_id="r1")
        self.pool.claim(env="test", instance_id="r1")
        recycled = self.pool.recycle(env="test", instance_id="r1", keep_warm=True,
                                     result="win")
        calls = self.bridge_for(entry).calls
        self.assertIn("end_game", calls)
        self.assertEqual(self.bridge_for(entry).end_game_calls, ["win"])
        self.assertIn("round_reset", calls)
        self.assertIn("pause_game", calls)
        self.assertEqual(recycled.state, ParkedState.PARKED)

    def test_recycle_cold_stops_instance(self):
        self.pool.warm_up(env="test", instance_id="r1")
        self.pool.claim(env="test", instance_id="r1")
        recycled = self.pool.recycle(env="test", instance_id="r1", keep_warm=False)
        self.assertEqual(recycled.state, ParkedState.STOPPED)
        self.assertEqual(len(self.provisioner.stops), 1)
        self.assertIsNone(recycled.gns_endpoint)  # gestoppt -> Endpoint weg

    def test_recycle_keep_warm_keeps_gns_endpoint(self):
        entry = self.pool.warm_up(env="test", instance_id="r1")
        self.pool.claim(env="test", instance_id="r1")
        recycled = self.pool.recycle(env="test", instance_id="r1", keep_warm=True)
        self.assertEqual(recycled.gns_endpoint, "127.0.0.1:41001")

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


class WorldProgressInvariantTests(PoolHarness):
    """Blocker 1b: DoD „kein Weltfortschritt im Parked-Zustand".

    Die Invariante wird ueber ``get_state`` (echte Quelle) geprueft, nicht nur
    ueber „pause_game wurde aufgerufen".
    """

    def test_no_world_progress_while_parked(self):
        entry = self.pool.warm_up(env="test", instance_id="r1")
        bridge = self.bridge_for(entry)
        before = bridge.get_state()["world_tick"]
        self.clock.advance(30.0)  # Parked-Intervall
        after = bridge.get_state()["world_tick"]
        self.assertEqual(before, after)  # kein Welt-Tick im PARKED-Zustand
        self.assertIn("get_state", bridge.calls)  # echte Invarianten-Quelle
        self.assertTrue(bridge.paused)

    def test_world_progress_resumes_after_claim(self):
        entry = self.pool.warm_up(env="test", instance_id="r1")
        bridge = self.bridge_for(entry)
        self.pool.claim(env="test", instance_id="r1")
        before = bridge.get_state()["world_tick"]
        self.clock.advance(10.0)
        after = bridge.get_state()["world_tick"]
        self.assertGreater(after, before)  # nach resume laeuft die Welt wieder
        self.assertIn("get_state", bridge.calls)

    def test_pause_is_load_bearing_red_before_green(self):
        # Red-before-green: eine No-op-`pause_game` laesst den Welt-Tick laufen
        # -> die Invariante aus test_no_world_progress_while_parked schlaegt fehl.
        def factory(url):
            bridge = FakeBridge(url, self.clock, pause_noop=True)
            self.bridges[url] = bridge
            return bridge

        self.pool.bridge_factory = factory
        entry = self.pool.warm_up(env="test", instance_id="r1")
        bridge = self.bridge_for(entry)
        before = bridge.get_state()["world_tick"]
        self.clock.advance(30.0)
        after = bridge.get_state()["world_tick"]
        self.assertNotEqual(before, after)  # kaputtes Parken -> Welt laeuft weiter


class ReapFailureTests(PoolHarness):
    def test_reap_continues_after_stop_failure_and_raises_aggregated(self):
        self.pool.warm_up(env="test", instance_id="a")
        self.pool.warm_up(env="test", instance_id="b")
        self.clock.advance(30.0)
        self.provisioner.fail_stop.add("a")
        with self.assertRaises(ParkedError) as ctx:
            self.pool.reap(max_park_seconds=10.0)
        self.assertIn("a", str(ctx.exception))
        # Trotz Fehler bei 'a' wurde 'b' weiterhin gestoppt (Auslaufschutz komplett).
        self.assertIn(("test", "b"), self.provisioner.stops)
        states = {row["instance"]: row["state"] for row in self.pool.status()}
        self.assertEqual(states["b"], ParkedState.STOPPED.value)
        self.assertEqual(states["a"], ParkedState.PARKED.value)


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
        self.assertEqual(row["gns_endpoint"], "127.0.0.1:41001")

    def test_status_empty_pool(self):
        self.assertEqual(self.pool.status(), [])


if __name__ == "__main__":
    unittest.main()
