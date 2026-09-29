#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Host-Test fuer identity.py (Issue #992).

Identische Testfall-Tabelle wie der C++-Test ``test_player_identity.cpp``
(dual-implementierung C++/Python laeuft nicht auseinander). Laeuft in der CI
ueber ``python3 -m unittest``; kein Wine/Spiel.
"""

from __future__ import annotations

import unittest

from identity import Kind, canonicalize, is_authorized, is_identity_like, parse_identity


class ParseTests(unittest.TestCase):
    def test_steam(self):
        pid = parse_identity("steamid:123")
        self.assertTrue(pid.valid)
        self.assertEqual(pid.kind, Kind.STEAM)
        self.assertEqual(pid.raw, "steamid:123")
        self.assertEqual(pid.canonical, "steamid:123")  # byte-gleich

    def test_generic_lowercases_hex(self):
        pid = parse_identity("str:AB12")
        self.assertTrue(pid.valid)
        self.assertEqual(pid.kind, Kind.GENERIC)
        self.assertEqual(pid.raw, "str:AB12")
        self.assertEqual(pid.canonical, "str:ab12")  # Hex lowercase

    def test_account(self):
        pid = parse_identity("account:4711")
        self.assertTrue(pid.valid)
        self.assertEqual(pid.kind, Kind.ACCOUNT)
        self.assertEqual(pid.canonical, "account:4711")

    def test_invalid(self):
        for raw in ("", "momo", "steamid:", "str:", "steam:1"):
            pid = parse_identity(raw)
            self.assertFalse(pid.valid, raw)


class IsIdentityLikeTests(unittest.TestCase):
    def test_prefixes(self):
        self.assertTrue(is_identity_like("str:AB12"))
        self.assertTrue(is_identity_like("steamid:7"))
        self.assertTrue(is_identity_like("account:9"))

    def test_game_names(self):
        for raw in ("momo-dev", "", "stranger:1"):
            self.assertFalse(is_identity_like(raw), raw)


class CanonicalTests(unittest.TestCase):
    def test_kinds_never_share_canonical(self):
        steam = parse_identity("steamid:123")
        generic = parse_identity("str:123")
        self.assertNotEqual(steam.canonical, generic.canonical)
        self.assertNotEqual(steam.kind, generic.kind)

    def test_two_generic_installations_differ(self):
        self.assertNotEqual(
            parse_identity("str:aa").canonical, parse_identity("str:ab").canonical
        )

    def test_canonical_idempotent(self):
        pid = parse_identity("str:AB12")
        self.assertEqual(parse_identity(pid.canonical).canonical, pid.canonical)

    def test_canonicalize_passthrough(self):
        self.assertEqual(canonicalize("str:AB12"), "str:ab12")
        self.assertEqual(canonicalize("steamid:7"), "steamid:7")
        self.assertIsNone(canonicalize(None))
        # unbekannt -> unveraendert durchgereicht
        self.assertEqual(canonicalize("momo"), "momo")


class AuthorizedTests(unittest.TestCase):
    def test_passive(self):
        good = parse_identity("str:AB12")
        bad = parse_identity("momo")
        self.assertTrue(is_authorized(True, good))
        self.assertFalse(is_authorized(False, good))
        self.assertFalse(is_authorized(True, bad))
        self.assertFalse(is_authorized(False, bad))


if __name__ == "__main__":
    unittest.main()