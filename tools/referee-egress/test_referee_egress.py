#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-Tests für referee_egress.py (Egress-Spiel→Referee, #358/#268).

Testet deterministisch ohne Spiel und ohne Netz:
  - parse_rbbattle(): [RBBATTLE]-Zeile -> (event, fields)
  - map_referee_event(): Log-Event -> Referee-Event (ready/wave_done/hq_destroyed)
  - POST-Kontrakt: richtiger Body + Pfad /referee/event
"""

import json
import os
import tempfile
import unittest
import urllib.request

import referee_egress as re_


class ParseTest(unittest.TestCase):
    def test_parses_event_and_fields(self):
        self.assertEqual(
            re_.parse_rbbattle("[RBBATTLE] event=mod_load version=0.34.3 status=ok"),
            ("mod_load", {"version": "0.34.3", "status": "ok"}),
        )

    def test_skips_event_key_in_fields(self):
        self.assertEqual(
            re_.parse_rbbattle("[RBBATTLE] event=wave level=3 status=done"),
            ("wave", {"level": "3", "status": "done"}),
        )

    def test_no_marker_returns_none(self):
        self.assertIsNone(re_.parse_rbbattle("ServerGameplayState: Player '0':'momo'"))

    def test_no_event_returns_none(self):
        self.assertIsNone(re_.parse_rbbattle("[RBBATTLE] skeleton ok"))


class MapTest(unittest.TestCase):
    def test_mod_load_and_setup_map_to_ready(self):
        self.assertEqual(re_.map_referee_event("mod_load", {}), {"world": "A", "type": "ready"})
        self.assertEqual(re_.map_referee_event("setup", {"status": "ok"}), {"world": "A", "type": "ready"})

    def test_wave_done_maps_to_wave_done_with_level(self):
        self.assertEqual(
            re_.map_referee_event("wave", {"status": "done", "level": "3"}),
            {"world": "A", "type": "wave_done", "level": 3},
        )

    def test_wave_start_is_not_a_referee_signal(self):
        self.assertIsNone(re_.map_referee_event("wave", {"status": "start", "level": "3"}))

    def test_wave_done_requires_level(self):
        self.assertIsNone(re_.map_referee_event("wave", {"status": "done"}))
        self.assertIsNone(re_.map_referee_event("wave", {"status": "done", "level": "abc"}))

    def test_hq_dead_maps_to_hq_destroyed(self):
        self.assertEqual(re_.map_referee_event("hq_dead", {}), {"world": "A", "type": "hq_destroyed"})

    def test_other_events_are_ignored(self):
        self.assertIsNone(re_.map_referee_event("economy_farm", {"amount": "5"}))
        self.assertIsNone(re_.map_referee_event("hq_autodetect", {"status": "ok"}))

    def test_world_is_forwarded(self):
        self.assertEqual(re_.map_referee_event("hq_dead", {}, world="B"), {"world": "B", "type": "hq_destroyed"})


class PostContractTest(unittest.TestCase):
    def test_deliver_posts_correct_body_and_path(self):
        captured = {}

        class _Resp:
            status = 200

            @staticmethod
            def read():
                return b'{"accepted":true}'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout):
            self.assertEqual(req.full_url, "http://127.0.0.1:8081/referee/event")
            self.assertEqual(req.get_method(), "POST")
            self.assertEqual(req.headers.get("Content-type"), "application/json")
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _Resp()

        feeder = re_.RefereeEgress("/nonexistent.log", "http://127.0.0.1:8081")
        orig = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            ok = feeder._deliver({"world": "A", "type": "wave_done", "level": 3})
        finally:
            urllib.request.urlopen = orig
        self.assertTrue(ok)
        self.assertEqual(captured["body"], {"world": "A", "type": "wave_done", "level": 3})


class IdentityTest(unittest.TestCase):
    """Deploy-Identitaet env/ref je Event-Record (Issue #483, US4)."""

    class _Resp:
        status = 200

        @staticmethod
        def read():
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _deliver_with(self, line, feeder):
        captured = {}

        def fake_urlopen(req, timeout):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return self._Resp()

        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "exor_logs.txt")
            with open(log, "w", encoding="utf-8") as handle:
                handle.write(line + "\n")
            feeder.path = log
            orig = urllib.request.urlopen
            urllib.request.urlopen = fake_urlopen
            try:
                delivered = feeder.poll_once()
            finally:
                urllib.request.urlopen = orig
        return delivered, captured

    def test_posted_event_carries_env_and_ref(self):
        feeder = re_.RefereeEgress("/nonexistent.log", "http://127.0.0.1:8081",
                                   env="prod", ref="v1.2+aabbcc")
        delivered, captured = self._deliver_with(
            "[RBBATTLE] event=wave level=3 status=done", feeder)
        self.assertEqual(delivered, 1)
        self.assertEqual(captured["body"], {
            "world": "A", "type": "wave_done", "level": 3,
            "env": "prod", "ref": "v1.2+aabbcc",
        })

    def test_default_identity_is_unknown(self):
        feeder = re_.RefereeEgress("/nonexistent.log", "http://127.0.0.1:8081")
        _delivered, captured = self._deliver_with("[RBBATTLE] event=hq_dead", feeder)
        self.assertEqual(captured["body"]["env"], "unknown")
        self.assertEqual(captured["body"]["ref"], "unknown")

    def test_cli_defaults_from_env_vars(self):
        os.environ["RBB_ENV"] = "test"
        os.environ["RBB_REF"] = "sha-9"
        try:
            args = re_.build_parser().parse_args(["--once"])
        finally:
            os.environ.pop("RBB_ENV", None)
            os.environ.pop("RBB_REF", None)
        self.assertEqual(args.env, "test")
        self.assertEqual(args.ref, "sha-9")


if __name__ == "__main__":
    unittest.main()
