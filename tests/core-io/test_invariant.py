#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermetische Selbsttests der Core-IO-Gate-Logik (Issue #289).

Beweist OHNE Infrastruktur (kein Container, kein Netz), dass die Gate-Logik
die Falsch-Gruen-Semantik aus #288 wirklich rot macht und die offenen Punkte
korrekt klassifiziert. Laeuft in ci.yml (Job `test`).

Ausfuehren:  python3 -m unittest test_invariant -v   (cwd = tests/core-io)
"""

import unittest

import core_io_probe as cio


class ParseExecResponseTest(unittest.TestCase):
    def test_ok_true_parsed(self):
        resp = cio.parse_exec_response(200, '{"ok":true,"results":[{"command":"rb_status","ok":true}]}')
        self.assertTrue(resp["transport_ok"])
        self.assertIs(resp["exec_ok"], True)

    def test_http_503_pipe_unavailable_is_transport_fail(self):
        resp = cio.parse_exec_response(503, '{"ok":false,"reason":"pipe_unavailable"}')
        self.assertFalse(resp["transport_ok"])
        self.assertIs(resp["exec_ok"], False)
        self.assertEqual(resp["reason"], "pipe_unavailable")

    def test_200_with_exec_ok_false_stays_false(self):
        body = ('{"ok":false,"results":'
                '[{"command":"rb_status","ok":false,"reason":"timeout"}]}')
        resp = cio.parse_exec_response(200, body)
        self.assertTrue(resp["transport_ok"])
        self.assertIs(resp["exec_ok"], False)

    def test_garbage_body_does_not_crash(self):
        resp = cio.parse_exec_response(200, "not json")
        self.assertTrue(resp["transport_ok"])
        self.assertIsNone(resp["exec_ok"])


class IngressInvariantTest(unittest.TestCase):
    """Kern-INVARIANTE: ok:true OHNE Effekt-Logzeile ist ROT (#288)."""

    def test_ok_true_without_effect_is_red(self):
        resp = cio.parse_exec_response(200, '{"ok":true,"results":[{"ok":true}]}')
        verdict, detail = cio.classify_ingress(resp, effect_delta=0)
        self.assertEqual(verdict, cio.EXEC_RESULT_FAIL)
        self.assertIn("FALSCH-GRUEN", detail)

    def test_ok_true_with_effect_is_green(self):
        resp = cio.parse_exec_response(200, '{"ok":true,"results":[{"ok":true}]}')
        verdict, _ = cio.classify_ingress(resp, effect_delta=1)
        self.assertEqual(verdict, cio.EXEC_RESULT_OK)

    def test_transport_fail_is_red_regardless_of_effect(self):
        resp = cio.parse_exec_response(None, "__curl_timeout__")
        verdict, _ = cio.classify_ingress(resp, effect_delta=5)
        self.assertEqual(verdict, cio.EXEC_RESULT_FAIL)


class CountEffectTest(unittest.TestCase):
    def test_counts_only_matching_lines(self):
        lines = ["[RBBATTLE] event=status mode=sp", "noise", "[RBBATTLE] event=wave level=1 status=start"]
        self.assertEqual(cio.count_effect(lines, "event=status"), 1)
        self.assertEqual(cio.count_effect(lines, "event=wave"), 1)
        self.assertEqual(cio.count_effect(lines, "event=never"), 0)


class WaveClassificationTest(unittest.TestCase):
    def test_done_with_spawned_positive(self):
        res = cio.classify_wave([
            "[RBBATTLE] event=wave level=1 status=start",
            "[RBBATTLE] event=wave level=1 status=done spawned=5 skipped=0 anchor=border anchors=16",
        ])
        self.assertTrue(res["reached"])
        self.assertEqual(res["spawned"], 5)
        self.assertTrue(res["spawn_ok"])

    def test_headless_no_player_is_not_spawn_ok(self):
        # #288-Fall: Auftrag kommt an (start), aber headless kein Spawn.
        res = cio.classify_wave([
            "[RBBATTLE] event=wave level=1 status=start",
            "[RBBATTLE] event=wave level=1 status=no_border_spawners warn=fallback_mech",
            "[RBBATTLE] event=wave level=1 status=no_player",
        ])
        self.assertTrue(res["reached"])
        self.assertEqual(res["terminal"], "no_player")
        self.assertFalse(res["spawn_ok"])

    def test_spawned_zero_in_done_is_not_spawn_ok(self):
        res = cio.classify_wave([
            "[RBBATTLE] event=wave level=1 status=start",
            "[RBBATTLE] event=wave level=1 status=done spawned=0 skipped=0 anchor=mech anchors=1",
        ])
        self.assertEqual(res["spawned"], 0)
        self.assertFalse(res["spawn_ok"])

    def test_no_wave_lines_not_reached(self):
        res = cio.classify_wave(["[RBBATTLE] event=status mode=sp"])
        self.assertFalse(res["reached"])


class ConsumerTest(unittest.TestCase):
    def test_missing_consumer_is_red(self):
        verdict, _ = cio.classify_consumer(False, False)
        self.assertEqual(verdict, cio.EXEC_RESULT_FAIL)

    def test_running_consumer_without_event_is_red(self):
        verdict, _ = cio.classify_consumer(True, False)
        self.assertEqual(verdict, cio.EXEC_RESULT_FAIL)

    def test_running_consumer_with_event_is_green(self):
        verdict, _ = cio.classify_consumer(True, True)
        self.assertEqual(verdict, cio.EXEC_RESULT_OK)


if __name__ == "__main__":
    unittest.main()
