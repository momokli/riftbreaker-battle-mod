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
        self.fail_on_world = None
        self.fail_on_instance = None
        self._port = 40000

    def start(self, env, mode, instance_id, world=None):
        if self.fail_on_world is not None and world == self.fail_on_world:
            raise RuntimeError("fake provisioner explodiert fuer world=%s" % world)
        if self.fail_on_instance is not None and instance_id == self.fail_on_instance:
            raise RuntimeError("fake provisioner explodiert fuer %s" % instance_id)
        self.starts.append({"env": env, "mode": mode, "instance_id": instance_id,
                            "world": world})
        self._port += 1
        return {
            "instance": instance_id,
            "running": True,
            "created": True,
            "ports": {"bridge": self._port, "gns": "127.0.0.1:%d" % self._port},
        }

    def stop(self, instance_id, env=None):
        self.stops.append({"instance_id": instance_id, "env": env})
        return {"instance": instance_id, "removed": True}


class FakeReferee(object):
    def __init__(self) -> None:
        self.lobbies = []
        self.fail = False

    def lobby(self, player, world):
        if self.fail:
            raise RuntimeError("fake referee explodiert")
        self.lobbies.append({"player": player, "world": world})
        return {"ok": True}


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
        by_world = {l["world"]: l["player"] for l in self.referee.lobbies}
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
        self.assertEqual(revived.core.match_for("str:aa").match_id, 1)

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


if __name__ == "__main__":
    unittest.main()