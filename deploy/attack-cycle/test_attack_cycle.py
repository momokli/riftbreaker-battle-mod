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
    CREATURE_ATTACK_EVENTS,
    DEFAULT_DIFFICULTY_PROFILE,
    DIFFICULTY_RULES,
    DEFAULT_DIFFICULTY_INTERVAL_FIRST_S,
    DEFAULT_WARMUP_S,
    STATE_GAME_OVER,
    STATE_PAUSED,
    STATE_RUNNING,
    STATE_WARMUP,
    WAVE_COUNT,
    _expand_counts,
    _normalize_counts,
    _normalize_difficulty_rules,
    _normalize_creature_events,
    _normalize_toggles,
    _pick_creature_event,
    load_personas,
    parse_hq_alive,
    parse_send_level,
)


# Tests, die nur den Feuer-/Difficulty-Pfad pruefen, setzen das Warmup auf 0s:
# Start-Signal + Warmup-Ende fallen dann auf denselben Tick (t) -> RUNNING.
WARMUP_OFF = 0.0


def wave_count(*levels):
    """Baue einen WAVE_COUNT-Count-Vektor aus Leveln (Duplikate = count)."""
    counts = [0] * WAVE_COUNT
    for lvl in levels:
        counts[lvl - 1] += 1
    return counts


def start_cycle(cycle):
    """Start-Signal + Warmup-Ende (WARMUP_OFF) -> RUNNING.

    Ersetzt das alte ``cycle.step()  # started`` (HQ-Trigger). Liefert die
    step()-Aktion des Warmup-Endes zurueck ("started" oder "game_over").
    """
    cycle.signal_start()
    return cycle.step()


class FakePoster:
    def __init__(self, state_resp='{"ok":true,"hq_hp":100.0}'):
        self.calls = []
        self.state_resp = state_resp
        self.spend_ok = True
        self.spend_resp = '{"ok":true,"balance":50000000}'
        self.reset_epoch = 0
        # Antwort auf POST /start — wie die Bridge: quittiert JEDEN Aufruf
        # mit start:true (pipe_bridge.c handle_post_start).
        self.start_resp = '{"ok":true,"start":true}'

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
        if path == "/start":
            return (200, self.start_resp)
        return (404, '{"ok":false}')


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeGetter:
    """GET-Gegenstueck zum FakePoster (liefert einen festen Body)."""

    def __init__(self, body='{}', status=200):
        self.body = body
        self.status = status
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        return (self.status, self.body)


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
    # difficulty_interval (erster Schritt) default hier bewusst riesig: die
    # meisten Tests unten pruefen NUR den Wellen-Feuer-Pfad und sollen vom
    # (jetzt entkoppelten) Difficulty-Timer unberuehrt bleiben. Tests, die den
    # Timer selbst pruefen, ueberschreiben ihn explizit (siehe
    # TestAttackCycleDifficultyTimer unten).
    def _cycle(self, poster, interval=420.0, difficulty_interval_first=1e9, difficulty_schedule=None, clock=None,
               warmup_s=WARMUP_OFF, **kwargs):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=interval,
            difficulty_interval_first_s=difficulty_interval_first,
            difficulty_schedule=difficulty_schedule,
            warmup_s=warmup_s,
            _poster=poster,
            _clock=clock or FakeClock(),
            **kwargs,
        )

    def test_paused_does_not_auto_start_on_hq(self):
        """Der Server bootet PAUSED: das HQ ist NICHT mehr Start-Trigger und
        in PAUSED wird nicht einmal get_state gepollt (keine Timer)."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        self.assertEqual(cycle.state, STATE_PAUSED)
        self.assertIsNone(cycle.step())
        clock.t = 10000.0
        self.assertIsNone(cycle.step())
        self.assertEqual(cycle.state, STATE_PAUSED)
        self.assertFalse(cycle.active)
        self.assertEqual(poster.calls, [])

    def test_start_signal_enters_warmup(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock, warmup_s=120.0)
        self.assertTrue(cycle.signal_start())
        self.assertEqual(cycle.state, STATE_WARMUP)
        self.assertEqual(cycle.next_warmup_end, 120.0)
        self.assertFalse(cycle.active)  # feuert erst in RUNNING
        self.assertFalse(cycle.signal_start())  # idempotent
        self.assertEqual(cycle.state, STATE_WARMUP)

    def test_warmup_runs_full_then_running(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock, warmup_s=120.0,
                           difficulty_interval_first=DEFAULT_DIFFICULTY_INTERVAL_FIRST_S)
        cycle.signal_start()
        clock.t = 119.0
        self.assertIsNone(cycle.step())  # Warmup laeuft IMMER voll
        self.assertEqual(cycle.state, STATE_WARMUP)
        clock.t = 120.0
        self.assertEqual(cycle.step(), "started")
        self.assertEqual(cycle.state, STATE_RUNNING)
        self.assertTrue(cycle.active)
        self.assertEqual(cycle.level, 1)
        self.assertEqual(cycle.next_attack_at, 540.0)  # 120 (Warmup) + 420
        self.assertEqual(cycle.next_difficulty_at, 320.0)  # 120 + 200

    def test_warmup_end_without_hq_game_over(self):
        poster = FakePoster('{"ok":true,"hq_hp":null}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock, warmup_s=120.0)
        cycle.signal_start()
        clock.t = 120.0
        self.assertEqual(cycle.step(), "game_over")
        self.assertEqual(cycle.state, STATE_GAME_OVER)
        self.assertFalse(cycle.active)
        self.assertIsNone(cycle.next_attack_at)
        # GAME_OVER ist terminal bis reset().
        clock.t = 1000.0
        self.assertIsNone(cycle.step())
        self.assertEqual(cycle.state, STATE_GAME_OVER)

    def test_reset_from_game_over_back_to_paused(self):
        poster = FakePoster('{"ok":true,"hq_hp":null}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock, warmup_s=120.0)
        cycle.signal_start()
        clock.t = 120.0
        cycle.step()  # game_over
        cycle.reset()
        self.assertEqual(cycle.state, STATE_PAUSED)
        self.assertFalse(cycle.active)
        self.assertIsNone(cycle.next_warmup_end)
        self.assertFalse(cycle.ready)
        # Neue Runde: wieder ueber das Start-Signal.
        self.assertTrue(cycle.signal_start())
        self.assertEqual(cycle.state, STATE_WARMUP)

    def test_fire_natural_wave_after_interval(self):
        """Ein Wellen-Feuer-Tick erhoeht das Level NICHT mehr selbst (Issue #778,
        der Difficulty-Timer ist hier bewusst riesig und laeuft in diesem Test
        nie ab)."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        self.assertEqual(start_cycle(cycle), "started")
        clock.t = 420.0
        self.assertEqual(cycle.step(), "attack")
        logic_calls = [c for c in poster.calls if c[0] == "/activate_mission_flow"]
        self.assertEqual(len(logic_calls), 1)
        self.assertIn("attack_level_1_id_1.logic", logic_calls[0][1].decode())
        self.assertEqual(cycle.level, 1)

    def test_hq_destroyed_in_running_game_over(self):
        """HQ-Tod in RUNNING -> sofort game_over (D2, keine Gnadenfrist)."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        start_cycle(cycle)
        poster.state_resp = '{"ok":true,"hq_hp":0}'
        clock.t = 10.0
        self.assertEqual(cycle.step(), "game_over")
        self.assertEqual(cycle.state, STATE_GAME_OVER)
        self.assertFalse(cycle.active)
        # Keine weitere Attack nach dem HQ-Tod.
        clock.t = 420.0
        self.assertIsNone(cycle.step())
        logic_calls = [c for c in poster.calls if c[0] == "/activate_mission_flow"]
        self.assertEqual(logic_calls, [])

    def test_buy_stacks_and_fires_with_natural(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        start_cycle(cycle)

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
        cycle = self._cycle(poster, clock=clock, difficulty_schedule=[1.0] * 8)
        start_cycle(cycle)  # started (level 1)
        for _ in range(20):
            clock.t += 1.0
            cycle.step()
        self.assertEqual(cycle.level, 9)

    def test_multiple_buys_same_level_stack(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        start_cycle(cycle)
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
        start_cycle(cycle)
        cycle.buy(2)
        cycle._resolve_orders()
        self.assertTrue(cycle.active)
        self.assertEqual(cycle.bought, [2])
        cycle.reset()
        self.assertFalse(cycle.active)
        self.assertEqual(cycle.state, STATE_PAUSED)
        self.assertEqual(cycle.level, 1)
        self.assertIsNone(cycle.next_attack_at)
        self.assertIsNone(cycle.next_difficulty_at)
        self.assertEqual(cycle.bought, [])
        self.assertEqual(cycle.orders, [])

    def test_sync_reset_resets_on_epoch_change(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        start_cycle(cycle)
        self.assertTrue(cycle.active)
        poster.reset_epoch = 1
        cycle.sync_reset()
        self.assertFalse(cycle.active)
        self.assertEqual(cycle.state, STATE_PAUSED)
        self.assertEqual(cycle._reset_epoch, 1)


class TestAttackCycleDifficultyTimer(unittest.TestCase):
    """Difficulty-Level laeuft auf einem eigenen, vom Wellen-Feuer-Intervall
    entkoppelten Timer — und folgt der Base-Game-Kurve (§3.1 DOM_REPLICA.md):
    200s fuer Schritt 1→2, danach 600s je Schritt (2→3 … 8→9)."""

    def _cycle(self, poster, interval=420.0, difficulty_interval_first=200.0, difficulty_schedule=None, clock=None):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=interval,
            difficulty_interval_first_s=difficulty_interval_first,
            difficulty_schedule=difficulty_schedule,
            creature_events=[],  # Event-Layer aus (reiner Difficulty-/Wellen-Pfad)
            warmup_s=WARMUP_OFF,
            _poster=poster,
            _clock=clock or FakeClock(),
        )

    def test_default_schedule_is_base_game_curve(self):
        cycle = AttackCycle("http://x", _poster=lambda *a: (200, "{}"))
        self.assertEqual(
            cycle.difficulty_schedule,
            [200.0, 600.0, 600.0, 600.0, 600.0, 600.0, 600.0, 600.0],
        )

    def test_level_follows_base_game_curve(self):
        """Wellen-Feuer-Intervall bleibt riesig (feuert nie) -> jede
        Level-Erhoehung stammt garantiert vom Difficulty-Timer allein."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock, interval=1e9)
        start_cycle(cycle)  # started, level 1
        self.assertEqual(cycle.level, 1)

        clock.t = 199.0
        cycle.step()
        self.assertEqual(cycle.level, 1)  # 1→2 erst bei 200s

        clock.t = 200.0
        cycle.step()
        self.assertEqual(cycle.level, 2)

        clock.t = 799.0
        cycle.step()
        self.assertEqual(cycle.level, 2)  # 2→3 erst bei 800s (200 + 600)

        clock.t = 800.0
        cycle.step()
        self.assertEqual(cycle.level, 3)

        clock.t = 1400.0
        cycle.step()
        self.assertEqual(cycle.level, 4)  # 3→4 bei 1400s

    def test_flat_schedule_override_still_works(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock, interval=1e9, difficulty_schedule=[200.0] * 8)
        start_cycle(cycle)
        clock.t = 200.0
        cycle.step()
        self.assertEqual(cycle.level, 2)
        clock.t = 400.0
        cycle.step()
        self.assertEqual(cycle.level, 3)  # flach: +1 je 200s

    def test_difficulty_caps_at_max_level(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock, interval=1e9)
        start_cycle(cycle)
        # 8 Schritte: 200 + 7*600 = 4400s bis Level 9.
        clock.t = 10000.0
        cycle.step()
        self.assertEqual(cycle.level, 9)

    def test_wave_fires_with_level_reached_via_difficulty_timer(self):
        """Ein spaeter feuernder Wave-Tick nutzt das inzwischen (rein
        zeitbasiert) gestiegene Level, ohne es selbst zu erhoehen."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock, interval=420.0)
        start_cycle(cycle)  # started, level 1

        clock.t = 420.0
        self.assertEqual(cycle.step(), "attack")
        # Bis t=420 ist ein Difficulty-Tick faellig (200) -> Level 2.
        self.assertEqual(cycle.level, 2)
        logic_calls = [c for c in poster.calls if c[0] == "/activate_mission_flow"]
        # Attack-Count (#802): Level 2 feuert maxAttackCountPerDifficulty[2] = 2
        # Natural-Wellen (normal: Boss erst ab L5).
        self.assertEqual(len(logic_calls), 2)
        self.assertIn("attack_level_2_id_1.logic", logic_calls[0][1].decode())

    def test_status_reports_seconds_to_next_difficulty(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock, interval=1e9)
        start_cycle(cycle)
        clock.t = 50.0
        s = cycle.status()
        self.assertEqual(s["seconds_to_next_difficulty"], 150.0)
        self.assertEqual(
            s["difficulty_schedule"],
            [200.0, 600.0, 600.0, 600.0, 600.0, 600.0, 600.0, 600.0],
        )


class TestPersona(unittest.TestCase):
    """Persona = Folge von Attacken; je Attack eine Liste der vom Gegner
    gekauften Extra-Wellen (mehrere erlaubt). Geschichtet auf die Natural
    Waves. Laeuft aus (kein Loop)."""

    def _cycle(self, poster, persona=None, clock=None):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=420.0,
            difficulty_interval_first_s=1e9,
            persona=persona,
            warmup_s=WARMUP_OFF,
            _poster=poster,
            _clock=clock or FakeClock(),
        )

    def _fire_logics(self, poster):
        return [json.loads(c[1].decode())["logic"] for c in poster.calls if c[0] == "/activate_mission_flow"]

    def test_persona_extra_fires_then_runs_out(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, persona=[wave_count(3), wave_count(5)], clock=clock)
        start_cycle(cycle)

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
        start_cycle(cycle)

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
        start_cycle(cycle)

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
        start_cycle(cycle)

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
        start_cycle(cycle)
        clock.t = 420.0
        cycle.step()
        self.assertEqual(len(self._fire_logics(poster)), 1)

    def test_persona_resets_on_reset(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, persona=[wave_count(3), wave_count(5)], clock=clock)
        start_cycle(cycle)
        clock.t = 420.0
        cycle.step()  # attack_index = 1
        self.assertEqual(cycle.attack_index, 1)
        cycle.reset()
        self.assertEqual(cycle.attack_index, 0)


class TestSendYourself(unittest.TestCase):
    """Routing eigener Kaeufe (Toggles send_yourself/send_enemy): `buy` wird
    IMMER ausgeloest + getrackt; beim Feuern entscheidet der Toggle, wohin die
    gekaufte Welle geht. off = Carbonium abgezogen, nicht lokal feuern (sondern
    tracken, spaeter Server B)."""

    def _cycle(self, poster, send_yourself=True, clock=None, toggles=None):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=420.0,
            difficulty_interval_first_s=1e9,
            send_yourself=send_yourself,
            toggles=toggles,
            warmup_s=WARMUP_OFF,
            _poster=poster,
            _clock=clock or FakeClock(),
        )

    def test_off_still_tracks_bought(self):
        """buy wird immer getrackt, auch wenn send_yourself off ist."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        cycle = self._cycle(poster, send_yourself=False)
        cycle.buy(3)
        cycle._resolve_orders()
        self.assertEqual(cycle.bought, [3])
        self.assertEqual(cycle.outgoing, [])  # erst beim Feuern geroutet

    def test_off_tracks_outgoing_at_fire_time(self):
        """send_yourself off: die bezahlte Welle wird beim Feuern nur in
        `outgoing` getrackt und NICHT lokal gefeuert."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, send_yourself=False, clock=clock)
        start_cycle(cycle)
        cycle.buy(3)
        cycle._resolve_orders()
        clock.t = 420.0
        cycle.step()  # attack
        self.assertEqual(cycle.bought, [])
        self.assertEqual([o["level"] for o in cycle.outgoing], [3])
        self.assertEqual(cycle.enemy_outgoing, [])  # send_enemy default off

    def test_off_still_deducts_carbonium(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        cycle = self._cycle(poster, send_yourself=False)
        cycle.buy(3)
        cycle._resolve_orders()
        spend_calls = [c for c in poster.calls if c[0] == "/try_spend"]
        self.assertEqual(len(spend_calls), 1)

    def test_off_does_not_fire_locally(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, send_yourself=False, clock=clock)
        start_cycle(cycle)
        cycle.buy(3)
        cycle._resolve_orders()
        clock.t = 420.0
        cycle.step()  # attack
        logics = [json.loads(c[1].decode())["logic"] for c in poster.calls if c[0] == "/activate_mission_flow"]
        self.assertEqual(logics, ["logic/missions/survival/attack_level_1_id_1.logic"])

    def test_on_fires_locally_not_enemy(self):
        """send_yourself on + send_enemy off: lokal feuern, kein Gegner-Zaehler."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, send_yourself=True, clock=clock)
        start_cycle(cycle)
        cycle.buy(3)
        cycle._resolve_orders()
        clock.t = 420.0
        cycle.step()
        logics = [json.loads(c[1].decode())["logic"] for c in poster.calls if c[0] == "/activate_mission_flow"]
        self.assertIn("logic/missions/survival/attack_level_3_id_1.logic", logics)
        self.assertEqual(cycle.enemy_outgoing, [])
        self.assertEqual(cycle.outgoing, [])

    def test_enemy_only_fires_nowhere_but_counts(self):
        """send_yourself off + send_enemy on: nicht lokal feuern, aber als
        Gegner-Send zaehlen (SOLO: kein zweiter Server)."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(
            poster,
            clock=clock,
            toggles={"send_yourself": False, "send_enemy": True},
        )
        start_cycle(cycle)
        cycle.buy(3)
        cycle._resolve_orders()
        clock.t = 420.0
        cycle.step()
        logics = [json.loads(c[1].decode())["logic"] for c in poster.calls if c[0] == "/activate_mission_flow"]
        self.assertEqual(logics, ["logic/missions/survival/attack_level_1_id_1.logic"])
        self.assertEqual([o["level"] for o in cycle.enemy_outgoing], [3])
        self.assertEqual([o["level"] for o in cycle.outgoing], [3])

    def test_both_toggles_fire_and_count(self):
        """Beide Toggles an (Spec C2): die Welle geht an sich selbst UND an den
        Gegner — zwei unabhaengige Zaehler/Senken."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(
            poster,
            clock=clock,
            toggles={"send_yourself": True, "send_enemy": True},
        )
        start_cycle(cycle)
        cycle.buy(3)
        cycle._resolve_orders()
        clock.t = 420.0
        cycle.step()
        logics = [json.loads(c[1].decode())["logic"] for c in poster.calls if c[0] == "/activate_mission_flow"]
        self.assertEqual(logics.count("logic/missions/survival/attack_level_3_id_1.logic"), 1)
        self.assertEqual([o["level"] for o in cycle.enemy_outgoing], [3])
        self.assertEqual(cycle.outgoing, [])  # lokal gefeuert -> kein outgoing


class TestGameFlowToggles(unittest.TestCase):
    """Die vier Toggles (Spec §3) steuern die Attack-Zusammensetzung —
    unabhaengig voneinander. Der Attack-Timer laeuft dabei immer weiter."""

    def _cycle(self, poster, clock, toggles=None, persona=None):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=420.0,
            difficulty_interval_first_s=1e9,
            persona=persona,
            toggles=toggles,
            warmup_s=WARMUP_OFF,
            _poster=poster,
            _clock=clock,
            _rng=lambda: 0.0,
        )

    def _fire_calls(self, poster):
        return [c for c in poster.calls if c[0] == "/activate_mission_flow"]

    def _logics(self, poster):
        return [json.loads(c[1].decode())["logic"] for c in self._fire_calls(poster)]

    def test_defaults_are_natural_persona_self_on_enemy_off(self):
        cycle = self._cycle(FakePoster(), FakeClock(0.0))
        self.assertEqual(
            cycle.toggles,
            {"natural": True, "persona": True, "send_yourself": True, "send_enemy": False},
        )
        self.assertTrue(cycle.send_yourself)  # Legacy-Property = Toggle

    def test_natural_off_fires_no_natural_wave_or_boss(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock, toggles={"natural": False})
        start_cycle(cycle)
        cycle.level = 9  # ohne Toggle: 3 Natural + Boss
        clock.t = 420.0
        self.assertEqual(cycle.step(), "attack")  # Timer laeuft weiter
        self.assertEqual(self._fire_calls(poster), [])
        self.assertEqual(cycle.last_fire["natural_count"], 0)
        self.assertFalse(cycle.last_fire["boss"])

    def test_natural_off_fires_no_creature_event(self):
        """Das Event-Fenster (prepare) liegt bei 273s; natural off -> kein Event."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock, toggles={"natural": False})
        start_cycle(cycle)
        cycle.level = 2  # ohne Toggle: shegret normal
        clock.t = 273.0
        self.assertIsNone(cycle.step())
        self.assertEqual(self._fire_calls(poster), [])
        self.assertIsNone(cycle.last_event)

    def test_natural_on_fires_creature_event(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock)  # natural default on
        start_cycle(cycle)
        cycle.level = 2
        clock.t = 273.0
        cycle.step()
        self.assertEqual(self._logics(poster), ["logic/event/shegret_attack.logic"])
        self.assertEqual(cycle.last_event["name"], "shegret_attack")

    def test_persona_off_fires_no_persona_waves(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(
            poster, clock, toggles={"persona": False}, persona=[wave_count(3), wave_count(5)]
        )
        start_cycle(cycle)
        clock.t = 420.0
        cycle.step()
        self.assertEqual(self._logics(poster), ["logic/missions/survival/attack_level_1_id_1.logic"])
        self.assertEqual(cycle.attack_index, 1)  # Attack zaehlt trotzdem
        self.assertEqual(cycle.history[-1]["enemy"], [])

    def test_persona_on_fires_persona_waves(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock, persona=[wave_count(3)])
        start_cycle(cycle)
        clock.t = 420.0
        cycle.step()
        self.assertIn("logic/missions/survival/attack_level_3_id_1.logic", self._logics(poster))

    def test_all_off_keeps_attack_timer_running(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock, toggles={"natural": False, "persona": False})
        start_cycle(cycle)
        clock.t = 420.0
        self.assertEqual(cycle.step(), "attack")
        self.assertEqual(self._fire_calls(poster), [])
        clock.t = 840.0
        self.assertEqual(cycle.step(), "attack")  # kein Stillstand


class TestGameConfigSync(unittest.TestCase):
    """sync_game_config()/sync_start(): Bridge-Config (mode/warmup_s/Toggles) +
    Start-Signal (start/start_epoch) defensiv uebernehmen."""

    def _cycle(self, getter_resp, getter_status=200, poster=None, **kwargs):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=420.0,
            difficulty_interval_first_s=1e9,
            _poster=poster or FakePoster('{"ok":true,"hq_hp":100.0}'),
            _getter=FakeGetter(getter_resp, getter_status),
            _clock=FakeClock(0.0),
            **kwargs,
        )

    def test_applies_mode_warmup_and_toggles(self):
        body = json.dumps(
            {
                "mode": "vs",
                "warmup_s": 60.5,
                "natural": False,
                "persona": False,
                "send_yourself": False,
                "send_enemy": True,
            }
        )
        cycle = self._cycle(body)
        cycle.sync_game_config()
        self.assertEqual(cycle.mode, "vs")
        self.assertEqual(cycle.warmup_s, 60.5)
        self.assertFalse(cycle.toggles["natural"])
        self.assertFalse(cycle.toggles["persona"])
        self.assertFalse(cycle.toggles["send_yourself"])
        self.assertTrue(cycle.toggles["send_enemy"])

    def test_invalid_values_keep_old(self):
        cycle = self._cycle('{"mode":"nope","warmup_s":"60","natural":"yes"}')
        cycle.sync_game_config()
        self.assertEqual(cycle.mode, "solo")
        self.assertEqual(cycle.warmup_s, DEFAULT_WARMUP_S)
        self.assertTrue(cycle.toggles["natural"])  # ungueltig -> unveraendert

    def test_partial_payload_only_touches_given_fields(self):
        cycle = self._cycle('{"warmup_s":5}')
        cycle.sync_game_config()
        self.assertEqual(cycle.warmup_s, 5.0)
        self.assertEqual(cycle.mode, "solo")
        self.assertEqual(cycle.toggles, {
            "natural": True, "persona": True, "send_yourself": True, "send_enemy": False,
        })

    def test_non_200_ignored(self):
        cycle = self._cycle('{"mode":"vs"}', getter_status=500)
        cycle.sync_game_config()
        self.assertEqual(cycle.mode, "solo")

    def test_invalid_json_ignored(self):
        cycle = self._cycle("not json")
        cycle.sync_game_config()
        self.assertEqual(cycle.mode, "solo")
        self.assertEqual(cycle.state, STATE_PAUSED)

    def test_start_flag_triggers_warmup(self):
        cycle = self._cycle('{"start":true,"warmup_s":30}')
        cycle.sync_game_config()
        self.assertTrue(cycle.sync_start())
        self.assertEqual(cycle.state, STATE_WARMUP)
        self.assertEqual(cycle.next_warmup_end, 30.0)
        # Signal ist verbraucht -> kein zweiter Uebergang.
        self.assertFalse(cycle.sync_start())

    def test_start_epoch_is_edge_triggered(self):
        cycle = self._cycle('{"start_epoch":1}')
        cycle.sync_game_config()
        self.assertTrue(cycle.sync_start())
        cycle.reset()
        # gleicher epoch-Wert -> kein neues Signal
        cycle.sync_game_config()
        self.assertFalse(cycle.sync_start())
        self.assertEqual(cycle.state, STATE_PAUSED)
        # neuer Wert -> neues Start-Signal
        cycle._getter = FakeGetter('{"start_epoch":2}')
        cycle.sync_game_config()
        self.assertTrue(cycle.sync_start())
        self.assertEqual(cycle.state, STATE_WARMUP)

    def test_ready_flag_does_not_start(self):
        cycle = self._cycle('{"ready":true}')
        cycle.sync_game_config()
        self.assertTrue(cycle.ready)
        self.assertFalse(cycle.sync_start())
        self.assertEqual(cycle.state, STATE_PAUSED)

    def test_poll_start_off_by_default(self):
        """Die Bridge quittiert jeden POST /start mit start:true -> ohne
        explizites poll_start darf NICHTS automatisch starten."""
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        cycle = self._cycle('{}', poster=poster)
        cycle.sync_game_config()
        self.assertFalse(cycle.sync_start())
        self.assertEqual(cycle.state, STATE_PAUSED)
        self.assertEqual([c for c in poster.calls if c[0] == "/start"], [])

    def test_poll_start_when_enabled(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        poster.start_resp = '{"ok":true,"start":true}'
        cycle = self._cycle('{}', poster=poster, poll_start=True)
        cycle.sync_game_config()
        self.assertTrue(cycle.sync_start())
        self.assertEqual(cycle.state, STATE_WARMUP)
        self.assertEqual([c for c in poster.calls if c[0] == "/start"], [("/start", b"{}")])

    def test_status_reports_flow_fields(self):
        cycle = self._cycle('{"mode":"vs","warmup_s":90,"ready":true,"send_enemy":true}')
        cycle.sync_game_config()
        st = cycle.status()
        self.assertEqual(st["state"], STATE_PAUSED)
        self.assertEqual(st["mode"], "vs")
        self.assertEqual(st["warmup_s"], 90.0)
        self.assertTrue(st["ready"])
        self.assertTrue(st["toggles"]["send_enemy"])
        self.assertIsNone(st["seconds_to_warmup_end"])

    def test_set_ready_toggles_status(self):
        cycle = self._cycle('{}')
        self.assertFalse(cycle.status()["ready"])
        self.assertTrue(cycle.set_ready(True))
        self.assertTrue(cycle.status()["ready"])
        self.assertFalse(cycle.set_ready(False))

    def test_normalize_toggles_ignores_non_bool(self):
        # 0/1/"yes" sind keine bool-Werte -> Default bleibt.
        self.assertTrue(_normalize_toggles({"natural": 0})["natural"])
        self.assertTrue(_normalize_toggles("nope")["natural"])
        self.assertTrue(_normalize_toggles({"send_enemy": True})["send_enemy"])
        self.assertFalse(_normalize_toggles({"send_yourself": False})["send_yourself"])


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
            difficulty_interval_first_s=1e9,
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


class TestSyncNaturalAttackRules(unittest.TestCase):
    """sync_natural_attack_rules(): pollt GET /natural_attack_rules (Bridge)
    und uebernimmt Attack-Count/Boss je Level + Creature-Events + Event-Offset
    zur Laufzeit (Issue #819)."""

    def _cycle(self, getter_resp):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=420.0,
            difficulty_interval_first_s=1e9,
            _poster=FakePoster('{"ok":true,"hq_hp":100.0}'),
            _getter=lambda path: (200, getter_resp),
            _clock=FakeClock(),
        )

    def _rules_json(self, **overrides):
        base = {
            "max_attack_count": [1, 1, 1, 1, 1, 1, 1, 1, 1],
            "boss_min_level": 2,
            "creature_events": [
                {"name": "shegret_attack",
                 "logic": "logic/event/shegret_attack.logic",
                 "min_level": 2, "max_level": 4,
                 "attack_strength": "normal", "weight": 3},
            ],
            "event_offset_fraction": 0.5,
        }
        base.update(overrides)
        return json.dumps(base)

    def test_applies_rules(self):
        cycle = self._cycle(self._rules_json())
        cycle.sync_natural_attack_rules()
        self.assertEqual(cycle.difficulty_rules["max_attack_count"], [1] * 9)
        self.assertEqual(cycle.difficulty_rules["boss_min_level"], 2)
        self.assertEqual(len(cycle.creature_events), 1)
        self.assertEqual(cycle.creature_events[0]["attack_strength"], "normal")
        self.assertEqual(cycle.event_offset_s, 210.0)  # 420 * 0.5

    def test_boss_none_supported(self):
        cycle = self._cycle(self._rules_json(boss_min_level=None))
        cycle.sync_natural_attack_rules()
        self.assertIsNone(cycle.difficulty_rules["boss_min_level"])

    def test_invalid_attack_count_ignored(self):
        cycle = self._cycle(self._rules_json(max_attack_count="nope"))
        before = cycle.difficulty_rules["max_attack_count"]
        cycle.sync_natural_attack_rules()
        self.assertEqual(cycle.difficulty_rules["max_attack_count"], before)

    def test_invalid_events_ignored(self):
        cycle = self._cycle(self._rules_json(creature_events="nope"))
        before = len(cycle.creature_events)
        cycle.sync_natural_attack_rules()
        self.assertEqual(len(cycle.creature_events), before)

    def test_non_200_ignored(self):
        cycle = AttackCycle(
            "http://127.0.0.1:9001",
            _poster=FakePoster('{"ok":true,"hq_hp":100.0}'),
            _getter=lambda path: (500, '{"ok":false}'),
        )
        before = cycle.difficulty_rules["max_attack_count"]
        cycle.sync_natural_attack_rules()
        self.assertEqual(cycle.difficulty_rules["max_attack_count"], before)


class TestTimersAndPreview(unittest.TestCase):
    def test_sync_difficulty_interval(self):
        def poster(path, body):
            if path == "/difficulty_interval":
                return (200, '{"ok":true,"difficulty_interval_first_s":100,"difficulty_interval_subsequent_s":600}')
            return (404, "{}")

        cycle = AttackCycle("http://x", _poster=poster)
        cycle.sync_difficulty_interval()
        self.assertEqual(cycle.difficulty_interval_first_s, 100.0)
        self.assertEqual(cycle.difficulty_interval_subsequent_s, 600.0)
        self.assertEqual(
            cycle.difficulty_schedule,
            [100.0, 600.0, 600.0, 600.0, 600.0, 600.0, 600.0, 600.0],
        )

    def test_sync_difficulty_two_values(self):
        def poster(path, body):
            if path == "/difficulty_interval":
                return (200, '{"ok":true,"difficulty_interval_first_s":100,"difficulty_interval_subsequent_s":300}')
            return (404, "{}")

        cycle = AttackCycle("http://x", _poster=poster)
        cycle.sync_difficulty_interval()
        self.assertEqual(
            cycle.difficulty_schedule,
            [100.0, 300.0, 300.0, 300.0, 300.0, 300.0, 300.0, 300.0],
        )

    def test_sync_difficulty_schedule_full_list(self):
        def poster(path, body):
            if path == "/difficulty_interval":
                return (200, '{"ok":true,"difficulty_schedule":[100,200,300,400,500,600,700,800]}')
            return (404, "{}")

        cycle = AttackCycle("http://x", _poster=poster)
        cycle.sync_difficulty_interval()
        self.assertEqual(
            cycle.difficulty_schedule,
            [100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0],
        )

    def test_status_next_attack_preview(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = AttackCycle(
            "http://x",
            interval_s=420.0,
            difficulty_interval_first_s=1e9,
            persona=[wave_count(2, 3), wave_count(5)],
            warmup_s=WARMUP_OFF,
            _poster=poster,
            _clock=clock,
        )
        start_cycle(cycle)  # started, attack_index=0
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
        cycle = AttackCycle("http://x", difficulty_profile="default", _poster=lambda *a: (200, "{}"))
        self.assertEqual(cycle._wave_plan(1)["natural_count"], 1)
        self.assertEqual(cycle._wave_plan(2)["natural_count"], 2)
        self.assertEqual(cycle._wave_plan(9)["natural_count"], 4)

    def test_wave_plan_normal_curve(self):
        cycle = AttackCycle("http://x", _poster=lambda *a: (200, "{}"))
        self.assertEqual(cycle._wave_plan(9)["natural_count"], 3)

    def test_wave_plan_boss_threshold(self):
        rules = {
            "max_attack_count": [1] * 9,
            "boss_min_level": 2,
        }
        cycle = AttackCycle("http://x", difficulty_rules=rules, _poster=lambda *a: (200, "{}"))
        self.assertEqual(cycle._wave_plan(1), {"natural_count": 1, "boss": False})
        self.assertEqual(cycle._wave_plan(2), {"natural_count": 1, "boss": True})
        self.assertEqual(cycle._wave_plan(9), {"natural_count": 1, "boss": True})


class TestWaveFiring(unittest.TestCase):
    """Feuer-Pfad: Attack-Count + Elite-Boss (Issue #802).
    Der Difficulty-Timer bleibt bewusst riesig (1e9); das Level wird pro Test
    direkt gesetzt, um die FEUER-Komposition deterministisch zu pruefen."""

    def _cycle(self, poster, difficulty_rules=None, difficulty_profile=DEFAULT_DIFFICULTY_PROFILE, clock=None):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=420.0,
            difficulty_interval_first_s=1e9,
            difficulty_interval_subsequent_s=1e9,
            difficulty_profile=difficulty_profile,
            difficulty_rules=difficulty_rules,
            creature_events=[],  # Event-Layer aus (reiner Wellen-Feuer-Pfad)
            warmup_s=WARMUP_OFF,
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
        start_cycle(cycle)
        cycle.level = 9
        clock.t = 420.0
        cycle.step()  # attack
        logics = self._fire_logics(poster)
        # 3 Natural-Wellen + 1 Boss (normal: maxAttackCount[9]=3, Boss ab L5).
        self.assertEqual(logics.count("logic/missions/survival/attack_level_8_id_1.logic"), 3)
        self.assertEqual(logics.count(BOSS_LOGIC), 1)
        self.assertEqual(len(logics), 4)

    def test_normal_profile_fires_two_waves_at_level_2(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, difficulty_profile="normal", clock=clock)
        start_cycle(cycle)
        cycle.level = 2
        clock.t = 420.0
        cycle.step()  # attack
        logics = self._fire_logics(poster)
        self.assertEqual(len(logics), 2)
        self.assertEqual(logics.count("logic/missions/survival/attack_level_2_id_1.logic"), 2)

    def test_fires_boss_at_level_5(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        start_cycle(cycle)
        cycle.level = 5
        clock.t = 420.0
        cycle.step()  # attack
        logics = self._fire_logics(poster)
        # Level 5 (normal): 2 Natural-Wellen + 1 Elite-Boss.
        self.assertEqual(len(logics), 3)
        self.assertEqual(logics.count(BOSS_LOGIC), 1)
        self.assertEqual(logics.count("logic/missions/survival/attack_level_5_id_1.logic"), 2)

    def test_no_boss_below_level_5(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        start_cycle(cycle)
        cycle.level = 4
        clock.t = 420.0
        cycle.step()  # attack
        self.assertEqual(self._fire_logics(poster).count(BOSS_LOGIC), 0)

    def test_last_fire_reports_wave_composition(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        start_cycle(cycle)
        cycle.level = 8
        clock.t = 420.0
        cycle.step()  # attack
        lf = cycle.last_fire
        self.assertEqual(lf["natural_count"], 3)
        self.assertTrue(lf["boss"])


class TestCreatureAttackEvents(unittest.TestCase):
    """Creature-Attack-Event-Layer (Issue #816): Auswahl + Eskalation + Timing."""

    def _cycle(self, poster, clock=None, rng=None, creature_events=None):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=420.0,
            difficulty_interval_first_s=1e9,
            difficulty_interval_subsequent_s=1e9,
            creature_events=creature_events,
            warmup_s=WARMUP_OFF,
            _poster=poster,
            _clock=clock or FakeClock(),
            _rng=rng or (lambda: 0.0),
        )

    def _fire_calls(self, poster):
        return [json.loads(c[1].decode()) for c in poster.calls if c[0] == "/activate_mission_flow"]

    # --- reine Auswahl-Funktion -------------------------------------

    def test_level_1_no_event(self):
        self.assertIsNone(_pick_creature_event(1, CREATURE_ATTACK_EVENTS, lambda: 0.0))

    def test_level_2_shegret_normal_only(self):
        # Level 2: nur shegret (normal) im Pool (kermon ab 4, phirian ab 3).
        e = _pick_creature_event(2, CREATURE_ATTACK_EVENTS, lambda: 0.0)
        self.assertEqual(e["name"], "shegret_attack")
        self.assertEqual(e["attack_strength"], "normal")

    def test_level_3_phirian_and_shegret(self):
        # Level 3: shegret normal (w3) + phirian (w1). rng=0.9 -> phirian.
        e = _pick_creature_event(3, CREATURE_ATTACK_EVENTS, lambda: 0.9)
        self.assertEqual(e["name"], "phirian_attack")
        self.assertIsNone(e["attack_strength"])

    def test_level_6_escalates_to_hard(self):
        # Level 6: shegret hard (L5-7) + kermon hard (L6-7) + phirian.
        # rng=0.0 -> shegret (erstes im Pool, w3) -> hard.
        e = _pick_creature_event(6, CREATURE_ATTACK_EVENTS, lambda: 0.0)
        self.assertEqual(e["name"], "shegret_attack")
        self.assertEqual(e["attack_strength"], "hard")

    def test_level_8_escalates_to_very_hard(self):
        e = _pick_creature_event(8, CREATURE_ATTACK_EVENTS, lambda: 0.0)
        self.assertEqual(e["name"], "shegret_attack")
        self.assertEqual(e["attack_strength"], "very_hard")

    def test_no_normal_or_hard_strength_above_level_7(self):
        # Ab Level 8 gibt es keinen normal/hard shegret/kermon mehr (nur very_hard + phirian).
        for level in (8, 9):
            pool = [e for e in CREATURE_ATTACK_EVENTS if e["min_level"] <= level <= e["max_level"]]
            strengths = {e["attack_strength"] for e in pool if e["attack_strength"] is not None}
            self.assertEqual(strengths, {"very_hard"})

    def test_weighted_distribution(self):
        # Level 3: shegret (w3) + phirian (w1). rng nahe 0 -> shegret, nahe 1 -> phirian.
        self.assertEqual(_pick_creature_event(3, CREATURE_ATTACK_EVENTS, lambda: 0.01)["name"], "shegret_attack")
        self.assertEqual(_pick_creature_event(3, CREATURE_ATTACK_EVENTS, lambda: 0.99)["name"], "phirian_attack")

    def test_normalize_creature_events_rejects_bad(self):
        with self.assertRaises(ValueError):
            _normalize_creature_events("not a list")
        with self.assertRaises(ValueError):
            _normalize_creature_events([{"name": "x", "logic": "l", "min_level": 2, "max_level": 1}])
        with self.assertRaises(ValueError):
            _normalize_creature_events([
                {"name": "x", "logic": "l", "min_level": 1, "max_level": 2,
                 "attack_strength": "nope"},
            ])

    # --- Integration: Event-Timer im step ---------------------------

    def test_event_fires_in_prepare_window_before_attack(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        self.assertEqual(start_cycle(cycle), "started")
        self.assertEqual(cycle.next_event_at, 420.0 - 0.35 * 420.0)  # 273.0
        cycle.level = 2  # damit ein Event existiert (shegret normal)
        clock.t = 273.0
        self.assertIsNone(cycle.step())  # Event, aber KEIN Attack (erst bei 420)
        calls = self._fire_calls(poster)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["logic"], "logic/event/shegret_attack.logic")
        self.assertEqual(calls[0]["attack_strength"], "normal")
        self.assertEqual(cycle.last_event["name"], "shegret_attack")

    def test_no_event_before_prepare_window(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        start_cycle(cycle)
        cycle.level = 2
        clock.t = 100.0  # vor dem prepare-Fenster (273.0)
        cycle.step()
        self.assertEqual(len(self._fire_calls(poster)), 0)

    def test_event_reports_in_status(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        start_cycle(cycle)
        cycle.level = 2
        clock.t = 273.0
        cycle.step()
        st = cycle.status()
        self.assertIsNotNone(st["last_event"])
        self.assertIsNotNone(st["seconds_to_next_event"])


if __name__ == "__main__":
    unittest.main()
