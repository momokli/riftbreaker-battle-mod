#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-Tests für den Match-Loop (Issue #730).

Rein stdlib + hermetic: `parse_state`, `is_alive`/`is_defeat` und der
`MatchLoop`-Zustandsautomat werden mit Fake-Poster/Fake-Clock getestet —
kein Netz, kein Spiel, kein DOM.
"""

import json
import unittest

from match_loop import MatchLoop, is_alive, is_defeat, parse_state


def _state(ok=True, hp=None, hp_max=None, dead=None):
    body = {"event": "get_state_result", "ok": ok}
    if hp is not None:
        body["hq_hp"] = hp
    if hp_max is not None:
        body["hq_hp_max"] = hp_max
    if dead is not None:
        body["hq_dead"] = dead
    return json.dumps(body)


class TestParseState(unittest.TestCase):
    def test_null_fields(self):
        v = parse_state(_state(hp=None, hp_max=None, dead=None))
        self.assertTrue(v["ok"])
        self.assertIsNone(v["hq_hp"])
        self.assertIsNone(v["hq_hp_max"])
        self.assertIsNone(v["hq_dead"])

    def test_numbers_and_bool(self):
        v = parse_state(_state(hp=850.5, hp_max=1000.0, dead=False))
        self.assertEqual(v["hq_hp"], 850.5)
        self.assertEqual(v["hq_hp_max"], 1000.0)
        self.assertIs(v["hq_dead"], False)

    def test_dead_true(self):
        v = parse_state(_state(hp=0, hp_max=1000.0, dead=True))
        self.assertEqual(v["hq_hp"], 0.0)
        self.assertIs(v["hq_dead"], True)

    def test_not_json(self):
        v = parse_state("not json")
        self.assertFalse(v["ok"])

    def test_ok_false(self):
        v = parse_state('{"event":"get_state_result","ok":false}')
        self.assertFalse(v["ok"])


class TestPredicates(unittest.TestCase):
    def test_alive(self):
        self.assertTrue(is_alive({"hq_hp": 100.0}))
        self.assertFalse(is_alive({"hq_hp": 0.0}))
        self.assertFalse(is_alive({"hq_hp": None}))

    def test_defeat(self):
        self.assertTrue(is_defeat({"hq_dead": True}))
        self.assertTrue(is_defeat({"hq_hp": None}))
        self.assertFalse(is_defeat({"hq_hp": 100.0, "hq_dead": False}))


class FakePoster:
    def __init__(self, state_resp='{"ok":true}'):
        self.calls = []
        self.state_resp = state_resp

    def __call__(self, path, body):
        self.calls.append((path, body))
        if path == "/get_state":
            return (200, self.state_resp)
        return (200, '{"ok":true}')


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


class TestMatchLoop(unittest.TestCase):
    def _loop(self, poster, delay=10.0, clock=None):
        return MatchLoop("http://127.0.0.1:9001", restart_delay=delay, _poster=poster, _clock=clock or FakeClock())

    def test_not_built_does_not_fire(self):
        # Rundenstart: HQ nicht gebaut (hp null) -> kein end_game/restart.
        poster = FakePoster(_state(hp=None, hp_max=None, dead=None))
        loop = self._loop(poster)
        self.assertIsNone(loop.step())
        self.assertIsNone(loop.step())
        self.assertEqual(poster.calls, [("/get_state", b"{}"), ("/get_state", b"{}")])

    def test_alive_then_defeat_then_restart(self):
        poster = FakePoster(_state(hp=100.0, hp_max=1000.0, dead=False))
        clock = FakeClock(0.0)
        loop = self._loop(poster, delay=10.0, clock=clock)

        # HQ gebaut -> "alive", Latch gesetzt, kein POST ausser get_state.
        self.assertEqual(loop.step(), "alive")
        self.assertTrue(loop.seen_alive)
        self.assertEqual(len(poster.calls), 1)

        # HQ zerstoert (hp 0, dead true) -> "defeat" + end_game(lose).
        poster.state_resp = _state(hp=0.0, hp_max=1000.0, dead=True)
        self.assertEqual(loop.step(), "defeat")
        self.assertFalse(loop.seen_alive)
        self.assertIsNotNone(loop.defeat_at)
        self.assertIn(("/end_game", json.dumps({"result": "lose"}).encode("utf-8")), poster.calls)

        # Vor Ablauf des Delay: kein Restart.
        clock.t = 5.0
        self.assertIsNone(loop.step())
        self.assertNotIn(("/restart_map", json.dumps({"op": "reset"}).encode("utf-8")), poster.calls)

        # Nach Ablauf: "restart" + restart_map(reset), Latch zurueck.
        clock.t = 11.0
        self.assertEqual(loop.step(), "restart")
        self.assertIn(("/restart_map", json.dumps({"op": "reset"}).encode("utf-8")), poster.calls)
        self.assertIsNone(loop.defeat_at)

    def test_defeat_via_despawn(self):
        # Entity verschwindet nach HQ-Leben (hp null, dead false) -> trotzdem Defeat.
        poster = FakePoster(_state(hp=100.0, hp_max=1000.0, dead=False))
        loop = self._loop(poster)
        loop.step()  # alive
        poster.state_resp = _state(hp=None, hp_max=None, dead=False)
        self.assertEqual(loop.step(), "defeat")

    def test_end_game_fires_once(self):
        # Defeat ist edge-getriggert: zweites step feuert kein zweites end_game.
        poster = FakePoster(_state(hp=100.0, hp_max=1000.0, dead=False))
        loop = self._loop(poster)
        loop.step()  # alive
        poster.state_resp = _state(hp=0.0, hp_max=1000.0, dead=True)
        loop.step()  # defeat -> end_game
        end_games = [c for c in poster.calls if c[0] == "/end_game"]
        self.assertEqual(len(end_games), 1)

    def test_ok_false_does_nothing(self):
        poster = FakePoster('{"event":"get_state_result","ok":false}')
        loop = self._loop(poster)
        self.assertIsNone(loop.step())
        self.assertFalse(loop.seen_alive)


if __name__ == "__main__":
    unittest.main()
