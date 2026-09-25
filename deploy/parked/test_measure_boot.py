#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermetische Tests fuer deploy/parked/measure_boot.py (Issue #909).

Provisioner und Pool sind Fakes, die eine gemeinsame ``FakeClock`` vorantreiben
(Cold-Boot +15 s, Handover +0.13 s) — kein Docker, kein Netz, kein Spiel.

Aufruf: ``cd deploy/parked && python3 -m unittest -v``
"""

from __future__ import annotations

import contextlib
import io
import unittest

import measure_boot
from test_parked_pool import FakeClock


class AdvancingProvisioner(object):
    """Fake-Provisioner, dessen ``start`` die Uhr um die Boot-Zeit vorstellt."""

    def __init__(self, clock: FakeClock, boot_seconds: float) -> None:
        self.clock = clock
        self.boot_seconds = boot_seconds
        self.starts = []
        self.stops = []

    def start(self, env=None, instance_id=None):
        self.starts.append(instance_id)
        self.clock.advance(self.boot_seconds)
        return {"instance": instance_id, "container": "c", "ports": {"bridge": 40001}}

    def stop(self, instance_id=None, env=None):
        self.stops.append(instance_id)
        return {"instance": instance_id, "removed": {}}


class AdvancingPool(object):
    """Fake-Pool, dessen ``claim`` die Uhr um die Handover-Zeit vorstellt."""

    def __init__(self, clock: FakeClock, handover_seconds: float) -> None:
        self.clock = clock
        self.handover_seconds = handover_seconds
        self.claims = []
        self.warmups = []
        self.recycled = []

    def warm_up(self, env=None, instance_id=None):
        self.warmups.append(instance_id)
        return instance_id

    def claim(self, env=None, instance_id=None):
        self.claims.append(instance_id)
        self.clock.advance(self.handover_seconds)
        return {"instance": instance_id, "state": "claimed", "handover_seconds": self.handover_seconds}

    def recycle(self, env=None, instance_id=None, keep_warm=True, result=None):
        self.recycled.append((instance_id, keep_warm))
        return instance_id


class FailingClaimPool(AdvancingPool):
    """Fake-Pool, dessen ``claim`` knallt — fuer den Cleanup-im-Fehlerfall-Test."""

    def claim(self, env=None, instance_id=None):
        raise RuntimeError("claim exploded")


class AdvancingVSPool(object):
    """Fake-VS-Pool: ``join``/``ready`` stellen die Uhr vor.

    Der 2. ``ready`` loest (wie das echte Ready-Gate) den Handover aus und
    stellt die Uhr um ``handover_seconds`` vor.
    """

    def __init__(self, clock: FakeClock, join_seconds: float = 0.05,
                 handover_seconds: float = 0.13) -> None:
        self.clock = clock
        self.join_seconds = join_seconds
        self.handover_seconds = handover_seconds
        self.warmups = []
        self.joins = []
        self.readies = []
        self.recycled = []

    def warm_up(self, env=None, instance_id=None):
        self.warmups.append(instance_id)
        return instance_id

    def join(self, player, env=None, instance_id=None):
        self.joins.append(player)
        self.clock.advance(self.join_seconds)
        return {"instance": instance_id, "player": player}

    def ready(self, player, env=None, instance_id=None):
        self.readies.append(player)
        if len(self.readies) >= 2:
            self.clock.advance(self.handover_seconds)
        return {"instance": instance_id, "player": player}

    def recycle(self, env=None, instance_id=None, keep_warm=True, result=None):
        self.recycled.append((instance_id, keep_warm))
        return instance_id


class FailingReadyVSPool(AdvancingVSPool):
    """Fake-VS-Pool, dessen 2. ``ready`` (Gate) knallt — Cleanup-Test."""

    def ready(self, player, env=None, instance_id=None):
        self.readies.append(player)
        if len(self.readies) >= 2:
            raise RuntimeError("handover exploded")
        return {"instance": instance_id, "player": player}


class MeasureBootTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock(start=0.0)

    def test_cold_boot_measures_provisioner_start(self):
        provisioner = AdvancingProvisioner(self.clock, boot_seconds=15.2)
        seconds = measure_boot.measure_cold_boot(provisioner, self.clock, instance_id="c")
        self.assertAlmostEqual(seconds, 15.2, places=9)

    def test_parked_handover_measures_claim(self):
        pool = AdvancingPool(self.clock, handover_seconds=0.13)
        instance = pool.warm_up(instance_id="p")
        seconds = measure_boot.measure_parked_handover(pool, self.clock, instance_id=instance)
        self.assertAlmostEqual(seconds, 0.13, places=9)

    def test_run_measurement_reports_saved_seconds(self):
        provisioner = AdvancingProvisioner(self.clock, boot_seconds=15.2)
        pool = AdvancingPool(self.clock, handover_seconds=0.13)
        result = measure_boot.run_measurement(provisioner, pool, self.clock,
                                              cold_instance_id="c", parked_instance_id="p")
        self.assertAlmostEqual(result["cold_boot_seconds"], 15.2, places=9)
        self.assertAlmostEqual(result["parked_handover_seconds"], 0.13, places=9)
        self.assertAlmostEqual(result["saved_seconds"], 15.07, places=9)
        self.assertEqual(pool.warmups, ["p"])
        self.assertEqual(pool.claims, ["p"])
        self.assertEqual(set(result), {"cold_boot_seconds", "parked_handover_seconds", "saved_seconds"})

    def test_run_measurement_skips_warmup_without_parked_id(self):
        provisioner = AdvancingProvisioner(self.clock, boot_seconds=15.2)
        pool = AdvancingPool(self.clock, handover_seconds=0.13)
        measure_boot.run_measurement(provisioner, pool, self.clock)
        self.assertEqual(pool.warmups, [])

    def test_run_measurement_cleans_up_cold_and_parked(self):
        # NIT 1: kein Container-Leak — Cold gestoppt, Parked recycelt (keep_warm=False).
        provisioner = AdvancingProvisioner(self.clock, boot_seconds=15.2)
        pool = AdvancingPool(self.clock, handover_seconds=0.13)
        measure_boot.run_measurement(provisioner, pool, self.clock,
                                     cold_instance_id="c", parked_instance_id="p")
        self.assertEqual(provisioner.stops, ["c"])
        self.assertEqual(pool.recycled, [("p", False)])

    def test_run_measurement_cleans_up_even_when_claim_fails(self):
        # try/finally: Cleanup laeuft auch, wenn die Handover-Messung knallt.
        provisioner = AdvancingProvisioner(self.clock, boot_seconds=15.2)
        pool = FailingClaimPool(self.clock, handover_seconds=0.13)
        with self.assertRaises(RuntimeError):
            measure_boot.run_measurement(provisioner, pool, self.clock,
                                         cold_instance_id="c", parked_instance_id="p")
        self.assertEqual(provisioner.stops, ["c"])
        self.assertEqual(pool.recycled, [("p", False)])


class MeasureVSTests(unittest.TestCase):
    """Issue #910: Cold-Boot vs. 2-Beitritt-Handover (hermetisch)."""

    def setUp(self) -> None:
        self.clock = FakeClock(start=0.0)

    def test_measure_vs_handover_two_joins_and_gate(self):
        pool = AdvancingVSPool(self.clock, join_seconds=0.05, handover_seconds=0.13)
        pool.warm_up(instance_id="p")
        result = measure_boot.measure_vs_handover(pool, self.clock, instance_id="p")
        # vs_join_seconds = erster Beitritt; vs_handover_seconds = 2. Beitritt + Gate.
        self.assertAlmostEqual(result["vs_join_seconds"], 0.05, places=9)
        self.assertAlmostEqual(result["vs_handover_seconds"], 0.18, places=9)
        self.assertEqual(pool.joins, ["p1", "p2"])   # genau zwei Beitritte
        self.assertEqual(pool.readies, ["p1", "p2"])  # Gate erst nach beiden

    def test_run_vs_measurement_reports_saved_seconds(self):
        provisioner = AdvancingProvisioner(self.clock, boot_seconds=15.2)
        pool = AdvancingVSPool(self.clock, join_seconds=0.05, handover_seconds=0.13)
        result = measure_boot.run_vs_measurement(provisioner, pool, self.clock,
                                                 cold_instance_id="c", parked_instance_id="p")
        self.assertAlmostEqual(result["cold_boot_seconds"], 15.2, places=9)
        self.assertAlmostEqual(result["vs_join_seconds"], 0.05, places=9)
        self.assertAlmostEqual(result["vs_handover_seconds"], 0.18, places=9)
        self.assertAlmostEqual(result["saved_seconds"], 15.02, places=9)
        self.assertEqual(pool.warmups, ["p"])
        self.assertEqual(set(result), {"cold_boot_seconds", "vs_join_seconds",
                                       "vs_handover_seconds", "saved_seconds"})

    def test_run_vs_measurement_skips_warmup_without_parked_id(self):
        provisioner = AdvancingProvisioner(self.clock, boot_seconds=15.2)
        pool = AdvancingVSPool(self.clock)
        measure_boot.run_vs_measurement(provisioner, pool, self.clock)
        self.assertEqual(pool.warmups, [])

    def test_run_vs_measurement_cleans_up_cold_and_parked(self):
        provisioner = AdvancingProvisioner(self.clock, boot_seconds=15.2)
        pool = AdvancingVSPool(self.clock, join_seconds=0.05, handover_seconds=0.13)
        measure_boot.run_vs_measurement(provisioner, pool, self.clock,
                                        cold_instance_id="c", parked_instance_id="p")
        self.assertEqual(provisioner.stops, ["c"])
        self.assertEqual(pool.recycled, [("p", False)])

    def test_run_vs_measurement_cleans_up_even_when_gate_fails(self):
        provisioner = AdvancingProvisioner(self.clock, boot_seconds=15.2)
        pool = FailingReadyVSPool(self.clock, join_seconds=0.05, handover_seconds=0.13)
        with self.assertRaises(RuntimeError):
            measure_boot.run_vs_measurement(provisioner, pool, self.clock,
                                             cold_instance_id="c", parked_instance_id="p")
        self.assertEqual(provisioner.stops, ["c"])
        self.assertEqual(pool.recycled, [("p", False)])

    def test_cli_vs_routes_to_run_vs_measurement(self):
        # --vs schaltet auf den VS-Pfad (2 Beitritte) und laesst den Solo-Pfad aus.
        captured = {}

        class FakeMod(object):
            class ConfigError(Exception):
                pass

            @staticmethod
            def load_config():
                return object()

            @staticmethod
            def Provisioner(cfg):  # pragma: no cover - nur Stand-in
                return object()

        def fake_run_vs(provisioner, pool_vs, clock=None, env=None,
                        cold_instance_id=None, parked_instance_id=None,
                        players=("p1", "p2")):
            captured["called"] = True
            captured["cold"] = cold_instance_id
            captured["parked"] = parked_instance_id
            return {"cold_boot_seconds": 1.0, "vs_join_seconds": 0.1,
                    "vs_handover_seconds": 0.2, "saved_seconds": 0.8}

        def forbidden_solo(*_a, **_k):
            raise AssertionError("Solo-Pfad unerwartet benutzt")

        orig_load = measure_boot._load_provisioner_module
        orig_run_vs = measure_boot.run_vs_measurement
        orig_run_solo = measure_boot.run_measurement
        measure_boot._load_provisioner_module = lambda: FakeMod
        measure_boot.run_vs_measurement = fake_run_vs
        measure_boot.run_measurement = forbidden_solo
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = measure_boot.main(["--vs", "--json", "--instance-id", "m910"])
        finally:
            measure_boot._load_provisioner_module = orig_load
            measure_boot.run_vs_measurement = orig_run_vs
            measure_boot.run_measurement = orig_run_solo

        self.assertEqual(rc, 0)
        self.assertTrue(captured.get("called"))
        self.assertEqual(captured["cold"], "m910-cold")
        self.assertEqual(captured["parked"], "m910")
        self.assertIn("vs_handover_seconds", out.getvalue())


if __name__ == "__main__":
    unittest.main()
