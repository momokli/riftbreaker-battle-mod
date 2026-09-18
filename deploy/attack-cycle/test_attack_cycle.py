#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-Tests für den Attack-Cycle (PoC).

Rein stdlib + hermetic: parse_hq_alive, parse_send_level und die
AttackCycle-Zustandsmaschine laufen gegen Fake-Poster/Fake-Clock —
kein Netz, kein Spiel, kein DOM.
"""

import json
import unittest

from attack_cycle import AttackCycle, parse_hq_alive, parse_send_level


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
    def _cycle(self, poster, interval=420.0, clock=None):
        return AttackCycle(
            "http://127.0.0.1:9001",
            interval_s=interval,
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
        cycle = self._cycle(poster, clock=clock)
        self.assertEqual(cycle.step(), "started")
        self.assertTrue(cycle.active)
        self.assertEqual(cycle.level, 1)
        self.assertEqual(cycle.next_attack_at, 420.0)

    def test_fire_natural_wave_after_interval(self):
        poster = FakePoster('{"ok":true,"hq_hp":100.0}')
        clock = FakeClock(0.0)
        cycle = self._cycle(poster, clock=clock)
        cycle.step()  # started
        clock.t = 420.0
        self.assertEqual(cycle.step(), "attack")
        logic_calls = [c for c in poster.calls if c[0] == "/activate_mission_flow"]
        self.assertEqual(len(logic_calls), 1)
        self.assertIn("attack_level_1_id_1.logic", logic_calls[0][1].decode())
        # Level erhoeht sich auf 2.
        self.assertEqual(cycle.level, 2)

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
        cycle = self._cycle(poster, clock=clock, interval=1.0)
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


if __name__ == "__main__":
    unittest.main()
