#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-Tests für den Send-Tailer (Issue #713).

Rein stdlib + hermetic: `parse_send_order` und der `SendTailer`-POST-Pfad werden
mit einem Fake-Poster getestet — kein Netz, kein Spiel, kein DOM.
"""

import unittest

from send_tailer import SendTailer, parse_send_order


class TestParseSendOrder(unittest.TestCase):
    def test_button_chat_with_id(self):
        line = "[LUA 'lua/buildings/rbbattle_button.lua']: [RBBATTLE] button_chat sent: -send wave1 0000000000-000001-abcd1234"
        self.assertEqual(parse_send_order(line), ("wave1", "0000000000-000001-abcd1234"))

    def test_button_chat_without_id(self):
        line = "[LUA 'lua/buildings/rbbattle_button.lua']: [RBBATTLE] button_chat sent: -send wave1"
        self.assertEqual(parse_send_order(line), ("wave1", ""))

    def test_rbsendchat(self):
        line = "[RBBATTLE:20260917-market-wave] event=chat_send status=ok text=-send wave9 42"
        self.assertEqual(parse_send_order(line), ("wave9", "42"))

    def test_leading_whitespace_name(self):
        line = "[RBBATTLE] button_chat sent: -send   wave2 abc-123"
        self.assertEqual(parse_send_order(line), ("wave2", "abc-123"))

    def test_not_send(self):
        self.assertIsNone(parse_send_order("[RBBATTLE] button_chat sent: hello world"))

    def test_typed_chat_ignored(self):
        # Getippter Chat (kein Mod-Log) darf NICHT als Order erkannt werden.
        self.assertIsNone(parse_send_order("[chat] -send wave1"))

    def test_empty_and_none(self):
        self.assertIsNone(parse_send_order(""))
        self.assertIsNone(parse_send_order(None))


class TestSendTailer(unittest.TestCase):
    def _tailer(self, poster):
        return SendTailer("http://127.0.0.1:9001/order", _poster=poster)

    def test_feed_posts_and_returns_name(self):
        calls = []

        def poster(name, id):
            calls.append((name, id))
            return (200, '{"ok":true}')

        tailer = self._tailer(poster)
        line = "[RBBATTLE] button_chat sent: -send wave1 0000000000-000001-abcd1234"
        self.assertEqual(tailer.feed(line), "wave1")
        self.assertEqual(calls, [("wave1", "0000000000-000001-abcd1234")])

    def test_feed_without_id_posts_empty_id(self):
        calls = []

        def poster(name, id):
            calls.append((name, id))
            return (200, '{"ok":true}')

        tailer = self._tailer(poster)
        tailer.feed("[RBBATTLE] button_chat sent: -send wave1")
        self.assertEqual(calls, [("wave1", "")])

    def test_feed_non_order_returns_none(self):
        tailer = self._tailer(lambda name, id: (200, "{}"))
        self.assertIsNone(tailer.feed("[RBBATTLE] button_chat sent: hi"))

    def test_feed_http_error_returns_name_still(self):
        # Auch bei HTTP-Fehler liefert feed den Namen zurück (Logging übernimmt).
        tailer = self._tailer(lambda name, id: (503, '{"ok":false}'))
        self.assertEqual(tailer.feed("[RBBATTLE] button_chat sent: -send wave1 xyz"), "wave1")


if __name__ == "__main__":
    unittest.main()
