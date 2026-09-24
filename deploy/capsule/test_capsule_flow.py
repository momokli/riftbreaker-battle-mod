#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermetische Tests fuer deploy/capsule/capsule_flow.py (Issue #931).

Kein Netz, kein Spiel, kein Docker: Parked-Dienst, Attack-Cycle und Bridge sind
Fakes mit gemeinsamem Zustand; die Uhr ist eine ``FakeClock``. Aufruf:

    cd deploy/capsule && python3 -m unittest -v
"""

from __future__ import annotations

import unittest

from capsule_flow import (
    Capsule,
    CapsuleCoordinator,
    CapsuleError,
    CapsulePhase,
    _ALLOWED,
)


class FakeClock(object):
    """Monotone Fake-Uhr; nur explizites ``advance`` bewegt die Zeit."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeBridge(object):
    """Fake-Bridge: Welt-Pause/Resume + Operator-Override, protokolliert Aufrufe."""

    def __init__(self, url: str, log=None) -> None:
        self.url = url
        self.paused = True  # nach dem Parken angehalten
        self.calls = []
        self.auto_ops = []
        self.fail_on = set()
        self.pause_auto = None
        self.resume_seconds = 0.0
        self.log = log  # gemeinsames Ereignis-Log (Ketten-Reihenfolge)

    def _guard(self, name: str) -> None:
        self.calls.append(name)
        if self.log is not None:
            self.log.append("bridge:" + name)
        if name in self.fail_on:
            raise RuntimeError("fake bridge %s exploded" % name)

    def resume_game(self):
        self._guard("resume_game")
        self.paused = False
        return {"ok": True, "game_paused": False, "pause_want": 0}

    def pause_game(self, op=None):
        self._guard("pause_game")
        self.auto_ops.append(op)
        if op == "auto":
            self.pause_auto = -1  # Operator-Override frei (Engine steuert)
            return {"ok": True, "pause_want": -1}
        self.paused = True
        return {"ok": True, "game_paused": True, "pause_want": 1}

    def set_auto(self):
        return self.pause_game("auto")


class FakeCycle(object):
    """Fake-Attack-Cycle: ZSM PAUSED -> WARMUP -> RUNNING -> GAME_OVER."""

    def __init__(self, env: str, log=None) -> None:
        self.env = env
        self.state = "paused"
        self.calls = []
        self.fail_start = False
        self.log = log  # gemeinsames Ereignis-Log (Ketten-Reihenfolge)

    def _log(self, name: str) -> None:
        if self.log is not None:
            self.log.append("cycle:" + name)

    def start(self):
        self.calls.append("start")
        self._log("start")
        if self.fail_start:
            raise RuntimeError("fake cycle start exploded")
        if self.state == "paused":
            self.state = "warmup"
        return {"ok": True, "started": True, "state": self.state}

    def status(self):
        self.calls.append("status")
        self._log("status")
        return {"ok": True, "state": self.state, "mode": "solo", "round": 0}

    # -- Testhilfen --------------------------------------------------------
    def to_running(self):
        self.state = "running"

    def to_game_over(self):
        self.state = "game_over"


class FakeParked(object):
    """Fake-Parked-Dienst: claim/recycle mit gemeinsamem Zustand."""

    def __init__(self, instance: str = "parked-1",
                 bridge_url: str = "http://127.0.0.1:40001",
                 gns: str = "127.0.0.1:41001") -> None:
        self.instance = instance
        self.bridge_url = bridge_url
        self.gns = gns
        self.claims = []
        self.recycles = []
        self.rounds = 0
        self.fail_claim = None
        self.fail_recycle = None

    def claim(self, env=None, instance_id=None, resume=True):
        self.claims.append({"env": env, "instance_id": instance_id, "resume": resume})
        if self.fail_claim is not None:
            raise self.fail_claim
        return {
            "instance": instance_id or self.instance,
            "env": env,
            "bridge_url": self.bridge_url,
            "gns_endpoint": self.gns,
            "state": "claimed",
            "resumed": bool(resume),
            "handover_seconds": 0.0,
        }

    def recycle(self, env=None, instance_id=None, keep_warm=True, result=None):
        self.recycles.append(
            {"env": env, "instance_id": instance_id, "keep_warm": keep_warm, "result": result}
        )
        if self.fail_recycle is not None:
            raise self.fail_recycle
        self.rounds += 1
        return {"instance": instance_id, "state": "parked", "rounds": self.rounds}


class Harness(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.parked = FakeParked()
        self.bridges = {}
        self.cycles = {}
        # Gemeinsames Ereignis-Log: reiht Bridge- UND Cycle-Calls in echter
        # Reihenfolge aneinander (Ketten-Nachweis, nicht nur Einzelfunktionen).
        self.events = []

        def bridge_factory(url):
            bridge = self.bridges.get(url)
            if bridge is None:
                bridge = FakeBridge(url, log=self.events)
                self.bridges[url] = bridge
            return bridge

        def cycle_factory(env):
            cycle = self.cycles.get(env)
            if cycle is None:
                cycle = FakeCycle(env, log=self.events)
                self.cycles[env] = cycle
            return cycle

        self.cycle_factory = cycle_factory
        self.coord = CapsuleCoordinator(
            self.parked, cycle_factory, bridge_factory, clock=self.clock, env="test"
        )

    def bridge(self):
        return self.bridges[self.parked.bridge_url]

    def cycle(self):
        return self.cycles["test"]

    def opened(self) -> Capsule:
        return self.coord.open(env="test", identitaet="str:AB12")


class PhaseModelTests(unittest.TestCase):
    def test_transition_table_is_complete(self):
        # Jede Phase ist Schluessel; keine Kante zeigt auf ``idle``.
        for phase in CapsulePhase:
            self.assertIn(phase, _ALLOWED)
            for target in _ALLOWED[phase]:
                self.assertNotEqual(target, CapsulePhase.IDLE)


class OpenTests(Harness):
    def test_open_claims_without_resume(self):
        cap = self.opened()
        self.assertEqual(cap.phase, CapsulePhase.CLAIMED)
        self.assertFalse(self.parked.claims[0]["resume"])  # Welt bleibt pausiert
        self.assertTrue(self.bridge().paused)
        self.assertEqual(cap.instance_id, "parked-1")
        self.assertEqual(cap.gns_endpoint, "127.0.0.1:41001")
        self.assertEqual(cap.identitaet, "str:AB12")

    def test_open_twice_is_rejected(self):
        self.opened()
        with self.assertRaises(CapsuleError) as ctx:
            self.coord.open(env="test")
        self.assertEqual(ctx.exception.reason, "already_open")
        self.assertEqual(ctx.exception.status, 409)

    def test_open_passes_instance_id(self):
        cap = self.coord.open(env="test", instance_id="warm-7")
        self.assertEqual(cap.instance_id, "warm-7")

    def test_open_claim_failure_is_mapped(self):
        self.parked.fail_claim = CapsuleError("none_parked", "kein Slot", 409)
        with self.assertRaises(CapsuleError) as ctx:
            self.coord.open(env="test")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.reason, "none_parked")
        self.assertEqual(self.coord.status("test")["phase"], "idle")


class ReadyTests(Harness):
    def test_ready_resumes_then_starts_cycle(self):
        self.opened()
        bridge, cycle = self.bridge(), self.cycle()
        cap = self.coord.ready(env="test")
        self.assertEqual(cap.phase, CapsulePhase.WARMUP)
        # Reihenfolge: erst resume_game, dann Cycle start.
        self.assertEqual(bridge.calls, ["resume_game"])
        self.assertEqual(cycle.calls, ["start"])
        self.assertFalse(bridge.paused)
        self.assertEqual(cycle.state, "warmup")

    def test_ready_is_idempotent_in_warmup_and_running(self):
        self.opened()
        self.coord.ready(env="test")
        bridge = self.bridge()
        self.coord.ready(env="test")  # warmup -> no-op
        self.cycle().to_running()
        cap = self.coord.ready(env="test")  # running -> no-op
        self.assertEqual(cap.phase, CapsulePhase.WARMUP)
        self.assertEqual(bridge.calls.count("resume_game"), 1)
        self.assertEqual(self.cycle().calls.count("start"), 1)

    def test_ready_without_open_fails(self):
        with self.assertRaises(CapsuleError) as ctx:
            self.coord.ready(env="test")
        self.assertEqual(ctx.exception.reason, "no_instance")

    def test_ready_resume_failure_keeps_claimed(self):
        self.opened()
        self.bridge().fail_on.add("resume_game")
        with self.assertRaises(CapsuleError) as ctx:
            self.coord.ready(env="test")
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(self.coord.status("test")["phase"], "claimed")
        self.assertNotIn("start", self.cycle().calls)  # Cycle NICHT gestartet

    def test_ready_cycle_failure_rolls_back_to_claimed(self):
        self.opened()
        self.cycle().fail_start = True
        bridge = self.bridge()
        with self.assertRaises(CapsuleError) as ctx:
            self.coord.ready(env="test")
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.reason, "cycle_unreachable")
        # Rollback: Welt wieder pausiert, Phase bleibt claimed (kein Teilzustand).
        self.assertIn("pause_game", bridge.calls)
        self.assertTrue(bridge.paused)
        self.assertEqual(self.coord.status("test")["phase"], "claimed")


    def test_ready_after_parked_is_wrong_phase_409(self):
        # Nach einer beendeten Runde (parked) ist `ready` nicht mehr erlaubt.
        self.opened()
        self.coord.ready(env="test")
        self.coord.finish(env="test")  # -> parked
        with self.assertRaises(CapsuleError) as ctx:
            self.coord.ready(env="test")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.reason, "wrong_phase")


class FinishTests(Harness):
    def test_finish_without_result_recycles_and_parks(self):
        self.opened()
        self.coord.ready(env="test")
        self.cycle().to_running()
        cap = self.coord.finish(env="test")
        self.assertEqual(cap.phase, CapsulePhase.PARKED)
        self.assertEqual(cap.rounds, 1)
        self.assertEqual(self.parked.recycles[0]["result"], None)
        self.assertTrue(self.parked.recycles[0]["keep_warm"])

    def test_finish_with_result_passes_result(self):
        self.opened()
        self.coord.ready(env="test")
        self.cycle().to_game_over()
        cap = self.coord.finish(result="win", env="test")
        self.assertEqual(cap.phase, CapsulePhase.PARKED)
        self.assertEqual(self.parked.recycles[0]["result"], "win")

    def test_finish_invalid_result_400(self):
        self.opened()
        with self.assertRaises(CapsuleError) as ctx:
            self.coord.finish(result="draw", env="test")
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.reason, "bad_request")

    def test_double_finish_409(self):
        self.opened()
        self.coord.ready(env="test")
        self.coord.finish(env="test")
        with self.assertRaises(CapsuleError) as ctx:
            self.coord.finish(env="test")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.reason, "wrong_phase")

    def test_finish_recycle_failure_restores_phase(self):
        self.opened()
        self.coord.ready(env="test")
        self.parked.fail_recycle = CapsuleError("not_recyclable", "boom", 409)
        with self.assertRaises(CapsuleError):
            self.coord.finish(env="test")
        # Kein halber Zustand: Phase faellt auf `warmup` zurueck -> Retry moeglich.
        self.assertEqual(self.coord._capsules["test"].phase, CapsulePhase.WARMUP)
        self.assertEqual(self.coord.status("test")["phase"], "warmup")

    def test_finish_before_open_fails(self):
        with self.assertRaises(CapsuleError) as ctx:
            self.coord.finish(env="test")
        self.assertEqual(ctx.exception.reason, "no_instance")


class AutoTests(Harness):
    def test_auto_releases_operator_override(self):
        self.opened()
        cap = self.coord.auto(env="test")
        self.assertEqual(cap.phase, CapsulePhase.CLAIMED)
        self.assertIn("auto", self.bridge().auto_ops)
        self.assertEqual(self.bridge().pause_auto, -1)

    def test_auto_without_kapsel_fails(self):
        with self.assertRaises(CapsuleError) as ctx:
            self.coord.auto(env="test")
        self.assertEqual(ctx.exception.reason, "no_instance")


class StatusTests(Harness):
    def test_status_idle_without_kapsel(self):
        row = self.coord.status("test")
        self.assertEqual(row["phase"], "idle")
        self.assertIsNone(row["instance"])

    def test_status_follows_cycle_state(self):
        self.opened()
        self.assertEqual(self.coord.status("test")["phase"], "claimed")
        self.coord.ready(env="test")
        self.assertEqual(self.coord.status("test")["phase"], "warmup")
        self.cycle().to_running()
        self.assertEqual(self.coord.status("test")["phase"], "running")
        self.cycle().to_game_over()
        self.assertEqual(self.coord.status("test")["phase"], "game_over")

    def test_status_parked_survives_cycle_absence(self):
        self.opened()
        self.coord.finish(env="test")
        row = self.coord.status("test")
        self.assertEqual(row["phase"], "parked")
        self.assertEqual(row["round"], 1)


class FullChainTests(Harness):
    """KERN-NACHWEIS: eine ganze Runde end-to-end, hermetisch (Fake-Stack).

    Der Test spielt die Kette wirklich durch (open -> ready -> running ->
    game_over -> finish -> parked -> auto) und prueft ueber das gemeinsame
    Ereignis-Log die **Reihenfolge** der Bridge-/Cycle-Calls ueber ALLE
    Schritte hinweg — nicht nur einzelne Funktionen isoliert.
    """

    def test_one_full_round(self):
        # 1) open -> claimed: Welt pausiert, claim OHNE resume, noch kein
        #    Bridge-/Cycle-Call.
        cap = self.opened()
        self.assertEqual(cap.phase, CapsulePhase.CLAIMED)
        self.assertTrue(self.bridge().paused)
        self.assertFalse(self.parked.claims[0]["resume"])
        self.assertEqual(self.events, [])

        # 2) ready -> resume_game DANN Cycle-Start (Reihenfolge!) -> warmup.
        self.coord.ready(env="test")
        self.assertEqual(self.events, ["bridge:resume_game", "cycle:start"])
        self.assertFalse(self.bridge().paused)
        self.assertEqual(self.cycle().state, "warmup")
        self.assertEqual(self.coord.status("test")["phase"], "warmup")

        # 3) Warmup-Ende + HQ -> running.
        self.cycle().to_running()
        self.assertEqual(self.coord.status("test")["phase"], "running")

        # 4) HQ-Tod -> game_over sichtbar.
        self.cycle().to_game_over()
        self.assertEqual(self.coord.status("test")["phase"], "game_over")

        # 5) finish(win) -> end_game/reset/recycle ueber den Parked-Dienst ->
        #    parked; genau EIN recycle mit result + keep_warm.
        cap = self.coord.finish(result="win", env="test")
        self.assertEqual(cap.phase, CapsulePhase.PARKED)
        self.assertEqual(len(self.parked.recycles), 1)
        self.assertEqual(self.parked.recycles[0]["result"], "win")
        self.assertTrue(self.parked.recycles[0]["keep_warm"])
        self.assertEqual(self.parked.rounds, 1)
        self.assertEqual(self.cycle().calls.count("start"), 1)
        # recycle laeuft ueber den Pool, nicht direkt am Cycle/Bridge.
        self.assertNotIn("pause_game", self.bridge().calls)

        # 6) auto -> Operator-Override frei (letzter Bridge-Call = pause_game).
        self.coord.auto(env="test")
        self.assertEqual(self.bridge().auto_ops, ["auto"])
        self.assertEqual(self.bridge().pause_auto, -1)
        self.assertEqual(self.events[-1], "bridge:pause_game")

    def test_neue_runde_nach_parked(self):
        self.opened()
        self.coord.ready(env="test")
        self.coord.finish(env="test")
        # Nach `parked` ist ein erneutes open erlaubt (naechste Runde).
        cap = self.coord.open(env="test")
        self.assertEqual(cap.phase, CapsulePhase.CLAIMED)
        self.assertEqual(self.parked.rounds, 1)


class ErrorMappingTests(Harness):
    def test_generic_claim_error_maps_to_503(self):
        self.parked.fail_claim = RuntimeError("connection refused")
        with self.assertRaises(CapsuleError) as ctx:
            self.coord.open(env="test")
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.reason, "claim_failed")

    def test_env_ambiguous_with_two_capsules(self):
        self.opened()
        self.coord.open(env="prod", instance_id="parked-2")
        with self.assertRaises(CapsuleError) as ctx:
            self.coord.status()
        self.assertEqual(ctx.exception.reason, "env_ambiguous")


class ClientPathTests(unittest.TestCase):
    """Die Produktions-Clients rufen die vereinbarten Pfade (Fake-Opener)."""

    def _capture(self):
        calls = []

        class _Resp:
            status = 200

            def __init__(self, body):
                self._body = body

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(request, timeout=None):
            calls.append((request.method, request.full_url, request.data))
            url = request.full_url
            if url.endswith("/status"):
                return _Resp(b'{"ok":true,"state":"warmup"}')
            if url.endswith("/claim"):
                return _Resp(b'{"ok":true,"instance":"parked-1","bridge_url":"http://127.0.0.1:40001"}')
            if url.endswith("/recycle"):
                return _Resp(b'{"ok":true,"instance":"parked-1","state":"parked","rounds":1}')
            return _Resp(b'{"ok":true}')

        return calls, opener

    def test_parked_client_paths_and_resume_false(self):
        from capsule_flow import ParkedServiceClient

        calls, opener = self._capture()
        client = ParkedServiceClient("http://127.0.0.1:8095", opener=opener)
        client.claim(env="dev", instance_id="parked-3", resume=False)
        method, url, data = calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/claim"))
        self.assertIn(b'"resume": false', data)
        self.assertIn(b'"instance_id": "parked-3"', data)

    def test_cycle_client_paths(self):
        from capsule_flow import CycleClient

        calls, opener = self._capture()
        client = CycleClient("http://127.0.0.1:9102", opener=opener)
        client.start()
        self.assertTrue(calls[0][1].endswith("/start"))
        client.status()
        self.assertTrue(calls[1][1].endswith("/status"))

    def test_bridge_control_set_auto(self):
        from capsule_flow import BridgeControl

        calls, opener = self._capture()
        client = BridgeControl("http://127.0.0.1:9001", opener=opener)
        client.set_auto()
        method, url, data = calls[0]
        self.assertTrue(url.endswith("/pause_game"))
        self.assertIn(b'"op": "auto"', data)

    def test_client_maps_http_error_reason(self):
        from capsule_flow import ParkedServiceClient

        class _ErrOpener:
            def __call__(self, request, timeout=None):
                raise RuntimeError("no")

        import urllib.error

        def opener(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 409, "Conflict", {},
                _Body(b'{"ok":false,"reason":"none_parked","detail":"leer"}'),
            )

        client = ParkedServiceClient("http://127.0.0.1:8095", opener=opener)
        with self.assertRaises(CapsuleError) as ctx:
            client.claim(env="test")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.reason, "none_parked")


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        pass


if __name__ == "__main__":
    unittest.main()