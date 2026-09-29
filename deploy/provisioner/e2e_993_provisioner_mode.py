#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2E-Beweis fuer Issue #993 (Provisioner-Modus) — Tester-Stage.

Treibt den ECHTEN Provisioner-Flow gegen das im Repo vorhandene Fake-Docker
(JSON-Lines-Log + State-Datei) und einen lokalen HTTP-Stub (Health +
Bridge-Seeding-Endpunkte). Kein echtes Docker, kein Netz, kein Spiel.

Warum zwei Ebenen?
- Flow-Ebene: ``Provisioner.start()`` (produktiver Code: ``parse_mode``,
  Preflight, Persona-Staging, Bridge-Seeding, Container-/Sidecar-Args) wird real
  ausgefuehrt. Nur die beiden dokumentierten Naehte des Provisioners werden
  injiziert (``docker``-Binary + ``health_probe``/``bridge_url``) — denn der
  ``_preflight`` prueft ``bridge_port`` auf Freiheit, was ein Emulator-Stub auf
  genau diesem Port als "belegt" faelschlich sehen wuerde. Das ist exakt der
  vom Repo etablierte Hermetik-Pfad (vgl. ``test_provisioner.BaseFixture``).
- CLI-Ebene: das ECHTE ``provisioner.py``-Binary wird als Subprozess gestartet
  (argparse-Defaults, ``main()``-Exit/JSON-Fehler) fuer die Fail-loud-AC.

Aufruf (Exit 0 = alle ACs belegt):

    cd deploy/provisioner && python3 e2e_993_provisioner_mode.py
"""

import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import provisioner as prov  # noqa: E402
import test_provisioner as tt  # noqa: E402

IMAGE = tt.IMAGE
PROVISIONER = os.path.join(HERE, "provisioner.py")


class E2EModeCase(tt.BaseFixture):
    """Realer Provisioner-Flow + echter CLI-Subprozess gegen #993-ACs."""

    def _cycle(self, instance_id="0"):
        name = self.spec(instance_id).attack_cycle_container
        for call in self.run_calls():
            if name in call:
                return call
        return None

    def _posts(self):
        return e_posts(self.stub)

    # -- AC1 -------------------------------------------------------------
    def test_ac1_solo_self_flow(self):
        status = self.provisioner().start("test", "solo_self", "0")
        self.assertTrue(status["running"])
        run = self.run_calls()[0]
        self.assertIn("RIFTBREAKER_MODE=solo_self", run)
        cycle = self._cycle()
        self.assertEqual(cycle[cycle.index("--send-yourself") + 1], "on")
        self.assertNotIn("--persona", cycle)
        posts = dict(self.stub.server.posts)
        self.assertIs(posts["/game_config"]["send_yourself"], True)
        self.assertIs(posts["/game_config"]["persona"], False)
        self.assertEqual(self.stub.server.persona_active, {"name": ""})
        self.assertNotIn("/personas", posts)

    # -- AC2 -------------------------------------------------------------
    def test_ac2_solo_persona_flow(self):
        status = self.provisioner().start("test", "solo_persona:aggro", "0")
        self.assertTrue(status["running"])
        run = self.run_calls()[0]
        self.assertIn("RIFTBREAKER_MODE=solo_persona:aggro", run)
        spec = self.spec("0")
        cycle = self._cycle()
        self.assertEqual(cycle[cycle.index("--send-yourself") + 1], "off")
        self.assertEqual(cycle[cycle.index("--persona") + 1], "aggro")
        self.assertEqual(cycle[cycle.index("--persona-file") + 1], "/data/personas.json")
        self.assertIn("%s:/data/personas.json:ro" % spec.personas_staged, cycle)
        with open(spec.personas_staged, "r", encoding="utf-8") as fh:
            staged = json.load(fh)
        self.assertIn("aggro", staged["personas"])
        posts = dict(self.stub.server.posts)
        self.assertIs(posts["/game_config"]["send_yourself"], False)
        self.assertIs(posts["/game_config"]["persona"], True)
        self.assertIn("aggro", posts["/personas"]["personas"])
        self.assertEqual(self.stub.server.persona_active, {"name": "aggro"})

    # -- AC3 (Flow-Ebene: kein Docker-Call) ------------------------------
    def test_ac3_invalid_mode_no_docker_call(self):
        for mode in ("solo", "campaign", ""):
            with self.assertRaises(prov.ProvisionError, msg=repr(mode)):
                self.provisioner().start("test", mode, "0")
        self.assertEqual(self.run_calls(), [])

    # -- AC3 (CLI-Ebene: echtes Binary, Exit 1 + JSON) -------------------
    def test_ac3_cli_binary_invalid_mode(self):
        env = dict(os.environ)
        env["PROVISIONER_IMAGE"] = IMAGE
        env.pop("PROVISIONER_CONFIG", None)
        for mode in ("solo", "campaign", "", "solo_persona:"):
            proc = subprocess.run(
                [sys.executable, PROVISIONER, "start", "--mode", mode],
                env=env, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(proc.returncode, 1, mode)
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertIs(payload["ok"], False, mode)
            self.assertIn("error", payload, mode)

    # -- AC4 (Override wirkt: nur die uebergebene Datei kennt die Persona)
    def test_ac4_personas_file_override_changes_source(self):
        override = os.path.join(self.sources, "personas-onlycli.json")
        with open(override, "w", encoding="utf-8") as fh:
            json.dump({"personas": {"onlycli": [[[0] * 10]]}}, fh)
        # Default-Datei kennt 'onlycli' NICHT -> fail-loud, kein Container.
        with self.assertRaises(prov.ProvisionError):
            self.provisioner().start("test", "solo_persona:onlycli", "0")
        self.assertEqual(self.run_calls(), [])
        # Mit Override laeuft es UND stagt genau die uebergebene Datei.
        status = self.provisioner(personas_file=override).start(
            "test", "solo_persona:onlycli", "0")
        self.assertTrue(status["running"])
        spec = self.spec("0")
        with open(spec.personas_staged, "r", encoding="utf-8") as fh:
            self.assertIn("onlycli", json.load(fh)["personas"])

    # -- AC5 (laufender Container, abweichender Modus -> laut scheitern) -
    def test_ac5_existing_container_mode_mismatch_fails_loud(self):
        provisioner = self.provisioner()
        provisioner.start("test", "solo_self", "0")
        runs_before = len(self.run_calls())
        posts_before = len(self.stub.server.posts)
        with self.assertRaises(prov.ProvisionError):
            provisioner.start("test", "solo_persona:aggro", "0")
        self.assertEqual(len(self.run_calls()), runs_before)
        self.assertEqual(len(self.stub.server.posts), posts_before)


def e_posts(stub):
    return {path: body for path, body in stub.server.posts}


if __name__ == "__main__":
    unittest.main(verbosity=2)
