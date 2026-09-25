#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermetische Tests fuer deploy/parked/parked_vs.py (Issue #910).

Kein Docker, kein Netz, kein Spiel: Provisioner und Bridge sind Fakes (aus
``test_parked_pool`` wiederverwendet), die Uhr ist eine ``FakeClock``. Der
**Ready-Gate** ist red-before-green belegt.

Aufruf:

    cd deploy/parked && python3 -m unittest -v
"""

from __future__ import annotations

import unittest

from parked_pool import ParkedPool, ParkedState
from parked_vs import ParkedVSPool, VSError, VSState
from test_parked_pool import FakeBridge, FakeClock, FakeProvisioner


class VSHarness(unittest.TestCase):
    """Baut VS-Pool ueber einem echten ParkedPool + Fakes zusammen."""

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
        self.pool = ParkedPool(self.provisioner, factory, clock=self.clock,
                               sleep=lambda _s: None)
        self.vs = ParkedVSPool(self.provisioner, pool=self.pool, clock=self.clock)

    # -- Helfer ------------------------------------------------------------
    def bridge_for(self, instance_id="r1", env="test") -> FakeBridge:
        entry = self.pool._entries[(env, instance_id)]
        return self.bridges[entry.bridge_url]

    def warm(self, instance_id="r1"):
        session = self.vs.warm_up(env="test", instance_id=instance_id)
        self.assertEqual(session.state, VSState.WAITING_OPPONENT)
        return session

    def join_two(self, instance_id="r1"):
        self.vs.join("p1", env="test", instance_id=instance_id)
        self.vs.join("p2", env="test", instance_id=instance_id)


class ReadyGateTests(VSHarness):
    def test_warm_up_starts_waiting_opponent(self):
        session = self.vs.warm_up(env="test", instance_id="r1")
        self.assertEqual(session.state, VSState.WAITING_OPPONENT)
        self.assertEqual(session.players, [])
        self.assertEqual(session.ready, set())
        entry = self.pool._entries[("test", "r1")]
        self.assertEqual(entry.state, ParkedState.PARKED)
        self.assertIn("pause_game", self.bridge_for("r1").calls)

    def test_first_join_waits_opponent(self):
        self.warm()
        session = self.vs.join("p1", env="test", instance_id="r1")
        self.assertEqual(session.state, VSState.WAITING_OPPONENT)
        self.assertEqual(session.players, ["p1"])
        self.assertNotIn("resume_game", self.bridge_for("r1").calls)

    def test_second_join_waits_both_ready(self):
        self.warm()
        self.vs.join("p1", env="test", instance_id="r1")
        session = self.vs.join("p2", env="test", instance_id="r1")
        self.assertEqual(session.state, VSState.WAITING_BOTH_READY)
        self.assertEqual(session.players, ["p1", "p2"])
        self.assertNotIn("resume_game", self.bridge_for("r1").calls)

    def test_one_ready_does_not_resume(self):
        self.warm()
        self.join_two()
        session = self.vs.ready("p1", env="test", instance_id="r1")
        self.assertEqual(session.state, VSState.WAITING_BOTH_READY)
        self.assertEqual(session.ready, {"p1"})
        self.assertNotIn("resume_game", self.bridge_for("r1").calls)

    def test_both_ready_triggers_single_handover(self):
        self.warm()
        self.join_two()
        bridge = self.bridge_for("r1")
        bridge.handover_seconds = 0.12
        self.vs.ready("p1", env="test", instance_id="r1")
        session = self.vs.ready("p2", env="test", instance_id="r1")
        self.assertEqual(session.state, VSState.CLAIMED)
        self.assertEqual(bridge.calls.count("resume_game"), 1)  # genau EIN Handover
        self.assertAlmostEqual(session.handover_seconds, 0.12, places=9)
        self.assertEqual(session.claimed_at, self.clock())

    def test_gate_is_load_bearing_red_before_green(self):
        # Red-before-green: ein absichtlich zu fruehes Gate (resume nach dem
        # ERSTEN Join) verletzt die Gate-Invariante — dieselbe Behauptung, die
        # fuer den echten Pool gilt (kein resume vor 2x join + 2x ready),
        # schlaegt fuer den kaputten Pool fehl. Damit ist bewiesen, dass der
        # Test das Gate wirklich prueft (und nicht zufaellig gruen ist).
        broken_ready = _TooEarlyGatePool(self.provisioner, pool=self.pool, clock=self.clock)

        # korrekt: erster Join loest KEIN resume aus
        self.warm()
        self.vs.join("p1", env="test", instance_id="r1")
        self.assertNotIn("resume_game", self.bridge_for("r1").calls)

        # kaputt: derselbe Join loest resume aus -> die Invariante schlaegt fehl
        broken_ready.warm_up(env="test", instance_id="r1")
        broken_ready.join("p1", env="test", instance_id="r1")
        self.assertIn("resume_game", self.bridge_for("r1").calls)


class _TooEarlyGatePool(ParkedVSPool):
    """Absichtlich zu fruehes Gate (red-before-green): claim schon nach Join 1."""

    def join(self, player, env=None, instance_id=None):
        session = super().join(player, env=env, instance_id=instance_id)
        self.pool.claim(env=session.env, instance_id=session.instance_id)
        return session


class RecycleTests(VSHarness):
    def test_recycle_keep_warm_resets_slots_to_waiting(self):
        self.warm()
        self.join_two()
        self.vs.ready("p1", env="test", instance_id="r1")
        self.vs.ready("p2", env="test", instance_id="r1")
        bridge = self.bridge_for("r1")
        session = self.vs.recycle(env="test", instance_id="r1", keep_warm=True)
        self.assertEqual(session.state, VSState.WAITING_OPPONENT)
        self.assertEqual(session.players, [])
        self.assertEqual(session.ready, set())
        self.assertEqual(session.rounds, 1)
        self.assertIsNone(session.handover_seconds)
        entry = self.pool._entries[("test", "r1")]
        self.assertEqual(entry.state, ParkedState.PARKED)
        self.assertEqual(self.provisioner.stops, [])  # warm geblieben
        # #928-Semantik: OHNE `result` ist `end_game` NICHT erlaubt (die Bridge
        # verlangt ein Pflicht-`result` und lehnt `end_game(None)` mit HTTP 400
        # ab) — der weltunabhaengige Pfad ist `round_reset` + `pause_game`.
        for expected in ("round_reset", "pause_game"):
            self.assertIn(expected, bridge.calls)
        self.assertNotIn("end_game", bridge.calls)

    def test_recycle_keep_warm_with_result_calls_end_game(self):
        # Mit `result` (win/lose) ist `end_game(result)` der gueltige Pfad.
        self.warm()
        self.join_two()
        self.vs.ready("p1", env="test", instance_id="r1")
        self.vs.ready("p2", env="test", instance_id="r1")
        bridge = self.bridge_for("r1")
        session = self.vs.recycle(env="test", instance_id="r1", keep_warm=True,
                                  result="win")
        self.assertEqual(session.state, VSState.WAITING_OPPONENT)
        self.assertEqual(session.rounds, 1)
        self.assertIn("end_game", bridge.calls)
        self.assertEqual(bridge.end_game_calls, ["win"])
        for expected in ("round_reset", "pause_game"):
            self.assertIn(expected, bridge.calls)

    def test_recycle_cold_stops_instance(self):
        self.warm()
        self.join_two()
        self.vs.ready("p1", env="test", instance_id="r1")
        self.vs.ready("p2", env="test", instance_id="r1")
        session = self.vs.recycle(env="test", instance_id="r1", keep_warm=False)
        self.assertEqual(session.state, VSState.STOPPED)
        self.assertEqual(len(self.provisioner.stops), 1)

    def test_recycle_before_claimed_fails(self):
        self.warm()
        self.join_two()
        with self.assertRaises(VSError):
            self.vs.recycle(env="test", instance_id="r1")

    def test_next_match_after_recycle_join_ready(self):
        self.warm()
        self.join_two()
        bridge = self.bridge_for("r1")
        bridge.handover_seconds = 0.11
        self.vs.ready("p1", env="test", instance_id="r1")
        self.vs.ready("p2", env="test", instance_id="r1")
        self.vs.recycle(env="test", instance_id="r1", keep_warm=True)
        # Match 2: erneut zwei Beitritte + ready-Gate -> sauberer zweiter Handover
        session = self.vs.join("p1", env="test", instance_id="r1")
        self.assertEqual(session.state, VSState.WAITING_OPPONENT)
        self.vs.join("p2", env="test", instance_id="r1")
        self.vs.ready("p1", env="test", instance_id="r1")
        session = self.vs.ready("p2", env="test", instance_id="r1")
        self.assertEqual(session.state, VSState.CLAIMED)
        self.assertEqual(bridge.calls.count("resume_game"), 2)  # ein Handover je Match
        self.assertEqual(session.rounds, 1)


class ErrorTests(VSHarness):
    def test_duplicate_join_same_player_fails(self):
        self.warm()
        self.vs.join("p1", env="test", instance_id="r1")
        with self.assertRaises(VSError):
            self.vs.join("p1", env="test", instance_id="r1")

    def test_third_player_join_fails(self):
        self.warm()
        self.join_two()
        with self.assertRaises(VSError):
            self.vs.join("p3", env="test", instance_id="r1")

    def test_ready_before_join_fails(self):
        self.warm()
        with self.assertRaises(VSError):
            self.vs.ready("p1", env="test", instance_id="r1")

    def test_duplicate_ready_fails(self):
        self.warm()
        self.join_two()
        self.vs.ready("p1", env="test", instance_id="r1")
        with self.assertRaises(VSError):
            self.vs.ready("p1", env="test", instance_id="r1")

    def test_join_after_claimed_fails(self):
        self.warm()
        self.join_two()
        self.vs.ready("p1", env="test", instance_id="r1")
        self.vs.ready("p2", env="test", instance_id="r1")
        with self.assertRaises(VSError):
            self.vs.join("p3", env="test", instance_id="r1")

    def test_unknown_instance_fails(self):
        with self.assertRaises(VSError):
            self.vs.join("p1", env="test", instance_id="nope")

    def test_claim_failure_keeps_waiting_and_is_loud(self):
        self.warm()
        self.join_two()
        bridge = self.bridge_for("r1")
        bridge.healthy = False
        self.vs.ready("p1", env="test", instance_id="r1")
        with self.assertRaises(VSError):
            self.vs.ready("p2", env="test", instance_id="r1")
        session = self.vs._sessions[("test", "r1")]
        self.assertEqual(session.state, VSState.WAITING_BOTH_READY)
        entry = self.pool._entries[("test", "r1")]
        self.assertEqual(entry.state, ParkedState.PARKED)  # kein halber Handover


class WorldProgressInvariantTests(VSHarness):
    """Ready-Gate haelt die Welt im PARKED-Zustand (Invariante aus #909)."""

    def test_no_world_progress_while_waiting(self):
        self.warm()
        self.join_two()
        self.vs.ready("p1", env="test", instance_id="r1")  # nur 1 von 2 ready
        bridge = self.bridge_for("r1")
        before = bridge.get_state()["world_tick"]
        self.clock.advance(30.0)
        after = bridge.get_state()["world_tick"]
        self.assertEqual(before, after)  # kein Welt-Tick, solange < 2 ready
        self.assertTrue(bridge.paused)
        self.assertIn("get_state", bridge.calls)

    def test_world_progress_after_both_ready_handover(self):
        self.warm()
        self.join_two()
        self.vs.ready("p1", env="test", instance_id="r1")
        self.vs.ready("p2", env="test", instance_id="r1")
        bridge = self.bridge_for("r1")
        before = bridge.get_state()["world_tick"]
        self.clock.advance(10.0)
        after = bridge.get_state()["world_tick"]
        self.assertGreater(after, before)  # nach dem Gate laeuft die Welt wieder


class StatusTests(VSHarness):
    def test_status_reports_vs_state_and_players(self):
        self.warm()
        self.join_two()
        row = self.vs.status()[0]
        self.assertEqual(row["instance"], "r1")
        self.assertEqual(row["env"], "test")
        self.assertEqual(row["state"], VSState.WAITING_BOTH_READY.value)
        self.assertEqual(row["players"], ["p1", "p2"])
        self.assertEqual(row["ready"], [])
        self.assertEqual(row["required_players"], 2)
        self.assertEqual(row["rounds"], 0)

    def test_status_empty_pool(self):
        self.assertEqual(self.vs.status(), [])


if __name__ == "__main__":
    unittest.main()
