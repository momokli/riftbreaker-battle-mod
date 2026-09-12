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

Ergaenzt (Issue #238) um PlayerCountTest: den Log-Provider player_count.py
(direkte Funktion + CLI ueber Temp-Fixtures) sowie die Negativ-Semantik
(Spieler online / unbekannt -> PARK, wait_until_empty() nie DEPLOY bei Timeout).
Laeuft ueber den bestehenden CI-Step (ci.yml, Job test) - keine ci.yml-Aenderung.

Nur Standardbibliothek (unittest, os, sys, subprocess, tempfile).

Aufruf: python3 -m unittest test_deploy_gate -v   (aus diesem Verzeichnis)
"""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deploy_gate  # noqa: E402
import player_count  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PLAYER_COUNT = os.path.join(HERE, "player_count.py")


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


# --- PlayerCountTest (Issue #238): Log-Provider player_count.py ---------------

# Fixture-Zeilen in chronologischer Reihenfolge (echte Log-Form aus feed.py).
JOIN_CREATE = (
    "[server] [info] ServerGameplayState.cpp:867 - "
    "ServerGameplayState: OnNetPlayerCreateRequest '0':'crossover'!"
)
JOIN_PLAYER = (
    "[server] [info] ServerGameplayState.cpp:966 - "
    "ServerGameplayState: Player '0':'crossover'!"
)
RESUME = "[server] [info] GameplayState.cpp:1667 - GameplayState::ResumeGame"
PAUSE = "[server] [info] GameplayState.cpp:1200 - GameplayState::PauseGame"
UNKNOWN = "[server] [info] NetServerGNS.cpp:173 - [NetServerGNS] listening"


class PlayerCountTest(unittest.TestCase):
    """Der Provider leitet die Spielerzahl aus Log-Zeilen ab (konservativ)."""

    def _count(self, lines):
        return player_count.count_players(lines)

    def _run_cli(self, log_text=None, extra_args=None):
        """Ruft player_count.py als Subprozess auf; Fixture via Temp-Datei."""
        args = [sys.executable, PLAYER_COUNT]
        with tempfile.TemporaryDirectory() as tmp:
            if log_text is not None:
                path = os.path.join(tmp, "fixture.log")
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(log_text)
                args += ["--log", path]
            if extra_args:
                args += extra_args
            proc = subprocess.run(args, capture_output=True, text=True)
        return proc.returncode, proc.stdout.strip()

    def test_empty_log_is_unsure(self):
        self.assertIsNone(self._count([]))
        code, out = self._run_cli(log_text="")
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")

    def test_only_unknown_lines_are_unsure(self):
        self.assertIsNone(self._count([UNKNOWN, UNKNOWN]))
        code, _ = self._run_cli(log_text=UNKNOWN + "\n")
        self.assertNotEqual(code, 0)

    def test_only_pause_is_zero(self):
        self.assertEqual(self._count([UNKNOWN, PAUSE]), 0)
        code, out = self._run_cli(log_text=PAUSE + "\n")
        self.assertEqual((code, out), (0, "0"))

    def test_resume_without_pause_is_at_least_one(self):
        self.assertEqual(self._count([RESUME]), 1)
        self.assertGreaterEqual(self._count([UNKNOWN, RESUME]), 1)
        code, out = self._run_cli(log_text=RESUME + "\n")
        self.assertEqual(code, 0)
        self.assertGreaterEqual(int(out), 1)

    def test_joins_after_pause_count(self):
        lines = [PAUSE, JOIN_CREATE, JOIN_PLAYER, JOIN_CREATE]
        self.assertEqual(self._count(lines), 3)
        code, out = self._run_cli(log_text="\n".join(lines) + "\n")
        self.assertEqual((code, out), (0, "3"))

    def test_pause_after_joins_is_zero(self):
        lines = [JOIN_CREATE, JOIN_PLAYER, RESUME, PAUSE]
        self.assertEqual(self._count(lines), 0)
        code, out = self._run_cli(log_text="\n".join(lines) + "\n")
        self.assertEqual((code, out), (0, "0"))

    def test_command_error_is_unsure(self):
        self.assertIsNone(player_count.run_log_cmd("exit 1"))
        code, out = self._run_cli(extra_args=["--log-cmd", "exit 1"])
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "")

    def test_stdout_is_single_integer(self):
        code, out = self._run_cli(log_text=JOIN_CREATE + "\n")
        self.assertEqual(code, 0)
        self.assertTrue(out.isdigit())


class NegativeSemanticsTest(unittest.TestCase):
    """Online/unbekannt -> PARK; wait_until_empty() wird NIE DEPLOY (Timeout)."""

    def _provider(self, lines):
        return lambda: player_count.count_players(lines)

    def test_online_parks_in_decide(self):
        provider = self._provider([JOIN_CREATE, RESUME])
        action, detail = deploy_gate.decide(provider())
        self.assertEqual(action, deploy_gate.PARK)
        self.assertIn("Spieler online", detail)

    def test_unknown_parks_in_decide(self):
        provider = self._provider([UNKNOWN])
        self.assertEqual(
            deploy_gate.decide(provider()), (deploy_gate.PARK, "unbekannt"),
        )

    def _wait(self, provider, timeout):
        clock = FakeClock()
        log = CollectLog()
        result = deploy_gate.wait_until_empty(
            provider,
            timeout=timeout,
            interval=30.0,
            sleep_fn=lambda s: clock.advance(s),
            now_fn=clock,
            log=log,
        )
        return result, log

    def test_wait_never_deploys_while_players_online(self):
        result, log = self._wait(self._provider([JOIN_CREATE, RESUME]), timeout=90.0)
        self.assertEqual(result[0], deploy_gate.TIMEOUT)
        self.assertNotEqual(result[0], deploy_gate.DEPLOY)
        self.assertTrue(any("geparkt" in line for line in log.lines))

    def test_wait_never_deploys_while_unknown(self):
        result, _ = self._wait(self._provider([UNKNOWN]), timeout=90.0)
        self.assertEqual(result, (deploy_gate.TIMEOUT, "unbekannt"))
        self.assertNotEqual(result[0], deploy_gate.DEPLOY)


if __name__ == "__main__":
    unittest.main()
