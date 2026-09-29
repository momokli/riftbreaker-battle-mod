#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Live-E2E #928 ueber 2 Zyklen — mit Test-Shim fuer den vorgeschlagenen Fix.

Treibt `ParkedPool` (echter Provisioner, echte Bridge) ueber 2 Zyklen:
  warm -> claim -> recycle -> PARKED  (x2)
und prueft docker ps / volume ls auf Leaks + restfreies Cleanup.

Der Test-Shim patcht NUR im Harness `BridgeClient.end_game`: bei `result is None`
wird der HTTP-Call uebersprungen (genau der vorgeschlagene Produktfix). Damit
belegt der Lauf: MIT Fix ist recycle->PARKED live ueber 2 Zyklen moeglich und
leakfrei. Ohne Shim scheitert recycle an `end_game(None)` -> 400 invalid_request
(siehe evidence/928-live-2026-09-24.txt / live928_probe.py).

    cd deploy/parked
    PROVISIONER_IMAGE=rb-dedicated:fbe485640bfb timeout 900 python3 evidence/live928_cycle.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

INSTANCE = os.environ.get("INSTANCE", "live928cyc")
IMAGE = os.environ["PROVISIONER_IMAGE"]
os.environ.setdefault("PROVISIONER_ENV", "dev")
os.environ.setdefault("PROVISIONER_INSTANCE_ID", INSTANCE)
os.environ.setdefault("PROVISIONER_HEALTH_DEADLINE", "300")
os.environ.setdefault("PROVISIONER_HEALTH_INTERVAL", "3")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import parked_pool  # noqa: E402
from parked_service import build_provisioner  # noqa: E402

# --- Test-Shim: der vorgeschlagene Produktfix (nur hier, kein Produktcode) ---
_orig_end_game = parked_pool.BridgeClient.end_game


def _end_game_shim(self, result=None):
    if result is None:
        return {"ok": True, "skipped": True, "reason": "no_result_provided"}
    return _orig_end_game(self, result)


parked_pool.BridgeClient.end_game = _end_game_shim


def sh(args):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.decode("utf-8", "replace")


def main():
    provisioner = build_provisioner(None, env=os.environ)
    pool = parked_pool.ParkedPool(provisioner)

    ps_before = sh(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"])
    vol_before = sh(["docker", "volume", "ls", "--format", "{{.Name}}"])
    print("### image=%s env=dev instance=%s" % (IMAGE, INSTANCE))
    print("### docker ps before lines=%d, volumes before lines=%d" % (
        len(ps_before.strip().splitlines()), len(vol_before.strip().splitlines())))

    try:
        t0 = time.time()
        entry = pool.warm_up(env="dev", instance_id=INSTANCE)
        print("### warm dt=%.2fs state=%s bridge=%s" % (time.time() - t0, entry.state.value, entry.bridge_url))
        for cycle in (1, 2):
            c = pool.claim(env="dev", instance_id=INSTANCE)
            print("### cycle%d claim state=%s handover=%.4fs" % (cycle, c["state"], c["handover_seconds"]))
            time.sleep(2.0)
            rec = pool.recycle(env="dev", instance_id=INSTANCE, keep_warm=True)
            print("### cycle%d recycle state=%s rounds=%d" % (cycle, rec.state.value, rec.rounds))
        print("### pool status=%s" % json.dumps(pool.status()))

        ps_after = sh(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"])
        vol_after = sh(["docker", "volume", "ls", "--format", "{{.Name}}"])
        ps_delta = [ln for ln in ps_after.splitlines() if ln not in ps_before.splitlines()]
        vol_delta = [ln for ln in vol_after.splitlines() if ln not in vol_before.splitlines()]
        print("### ps delta=%s" % json.dumps(ps_delta))
        print("### volume delta=%s" % json.dumps(vol_delta))
    finally:
        stop = provisioner.stop(instance_id=INSTANCE, env="dev")
        print("### stop=%s" % json.dumps(stop))
        names = sh(["docker", "ps", "-a", "--format", "{{.Names}}"])
        vols = sh(["docker", "volume", "ls", "--format", "{{.Name}}"])
        print("### leftover container=%s volumes=%s" % (
            "riftbreaker-dedicated-dev-%s" % INSTANCE in names,
            [v for v in vols.splitlines() if INSTANCE in v]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
