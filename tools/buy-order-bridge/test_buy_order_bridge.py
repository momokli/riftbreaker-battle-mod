#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-Tests für buy_order_bridge.py (PoC #518).

Reine Parser-/Payload-Tests ohne Netz, Datei oder Spiel. Aufruf:

    python3 -m unittest test_buy_order_bridge -v
"""

import unittest

import buy_order_bridge as bridge


class ParseTests(unittest.TestCase):
    def test_parses_buy_order_fields(self):
        fields = bridge.parse_buy_order(
            "[RBBATTLE] event=buy_order world=A amount=10 resource=carbonium item=boss order_id=7"
        )
        self.assertEqual(
            fields,
            {"world": "A", "amount": "10", "resource": "carbonium", "item": "boss", "order_id": "7"},
        )

    def test_ignores_other_events(self):
        self.assertIsNone(bridge.parse_buy_order("[RBBATTLE] event=mod_load version=1 status=ok"))
        self.assertIsNone(bridge.parse_buy_order("kein [RBBATTLE] Marker hier"))

    def test_build_payload_defaults(self):
        self.assertEqual(
            bridge.build_payload({"world": "B", "amount": "20"}),
            {"world": "B", "amount": 20, "resource": "carbonium", "item": "unknown"},
        )
        # world fehlt -> Default A; amount nicht-numerisch -> 0.
        self.assertEqual(
            bridge.build_payload({"amount": "viel"}),
            {"world": "A", "amount": 0, "resource": "carbonium", "item": "unknown"},
        )

    def test_build_payload_full(self):
        self.assertEqual(
            bridge.build_payload({"world": "A", "amount": "10", "resource": "carbonium", "item": "boss"}),
            {"world": "A", "amount": 10, "resource": "carbonium", "item": "boss"},
        )

    def test_default_log_paths(self):
        paths = bridge.default_log_paths("/data/.wine", "steamuser")
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(p.endswith("exor_logs.txt") for p in paths))
        self.assertTrue(all("steamuser" in p for p in paths))


if __name__ == "__main__":
    unittest.main()
