#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_referee.py - Unit-Tests fuer den Referee-Rueckkanal (Issue #268).

Testet den Relay-Rueckkanal ohne Spiel und ohne Netz deterministisch:
  - map_referee_event(): [RBBATTLE]-Log-Zeile -> Referee-Event (Event-In).
  - parse_line(): der Payload der uebersprungenen Typen (wave/hq_dead/mod_load)
    wird mitgeliefert (sonst waere der Rueckkanal blind).
  - Transport: POST /referee/event traegt den richtigen Body; GET /referee/poll
    legt die Commands als Dispatch an die rbbridge-Pipe (Command-Out), inkl.
    `cmd_id`-Dedup (ein per Push zugestellter Command wird nicht erneut
    dispatcht).

Die Entscheidung selbst (ready -> rb_wave 1, wave_done -> naechste Welle,
hq_destroyed -> restart) liegt im Server (`tournament/src/referee.rs`, ohne
Player getestet). Hier wird nur die Uebersetzung + Zustellung geprueft.

Nur Standardbibliothek: python3 -m unittest test_referee -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import relay  # noqa: E402


class MapRefereeEventTest(unittest.TestCase):
    """Event-In: Log-Event -> Referee-Event (deterministisch)."""

    def test_wave_done_maps_to_wave_done(self):
        self.assertEqual(
            relay.map_referee_event('wave', {'level': 3, 'status': 'done'}, 'A'),
            {'world': 'A', 'type': 'wave_done', 'level': 3},
        )

    def test_wave_start_is_not_a_signal(self):
        # Die start-Zeile desselben Events bleibt wirkungslos (nur status=done).
        self.assertIsNone(
            relay.map_referee_event('wave', {'level': 3, 'status': 'start'}, 'A'))

    def test_wave_done_without_level_is_ignored(self):
        self.assertIsNone(relay.map_referee_event('wave', {'status': 'done'}, 'A'))

    def test_hq_dead_maps_to_hq_destroyed(self):
        self.assertEqual(
            relay.map_referee_event('hq_dead', {'status': 'match_end', 'hp': 0}, 'A'),
            {'world': 'A', 'type': 'hq_destroyed'},
        )

    def test_map_ready_types_map_to_ready(self):
        for etype in ('mod_load', 'setup'):
            self.assertEqual(
                relay.map_referee_event(etype, {'status': 'ok'}, 'B'),
                {'world': 'B', 'type': 'ready'},
                etype,
            )

    def test_unrelated_events_are_ignored(self):
        for etype in ('bridge_test', 'economy_farm', 'match_end', 'score_update'):
            self.assertIsNone(relay.map_referee_event(etype, {}, 'A'), etype)


class ParseLineRefereePayloadTest(unittest.TestCase):
    """Die fuer Server 06 uebersprungenen Typen brauchen den Payload fuer #268."""

    def test_skipped_wave_line_keeps_payload(self):
        action, etype, payload = relay.parse_line(
            b'[RBBATTLE] event=wave level=3 status=done spawned=7 skipped=0 anchor=border')
        self.assertEqual((action, etype), ('skip', 'wave'))
        self.assertEqual(payload.get('status'), 'done')
        self.assertEqual(payload.get('level'), 3)

    def test_hq_dead_line_keeps_payload(self):
        action, etype, payload = relay.parse_line(
            b'[RBBATTLE] event=hq_dead status=match_end hp=0')
        self.assertEqual((action, etype), ('skip', 'hq_dead'))
        self.assertEqual(payload.get('status'), 'match_end')
        self.assertIn('hp', payload)

    def test_server_capable_line_still_posts(self):
        action, etype, payload = relay.parse_line(
            b'[RBBATTLE] event=score_update score=10 wave=2')
        self.assertEqual(action, 'post')
        self.assertEqual(payload.get('score'), 10)

    def test_log_line_to_referee_event_end_to_end(self):
        # Genau die Kette, die der Referee-Rueckkanal fahrt: Log-Zeile -> Event.
        raw = b'[RBBATTLE] event=wave level=3 status=done spawned=7 skipped=0 anchor=border'
        action, etype, payload = relay.parse_line(raw)
        rev = relay.map_referee_event(etype, payload, 'A')
        self.assertEqual(rev, {'world': 'A', 'type': 'wave_done', 'level': 3})


class StubPipe:
    """In-Memory-Pipe-Fake (deterministisch, kein Spiel)."""

    def __init__(self):
        self.calls = []

    def send_exec_and_wait(self, command, cmd_id):
        self.calls.append((command, cmd_id))
        return 123, None  # Byte-Zahl + keine exec_result-Antwort (kein Fehler)


class RefereeTransportTest(unittest.TestCase):
    """Command-Out: Referee-Commands -> Dispatch an die rbbridge-Pipe."""

    def _relay(self, pipe):
        cfg = {
            'server': 'http://127.0.0.1:8081',
            'player_id': 'p',
            'match_id': '',           # Referee-Pfad braucht KEINE match_id
            'poll_s': 0.01,
            'pipe_path': '/tmp/nope',
            'pipe_timeout_s': 0.1,
            'referee': True,
            'world': 'A',
        }
        return relay.Relay(cfg, pipe=pipe)

    def test_referee_urls(self):
        r = self._relay(StubPipe())
        self.assertEqual(r.url_referee_event(), 'http://127.0.0.1:8081/referee/event')
        self.assertEqual(r.url_referee_poll(),
                         'http://127.0.0.1:8081/referee/poll?world=A')

    def test_post_event_posts_referee_body(self):
        r = self._relay(StubPipe())
        calls = []

        def fake(method, url, body=None):
            calls.append((method, url, body))
            return 200, {'commands': [], 'state': {}}

        with mock.patch.object(relay, 'http_json', fake):
            status, _data = r.post_referee_event({'world': 'A', 'type': 'ready'})
        self.assertEqual(status, 200)
        self.assertEqual(calls, [
            ('POST', 'http://127.0.0.1:8081/referee/event',
             {'world': 'A', 'type': 'ready'})])

    def test_poll_commands_are_dispatched_to_pipe(self):
        pipe = StubPipe()
        r = self._relay(pipe)
        # Antwortform laut docs/TOURNAMENT_API.md (GET /referee/poll).
        r.handle_referee_poll({'commands': [
            {'world': 'A', 'command': 'rb_wave 4', 'cmd_id': 7, 'reason': 'wave_done'}]})
        self.assertEqual(r.dispatch_queue.qsize(), 1)
        _due, _seq, item = r.dispatch_queue.get_nowait()
        self.assertEqual(item['command'], 'rb_wave 4')
        self.assertTrue(r._handle_dispatch_item(item))
        self.assertEqual(pipe.calls, [('rb_wave 4', 7)])

    def test_cmd_id_dedup_prevents_second_dispatch(self):
        # Push-und-Poll teilen die cmd_id -> kein Doppel-Restart (der Vertrag).
        pipe = StubPipe()
        r = self._relay(pipe)
        payload = {'commands': [{'world': 'A', 'command': 'restart', 'cmd_id': 9}]}
        r.handle_referee_poll(payload)
        _due, _seq, item = r.dispatch_queue.get_nowait()
        r._handle_dispatch_item(item)
        # Zweiter Poll mit derselben cmd_id: nichts mehr.
        r.handle_referee_poll(payload)
        self.assertTrue(r.dispatch_queue.empty())
        self.assertEqual(pipe.calls, [('restart', 9)])

    def test_command_without_cmd_id_is_still_dispatched(self):
        pipe = StubPipe()
        r = self._relay(pipe)
        r.handle_referee_poll({'commands': [{'command': 'rb_wave 1'}]})
        _due, _seq, item = r.dispatch_queue.get_nowait()
        self.assertTrue(r._handle_dispatch_item(item))
        self.assertEqual(pipe.calls, [('rb_wave 1', None)])

    def test_empty_and_malformed_polls_are_noops(self):
        r = self._relay(StubPipe())
        r.handle_referee_poll({})
        r.handle_referee_poll({'commands': [None, {}, {'command': ''}]})
        self.assertTrue(r.dispatch_queue.empty())


if __name__ == '__main__':
    unittest.main()
