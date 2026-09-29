#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-Tests fuer den Chat-Announcer (Issue #940).

Rein stdlib + hermetic: der Detektor-Kern laeuft gegen synthetische
``status``-Dicts, der Service gegen Fake-Getter/Fake-Poster — kein Netz, kein
Spiel, kein DOM.
"""

import unittest

from announcer import (
    Announcer,
    AnnouncerService,
    FormatConfig,
    fmt_attack_next,
    fmt_incoming,
    fmt_round_end,
    fmt_warmup,
    fmt_warmup_go,
    incoming_counts,
)


def warmup(remaining, attack_index=0):
    return {"state": "warmup", "seconds_to_warmup_end": remaining, "attack_index": attack_index}


def running(remaining_attack, attack_index=1, last_fire=None):
    return {
        "state": "running",
        "seconds_to_next_attack": remaining_attack,
        "attack_index": attack_index,
        "last_fire": last_fire,
    }


def texts(events):
    return [e["text"] for e in events]


def kinds(events):
    return [e["kind"] for e in events]


class TestFormatter(unittest.TestCase):
    def test_warmup_short(self):
        self.assertEqual(fmt_warmup(FormatConfig("short"), 180), "warmup 3:00")
        self.assertEqual(fmt_warmup(FormatConfig("short"), 30), "warmup 0:30")
        self.assertEqual(fmt_warmup(FormatConfig("short"), 10), "warmup 0:10")

    def test_warmup_go_short(self):
        self.assertEqual(fmt_warmup_go(FormatConfig("short")), "GO")

    def test_attack_next_short(self):
        self.assertEqual(fmt_attack_next(FormatConfig("short"), 60), "next attack in 60s")
        self.assertEqual(fmt_attack_next(FormatConfig("short"), 10), "next attack in 10s")

    def test_incoming_short(self):
        self.assertEqual(fmt_incoming(FormatConfig("short"), {1: 3, 4: 1}), "incoming [W1]x3 [W4]x1")

    def test_incoming_sorted_by_level(self):
        self.assertEqual(fmt_incoming(FormatConfig("short"), {9: 1, 2: 2}), "incoming [W2]x2 [W9]x1")

    def test_round_end_short(self):
        self.assertEqual(fmt_round_end(FormatConfig("short"), "hq_destroyed"), "round over - HQ destroyed")
        self.assertEqual(fmt_round_end(FormatConfig("short"), "no_hq"), "round over - no HQ")

    def test_style_validated(self):
        with self.assertRaises(ValueError):
            FormatConfig("fancy")

    def test_aligned_is_placeholder(self):
        # aligned ist ungeeicht (#939) —- muss aber stabil formatieren.
        self.assertEqual(fmt_warmup(FormatConfig("aligned"), 180), "warmup ··· 3:00")
        self.assertEqual(fmt_round_end(FormatConfig("aligned"), "no_hq"), "round over ··· no HQ")

    def test_no_format_literals_in_events(self):
        # Format-Stabilitaet: die kurze Form ist ASCII-nah.
        a = Announcer()
        ev = a.step(warmup(180), 1000.0)
        self.assertEqual(texts(ev), ["warmup 3:00"])


class TestIncomingCounts(unittest.TestCase):
    def test_natural_only(self):
        self.assertEqual(incoming_counts({"natural_level": 1, "natural_count": 3}), {1: 3})

    def test_natural_default_count(self):
        self.assertEqual(incoming_counts({"natural_level": 4}), {4: 1})

    def test_persona_and_sent(self):
        lf = {"natural_level": 1, "natural_count": 3, "persona_levels": [4], "sent_levels": [4, 9]}
        self.assertEqual(incoming_counts(lf), {1: 3, 4: 2, 9: 1})

    def test_none(self):
        self.assertEqual(incoming_counts(None), {})
        self.assertEqual(incoming_counts({}), {})


class TestAnnouncerWarmup(unittest.TestCase):
    def test_warmup_thresholds_each_once(self):
        a = Announcer()
        got = []
        # Restzeit faellt ueber alle Schwellen.
        for rem in (200, 180, 179, 120, 119, 60, 59, 30, 29, 10, 9):
            got += texts(a.step(warmup(rem), 0.0))
        self.assertEqual(got, ["warmup 3:00", "warmup 2:00", "warmup 1:00", "warmup 0:30", "warmup 0:10"])

    def test_no_doppel_send(self):
        a = Announcer()
        first = a.step(warmup(180), 0.0)
        self.assertEqual(texts(first), ["warmup 3:00"])
        # gleiche Restzeit viele Ticks -> keine Wiederholung
        for _ in range(50):
            self.assertEqual(a.step(warmup(180), 0.0), [])

    def test_no_spam_many_ticks(self):
        a = Announcer()
        sent = []
        rem = 200.0
        while rem >= 0:
            sent += texts(a.step(warmup(rem), 0.0))
            rem -= 0.5
        # 5 Schwellen + GO
        self.assertEqual(sent, ["warmup 3:00", "warmup 2:00", "warmup 1:00", "warmup 0:30", "warmup 0:10", "GO"])

    def test_gap_collapses_to_one_message(self):
        # Poll-Aussetzer: von 200 direkt auf 5 -> genau EINE Nachricht,
        # danach keine Rueckstands-Flut.
        a = Announcer()
        ev = a.step(warmup(200), 0.0)
        self.assertEqual(texts(ev), [])  # 200s: noch keine Schwelle
        ev = a.step(warmup(5), 1.0)
        self.assertEqual(texts(ev), ["warmup 3:00"])  # groesste ueberschrittene
        # Folge-Ticks identische Restzeit -> nichts mehr
        self.assertEqual(a.step(warmup(5), 2.0), [])

    def test_epoch_reset_on_new_warmup(self):
        a = Announcer()
        a.step(warmup(180), 0.0)
        a.step(warmup(120), 1.0)
        # Runde laeuft, dann neues Warmup -> Schwellen wieder frei
        a.step({"state": "running", "seconds_to_next_attack": 100, "attack_index": 1}, 2.0)
        a.step({"state": "game_over"}, 3.0)
        ev = a.step(warmup(180), 4.0)
        self.assertEqual(texts(ev), ["warmup 3:00"])

    def test_go_at_edge_only_once(self):
        a = Announcer()
        a.step(warmup(10), 0.0)
        ev = a.step({"state": "running", "seconds_to_next_attack": 400, "attack_index": 1}, 1.0)
        self.assertIn("GO", texts(ev))
        # kein zweites GO in running
        ev = a.step({"state": "running", "seconds_to_next_attack": 390, "attack_index": 1}, 2.0)
        self.assertNotIn("GO", texts(ev))

    def test_go_when_warmup_end_reached(self):
        a = Announcer()
        ev = a.step(warmup(0), 0.0)
        self.assertEqual(texts(ev), ["GO"])
        self.assertEqual(a.step(warmup(0), 1.0), [])


class TestAnnouncerAttack(unittest.TestCase):
    def test_attack_thresholds_each_once(self):
        a = Announcer()
        # erst warmup->running-Edge (GO), dann Attack-Schwellen
        a.step(warmup(0), 0.0)
        got = []
        for rem in (70, 60, 59, 30, 29, 10, 9):
            got += texts(a.step(running(rem), 0.0))
        self.assertEqual(got, ["next attack in 60s", "next attack in 30s", "next attack in 10s"])

    def test_attack_epoch_by_attack_index(self):
        a = Announcer()
        a.step(running(60, attack_index=1), 0.0)
        self.assertEqual(texts(a.step(running(60, attack_index=1), 1.0)), [])
        # neuer Angriff (attack_index springt) -> Schwellen wieder frei
        ev = a.step(running(60, attack_index=2), 2.0)
        self.assertIn("next attack in 60s", texts(ev))


class TestAnnouncerIncoming(unittest.TestCase):
    def test_incoming_on_attack_index_jump(self):
        a = Announcer()
        a.step(running(10, attack_index=1), 0.0)
        lf = {"natural_level": 1, "natural_count": 3, "persona_levels": [4], "sent_levels": []}
        ev = a.step(running(400, attack_index=2, last_fire=lf), 1.0)
        self.assertIn("incoming [W1]x3 [W4]x1", texts(ev))

    def test_no_incoming_on_first_sight(self):
        a = Announcer()
        lf = {"natural_level": 1, "natural_count": 3}
        ev = a.step(running(400, attack_index=5, last_fire=lf), 0.0)
        self.assertNotIn("incoming [W1]x3", texts(ev))

    def test_no_incoming_in_warmup(self):
        a = Announcer()
        a.step(running(400, attack_index=1), 0.0)
        lf = {"natural_level": 1, "natural_count": 3}
        # attack_index springt, aber Zustand warmup -> kein incoming (vgl. Reset)
        ev = a.step({"state": "warmup", "seconds_to_warmup_end": 180, "attack_index": 2, "last_fire": lf}, 1.0)
        self.assertNotIn("incoming [W1]x3", texts(ev))


class TestAnnouncerRoundEnd(unittest.TestCase):
    def test_round_end_from_running(self):
        a = Announcer()
        a.step(running(400, attack_index=1), 0.0)
        ev = a.step({"state": "game_over"}, 1.0)
        self.assertEqual(texts(ev), ["round over - HQ destroyed"])

    def test_round_end_from_warmup(self):
        a = Announcer()
        a.step(warmup(180), 0.0)
        ev = a.step({"state": "game_over"}, 1.0)
        self.assertEqual(texts(ev), ["round over - no HQ"])

    def test_no_round_end_without_prev(self):
        a = Announcer()
        ev = a.step({"state": "game_over"}, 0.0)
        self.assertEqual(ev, [])

    def test_round_end_type_is_announcement(self):
        a = Announcer()
        a.step(running(400, attack_index=1), 0.0)
        ev = a.step({"state": "game_over"}, 1.0)
        self.assertEqual(ev[0]["type"], "announcement")


class TestAnnouncerPaused(unittest.TestCase):
    def test_no_send_in_paused(self):
        a = Announcer()
        ev = a.step({"state": "paused", "seconds_to_warmup_end": 180, "attack_index": 0}, 0.0)
        self.assertEqual(ev, [])
        self.assertEqual(a.step({"state": "paused", "seconds_to_warmup_end": 10}, 1.0), [])

    def test_paused_then_warmup_still_announces(self):
        a = Announcer()
        a.step({"state": "paused", "attack_index": 0}, 0.0)
        ev = a.step(warmup(180), 1.0)
        self.assertEqual(texts(ev), ["warmup 3:00"])


class TestAnnouncerService(unittest.TestCase):
    def _service(self, status_body, sent, dry_run=False):
        calls = []

        def getter(url, timeout):
            calls.append(("GET", url))
            return 200, status_body

        def poster(url, payload, timeout):
            sent.append((url, payload))
            return 200, '{"ok":true}'

        svc = AnnouncerService(
            "http://ac:9102",
            "http://bridge:9001",
            dry_run=dry_run,
            _getter=getter,
            _poster=poster,
        )
        return svc, calls

    def test_send_posts_to_bridge_send_chat(self):
        sent = []
        svc, calls = self._service('{"state":"warmup","seconds_to_warmup_end":180,"attack_index":0}', sent)
        svc.poll_once()
        self.assertEqual(len(sent), 1)
        url, payload = sent[0]
        self.assertTrue(url.endswith("/send_chat"))
        self.assertEqual(payload["text"], "warmup 3:00")
        self.assertIn("type", payload)

    def test_dry_run_does_not_send(self):
        sent = []
        svc, _ = self._service('{"state":"warmup","seconds_to_warmup_end":180,"attack_index":0}', sent, dry_run=True)
        svc.poll_once()
        self.assertEqual(sent, [])

    def test_fallback_to_attack_status(self):
        calls = []

        def getter(url, timeout):
            calls.append(url)
            if url.endswith("/status"):
                return 503, "nope"
            return 200, '{"status":{"state":"warmup","seconds_to_warmup_end":180,"attack_index":0}}'

        svc = AnnouncerService("http://ac:9102", "http://bridge:9001", _getter=getter, _poster=lambda *a: (200, "{}"))
        self.assertEqual(texts(svc.poll_once()), ["warmup 3:00"])
        self.assertTrue(calls[0].endswith("/status"))
        self.assertTrue(calls[1].endswith("/attack_status"))

    def test_http_error_keeps_running(self):
        sent = []

        def getter(url, timeout):
            return 0, "connection refused"

        svc = AnnouncerService("http://ac:9102", "http://bridge:9001", _getter=getter, _poster=lambda *a: (200, "{}"))
        self.assertEqual(svc.poll_once(), [])

    def test_send_error_logged_not_raised(self):
        sent_payloads = []

        def getter(url, timeout):
            return 200, '{"state":"warmup","seconds_to_warmup_end":180,"attack_index":0}'

        def poster(url, payload, timeout):
            sent_payloads.append(payload)
            return 500, '{"ok":false}'

        svc = AnnouncerService("http://ac:9102", "http://bridge:9001", _getter=getter, _poster=poster)
        svc.poll_once()  # darf nicht raisen
        self.assertEqual(len(sent_payloads), 1)

    def test_once_loop_terminates(self):
        ticks = []

        def getter(url, timeout):
            return 200, '{"state":"warmup","seconds_to_warmup_end":180,"attack_index":0}'

        svc = AnnouncerService(
            "http://ac:9102",
            "http://bridge:9001",
            once=True,
            _getter=getter,
            _poster=lambda *a: (200, "{}"),
            _sleep=lambda s: ticks.append(s),
        )
        svc.run()
        self.assertEqual(ticks, [])


if __name__ == "__main__":
    unittest.main()