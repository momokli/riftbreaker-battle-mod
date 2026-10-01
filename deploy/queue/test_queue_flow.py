#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermetische Tests fuer deploy/queue/queue_flow.py (Issue #998, US2/US5).

Kein Netz, kein Docker, kein Spiel: Provisioner und Referee sind Fakes mit
gemeinsamem Zustand; die Uhr ist eine ``FakeClock``. Aufruf:

    cd deploy/queue && python3 -m unittest -v
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest

from queue_core import (
    QueueCore,
    QueueError,
    STATE_FAILED,
    STATE_FINISHED,
    STATE_READY,
)
from queue_flow import (
    QueueCoordinator,
    instance_id_for,
)


class FakeClock(object):
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeProvisioner(object):
    """Fake-Provisioner: kalte Starts mit je eigener Instanz + GNS-Endpoint."""

    def __init__(self) -> None:
        self.starts = []
        self.stops = []
        self.events = []  # #1030: globale Reihenfolge [("start"/"stop", iid)]
        self.fail_on_world = None
        self.fail_on_instance = None
        self.fail_stop_once = False  # #1028: erster Stop scheitert (Fail-safe-Test)
        self.fail_stop_always = False  # #1030: jeder Stop scheitert (Cleanup-Fehler)
        self._port = 40000

    def start(self, env, mode, instance_id, world=None):
        if self.fail_on_world is not None and world == self.fail_on_world:
            raise RuntimeError("fake provisioner explodiert fuer world=%s" % world)
        if self.fail_on_instance is not None and instance_id == self.fail_on_instance:
            raise RuntimeError("fake provisioner explodiert fuer %s" % instance_id)
        self.starts.append({"env": env, "mode": mode, "instance_id": instance_id,
                            "world": world})
        self.events.append(("start", instance_id))
        self._port += 1
        return {
            "instance": instance_id,
            "running": True,
            "created": True,
            "ports": {"bridge": self._port, "gns": "127.0.0.1:%d" % self._port},
        }

    def stop(self, instance_id, env=None):
        if self.fail_stop_always:
            raise RuntimeError("fake stop scheitert dauerhaft fuer %s" % instance_id)
        if self.fail_stop_once:
            self.fail_stop_once = False
            # Fail-fast: der Aufruf wird NICHT als Stop protokolliert.
            raise RuntimeError("fake stop scheitert einmalig fuer %s" % instance_id)
        self.stops.append({"instance_id": instance_id, "env": env})
        self.events.append(("stop", instance_id))
        return {"instance": instance_id, "removed": True}


class FakeReferee(object):
    def __init__(self) -> None:
        self.lobbies = []
        self.readies = []
        self.calls = []  # gemeinsames Reihenfolge-Log: (kind, world)
        self.fail = False
        self.ready_fail = False  # nur der Ready-Egress scheitert
        # #1030: Referee-Reset (POST /rematch); ``rematch_fail`` = 409 conflict.
        self.rematches = 0
        self.rematch_fail = False
        # #1028: autoritativer /state-Snapshot (GET /state, auth-frei).
        self.state_payload = None
        self.state_error = False
        self.state_calls = 0

    def lobby(self, player, world, match_id=None):
        self.calls.append(("lobby", world))
        if self.fail:
            raise RuntimeError("fake referee explodiert")
        self.lobbies.append({"player": player, "world": world,
                             "match_id": match_id})
        return {"ok": True}

    def ready(self, world):
        self.calls.append(("ready", world))
        if self.fail or self.ready_fail:
            raise RuntimeError("fake referee ready explodiert")
        self.readies.append({"world": world})
        return {"ok": True}

    def state(self):
        self.state_calls += 1
        if self.state_error:
            raise RuntimeError("fake referee state explodiert")
        return self.state_payload

    def rematch(self):
        self.calls.append(("rematch", None))
        if self.rematch_fail:
            raise QueueError("conflict", "laufendes Match", 409)
        self.rematches += 1
        return {"phase": "lobby", "rematches": self.rematches}


class Harness(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.provisioner = FakeProvisioner()
        self.referee = FakeReferee()
        self.tmp = tempfile.mkdtemp(prefix="queue-flow-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def coordinator(self, state_dir=None, **kwargs):
        core = QueueCore(clock=self.clock, **kwargs)
        return QueueCoordinator(
            core, self.provisioner, self.referee, clock=self.clock,
            env="test", state_dir=state_dir,
        )


class JoinProvisionTestCase(Harness):
    def test_first_join_is_pending(self):
        coord = self.coordinator()
        out = coord.join("str:aa")
        self.assertEqual(out["status"], "queued")
        self.assertEqual(out["position"], 1)
        self.assertEqual(self.provisioner.starts, [])
        self.assertEqual(self.referee.lobbies, [])

    def test_second_join_matches_and_provisions_two_cold_instances(self):
        coord = self.coordinator()
        coord.join("str:aa")
        out = coord.join("str:bb")
        self.assertEqual(out["status"], "matched")
        # Genau ZWEI kalte Provisionierungen, eine je Welt.
        self.assertEqual(len(self.provisioner.starts), 2)
        worlds = sorted(s["world"] for s in self.provisioner.starts)
        self.assertEqual(worlds, ["A", "B"])
        instances = [s["instance_id"] for s in self.provisioner.starts]
        self.assertEqual(sorted(instances), sorted([instance_id_for(1, "A"), instance_id_for(1, "B")]))
        self.assertEqual(len(set(instances)), 2)  # verschiedene instance_ids
        # Kein Warm-Pool-Reuse: beide Starts sind neu.
        self.assertTrue(all(s["mode"] == "solo_self" for s in self.provisioner.starts))

    def test_endpoints_are_distinct(self):
        coord = self.coordinator()
        coord.join("str:aa")
        out = coord.join("str:bb")
        endpoints = [p["endpoint"] for p in out["match"]["participants"]]
        self.assertEqual(len(endpoints), 2)
        self.assertEqual(len(set(endpoints)), 2)  # verschiedene GNS-Endpoints
        self.assertTrue(all(e for e in endpoints))

    def test_assignments_a_b(self):
        coord = self.coordinator()
        coord.join("str:aa")
        out = coord.join("str:bb")
        by_world = {p["world"]: p["identitaet"] for p in out["match"]["participants"]}
        self.assertEqual(by_world["A"], "str:aa")
        self.assertEqual(by_world["B"], "str:bb")

    def test_referee_gets_one_lobby_per_player(self):
        coord = self.coordinator()
        coord.join("str:aa")
        coord.join("str:bb")
        self.assertEqual(len(self.referee.lobbies), 2)
        by_world = {lobby["world"]: lobby["player"] for lobby in self.referee.lobbies}
        self.assertEqual(by_world["A"], "str:aa")
        self.assertEqual(by_world["B"], "str:bb")

    def test_match_record_carries_instance_and_endpoint(self):
        coord = self.coordinator()
        coord.join("str:aa")
        out = coord.join("str:bb")
        record = coord.core.get_match(out["match"]["match_id"]).to_dict()
        self.assertEqual(record["state"], STATE_READY)
        for p in record["participants"]:
            self.assertTrue(p["instance"])
            self.assertTrue(p["endpoint"])

    def test_identity_is_canonicalized(self):
        coord = self.coordinator()
        coord.join("str:AA")
        out = coord.join("str:BB")
        self.assertEqual(out["match"]["participants"][0]["identitaet"], "str:aa")


class ReadyEgressTestCase(Harness):
    """Issue #1025: Ready-Egress je Welt nach Provisionierung + Lobby."""

    def test_match_sends_one_ready_per_world_after_lobbies(self):
        coord = self.coordinator()
        coord.join("str:aa")
        coord.join("str:bb")
        # Genau ZWEI Ready, je Welt A/B einmal.
        self.assertEqual(len(self.referee.readies), 2)
        self.assertEqual(sorted(r["world"] for r in self.referee.readies), ["A", "B"])
        # Reihenfolge: erst beide /lobby, dann beide /ready (distinct Welt).
        self.assertEqual([c[0] for c in self.referee.calls],
                         ["lobby", "lobby", "ready", "ready"])
        self.assertEqual([c[1] for c in self.referee.calls], ["A", "B", "A", "B"])

    def test_first_join_sends_no_ready(self):
        coord = self.coordinator()
        out = coord.join("str:aa")
        self.assertEqual(out["status"], "queued")
        self.assertEqual(self.referee.readies, [])
        self.assertEqual(self.referee.calls, [])

    def test_ready_failure_rolls_back(self):
        coord = self.coordinator()
        coord.join("str:aa")
        # Lobbys gelingen, erst der Ready-Egress scheitert.
        self.referee.ready_fail = True
        with self.assertRaises(QueueError) as ctx:
            coord.join("str:bb")
        self.assertEqual(ctx.exception.reason, "provision_failed")
        self.assertEqual(len(self.referee.lobbies), 2)
        stopped = sorted(s["instance_id"] for s in self.provisioner.stops)
        self.assertEqual(stopped,
                         sorted([instance_id_for(1, "A"), instance_id_for(1, "B")]))
        self.assertEqual(coord.core.get_match(1).state, STATE_FAILED)


class RollbackTestCase(Harness):
    def test_second_provision_failure_rolls_back_first(self):
        coord = self.coordinator()
        coord.join("str:aa")
        self.provisioner.fail_on_world = "B"
        with self.assertRaises(QueueError) as ctx:
            coord.join("str:bb")
        self.assertEqual(ctx.exception.reason, "provision_failed")
        # A wurde gestartet und wieder gestoppt; B nie gestartet.
        self.assertEqual([s["world"] for s in self.provisioner.starts], ["A"])
        self.assertEqual([s["instance_id"] for s in self.provisioner.stops],
                         [instance_id_for(1, "A")])
        match = coord.core.get_match(1)
        self.assertEqual(match.state, STATE_FAILED)
        self.assertEqual(self.referee.lobbies, [])

    def test_referee_failure_rolls_back_both(self):
        coord = self.coordinator()
        coord.join("str:aa")
        self.referee.fail = True
        with self.assertRaises(QueueError) as ctx:
            coord.join("str:bb")
        self.assertEqual(ctx.exception.reason, "provision_failed")
        self.assertEqual(len(self.provisioner.starts), 2)
        stopped = sorted(s["instance_id"] for s in self.provisioner.stops)
        self.assertEqual(stopped, sorted([instance_id_for(1, "A"), instance_id_for(1, "B")]))
        self.assertEqual(coord.core.get_match(1).state, STATE_FAILED)


class FinishTestCase(Harness):
    def _matched(self):
        coord = self.coordinator()
        coord.join("str:aa")
        out = coord.join("str:bb")
        return coord, out

    def test_finish_records_result_and_stops_both(self):
        coord, out = self._matched()
        record = coord.finish(out["match"]["match_id"], result="winnerA")
        self.assertEqual(record["state"], STATE_FINISHED)
        self.assertEqual(record["result"], "winnerA")
        stopped = sorted(s["instance_id"] for s in self.provisioner.stops)
        self.assertEqual(stopped, sorted([instance_id_for(1, "A"), instance_id_for(1, "B")]))

    def test_finish_without_result_is_allowed(self):
        coord, out = self._matched()
        record = coord.finish(out["match"]["match_id"])
        self.assertEqual(record["state"], STATE_FINISHED)
        self.assertIsNone(record["result"])

    def test_finish_is_idempotent(self):
        coord, out = self._matched()
        coord.finish(out["match"]["match_id"], result="winnerA")
        stops_after_first = len(self.provisioner.stops)
        record = coord.finish(out["match"]["match_id"], result="winnerB")
        self.assertEqual(record["result"], "winnerA")  # zweiter Aufruf aendert nichts
        self.assertEqual(len(self.provisioner.stops), stops_after_first)  # kein zweites Stop

    def test_finish_rejects_bad_result(self):
        coord, out = self._matched()
        with self.assertRaises(QueueError) as ctx:
            coord.finish(out["match"]["match_id"], result="nope")
        self.assertEqual(ctx.exception.reason, "bad_result")
        self.assertEqual(self.provisioner.stops, [])  # kein Cleanup bei Fehler

    def test_finish_unknown_match_raises(self):
        coord = self.coordinator()
        with self.assertRaises(QueueError) as ctx:
            coord.finish(42)
        self.assertEqual(ctx.exception.reason, "unknown_match")

    def test_leave_after_match_raises(self):
        coord, _out = self._matched()
        with self.assertRaises(QueueError) as ctx:
            coord.leave("str:aa")
        self.assertEqual(ctx.exception.reason, "already_matched")


class ReconcileTestCase(Harness):
    """Issue #1028: Auto-Finish aus dem autoritativen Referee-Zustand (Pull)."""

    def _matched(self):
        coord = self.coordinator()
        coord.join("str:aa")
        out = coord.join("str:bb")
        return coord, out["match"]["match_id"]

    def _finished_state(self, mid, winner="A"):
        return {
            "phase": "finished",
            "winner": winner,
            "teams": {
                "A": {"player": "str:aa", "match_id": mid},
                "B": {"player": "str:bb", "match_id": mid},
            },
        }

    def test_lobby_carries_queue_match_id(self):
        # #1028: die Queue schickt ihre match_id in /lobby, damit der Referee
        # sie in /state echoisiert (Grundlage der Zuordnung).
        coord, mid = self._matched()
        self.assertEqual([lobby["match_id"] for lobby in self.referee.lobbies], [mid, mid])

    def test_reconcile_finishes_on_referee_finished(self):
        coord, mid = self._matched()
        self.referee.state_payload = self._finished_state(mid)
        out = coord.reconcile()
        self.assertEqual(out["finished"], [mid])
        record = coord.core.get_match(mid).to_dict()
        self.assertEqual(record["state"], STATE_FINISHED)
        self.assertEqual(record["result"], "winnerA")
        stopped = sorted(s["instance_id"] for s in self.provisioner.stops)
        self.assertEqual(stopped, sorted([instance_id_for(mid, "A"), instance_id_for(mid, "B")]))

    def test_reconcile_winner_b_maps_to_winnerb(self):
        coord, mid = self._matched()
        self.referee.state_payload = self._finished_state(mid, winner="B")
        coord.reconcile()
        self.assertEqual(coord.core.get_match(mid).result, "winnerB")

    def test_reconcile_without_winner_is_draw(self):
        coord, mid = self._matched()
        self.referee.state_payload = self._finished_state(mid, winner=None)
        coord.reconcile()
        self.assertEqual(coord.core.get_match(mid).result, "draw")

    def test_reconcile_not_finished_is_noop(self):
        coord, mid = self._matched()
        self.referee.state_payload = self._finished_state(mid)
        self.referee.state_payload["phase"] = "running"
        out = coord.reconcile()
        self.assertEqual(out["finished"], [])
        self.assertIsNone(coord.core.get_match(mid).result)
        self.assertEqual(self.provisioner.stops, [])

    def test_second_reconcile_is_noop(self):
        coord, mid = self._matched()
        self.referee.state_payload = self._finished_state(mid)
        coord.reconcile()
        stops_after = len(self.provisioner.stops)
        out = coord.reconcile()
        self.assertEqual(out["finished"], [])  # nicht mehr aktiv
        self.assertEqual(len(self.provisioner.stops), stops_after)  # kein Doppel-Stop
        # Der inaktive Match wird gar nicht erst gegen /state geprueft.
        self.assertEqual(self.referee.state_calls, 1)

    def test_reconcile_cleanup_retry_keeps_result(self):
        """US3: scheitert der Stop, bleibt das Ergebnis; naechster Tick retry."""
        coord, mid = self._matched()
        self.referee.state_payload = self._finished_state(mid)
        self.provisioner.fail_stop_once = True
        out = coord.reconcile()
        self.assertEqual(out["finished"], [])  # Cleanup scheiterte -> kein Abschluss
        record = coord.core.get_match(mid).to_dict()
        self.assertEqual(record["state"], STATE_FINISHED)
        self.assertEqual(record["result"], "winnerA")  # Ergebnis bleibt erhalten
        self.assertEqual(self.provisioner.stops, [])  # fail-fast: nichts protokolliert
        # Naechster Tick -> beide Stops, Ergebnis unveraendert.
        out2 = coord.reconcile()
        self.assertEqual(out2["finished"], [mid])
        self.assertEqual(len(self.provisioner.stops), 2)
        self.assertEqual(coord.core.get_match(mid).result, "winnerA")
        # Dritter Tick -> No-op.
        coord.reconcile()
        self.assertEqual(len(self.provisioner.stops), 2)

    def test_reconcile_never_overwrites_result(self):
        coord, mid = self._matched()
        coord.finish(mid, result="winnerA")
        stops_after = len(self.provisioner.stops)
        # Referee meldet spaeter B als Sieger -> darf nichts mehr aendern.
        self.referee.state_payload = self._finished_state(mid, winner="B")
        coord.reconcile()
        self.assertEqual(coord.core.get_match(mid).result, "winnerA")
        self.assertEqual(len(self.provisioner.stops), stops_after)

    def test_reconcile_referee_unavailable_is_noop(self):
        coord, mid = self._matched()
        self.referee.state_error = True
        out = coord.reconcile()
        self.assertEqual(out["finished"], [])
        self.assertEqual(out["error"], "referee_unavailable")
        self.assertIsNone(coord.core.get_match(mid).result)
        self.assertEqual(self.provisioner.stops, [])
        self.assertEqual(coord.core.get_match(mid).state, STATE_READY)  # Zustand erhalten

    def test_reconcile_skips_unmapped_match(self):
        coord, mid = self._matched()
        state = self._finished_state(mid)
        state["teams"]["A"]["match_id"] = 999
        state["teams"]["B"]["match_id"] = 999
        state["teams"]["A"]["player"] = "str:xx"
        state["teams"]["B"]["player"] = "str:yy"
        self.referee.state_payload = state
        out = coord.reconcile()
        self.assertEqual(out["finished"], [])
        self.assertIsNone(coord.core.get_match(mid).result)
        self.assertEqual(self.provisioner.stops, [])

    def test_reconcile_fallback_matches_by_player_names(self):
        coord, mid = self._matched()
        state = self._finished_state(mid)
        # Kein match_id-Echo -> Zuordnung defensiv ueber die Spielernamen.
        state["teams"]["A"].pop("match_id")
        state["teams"]["B"].pop("match_id")
        self.referee.state_payload = state
        out = coord.reconcile()
        self.assertEqual(out["finished"], [mid])
        self.assertEqual(coord.core.get_match(mid).result, "winnerA")


class PersistenceTestCase(Harness):
    def test_record_survives_restart(self):
        state_dir = os.path.join(self.tmp, "state")
        coord = self.coordinator(state_dir=state_dir)
        coord.join("str:aa")
        out = coord.join("str:bb")
        coord.finish(out["match"]["match_id"], result="draw")

        revived = self.coordinator(state_dir=state_dir)
        record = revived.core.get_match(1).to_dict()
        self.assertEqual(record["state"], STATE_FINISHED)
        self.assertEqual(record["result"], "draw")
        # Nach finish ist die Identitaet wieder frei (kein aktives Match).
        self.assertIsNone(revived.core.match_for("str:aa"))
        self.assertEqual(revived.core.get_match(1).state, STATE_FINISHED)

    def test_pending_queue_survives_restart(self):
        state_dir = os.path.join(self.tmp, "state")
        coord = self.coordinator(state_dir=state_dir)
        coord.join("str:aa")
        revived = self.coordinator(state_dir=state_dir)
        self.assertEqual([e.identitaet for e in revived.core.queue_snapshot()], ["str:aa"])

    def test_status_snapshot(self):
        coord = self.coordinator()
        coord.join("str:aa")
        snap = coord.status()
        self.assertEqual(snap["queued"], 1)
        self.assertEqual(snap["queue"][0]["identitaet"], "str:aa")


class ConcurrencyTestCase(Harness):
    """#998-Verifier: der Dienst laeuft hinter ThreadingHTTPServer -> der
    Koordinator muss Matchmaking (join/pair/leave/finish/status) serialisieren,
    sonst paaren zwei gleichzeitige Joins doppelt / korrumpieren den Kern."""

    def test_join_serialises_matchmaking(self):
        coord = self.coordinator()
        core = coord.core
        original_pair = core.pair
        guard = threading.Lock()
        state = {"inside": 0, "overlap": False}

        def tracked_pair():
            with guard:
                if state["inside"]:
                    state["overlap"] = True
                state["inside"] += 1
            # Weites Fenster: ohne Lock schluepft der zweite Join hier durch.
            time.sleep(0.05)
            try:
                return original_pair()
            finally:
                with guard:
                    state["inside"] -= 1

        core.pair = tracked_pair
        barrier = threading.Barrier(2)
        errors = []

        def worker(name):
            try:
                barrier.wait(timeout=5)
                coord.join(name)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,))
                   for n in ("str:aa", "str:bb")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertFalse(state["overlap"], "pair() lief ueberlappend (Lock fehlt)")
        matches = core.matches_snapshot()
        self.assertEqual(len(matches), 1)
        self.assertEqual(sorted(matches[0].participants()), ["str:aa", "str:bb"])


class RematchTestCase(Harness):
    """Issue #1030: Lobby-Rematch — dieselbe Paarung, neue Kalt-Welten."""

    def _matched_finished(self, result="winnerA"):
        coord = self.coordinator()
        coord.join("str:aa")
        coord.join("str:bb")
        coord.finish(1, result=result)
        return coord

    def test_rematch_spawns_two_fresh_instances(self):
        coord = self._matched_finished()
        res = coord.rematch(match_id=1)
        self.assertFalse(res["idempotent"])
        self.assertEqual(res["rematch_of"], 1)
        new = res["match"]
        self.assertEqual(new["match_id"], 2)
        self.assertEqual(new["rematch_of"], 1)
        self.assertEqual(new["state"], STATE_READY)
        # Gleiche zwei Spieler + gleiche A/B-Weltzuordnung.
        by_world = {p["world"]: p["identitaet"] for p in new["participants"]}
        self.assertEqual(by_world["A"], "str:aa")
        self.assertEqual(by_world["B"], "str:bb")
        # Zwei NEUE Instanzen (match_id 2), andere Endpoints.
        new_starts = [s for s in self.provisioner.starts
                      if s["instance_id"] in (instance_id_for(2, "A"),
                                               instance_id_for(2, "B"))]
        self.assertEqual(len(new_starts), 2)
        old_eps = {p["endpoint"] for p in coord.core.get_match(1).to_dict()["participants"]}
        new_eps = {p["endpoint"] for p in new["participants"]}
        self.assertTrue(new_eps and not (new_eps & old_eps))

    def test_old_instances_stopped_before_new_started(self):
        coord = self._matched_finished()
        coord.rematch(match_id=1)
        events = self.provisioner.events
        for world in ("A", "B"):
            stop_old = events.index(("stop", instance_id_for(1, world)))
            start_new = events.index(("start", instance_id_for(2, world)))
            self.assertLess(stop_old, start_new,
                            "Alt-Stop muss VOR Neu-Start liegen (world %s)" % world)

    def test_referee_rematch_before_lobby(self):
        coord = self._matched_finished()
        coord.rematch(match_id=1)
        kinds = [c[0] for c in self.referee.calls]
        # Initial: lobby,lobby,ready,ready; Rematch: rematch,lobby,lobby,ready,ready.
        self.assertEqual(kinds[:4], ["lobby", "lobby", "ready", "ready"])
        self.assertEqual(kinds[4], "rematch")
        self.assertEqual(kinds[5:], ["lobby", "lobby", "ready", "ready"])
        self.assertEqual(self.referee.rematches, 1)

    def test_rematch_is_idempotent_on_second_call(self):
        coord = self._matched_finished()
        first = coord.rematch(match_id=1)
        starts_after = len(self.provisioner.starts)
        second = coord.rematch(match_id=1)
        self.assertTrue(second["idempotent"])
        self.assertEqual(second["match"]["match_id"], first["match"]["match_id"])
        self.assertEqual(len(self.provisioner.starts), starts_after)  # kein weiterer Start
        self.assertEqual(self.referee.rematches, 1)

    def test_rematch_by_identitaet(self):
        coord = self._matched_finished()
        res = coord.rematch(identitaet="str:bb")
        self.assertEqual(res["rematch_of"], 1)
        self.assertEqual(res["match"]["match_id"], 2)

    def test_referee_failure_start_no_zombie(self):
        coord = self._matched_finished()
        self.referee.rematch_fail = True
        with self.assertRaises(QueueError) as ctx:
            coord.rematch(match_id=1)
        self.assertEqual(ctx.exception.reason, "referee_rematch_failed")
        self.assertEqual(ctx.exception.status, 409)
        # Keine neue Instanz gestartet (kein Zombie); Record failed.
        self.assertEqual(
            [s for s in self.provisioner.starts if s["instance_id"].startswith("queue-2")],
            [],
        )
        self.assertEqual(coord.core.get_match(2).state, STATE_FAILED)
        self.assertNotIn(1, coord._rematch_of)  # Retry moeglich

    def test_cleanup_failure_blocks_rematch(self):
        coord = self.coordinator()
        coord.join("str:aa")
        coord.join("str:bb")
        self.provisioner.fail_stop_always = True
        with self.assertRaises(QueueError) as ctx:
            coord.finish(1, result="winnerA")
        self.assertEqual(ctx.exception.reason, "cleanup_failed")
        self.assertEqual(coord.core.get_match(1).state, STATE_FINISHED)
        with self.assertRaises(QueueError) as ctx2:
            coord.rematch(match_id=1)
        self.assertEqual(ctx2.exception.reason, "cleanup_failed")
        self.assertEqual(ctx2.exception.status, 503)
        self.assertIsNone(coord.core.get_match(2))  # kein neuer Match
        self.assertNotIn(1, coord._rematch_of)

    def test_source_not_finished_is_refused(self):
        coord = self.coordinator()
        coord.join("str:aa")
        coord.join("str:bb")  # state=ready, nicht finished
        with self.assertRaises(QueueError) as ctx:
            coord.rematch(match_id=1)
        self.assertEqual(ctx.exception.reason, "match_not_finished")
        self.assertIsNone(coord.core.get_match(2))
        self.assertEqual(self.referee.rematches, 0)

    def test_unknown_match_and_no_match(self):
        coord = self.coordinator()
        with self.assertRaises(QueueError) as ctx:
            coord.rematch(match_id=99)
        self.assertEqual(ctx.exception.reason, "unknown_match")
        with self.assertRaises(QueueError) as ctx2:
            coord.rematch(identitaet="str:zz")
        self.assertEqual(ctx2.exception.reason, "no_match")

    def test_rematch_idempotency_survives_restart(self):
        state_dir = os.path.join(self.tmp, "state")
        coord = self.coordinator(state_dir=state_dir)
        coord.join("str:aa")
        coord.join("str:bb")
        coord.finish(1, result="winnerA")
        coord.rematch(match_id=1)
        revived = self.coordinator(state_dir=state_dir)
        self.assertEqual(revived._rematch_of, {1: 2})
        res = revived.rematch(match_id=1)
        self.assertTrue(res["idempotent"])
        self.assertEqual(res["match"]["match_id"], 2)


if __name__ == "__main__":
    unittest.main()
