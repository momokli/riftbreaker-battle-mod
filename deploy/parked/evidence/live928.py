#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Live-Nachweis #928: Parked-Pool-Dienst gegen den ECHTEN Provisioner (#908).

Treibt ParkedController direkt (kein systemd, kein Fake):
  warm (cold boot + pause_game) -> claim (resume_game, handover) -> recycle
  (end_game/round_reset/pause_game) -> wieder PARKED  — ueber 2 Zyklen,
plus docker ps / docker volume ls vorher/nachher und sauberes Cleanup.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

INSTANCE = "live928"
IMAGE = os.environ["PROVISIONER_IMAGE"]

os.environ.setdefault("PROVISIONER_ENV", "dev")
os.environ.setdefault("PROVISIONER_INSTANCE_ID", INSTANCE)
os.environ.setdefault("PROVISIONER_HEALTH_DEADLINE", "240")
os.environ.setdefault("PROVISIONER_HEALTH_INTERVAL", "3")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from parked_pool import BridgeClient  # noqa: E402
from parked_service import ParkedController, build_provisioner  # noqa: E402


def sh(args):
    out = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return out.stdout.decode("utf-8", "replace")


def snapshot():
    ps = sh(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"])
    vols = sh(["docker", "volume", "ls", "--format", "{{.Name}}"])
    return ps, vols


def main():
    print("### env PROVISIONER_IMAGE=%s PROVISIONER_ENV=%s INSTANCE=%s"
          % (IMAGE, os.environ["PROVISIONER_ENV"], INSTANCE))
    provisioner = build_provisioner_from_env()
    pool = ParkedPoolFor(provisioner)
    controller = ParkedController(pool, pool_size=1, max_park_seconds=900.0,
                                  reap_interval=30.0, env="dev", prefix=INSTANCE)

    ps_before, vol_before = snapshot()
    print("### docker ps (before), lines=%d" % len(ps_before.strip().splitlines()))
    print("### docker volume ls (before), lines=%d" % len(vol_before.strip().splitlines()))

    t0 = time.time()
    controller.maintain_once()
    print("### warm dt=%.2fs counters=%s" % (time.time() - t0, json.dumps(controller.counters())))
    rows = controller.pool.status()
    print("### pool entries=%s" % json.dumps(rows))
    if not rows or rows[0]["state"] != "parked":
        print("### FAIL: kein PARKED nach maintain_once")
        return 1
    bridge_url = rows[0]["bridge_url"]
    print("### bridge get_state (paused?) = %s" % json.dumps(_safe_get_state(bridge_url)))
    print("### docker inspect bridge-port = %s" % sh(["docker", "port", rows[0]["container"]]))

    for cycle in (1, 2):
        started = time.time()
        claimed = controller.claim()
        print("### cycle%d claim=%s (wall %.2fs)" % (cycle, json.dumps(claimed), time.time() - started))
        time.sleep(1.0)
        rec = controller.recycle(instance_id=claimed["instance"], keep_warm=True)
        print("### cycle%d recycle=%s" % (cycle, json.dumps(rec)))
        print("### cycle%d counters=%s" % (cycle, json.dumps(controller.counters())))

    ps_after, vol_after = snapshot()
    print("### docker ps (after)", )
    leaked_ps = [ln for ln in ps_after.splitlines() if ln not in ps_before.splitlines()]
    leaked_vol = [ln for ln in vol_after.splitlines() if ln not in vol_before.splitlines()]
    print("### ps delta: %s" % json.dumps(leaked_ps))
    print("### volume delta: %s" % json.dumps(leaked_vol))

    # Cleanup: Auslaufschutz reap(0) stoppt die geparkte Instanz restfrei.
    stopped = controller.reap(0)
    print("### reap(0) stopped=%s" % json.dumps([e.instance_id for e in stopped]))
    print("### docker ps (cleanup) has %s = %s" % (
        "riftbreaker-dedicated-dev-%s" % INSTANCE,
        "riftbreaker-dedicated-dev-%s" % INSTANCE in sh(["docker", "ps", "-a", "--format", "{{.Names}}"])))
    print("### volumes after cleanup has rb-dev-wine-%s = %s" % (
        INSTANCE, "rb-dev-wine-%s" % INSTANCE in sh(["docker", "volume", "ls", "--format", "{{.Name}}"])))
    return 0


def _safe_get_state(url):
    try:
        return BridgeClient(url).get_state()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def build_provisioner_from_env():
    return build_provisioner(None, env=os.environ)


class ParkedPoolFor:
    """Adapter: baut einen echten ParkedPool aus dem Provisioner."""
    def __new__(cls, provisioner):
        from parked_pool import ParkedPool
        return ParkedPool(provisioner)


if __name__ == "__main__":
    sys.exit(main())
