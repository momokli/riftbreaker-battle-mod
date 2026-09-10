#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-Tests fuer tools/solo-feed/feed.py (Issue #157).

Reine Logik-Tests ohne Docker/Netz: classify() und RestartGuard werden mit
Fakes getestet. Aufruf: `python3 -m unittest test_feed -v` aus tools/solo-feed/.
"""

import unittest

import feed


class ClassifyTests(unittest.TestCase):
    def test_game_over_matches_hq_dead(self):
        msg = ("[server] [14:45:01.000] [info] LogService.cpp:71 - "
               "[LUA 'ConsoleService']: [RBBATTLE] event=hq_dead status=match_end hp=0")
        self.assertEqual(feed.classify(msg), ("game_over", feed.GAME_OVER_TEXT))

    def test_mod_load_hq_dead_false_is_not_game_over(self):
        # Die mod_load-Zeile enthaelt `hq_dead=false`, aber NICHT `event=hq_dead`.
        msg = ("[server] [info] LogService.cpp:71 - [LUA 'ConsoleService']: "
               "[RBBATTLE] event=mod_load version=0.27.0 status=ok hq_dead=false")
        kind, _text = feed.classify(msg) if feed.classify(msg) else (None, None)
        self.assertNotEqual(kind, "game_over")

    def test_wave_up_join_start(self):
        self.assertEqual(feed.classify(
            "[server] [info] LogService.cpp:71 - [LUA 'ConsoleService']: "
            "[RBBATTLE] event=wave level=3 status=start")[0], "wave")
        self.assertEqual(feed.classify(
            "[entrypoint] UDP port 6321 is open — server should accept connections")[0], "up")
        self.assertEqual(feed.classify(
            "[server] [13:33:06.495] [info] GameplayState.cpp:1667 - "
            "GameplayState::ResumeGame")[0], "start")
        self.assertEqual(feed.classify(
            "[server] [13:14:09.449] [info] ServerGameplayState.cpp:867 - "
            "ServerGameplayState: OnNetPlayerCreateRequest '0':'crossover':"
            "':{89E9EB19-6C7B-4701-843F-D9A2A7EC3DF8}' !")[0], "join")

    def test_unknown_line_is_none(self):
        self.assertIsNone(feed.classify("[server] [info] irgendeine Zeile"))


class RestartGuardTests(unittest.TestCase):
    def _guard(self, cooldown_s=60.0):
        now = [0.0]
        calls = []

        def fake_restart():
            calls.append(now[0])
            return True, ""

        return feed.RestartGuard(fake_restart, now=lambda: now[0],
                                 cooldown_s=cooldown_s), now, calls

    def test_one_restart_per_match_end_with_cooldown(self):
        guard, now, calls = self._guard()
        self.assertEqual(guard.on_game_over(), "restarted")
        self.assertEqual(calls, [0.0])
        # Flapping-Guard: zweites hq_dead innerhalb der Cooldown-Frist.
        now[0] = 30.0
        self.assertEqual(guard.on_game_over(), "cooldown")
        self.assertEqual(calls, [0.0])
        # Neues Match nach Ablauf der Frist -> erneuter Restart erlaubt.
        now[0] = 61.0
        self.assertEqual(guard.on_game_over(), "restarted")
        self.assertEqual(calls, [0.0, 61.0])

    def test_restart_failure_is_reported(self):
        def failing_restart():
            return False, "docker kaputt"

        guard = feed.RestartGuard(failing_restart, now=lambda: 10.0)
        self.assertEqual(guard.on_game_over(), "restart_failed: docker kaputt")

    def test_take_new_game_is_one_shot(self):
        guard, _now, _calls = self._guard()
        self.assertFalse(guard.take_new_game())
        guard.on_game_over()
        self.assertTrue(guard.take_new_game())
        self.assertFalse(guard.take_new_game())


if __name__ == "__main__":
    unittest.main()
