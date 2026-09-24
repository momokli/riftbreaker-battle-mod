#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermetische Tests fuer deploy/parked/measure_boot.py (Issue #909).

Provisioner und Pool sind Fakes, die eine gemeinsame ``FakeClock`` vorantreiben
(Cold-Boot +15 s, Handover +0.13 s) — kein Docker, kein Netz, kein Spiel.

Aufruf: ``cd deploy/parked && python3 -m unittest -v``
"""

from __future__ import annotations

import unittest

import measure_boot
from test_parked_pool import FakeClock


class AdvancingProvisioner(object):
    """Fake-Provisioner, dessen ``start`` die Uhr um die Boot-Zeit vorstellt."""

    def __init__(self, clock: FakeClock, boot_seconds: float) -> None:
        self.clock = clock
        self.boot_seconds = boot_seconds
        self.starts = []

    def start(self, env=None, instance_id=None):
        self.starts.append(instance_id)
        self.clock.advance(self.boot_seconds)
        return {"instance": instance_id, "container": "c", "ports": {"bridge": 40001}}


class AdvancingPool(object):
    """Fake-Pool, dessen ``claim`` die Uhr um die Handover-Zeit vorstellt."""

    def __init__(self, clock: FakeClock, handover_seconds: float) -> None:
        self.clock = clock
        self.handover_seconds = handover_seconds
        self.claims = []
        self.warmups = []

    def warm_up(self, env=None, instance_id=None):
        self.warmups.append(instance_id)
        return instance_id

    def claim(self, env=None, instance_id=None):
        self.claims.append(instance_id)
        self.clock.advance(self.handover_seconds)
        return {"instance": instance_id, "state": "claimed", "handover_seconds": self.handover_seconds}


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


if __name__ == "__main__":
    unittest.main()
