#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-Tests für den Attack-Cycle (PoC).

Rein stdlib + hermetic: parse_hq_alive, parse_send_level und die
AttackCycle-Zustandsmaschine laufen gegen Fake-Poster/Fake-Clock —
kein Netz, kein Spiel, kein DOM.
"""

import json
import os
import tempfile
import unittest

from attack_cycle import (
    AttackCycle,
    BOSS_LOGIC,
    DEFAULT_DIFFICULTY_INTERVAL_S,
    DEFAULT_DIFFICULTY_PROFILE,
    DIFFICULTY_RULES,
    WAVE_COUNT,
    _expand_counts,
    _normalize_counts,
    _normalize_difficulty_rules,
    load_personas,
    parse_hq_alive,
    parse_send_level,
)


def wave_count(*levels):
    """Baue einen WAVE_COUNT-Count-Vektor aus Leveln (Duplikate = count)."""
    counts = [0] * WAVE_COUNT
    for lvl in levels:
        counts[lvl - 1] += 1
    return counts


class FakePoster:
    def __init__(self, state_resp='{"ok":true,"hq_hp":100.0}'):
        self.calls = []
        self.state_resp = state_resp
        self.spend_ok = True
        self.spend_resp = '{"ok":true,"balance":50000000}'
        self.reset_epoch = 0

    def __call__(self, path, body):
        self.calls.append((path, body))
        if path == "/get_state":
            return (200, self.state_resp)
        if path == "/try_spend":
            if self.spend_ok:
                return (200, self.spend_resp)
            return (200, '{"ok":false,"reason":"insufficient","balance":0}')
        if path == "/activate_mission_flow":
            return (200, '{"ok":true}')
        if path == "/attack_reset":
            return (200, '{"reset_epoch":%d}' % self.reset_epoch)
        return (404, '{"ok":false}')


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


class TestParseHqAlive(unittest.TestCase):
    def test_alive(self):
        self.assertTrue(parse_hq_alive('{"ok":true,"hq_hp":100.0}'))

    def test_not_built(self):
        self.assertFalse(parse_hq_alive('{"ok":true,"hq_hp":null}'))

    def test_zero(self):
        self.assertFalse(parse_hq_alive('{"ok":true,"hq_hp":0}'))

    def test_not_ok(self):
        self.assertFalse(parse_hq_alive('{"ok":false}'))

    def test_not_json(self):
        self.assertFalse(parse_hq_alive("not json"))


class TestParseSendLevel(unittest.TestCase):
    def test_name(self):
        self.assertEqual(parse_send_level('{"name":"wave3"}'), 3)

    def test_level(self):
        self.assertEqual(parse_send_level('{"level":5}'), 5)

    def test_unknown(self):
        self.assertIsNone(parse_send_level('{"name":"wave99"}'))

    def test_bad_level(self):
        self.assertIsNone(parse_send_level('{"level":99}'))

    def test_invalid_json(self):
        self.assertIsNone(parse_send_level("not json"))


class TestAttackCycle(unittest.TestCase):
    # difficulty_interval default hier bewusst riesig: die meisten Tests unten
    # pruefen NUR den Wellen-Feuer-Pfad und sollen vom (jetzt entkoppelten)
    # Difficulty-Timer unberuehrt bleiben. Tests, die den Timer selbst pruefen,
    # ueberschreiben ihn explizit (siehe TestAttackCycleDifficultyTimer unten).
    def _cycle(self, poster, interval=420.0, difficulty_interval=1e9, clock=None):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=interval,
            difficulty_interval_s=difficulty_interval,
            _poster=poster,
            _clock=clock or FakeClock(),
        )

    def test_not_built_does_not_start(self):
        poster = FakePoster('{"ok":true,"hq_hp":null}')
        cycle = self._cycle(poster)
        self.assertIsNone(cycle.step())
        self.assertFalse(cycle.active)

    def test_hq_built_starts_cycle(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock, difficulty_interval=DEFAULT_DIFFICULTY_INTERVAL_S)
        self.assertEqual(cycle.step(), "started")
        self.assertTrue(cycle.active)
        self.assertEqual(cycle.level, 1)
        self.assertEqual(cycle.next_attack_at, 420.0)
        self.assertEqual(cycle.next_difficulty_at, 200.0)

    def test_fire_natural_wave_after_interval(self):
        """Ein Wellen-Feuer-Tick erhoeht das Level NICHT mehr selbst (Issue #778,
        der Difficulty-Timer ist hier bewusst riesig und laeuft in diesem Test
        nie ab)."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        cycle.step()  # started
        clock.t = 420.0
        self.assertEqual(cycle.step(), "attack")
        logic_calls = [c for c in poster.calls if c[0] == "/activate_mission_flow"]
        self.assertEqual(len(logic_calls), 1)
        self.assertIn("attack_level_1_id_1.logic", logic_calls[0][1].decode())
        self.assertEqual(cycle.level, 1)

    def test_buy_stacks_and_fires_with_natural(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        cycle.step()  # started

        status, payload = cycle.buy(2)
        self.assertEqual(status, 200)
        self.assertEqual(payload["queued_level"], 2)
        cycle._resolve_orders()  # pay -> bought

        clock.t = 420.0
        cycle.step()  # attack
        logic_calls = [c for c in poster.calls if c[0] == "/activate_mission_flow"]
        logics = [json.loads(c[1].decode())["logic"] for c in logic_calls]
        self.assertIn("logic/missions/survival/attack_level_1_id_1.logic", logics)  # natural
        self.assertIn("logic/missions/survival/attack_level_2_id_1.logic", logics)  # sent
        self.assertEqual(len(logics), 2)

    def test_buy_queues_immediately(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        cycle = self._cycle(poster)
        status, payload = cycle.buy(2)
        self.assertEqual(status, 200)
        self.assertEqual(payload["queued_level"], 2)
        self.assertEqual(len(cycle.orders), 1)
        self.assertEqual(cycle.bought, [])

    def test_resolve_orders_pays(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        cycle = self._cycle(poster)
        cycle.buy(2)
        self.assertEqual(cycle.bought, [])
        cycle._resolve_orders()
        self.assertEqual(cycle.bought, [2])
        self.assertEqual(cycle.orders, [])

    def test_resolve_orders_rejects_insufficient(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        poster.spend_ok = False
        cycle = self._cycle(poster)
        cycle.buy(2)
        cycle._resolve_orders()
        self.assertEqual(cycle.bought, [])
        self.assertEqual(cycle.orders, [])

    def test_level_caps_at_max(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock, difficulty_interval=1.0)
        cycle.step()  # started (level 1)
        for _ in range(20):
            clock.t += 1.0
            cycle.step()
        self.assertEqual(cycle.level, 9)

    def test_multiple_buys_same_level_stack(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        cycle.step()  # started
        cycle.buy(2)
        cycle.buy(2)
        cycle._resolve_orders()  # pay -> bought = [2, 2]
        clock.t = 420.0
        cycle.step()
        logic_calls = [c for c in poster.calls if c[0] == "/activate_mission_flow"]
        logics = [json.loads(c[1].decode())["logic"] for c in logic_calls]
        self.assertEqual(logics.count("logic/missions/survival/attack_level_2_id_1.logic"), 2)

    def test_reset_clears_state(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        cycle.step()  # started
        cycle.buy(2)
        cycle._resolve_orders()
        self.assertTrue(cycle.active)
        self.assertEqual(cycle.bought, [2])
        cycle.reset()
        self.assertFalse(cycle.active)
        self.assertEqual(cycle.level, 1)
        self.assertIsNone(cycle.next_attack_at)
        self.assertIsNone(cycle.next_difficulty_at)
        self.assertEqual(cycle.bought, [])
        self.assertEqual(cycle.orders, [])

    def test_sync_reset_resets_on_epoch_change(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        cycle.step()  # started
        self.assertTrue(cycle.active)
        poster.reset_epoch = 1
        cycle.sync_reset()
        self.assertFalse(cycle.active)
        self.assertEqual(cycle._reset_epoch, 1)


class TestAttackCycleDifficultyTimer(unittest.TestCase):
    """Issue #778: Difficulty-Level laeuft auf einem eigenen, vom
    Wellen-Feuer-Intervall entkoppelten Timer."""

    def _cycle(self, poster, interval=420.0, difficulty_interval=200.0, clock=None):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=interval,
            difficulty_interval_s=difficulty_interval,
            _poster=poster,
            _clock=clock or FakeClock(),
        )

    def test_level_increases_every_200s_independent_of_wave_interval(self):
        """Wellen-Feuer-Intervall bleibt riesig (feuert nie) -> jede
        Level-Erhoehung stammt garantiert vom Difficulty-Timer allein."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock, interval=1e9, difficulty_interval=200.0)
        cycle.step()  # started, level 1
        self.assertEqual(cycle.level, 1)

        clock.t = 199.0
        cycle.step()
        self.assertEqual(cycle.level, 1)

        clock.t = 200.0
        cycle.step()
        self.assertEqual(cycle.level, 2)

        clock.t = 400.0
        cycle.step()
        self.assertEqual(cycle.level, 3)

        clock.t = 600.0
        cycle.step()
        self.assertEqual(cycle.level, 4)

    def test_difficulty_caps_at_max_level(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock, interval=1e9, difficulty_interval=200.0)
        cycle.step()  # started
        for _ in range(20):
            clock.t += 200.0
            cycle.step()
        self.assertEqual(cycle.level, 9)

    def test_wave_fires_with_level_reached_via_difficulty_timer(self):
        """Ein spaeter feuernder Wave-Tick nutzt das inzwischen (rein
        zeitbasiert) gestiegene Level, ohne es selbst zu erhoehen."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock, interval=420.0, difficulty_interval=200.0)
        cycle.step()  # started, level 1

        clock.t = 420.0
        self.assertEqual(cycle.step(), "attack")
        # Bis t=420 sind zwei Difficulty-Ticks faellig (200, 400) -> Level 3.
        self.assertEqual(cycle.level, 3)
        logic_calls = [c for c in poster.calls if c[0] == "/activate_mission_flow"]
        # Attack-Count (Issue #802): Level 3 feuert maxAttackCountPerDifficulty[3] = 2
        # Natural-Wellen (Default-Profil).
        self.assertEqual(len(logic_calls), 2)
        self.assertIn("attack_level_3_id_1.logic", logic_calls[0][1].decode())

    def test_status_reports_seconds_to_next_difficulty(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock, interval=1e9, difficulty_interval=200.0)
        cycle.step()  # started
        clock.t = 50.0
        s = cycle.status()
        self.assertEqual(s["seconds_to_next_difficulty"], 150.0)
        self.assertEqual(s["difficulty_interval_s"], 200.0)


class TestPersona(unittest.TestCase):
    """Persona = Folge von Attacken; je Attack eine Liste der vom Gegner
    gekauften Extra-Wellen (mehrere erlaubt). Geschichtet auf die Natural
    Waves. Laeuft aus (kein Loop)."""

    def _cycle(self, poster, persona=None, clock=None):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=420.0,
            difficulty_interval_s=1e9,
            persona=persona,
            _poster=poster,
            _clock=clock or FakeClock(),
        )

    def _fire_logics(self, poster):
        return [json.loads(c[1].decode())["logic"] for c in poster.calls if c[0] == "/activate_mission_flow"]

    def test_persona_extra_fires_then_runs_out(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, persona=[wave_count(3), wave_count(5)], clock=clock)
        cycle.step()  # started

        clock.t = 420.0
        cycle.step()  # attack 1
        logics = self._fire_logics(poster)
        self.assertEqual(
            logics,
            [
                "logic/missions/survival/attack_level_1_id_1.logic",  # natural
                "logic/missions/survival/attack_level_3_id_1.logic",  # persona wave3
            ],
        )

        clock.t = 840.0
        cycle.step()  # attack 2
        logics = self._fire_logics(poster)
        self.assertIn("logic/missions/survival/attack_level_5_id_1.logic", logics)

        clock.t = 1260.0
        cycle.step()  # attack 3 -> Persona laeuft aus
        logics = self._fire_logics(poster)
        self.assertEqual(len(logics), 5)  # 2 + 2 + 1: attack 3 ohne Extra
        # Persona-Wave 3 kam nur in attack 1, nie wieder (ausgelaufen).
        self.assertEqual(logics.count("logic/missions/survival/attack_level_3_id_1.logic"), 1)

    def test_persona_multiple_waves_per_attack(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, persona=[wave_count(2, 3), wave_count(5)], clock=clock)
        cycle.step()  # started

        clock.t = 420.0
        cycle.step()  # attack 1: natural + wave2 + wave3
        logics = self._fire_logics(poster)
        self.assertEqual(
            logics,
            [
                "logic/missions/survival/attack_level_1_id_1.logic",  # natural
                "logic/missions/survival/attack_level_2_id_1.logic",  # persona wave2
                "logic/missions/survival/attack_level_3_id_1.logic",  # persona wave3
            ],
        )

        clock.t = 840.0
        cycle.step()  # attack 2: natural + wave5
        self.assertEqual(len(self._fire_logics(poster)), 5)  # 3 + 2

    def test_persona_duplicate_waves(self):
        """Der Gegner kann dieselbe Welle mehrfach senden (z. B. wave1 3x) —
        kein Dedup: jede Nennung feuert als eigener activate_mission_flow-Call."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, persona=[wave_count(1, 1, 1)], clock=clock)
        cycle.step()  # started

        clock.t = 420.0
        cycle.step()  # attack 1: natural(level1) + wave1 x3
        logics = self._fire_logics(poster)
        self.assertEqual(
            logics.count("logic/missions/survival/attack_level_1_id_1.logic"), 4
        )  # 1 natural + 3 persona wave1
        self.assertEqual(len(logics), 4)

    def test_persona_none_entry_skips_extra(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, persona=[wave_count(), wave_count(2)], clock=clock)
        cycle.step()  # started

        clock.t = 420.0
        cycle.step()  # attack 1: kein Extra
        self.assertEqual(len(self._fire_logics(poster)), 1)

        clock.t = 840.0
        cycle.step()  # attack 2: wave2
        logics = self._fire_logics(poster)
        self.assertIn("logic/missions/survival/attack_level_2_id_1.logic", logics)

    def test_no_persona_fires_natural_only(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, persona=None, clock=clock)
        cycle.step()
        clock.t = 420.0
        cycle.step()
        self.assertEqual(len(self._fire_logics(poster)), 1)

    def test_persona_resets_on_reset(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, persona=[wave_count(3), wave_count(5)], clock=clock)
        cycle.step()
        clock.t = 420.0
        cycle.step()  # attack_index = 1
        self.assertEqual(cycle.attack_index, 1)
        cycle.reset()
        self.assertEqual(cycle.attack_index, 0)


class TestSendYourself(unittest.TestCase):
    """Routing eigener Kaeufe: on = lokal feuern (Default), off = Carbonium
    abziehen, aber Welle tracken statt lokal feuern (spaeter Server B)."""

    def _cycle(self, poster, send_yourself=True, clock=None):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=420.0,
            difficulty_interval_s=1e9,
            send_yourself=send_yourself,
            _poster=poster,
            _clock=clock or FakeClock(),
        )

    def test_off_tracks_outgoing_instead_of_bought(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        cycle = self._cycle(poster, send_yourself=False)
        cycle.step()  # started
        cycle.buy(3)
        cycle._resolve_orders()
        self.assertEqual(cycle.bought, [])
        self.assertEqual([o["level"] for o in cycle.outgoing], [3])

    def test_off_still_deducts_carbonium(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        cycle = self._cycle(poster, send_yourself=False)
        cycle.step()
        cycle.buy(3)
        cycle._resolve_orders()
        spend_calls = [c for c in poster.calls if c[0] == "/try_spend"]
        self.assertEqual(len(spend_calls), 1)

    def test_off_does_not_fire_locally(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, send_yourself=False, clock=clock)
        cycle.step()  # started
        cycle.buy(3)
        cycle._resolve_orders()
        clock.t = 420.0
        cycle.step()  # attack
        logics = [json.loads(c[1].decode())["logic"] for c in poster.calls if c[0] == "/activate_mission_flow"]
        self.assertEqual(logics, ["logic/missions/survival/attack_level_1_id_1.logic"])


class TestLoadPersonas(unittest.TestCase):
    def _write(self, content):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        return path

    def test_load_valid(self):
        aggro = [wave_count(3), wave_count(5), wave_count(7)]
        ruhig = [wave_count(), wave_count(2), wave_count()]
        path = self._write(json.dumps({"personas": {"aggro": aggro, "ruhig": ruhig}}))
        try:
            self.assertEqual(load_personas(path), {"aggro": aggro, "ruhig": ruhig})
        finally:
            os.unlink(path)

    def test_load_multi_wave_attack(self):
        aggro = [wave_count(1, 3), wave_count(5)]
        path = self._write(json.dumps({"personas": {"aggro": aggro}}))
        try:
            self.assertEqual(load_personas(path), {"aggro": aggro})
        finally:
            os.unlink(path)

    def test_load_negative_count_rejected(self):
        path = self._write('{"personas": {"bad": [[-1]]}}')
        try:
            with self.assertRaises(ValueError):
                load_personas(path)
        finally:
            os.unlink(path)

    def test_load_empty(self):
        path = self._write('{"personas": {}}')
        try:
            self.assertEqual(load_personas(path), {})
        finally:
            os.unlink(path)

    def test_load_missing_file(self):
        with self.assertRaises(OSError):
            load_personas("/nonexistent/personas.json")


class TestSyncPersonas(unittest.TestCase):
    """sync_personas(): pollt GET /personas (Bridge) und uebernimmt aktive
    Persona + send_yourself zur Laufzeit (CLI-Flags nur Start-Fallback)."""

    def _cycle(self, getter_resp, clock=None):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=420.0,
            difficulty_interval_s=1e9,
            _poster=FakePoster('{"ok":true,"hq_hp":100.0}'),
            _getter=lambda path: (200, getter_resp),
            _clock=clock or FakeClock(),
        )

    def test_applies_active_persona_and_send_yourself(self):
        aggro = [wave_count(3), wave_count(5)]
        ruhig = [wave_count(), wave_count(2)]
        resp = json.dumps({"personas": {"aggro": aggro, "ruhig": ruhig}, "active": "aggro", "send_yourself": False})
        cycle = self._cycle(resp)
        cycle.sync_personas()
        self.assertEqual(cycle.persona, aggro)
        self.assertEqual(cycle.persona_name, "aggro")
        self.assertFalse(cycle.send_yourself)

    def test_no_active_persona(self):
        aggro = [wave_count(3), wave_count(5)]
        resp = json.dumps({"personas": {"aggro": aggro}, "active": "", "send_yourself": True})
        cycle = self._cycle(resp)
        cycle.sync_personas()
        self.assertIsNone(cycle.persona)
        self.assertEqual(cycle.persona_name, "")
        self.assertTrue(cycle.send_yourself)

    def test_invalid_level_rejected(self):
        resp = '{"personas":{"bad":[[-1]]},"active":"bad","send_yourself":true}'
        cycle = self._cycle(resp)
        cycle.sync_personas()
        self.assertIsNone(cycle.persona)
        self.assertEqual(cycle.persona_name, "")

    def test_non_200_ignored(self):
        cycle = AttackCycle(
            "http://127.0.0.1:9001",
            _poster=FakePoster('{"ok":true,"hq_hp":100.0}'),
            _getter=lambda path: (500, '{"ok":false}'),
        )
        cycle.sync_personas()
        self.assertIsNone(cycle.persona)
        self.assertTrue(cycle.send_yourself)


class TestTimersAndPreview(unittest.TestCase):
    def test_sync_difficulty_interval(self):
        def poster(path, body):
            if path == "/difficulty_interval":
                return (200, '{"ok":true,"difficulty_interval_s":100}')
            return (404, "{}")

        cycle = AttackCycle("http://x", _poster=poster)
        cycle.sync_difficulty_interval()
        self.assertEqual(cycle.difficulty_interval_s, 100.0)

    def test_status_next_attack_preview(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = AttackCycle(
            "http://x",
            interval_s=420.0,
            difficulty_interval_s=1e9,
            persona=[wave_count(2, 3), wave_count(5)],
            _poster=poster,
            _clock=clock,
        )
        cycle.step()  # started, attack_index=0
        cycle.buy(1)
        cycle._resolve_orders()  # bought=[1]
        na = cycle.status()["next_attack"]
        self.assertEqual(na["natural"], 1)
        self.assertEqual(na["self"], [1])
        self.assertEqual(na["enemy"], [2, 3])


class TestCountHelpers(unittest.TestCase):
    def test_normalize_pads_to_nine(self):
        self.assertEqual(_normalize_counts([1, 2]), [1, 2, 0, 0, 0, 0, 0, 0, 0])

    def test_normalize_rejects_negative(self):
        self.assertIsNone(_normalize_counts([-1]))

    def test_normalize_rejects_non_int(self):
        self.assertIsNone(_normalize_counts(["a"]))

    def test_expand_counts(self):
        self.assertEqual(_expand_counts([3, 0, 1, 0, 0, 0, 0, 0, 0]), [1, 1, 1, 3])

    def test_expand_empty(self):
        self.assertEqual(_expand_counts([0, 0, 0, 0, 0, 0, 0, 0, 0]), [])


class TestDifficultyRules(unittest.TestCase):
    """Konfigurierbare Wellen-Feuer-Regeln (Attack-Count/Boss/Extra/MP),
    Spiegel der Base-Game-DOM (Issue #802)."""

    def test_default_profile_attack_count(self):
        self.assertEqual(DIFFICULTY_RULES["default"]["max_attack_count"], [1, 2, 2, 3, 3, 3, 3, 3, 4])

    def test_normal_profile_attack_count(self):
        self.assertEqual(DIFFICULTY_RULES["normal"]["max_attack_count"], [1, 2, 2, 2, 2, 2, 3, 3, 3])

    def test_normalize_pads_to_max_level(self):
        rules = _normalize_difficulty_rules({"max_attack_count": [1, 2]}, max_level=4)
        self.assertEqual(rules["max_attack_count"], [1, 2, 2, 2])

    def test_normalize_truncates_to_max_level(self):
        rules = _normalize_difficulty_rules({"max_attack_count": [1, 2, 2, 3, 3]}, max_level=3)
        self.assertEqual(rules["max_attack_count"], [1, 2, 2])

    def test_normalize_rejects_negative_count(self):
        with self.assertRaises(ValueError):
            _normalize_difficulty_rules({"max_attack_count": [-1]}, max_level=9)

    def test_normalize_rejects_bad_threshold(self):
        with self.assertRaises(ValueError):
            _normalize_difficulty_rules({"boss_min_level": 0}, max_level=9)

    def test_wave_plan_default_curve(self):
        cycle = AttackCycle("http://x", _poster=lambda *a: (200, "{}"))
        self.assertEqual(cycle._wave_plan(1)["natural_count"], 1)
        self.assertEqual(cycle._wave_plan(2)["natural_count"], 2)
        self.assertEqual(cycle._wave_plan(9)["natural_count"], 4)

    def test_wave_plan_boss_extra_mp_thresholds(self):
        rules = {
            "max_attack_count": [1] * 9,
            "boss_min_level": 8,
            "extra_min_level": 3,
            "extra_count": 2,
            "mp_min_level": 6,
        }
        cycle = AttackCycle("http://x", difficulty_rules=rules, _poster=lambda *a: (200, "{}"))
        self.assertEqual(
            cycle._wave_plan(7), {"natural_count": 1, "boss": False, "extra_count": 2, "mp": True}
        )
        self.assertEqual(
            cycle._wave_plan(8), {"natural_count": 1, "boss": True, "extra_count": 2, "mp": True}
        )
        self.assertEqual(
            cycle._wave_plan(2), {"natural_count": 1, "boss": False, "extra_count": 0, "mp": False}
        )


class TestWaveFiring(unittest.TestCase):
    """Feuer-Pfad: Attack-Count, Boss, Extra- und MP-Wellen (Issue #802).
    Der Difficulty-Timer bleibt bewusst riesig (1e9); das Level wird pro Test
    direkt gesetzt, um die FEUER-Komposition deterministisch zu pruefen."""

    def _cycle(self, poster, difficulty_rules=None, difficulty_profile=DEFAULT_DIFFICULTY_PROFILE, clock=None):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=420.0,
            difficulty_interval_s=1e9,
            difficulty_profile=difficulty_profile,
            difficulty_rules=difficulty_rules,
            _poster=poster,
            _clock=clock or FakeClock(),
        )

    def _fire_logics(self, poster):
        return [json.loads(c[1].decode())["logic"] for c in poster.calls if c[0] == "/activate_mission_flow"]

    def test_fires_attack_count_natural_waves(self):
        """Level 9 (default) feuert 4 Natural-Wellen statt 1 (Issue #802)."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        cycle.step()  # started
        cycle.level = 9
        clock.t = 420.0
        cycle.step()  # attack
        logics = self._fire_logics(poster)
        # 4 Natural-Wellen + 1 Boss (boss_min_level=8).
        self.assertEqual(logics.count("logic/missions/survival/attack_level_8_id_1.logic"), 4)
        self.assertEqual(logics.count(BOSS_LOGIC), 1)
        self.assertEqual(len(logics), 5)

    def test_normal_profile_fires_two_waves_at_level_2(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, difficulty_profile="normal", clock=clock)
        cycle.step()  # started
        cycle.level = 2
        clock.t = 420.0
        cycle.step()  # attack
        logics = self._fire_logics(poster)
        self.assertEqual(len(logics), 2)
        self.assertEqual(logics.count("logic/missions/survival/attack_level_2_id_1.logic"), 2)

    def test_fires_boss_at_level_8(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        cycle.step()  # started
        cycle.level = 8
        clock.t = 420.0
        cycle.step()  # attack
        logics = self._fire_logics(poster)
        # Level 8: 3 Natural-Wellen + 1 Boss.
        self.assertEqual(len(logics), 4)
        self.assertEqual(logics.count(BOSS_LOGIC), 1)
        self.assertEqual(logics.count("logic/missions/survival/attack_level_8_id_1.logic"), 3)

    def test_no_boss_below_level_8(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        cycle.step()  # started
        cycle.level = 7
        clock.t = 420.0
        cycle.step()  # attack
        self.assertEqual(self._fire_logics(poster).count(BOSS_LOGIC), 0)

    def test_fires_extra_and_mp_waves_when_configured(self):
        rules = {
            "max_attack_count": [1] * 9,
            "boss_min_level": None,
            "extra_min_level": 3,
            "extra_count": 2,
            "mp_min_level": 6,
        }
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, difficulty_rules=rules, clock=clock)
        cycle.step()  # started
        cycle.level = 7
        clock.t = 420.0
        cycle.step()  # attack
        logics = self._fire_logics(poster)
        # 1 Natural (level 7) + 2 Extra (level 7) + 1 MP-Elite-Boss.
        self.assertEqual(len(logics), 4)
        self.assertEqual(logics.count("logic/missions/survival/attack_level_7_id_1.logic"), 3)
        self.assertEqual(logics.count(BOSS_LOGIC), 1)

    def test_extra_and_mp_off_by_default(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        cycle.step()  # started
        cycle.level = 9
        clock.t = 420.0
        cycle.step()  # attack
        logics = self._fire_logics(poster)
        # Default-Profil: keine Extra-Wellen (random Event) und kein MP-Boss
        # (Solo-first, spaeterer Schritt). Nur 4 Natural + 1 Boss (Level 9).
        self.assertEqual(len(logics), 5)
        self.assertEqual(logics.count(BOSS_LOGIC), 1)

    def test_last_fire_reports_wave_composition(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        cycle.step()  # started
        cycle.level = 8
        clock.t = 420.0
        cycle.step()  # attack
        lf = cycle.last_fire
        self.assertEqual(lf["natural_count"], 3)
        self.assertTrue(lf["boss"])
        self.assertEqual(lf["extra_count"], 0)
        self.assertFalse(lf["mp"])


if __name__ == "__main__":
    unittest.main()
