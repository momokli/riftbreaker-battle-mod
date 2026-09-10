#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_deploy_gate.py - Unit-Tests fuer die Deploy-Parkierung (Issue #118).

Testet die reine Entscheidungslogik decide() und den Poll-Loop
wait_until_empty() deterministisch (Fake-Provider/-Clock/-Sleep):

  - 0 Spieler        -> deploy
  - n > 0            -> parken ("geparkt (n Spieler online)")
  - force            -> sofort deploy (auch bei n > 0)
  - unbekannt (None) -> parken (sicher)
  - timeout          -> nach Deadline (TIMEOUT), kein unbegrenztes Haengen
  - provider-Kommando (run_count_cmd) -> int / None bei Fehler

Nur Standardbibliothek (unittest, os, sys).

Aufruf: python3 -m unittest test_deploy_gate -v   (aus diesem Verzeichnis)
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deploy_gate  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class SeqProvider:
    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    def __call__(self):
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        return value


class CollectLog:
    def __init__(self):
        self.lines = []

    def __call__(self, message):
        self.lines.append(message)


class DecideTest(unittest.TestCase):
    def test_zero_players_deploys(self):
        self.assertEqual(deploy_gate.decide(0), (deploy_gate.DEPLOY, "leer"))

    def test_players_online_parks(self):
        self.assertEqual(
            deploy_gate.decide(3), (deploy_gate.PARK, "3 Spieler online"),
        )

    def test_force_deploys_even_with_players(self):
        self.assertEqual(
            deploy_gate.decide(3, force=True), (deploy_gate.DEPLOY, "forced"),
        )

    def test_unknown_parks_safely(self):
        self.assertEqual(deploy_gate.decide(None), (deploy_gate.PARK, "unbekannt"))

    def test_negative_count_rejected(self):
        with self.assertRaises(ValueError):
            deploy_gate.decide(-1)


class WaitUntilEmptyTest(unittest.TestCase):
    def _run(self, provider, **kwargs):
        log = CollectLog()
        clock = FakeClock()
        result = deploy_gate.wait_until_empty(
            provider,
            sleep_fn=lambda s: clock.advance(s),
            now_fn=clock,
            log=log,
            **kwargs
        )
        return result, log

    def test_empty_deploys_immediately(self):
        result, log = self._run(SeqProvider([0]))
        self.assertEqual(result, (deploy_gate.DEPLOY, "leer"))
        self.assertEqual(log.lines, ["deploy-gate: deploy - leer"])

    def test_force_deploys_without_polling(self):
        result, log = self._run(SeqProvider([3]), force=True)
        self.assertEqual(result, (deploy_gate.DEPLOY, "forced"))
        self.assertEqual(log.lines, ["deploy-gate: deploy - forced"])

    def test_parks_until_empty(self):
        result, log = self._run(SeqProvider([3, 2, 0]))
        self.assertEqual(result, (deploy_gate.DEPLOY, "leer"))
        self.assertEqual(log.lines, [
            "deploy-gate: geparkt (3 Spieler online)",
            "deploy-gate: geparkt (2 Spieler online)",
            "deploy-gate: deploy - leer",
        ])

    def test_timeout_stops_polling(self):
        result, log = self._run(SeqProvider([3]), timeout=25.0, interval=10.0)
        self.assertEqual(result, (deploy_gate.TIMEOUT, "3 Spieler online"))
        self.assertTrue(any("geparkt (3 Spieler online)" in line for line in log.lines))
        self.assertTrue(any("timeout nach 25.0s" in line for line in log.lines))

    def test_no_timeout_keeps_polling(self):
        # Provider liefert erst 3 Spieler, dann 0 -> vor Deadline leer, kein Timeout.
        result, log = self._run(SeqProvider([3, 0]), timeout=100.0, interval=10.0)
        self.assertEqual(result, (deploy_gate.DEPLOY, "leer"))


class RunCountCmdTest(unittest.TestCase):
    def test_parses_plain_integer(self):
        self.assertEqual(deploy_gate.run_count_cmd("echo 5"), 5)

    def test_parses_first_integer(self):
        self.assertEqual(deploy_gate.run_count_cmd("echo '3 Spieler online'"), 3)

    def test_failing_command_returns_none(self):
        self.assertIsNone(deploy_gate.run_count_cmd("exit 1"))


if __name__ == "__main__":
    unittest.main()
