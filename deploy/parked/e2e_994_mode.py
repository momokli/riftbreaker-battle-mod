#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2E-Beweis fuer Issue #994 (Lobby-Modus end-to-end) — Tester-Stage.

Treibt den VOLLEN HTTP-Pfad ueber die ECHTEN Dienste:

    capsule HTTP  POST /capsule/open {env, identitaet, mode}
      -> ParkedServiceClient (echtes HTTP)
      -> parked HTTP  POST /claim {env, instance_id, resume, mode}
      -> ParkedController / ParkedPool (real)
      -> Stub-Provisioner  start(env, mode, instance_id)

Der Stub-Provisioner recordet den Modus byte-genau und prueft ihn mit dem
ECHTEN ``provisioner.parse_mode`` (fail-loud) — nur der Docker-Aufruf selbst
ist ersetzt (kein Docker, kein Netz ausser 127.0.0.1:0-HTTP, kein Spiel).

Aufruf (Exit 0 = alle ACs belegt):

    cd deploy/parked && TMPDIR=/dev/shm python3 e2e_994_mode.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "deploy", "capsule"))

import parked_service as ps  # noqa: E402
from parked_pool import ParkedPool  # noqa: E402
from parked_service import (  # noqa: E402
    ParkedController,
    ParkedServiceConfig,
    build_server as build_parked_server,
)
from test_parked_service import FakeBridge, FakeClock, FakeProvisioner  # noqa: E402

from capsule_flow import CapsuleCoordinator, ParkedServiceClient  # noqa: E402
from capsule_service import (  # noqa: E402
    CapsuleServiceConfig,
    build_server as build_capsule_server,
)

PROV = ps._provisioner_module()  # echter provisioner.py (parse_mode/ModeError)


class RecordingProvisioner(FakeProvisioner):
    """Stub-Provisioner: recordet jeden ``start(env, mode, instance_id)``.

    ``parse_mode`` des echten Provisioners laeuft davor — so ist der Modus am
    Provisioner-Seam exakt das, was der echte Code akzeptiert/ablehnt.
    """

    def __init__(self) -> None:
        super().__init__()
        self.starts_modes = []

    def start(self, env=None, mode="solo_self", instance_id=None):
        PROV.parse_mode(mode)  # fail-loud, identisch zum echten Provisioner
        self.starts_modes.append((env or self.cfg.env, mode, instance_id))
        return super().start(env=env, mode=mode, instance_id=instance_id)


def _post(url: str, payload: dict):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class Stack(object):
    """Ein echter parked+capsule HTTP-Stack ueber einem Recording-Provisioner."""

    def __init__(self, case, warm_mode: str, env: str, state_dir: str) -> None:
        self.provisioner = RecordingProvisioner()
        self.clock = FakeClock(1000.0)
        self.bridges = {}

        def factory(url):
            bridge = self.bridges.get(url)
            if bridge is None:
                bridge = FakeBridge(url, self.clock)
                self.bridges[url] = bridge
            return bridge

        pool = ParkedPool(
            self.provisioner, factory, clock=self.clock, sleep=lambda _s: None,
            state_dir=state_dir,
        )
        self.controller = ParkedController(
            pool, pool_size=1, max_park_seconds=900.0, reap_interval=9999.0,
            env=env, prefix="parked", clock=self.clock, sleep=lambda _s: None,
            warm_mode=warm_mode,
        )
        pcfg = ParkedServiceConfig(
            env=env, bind="127.0.0.1", port=0, warm_mode=warm_mode, state_dir=state_dir
        )
        self.parked_httpd = build_parked_server(pcfg, self.controller)
        self.parked_port = self.parked_httpd.server_address[1]
        _serve(case, self.parked_httpd)
        # Warm-Pool fuellen: der ECHTE _fill-Pfad ruft provisioner.start(mode).
        self.controller.maintain_once()

        self.coord = CapsuleCoordinator(
            ParkedServiceClient("http://127.0.0.1:%d" % self.parked_port, timeout=5.0),
            cycle_factory=lambda _env, _url="": None,
            bridge_factory=lambda _url: None,
            clock=time.monotonic,
            env=env,
        )
        ccfg = CapsuleServiceConfig(env=env, bind="127.0.0.1", port=0, token="")
        self.capsule_httpd = build_capsule_server(ccfg, self.coord)
        self.capsule_port = self.capsule_httpd.server_address[1]
        _serve(case, self.capsule_httpd)

    def parked_post(self, path, payload):
        return _post("http://127.0.0.1:%d%s" % (self.parked_port, path), payload)

    def capsule_open(self, payload):
        return _post("http://127.0.0.1:%d/capsule/open" % self.capsule_port, payload)


def _serve(case, httpd):
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    case.addCleanup(httpd.shutdown)
    case.addCleanup(httpd.server_close)
    return thread


class ModeE2E(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="e2e994-state-")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.state_dir, True))

    # -- AC1: persona end-to-end durch capsule + parked bis start(mode) ----
    def test_persona_mode_end_to_end(self):
        st = Stack(self, warm_mode="solo_persona:aggro", env="e2e-aggro",
                   state_dir=self.state_dir)
        # Der Warm-Pool hat den Container bereits im angeforderten Modus gestartet.
        self.assertEqual(st.provisioner.starts_modes,
                         [("e2e-aggro", "solo_persona:aggro", "parked-1")])
        status, body = st.capsule_open({"env": "e2e-aggro", "identitaet": "str:AB12",
                                        "mode": "solo_persona:aggro"})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["mode"], "solo_persona:aggro", body)  # Mode in to_dict
        self.assertEqual(body["phase"], "claimed", body)
        self.assertEqual(body["instance"], "parked-1", body)

    # -- AC2: Default/fehlend -> solo_self (altes Verhalten) ---------------
    def test_default_mode_is_solo_self(self):
        st = Stack(self, warm_mode="solo_self", env="e2e-self", state_dir=self.state_dir)
        self.assertEqual(st.provisioner.starts_modes,
                         [("e2e-self", "solo_self", "parked-1")])
        status, body = st.capsule_open({"env": "e2e-self", "identitaet": "str:CD34"})
        self.assertEqual(status, 200, body)
        self.assertIsNone(body["mode"], body)  # kein mode-Feld -> None (alt)
        self.assertEqual(body["phase"], "claimed", body)

    # -- AC3a: unbekannter Modus -> 400 bad_mode ---------------------------
    def test_unknown_mode_bad_mode(self):
        st = Stack(self, warm_mode="solo_self", env="e2e-bad", state_dir=self.state_dir)
        status, body = st.parked_post("/claim", {"env": "e2e-bad", "mode": "campaign"})
        self.assertEqual(status, 400, body)
        self.assertEqual(body["reason"], "bad_mode", body)
        # Kein weiterer start durch den abgelehnten Claim:
        self.assertEqual(len(st.provisioner.starts_modes), 1)
        # Auch ueber die capsule-Ebene propagiert:
        cstatus, cbody = st.capsule_open({"env": "e2e-bad", "mode": "solo"})
        self.assertEqual(cstatus, 400, cbody)
        self.assertEqual(cbody["reason"], "bad_mode", cbody)

    # -- AC3b: Modus-Mismatch bei bestehender Instanz -> 409 --------------
    def test_mode_mismatch(self):
        st = Stack(self, warm_mode="solo_persona:aggro", env="e2e-mm",
                   state_dir=self.state_dir)
        status, body = st.parked_post(
            "/claim", {"env": "e2e-mm", "instance_id": "parked-1", "mode": "solo_self"})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["reason"], "mode_mismatch", body)
        # Passender Modus -> Erfolg (die Instanz bleibt nutzbar):
        status, body = st.parked_post(
            "/claim", {"env": "e2e-mm", "instance_id": "parked-1",
                       "mode": "solo_persona:aggro", "resume": False})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["state"], "claimed", body)
        # Und ueber die capsule-Ebene derselbe 409:
        st2 = Stack(self, warm_mode="solo_persona:aggro", env="e2e-mm2",
                    state_dir=self.state_dir)
        cstatus, cbody = st2.capsule_open({"env": "e2e-mm2", "mode": "solo_self"})
        self.assertEqual(cstatus, 409, cbody)
        self.assertEqual(cbody["reason"], "mode_mismatch", cbody)

    # -- AC4: kein mode-Feld => byte-identischer Alt-Claim -----------------
    def test_absent_mode_field_identical_old_behaviour(self):
        st = Stack(self, warm_mode="solo_self", env="e2e-old", state_dir=self.state_dir)
        # resume ohne mode-Feld darf die PARKED-Instanz NICHT anfassen.
        status, body = st.parked_post(
            "/claim", {"env": "e2e-old", "instance_id": "parked-1", "resume": False})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["state"], "claimed", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
