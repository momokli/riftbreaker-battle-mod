#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integration/E2E-Tests fuer #969 auf der Service-Ebene.

Deckt genau den Bug ab, den das Issue beschreibt: einen Prozess-Restart, bei
dem die getrackten Pool-Eintraege verloren gehen, die Container im Docker aber
weiterleben (Restart-Leak).

Ablauf (echter Service-Stack, kein Unit-Sonderweg):

  1. "Alte Generation": ein ``ParkedController`` startet Instanzen im
     **weiterlebenden** ``FakeProvisioner`` und wird dann verworfen.
  2. "Neue Generation": ein **frischer** ``ParkedController``/``ParkedPool``
     ueber **demselben** ``FakeProvisioner`` (Restart). ``start()`` reconciled
     synchron **vor** dem ersten ``_fill()``: die Alt-Container muessen weg
     sein, danach fuellt ``_fill()`` wieder bis ``pool_size``.
  3. Waehrend des Reconciles wird die Bridge **nie** beruehrt
     (``CountingBridgeFactory`` erzeugt/kontaktiert keine Bridge).
  4. Idempotenz: ein zweiter Reconcile-Lauf hat keinen Effekt.
  5. Fremd-Env-Schutz: Container einer anderen ``env`` werden nie gestoppt.
  6. ``POST /reconcile`` (echter ``ThreadingHTTPServer``) + ``orphans_removed``
     in den ``counters()``.

Stdlib only, kein Docker, kein Netz (ausser 127.0.0.1:0-HTTP). Aufruf:

    cd deploy/parked && TMPDIR=/dev/shm/parked-test python3 -m unittest -v
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from parked_pool import ParkedPool
from parked_service import ParkedController, ParkedServiceConfig, build_server
from test_parked_service import FakeBridge, FakeClock, FakeProvisioner, make_pool


class CountingBridgeFactory(object):
    """Bridge-Factory, die das (Neu-)Erzeugen und jeden Aufruf protokolliert.

    Eine Bridge entsteht erst beim ersten ``warm_up`` (also erst in ``_fill()``).
    Bleibt die Map nach einem Reconcile leer, wurde die Bridge nicht beruehrt.
    """

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.bridges = {}
        self.created = []

    def __call__(self, url: str) -> FakeBridge:
        bridge = self.bridges.get(url)
        if bridge is None:
            bridge = FakeBridge(url, self.clock)
            self.bridges[url] = bridge
            self.created.append(url)
        return bridge

    def total_calls(self) -> int:
        return sum(len(bridge.calls) for bridge in self.bridges.values())


def build_controller(clock, provisioner, factory, pool_size=1, **kwargs):
    """Wie ``make_controller``, aber mit einer frei waehlbaren Factory-Callable."""
    pool = ParkedPool(provisioner, factory, clock=clock, sleep=lambda _s: None)
    return ParkedController(
        pool,
        pool_size=pool_size,
        max_park_seconds=900.0,
        reap_interval=9999.0,
        env="test",
        prefix="parked",
        clock=clock,
        sleep=lambda _s: None,
        **kwargs,
    )


class RestartLeakE2ETests(unittest.TestCase):
    """#969: Restart-Leak wird beim Start reconciled, dann wieder aufgefuellt."""

    def test_restart_leak_is_reconciled_then_refilled_without_bridge(self):
        clock = FakeClock(1000.0)
        provisioner = FakeProvisioner()

        # --- Generation 1: alte Instanzen starten (werden "vergessen") ---
        old_factory = CountingBridgeFactory(clock)
        old = build_controller(clock, provisioner, old_factory, pool_size=2)
        old.maintain_once()
        old_ids = sorted(i for (env, i) in provisioner._containers if env == "test")
        self.assertEqual(len(old_ids), 2)
        self.assertEqual(old.counters()["parked"], 2)
        self.assertGreater(old_factory.total_calls(), 0, "warm_up muss die Bridge nutzen")

        # --- "Restart": frischer Controller ueber DEMSELBEN Provisioner ---
        new_factory = CountingBridgeFactory(clock)
        new = build_controller(
            clock, provisioner, new_factory, pool_size=2,
            reconcile_interval=9999.0, reconcile_on_start=True,
        )
        new._loop = lambda: None  # Thread sofort beenden -> kein Rennen/Fill
        new.start()
        try:
            # Reconcile lief vor dem ersten _fill(): Alt-Container weg.
            self.assertEqual(new.orphans_removed, 2)
            self.assertEqual(sorted(i for (env, i) in provisioner.stops
                                    if env == "test"), old_ids)
            self.assertEqual(
                [i for (env, i) in provisioner._containers if env == "test"], []
            )
            # Bridge waehrend des Reconciles nie beruehrt:
            self.assertEqual(new_factory.bridges, {})
            self.assertEqual(new_factory.total_calls(), 0)
            # Noch nichts aufgefuellt (Loop gepatcht):
            self.assertEqual(new.counters()["parked"], 0)

            # --- _fill() fuellt wieder bis pool_size ---
            new.maintain_once()
            self.assertEqual(new.counters()["parked"], 2)
            self.assertEqual(len(new_factory.bridges), 2)
            self.assertGreater(new_factory.total_calls(), 0)

            # --- Idempotenz: zweiter Reconcile hat keinen Effekt ---
            stops_before = list(provisioner.stops)
            result2 = new.reconcile()
            self.assertEqual(result2["removed"], [])
            self.assertEqual(result2["evicted"], [])
            self.assertEqual(result2["errors"], [])
            self.assertEqual(result2["kept"], 2)  # beide PARKED laufen weiter
            self.assertEqual(new.orphans_removed, 2)  # Zaehler unveraendert
            self.assertEqual(provisioner.stops, stops_before)  # kein weiterer stop
            self.assertEqual(new.counters()["parked"], 2)  # Pool unveraendert
        finally:
            new.stop()

    def test_start_reconcile_then_fill_via_maintain_loop(self):
        """Variant ohne gepatchten Loop: start() reconciled synchron, der Loop
        fuellt danach nach. Nutzt denselben FakeProvisioner als Restart-Welt."""
        clock = FakeClock(500.0)
        provisioner = FakeProvisioner()
        # Ein Leak aus einer früheren Generation:
        provisioner.add_container("ghost", "running")

        factory = CountingBridgeFactory(clock)
        controller = build_controller(
            clock, provisioner, factory, pool_size=2,
            reconcile_interval=9999.0, reconcile_on_start=True,
        )
        controller._loop = lambda: None  # Fill explizit, nicht per Thread
        controller.start()
        try:
            self.assertEqual(controller.orphans_removed, 1)
            self.assertNotIn(("test", "ghost"), provisioner._containers)
            self.assertEqual(factory.bridges, {})  # Reconcile ohne Bridge
            controller.maintain_once()
            self.assertEqual(controller.counters()["parked"], 2)
            self.assertEqual(
                len([i for (env, i) in provisioner._containers if env == "test"]), 2
            )
        finally:
            controller.stop()

    def test_foreign_env_survives_reconcile_integration(self):
        """Fremde ``env`` wird im Integrationslauf nie angefasst."""
        clock = FakeClock(1000.0)
        provisioner = FakeProvisioner()
        factory = CountingBridgeFactory(clock)
        controller = build_controller(clock, provisioner, factory, pool_size=1)
        controller.maintain_once()  # eigene, getrackte Instanz

        provisioner.add_container("x", "running", env="other")
        original = provisioner.list_instances

        def loose(env=None):
            # Erzwingt, dass die Discovery einen Fremd-Container sieht.
            return list(original(env)) + [{
                "container": "riftbreaker-dedicated-other-x",
                "env": "other",
                "instance": "x",
                "status": "running",
                "running": True,
            }]

        provisioner.list_instances = loose
        result = controller.reconcile()
        self.assertEqual(result["skipped_foreign"], 1)
        self.assertIn(("other", "x"), provisioner._containers)  # unberuehrt
        self.assertNotIn(("other", "x"), provisioner.stops)  # nie gestoppt
        # eigene getrackte Instanz bleibt:
        self.assertEqual(controller.counters()["parked"], 1)
        self.assertEqual(result["removed"], [])


class ReconcileHttpIntegrationTests(unittest.TestCase):
    """#969: ``POST /reconcile`` ueber den echten Handler + Counter."""

    def setUp(self):
        self.clock = FakeClock(1000.0)
        self.provisioner = FakeProvisioner()
        self.bridges = {}
        self.pool = make_pool(self.clock, self.provisioner, self.bridges)
        self.controller = ParkedController(
            self.pool, pool_size=1, max_park_seconds=900.0, reap_interval=9999.0,
            env="test", prefix="parked", clock=self.clock, sleep=lambda _s: None,
        )
        self.config = ParkedServiceConfig(env="test", bind="127.0.0.1", port=0)
        self.httpd = build_server(self.config, self.controller)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2.0)

    def _url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def _post(self, path, payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self._url(path), data=data, method="POST")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def _get(self, path):
        with urllib.request.urlopen(self._url(path), timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_post_reconcile_handler_removes_leaks_and_counts(self):
        # Restart-Leaks: laufender Orphan + Created-Zombie.
        self.provisioner.add_container("leak-a", "running")
        self.provisioner.add_container("leak-b", "created")
        status, body = self._post("/reconcile", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(sorted(body["removed"]), ["leak-a", "leak-b"])
        self.assertIn("skipped_foreign", body)
        _status, snapshot = self._get("/status")
        self.assertEqual(snapshot["counters"]["orphans_removed"], 2)

        # Idempotent: zweiter Handler-Aufruf ist leer.
        status2, body2 = self._post("/reconcile", {})
        self.assertEqual(status2, 200)
        self.assertEqual(body2["removed"], [])
        self.assertEqual(body2["evicted"], [])
        _status, snapshot2 = self._get("/status")
        self.assertEqual(snapshot2["counters"]["orphans_removed"], 2)

    def test_post_reconcile_503_on_error(self):
        self.provisioner.add_container("leak", "running")
        self.provisioner.fail_stop.add("leak")
        status, body = self._post("/reconcile", {})
        self.assertEqual(status, 503)
        self.assertEqual(body["reason"], "reconcile_failed")


if __name__ == "__main__":
    unittest.main()