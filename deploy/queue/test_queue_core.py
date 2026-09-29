#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermetische Tests fuer deploy/queue/queue_core.py (Issue #998, US1).

Kein Netz, kein Docker, kein Spiel: die reine Matchmaking-Logik mit einer
Fake-Uhr. Aufruf:

    cd deploy/queue && python3 -m unittest -v
"""

from __future__ import annotations

import unittest

from queue_core import (
    QueueCore,
    QueueEntry,
    QueueError,
    Match,
    Assignment,
    WORLD_A,
    WORLD_B,
    STATE_PROVISIONING,
    STATE_FAILED,
    STATE_FINISHED,
)


class FakeClock(object):
    """Monotone Fake-Uhr; nur explizites ``advance`` bewegt die Zeit."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class QueueCoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.core = QueueCore(clock=self.clock)

    # -- Enqueue / Idempotenz ---------------------------------------------
    def test_enqueue_single_entry_is_pending(self):
        entry = self.core.enqueue("str:aa")
        self.assertIsInstance(entry, QueueEntry)
        self.assertEqual(entry.identitaet, "str:aa")
        self.assertEqual(entry.mode, "vs")
        self.assertEqual(entry.team_size, 1)
        self.assertEqual(len(self.core.queue_snapshot()), 1)
        # Ein einzelner Eintrag erzeugt KEIN Match.
        self.assertIsNone(self.core.pair())
        self.assertEqual(self.core.matches_snapshot(), [])

    def test_duplicate_enqueue_is_idempotent(self):
        first = self.core.enqueue("str:aa")
        second = self.core.enqueue("str:aa")
        self.assertEqual(first, second)
        self.assertEqual(len(self.core.queue_snapshot()), 1)

    def test_fifo_position(self):
        a = self.core.enqueue("str:aa")
        b = self.core.enqueue("str:bb")
        c = self.core.enqueue("str:cc")
        self.assertEqual([a.seq, b.seq, c.seq], [0, 1, 2])
        self.assertEqual(self.core.position("str:aa"), 1)
        self.assertEqual(self.core.position("str:bb"), 2)
        self.assertEqual(self.core.position("str:cc"), 3)
        self.assertIsNone(self.core.position("str:zz"))

    # -- Pairing -----------------------------------------------------------
    def test_two_entries_pair_into_one_match(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        match = self.core.pair()
        self.assertIsInstance(match, Match)
        self.assertEqual(match.match_id, 1)
        self.assertEqual(match.mode, "vs")
        self.assertEqual(match.team_size, 1)
        self.assertEqual(match.state, STATE_PROVISIONING)
        # Genau EIN Match; die Queue ist danach leer.
        self.assertEqual(len(self.core.matches_snapshot()), 1)
        self.assertEqual(self.core.queue_snapshot(), [])

    def test_pairing_is_fifo_order(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        match = self.core.pair()
        assignments = match.assignments()
        self.assertEqual([a.identitaet for a in assignments], ["str:aa", "str:bb"])

    def test_assignment_is_a_b_then_b(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        match = self.core.pair()
        by_world = {a.world: a.identitaet for a in match.assignments()}
        self.assertEqual(by_world[WORLD_A], "str:aa")
        self.assertEqual(by_world[WORLD_B], "str:bb")
        self.assertEqual(match.world_of("str:aa"), WORLD_A)
        self.assertEqual(match.world_of("str:bb"), WORLD_B)

    def test_match_numbering_is_monotonic(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        self.core.enqueue("str:cc")
        self.core.enqueue("str:dd")
        first = self.core.pair()
        second = self.core.pair()
        self.assertEqual((first.match_id, second.match_id), (1, 2))
        self.assertIsNone(self.core.pair())

    def test_matched_identity_is_not_re_enqueueable(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        self.core.pair()
        with self.assertRaises(QueueError) as ctx:
            self.core.enqueue("str:aa")
        self.assertEqual(ctx.exception.reason, "already_matched")

    # -- Leave -------------------------------------------------------------
    def test_leave_removes_queued_entry(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        removed = self.core.leave("str:bb")
        self.assertEqual(removed.identitaet, "str:bb")
        self.assertEqual([e.identitaet for e in self.core.queue_snapshot()], ["str:aa"])
        # Nur noch einer -> kein Match.
        self.assertIsNone(self.core.pair())

    def test_leave_unknown_raises(self):
        with self.assertRaises(QueueError) as ctx:
            self.core.leave("str:zz")
        self.assertEqual(ctx.exception.reason, "not_queued")

    def test_leave_matched_identity_raises(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        self.core.pair()
        with self.assertRaises(QueueError) as ctx:
            self.core.leave("str:aa")
        self.assertEqual(ctx.exception.reason, "already_matched")

    # -- team_size gate ----------------------------------------------------
    def test_team_size_greater_one_is_rejected(self):
        with self.assertRaises(QueueError) as ctx:
            self.core.enqueue("str:aa", team_size=2)
        self.assertEqual(ctx.exception.reason, "unsupported_team_size")
        self.assertEqual(self.core.queue_snapshot(), [])

    def test_team_size_gate_only_accepts_one(self):
        for bad in (0, -1, 2, 5):
            with self.assertRaises(QueueError, msg=bad):
                self.core.enqueue("str:aa", team_size=bad)
        self.assertEqual(self.core.queue_snapshot(), [])

    # -- Match-Record (US5) ------------------------------------------------
    def test_match_record_holds_both_participants(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        match = self.core.pair()
        record = match.to_dict()
        self.assertEqual(record["match_id"], 1)
        self.assertEqual(record["mode"], "vs")
        self.assertEqual(record["state"], STATE_PROVISIONING)
        self.assertIsNone(record["result"])
        self.assertEqual(len(record["participants"]), 2)
        worlds = sorted(p["world"] for p in record["participants"])
        self.assertEqual(worlds, [WORLD_A, WORLD_B])
        # Kein MMR-/Ranking-Feld.
        for key in ("mmr", "rating", "elo", "rank"):
            self.assertNotIn(key, record)

    def test_match_assignments_carry_instance_and_endpoint(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        match = self.core.pair()
        self.core.set_assignment(match, "str:aa", instance="queue-1-a", endpoint="127.0.0.1:40001")
        self.core.set_assignment(match, "str:bb", instance="queue-1-b", endpoint="127.0.0.1:40002")
        record = match.to_dict()
        by_id = {p["identitaet"]: p for p in record["participants"]}
        self.assertEqual(by_id["str:aa"]["instance"], "queue-1-a")
        self.assertEqual(by_id["str:aa"]["endpoint"], "127.0.0.1:40001")
        self.assertEqual(by_id["str:bb"]["instance"], "queue-1-b")
        self.assertEqual(by_id["str:bb"]["endpoint"], "127.0.0.1:40002")

    def test_finish_sets_result_and_state(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        match = self.core.pair()
        self.core.mark_ready(match)
        finished = self.core.finish(match.match_id, result="winnerA")
        self.assertEqual(finished.state, STATE_FINISHED)
        self.assertEqual(finished.result, "winnerA")

    def test_finish_without_result_is_allowed(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        match = self.core.pair()
        finished = self.core.finish(match.match_id)
        self.assertEqual(finished.state, STATE_FINISHED)
        self.assertIsNone(finished.result)

    def test_finish_is_idempotent(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        match = self.core.pair()
        first = self.core.finish(match.match_id, result="winnerA")
        second = self.core.finish(match.match_id, result="winnerB")
        self.assertEqual(first.state, STATE_FINISHED)
        self.assertEqual(second.result, "winnerA")  # zweiter Aufruf aendert nichts

    def test_finish_unknown_match_raises(self):
        with self.assertRaises(QueueError) as ctx:
            self.core.finish(999)
        self.assertEqual(ctx.exception.reason, "unknown_match")

    def test_finish_rejects_bad_result(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        match = self.core.pair()
        with self.assertRaises(QueueError) as ctx:
            self.core.finish(match.match_id, result="nope")
        self.assertEqual(ctx.exception.reason, "bad_result")
        self.assertEqual(ctx.exception.status, 400)

    def test_mark_failed(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        match = self.core.pair()
        failed = self.core.mark_failed(match.match_id, detail="provisioner down")
        self.assertEqual(failed.state, STATE_FAILED)
        self.assertEqual(failed.detail, "provisioner down")
        # Match abgebrochen -> Identitaet geloest, Record bleibt erhalten.
        self.assertIsNone(self.core.match_for("str:aa"))
        self.assertEqual(self.core.get_match(1).state, STATE_FAILED)

    # -- Re-Queue nach Match-Ende (#998) -----------------------------------
    def test_re_queue_after_finish(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        first = self.core.pair()
        self.core.finish(first.match_id, result="winnerA")
        # Nach finish wieder einreihbar -> neues Match.
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        second = self.core.pair()
        self.assertIsNotNone(second)
        self.assertEqual(second.match_id, 2)
        self.assertEqual(sorted(second.participants()), ["str:aa", "str:bb"])

    def test_re_queue_after_failed(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        first = self.core.pair()
        self.core.mark_failed(first.match_id, detail="provisioner down")
        # Ein gescheitertes Match blockiert die erneute Einreihung nicht.
        entry = self.core.enqueue("str:aa")
        self.assertEqual(entry.identitaet, "str:aa")

    def test_re_queue_after_restart_once_match_finished(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        first = self.core.pair()
        self.core.finish(first.match_id, result="draw")
        revived = QueueCore(clock=FakeClock())
        revived.load_state(self.core.to_state())
        # Nach Restart darf ein abgeschlossenes Match die Einreihung nicht sperren.
        revived.enqueue("str:aa")
        revived.enqueue("str:bb")
        self.assertIsNotNone(revived.pair())

    def test_active_match_still_blocks_re_queue(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        self.core.pair()  # state=provisioning -> aktiv
        with self.assertRaises(QueueError) as ctx:
            self.core.enqueue("str:aa")
        self.assertEqual(ctx.exception.reason, "already_matched")

    # -- Snapshot / Lookup -------------------------------------------------
    def test_match_for_and_get_match(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        match = self.core.pair()
        self.assertIsNone(self.core.match_for("str:cc"))
        self.assertEqual(self.core.match_for("str:aa").match_id, match.match_id)
        self.assertEqual(self.core.get_match(1).match_id, 1)
        self.assertIsNone(self.core.get_match(999))

    def test_assignments_dataclass_roundtrip(self):
        a = Assignment(identitaet="str:aa", world=WORLD_A)
        self.assertEqual(a.to_dict()["world"], WORLD_A)

    # -- Persistenz (US5) --------------------------------------------------
    def test_state_roundtrip_survives_restart(self):
        self.core.enqueue("str:aa")
        self.core.enqueue("str:bb")
        match = self.core.pair()
        self.core.set_assignment(match, "str:aa", instance="queue-1-a", endpoint="127.0.0.1:40001")
        self.core.set_assignment(match, "str:bb", instance="queue-1-b", endpoint="127.0.0.1:40002")
        self.core.mark_ready(match)
        self.core.finish(match.match_id, result="winnerA")
        state = self.core.to_state()

        revived = QueueCore(clock=FakeClock())
        revived.load_state(state)
        record = revived.get_match(1).to_dict()
        self.assertEqual(record["state"], STATE_FINISHED)
        self.assertEqual(record["result"], "winnerA")
        by_id = {p["identitaet"]: p for p in record["participants"]}
        self.assertEqual(by_id["str:aa"]["instance"], "queue-1-a")
        self.assertEqual(by_id["str:bb"]["endpoint"], "127.0.0.1:40002")
        # Nach finish ist die Identitaet wieder frei -> kein aktives Match.
        self.assertIsNone(revived.match_for("str:aa"))
        self.assertEqual(revived.get_match(1).state, STATE_FINISHED)

    def test_state_roundtrip_keeps_pending_queue(self):
        self.core.enqueue("str:aa")
        state = self.core.to_state()
        revived = QueueCore(clock=FakeClock())
        revived.load_state(state)
        self.assertEqual([e.identitaet for e in revived.queue_snapshot()], ["str:aa"])
        self.assertEqual(revived.position("str:aa"), 1)


if __name__ == "__main__":
    unittest.main()
