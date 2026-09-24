#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""measure_boot — Mess-Harness Cold-Boot vs. Parked-Handover (Issue #909).

Liefert die fuer #909 geforderte Messung: Wie lange dauert der **Cold-Boot**
(Provisioner ``start()`` inkl. Health) gegenueber dem **Parked-Handover**
(``ParkedPool.claim()`` -> ``resume_game``)? Ergebnis als JSON.

Alle Mitarbeiter (Provisioner, Pool, ``clock``) sind injizierbar; im Test
laufen reine Fakes (kein Docker, kein Netz, kein Spiel).

CLI (braucht ``PROVISIONER_*``-Config):

    python3 measure_boot.py --json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from typing import Any, Callable, Dict, Optional, Sequence

from parked_pool import ParkedPool


def _load_provisioner_module():
    """Provisioner (#908) als Modul laden, ohne Paketkonvention vorauszusetzen."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "provisioner", "provisioner.py")
    spec = importlib.util.spec_from_file_location("rb_provisioner", path)
    if spec is None or spec.loader is None:  # pragma: no cover - nur defensiv
        raise RuntimeError("Provisioner-Modul nicht ladbar: %s" % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def measure_cold_boot(provisioner: Any, clock: Callable[[], float] = time.monotonic,
                      env: Optional[str] = None, instance_id: Optional[str] = None) -> float:
    """Sekunden von ``provisioner.start()`` bis die Instanz healthy ist."""
    start = clock()
    provisioner.start(env=env, instance_id=instance_id)
    return clock() - start


def measure_parked_handover(pool: Any, clock: Callable[[], float] = time.monotonic,
                            env: Optional[str] = None,
                            instance_id: Optional[str] = None) -> float:
    """Sekunden fuer ``pool.claim()`` (resume_game-Round-Trip) einer geparkten Instanz."""
    start = clock()
    pool.claim(env=env, instance_id=instance_id)
    return clock() - start


def run_measurement(provisioner: Any, pool: Any, clock: Callable[[], float] = time.monotonic,
                    env: Optional[str] = None, cold_instance_id: Optional[str] = None,
                    parked_instance_id: Optional[str] = None) -> Dict[str, Any]:
    """Cold-Boot vs. Parked-Handover messen und die Ersparnis ausrechnen."""
    cold = measure_cold_boot(provisioner, clock, env=env, instance_id=cold_instance_id)
    warm_pool = pool
    if parked_instance_id is not None:
        warm_pool.warm_up(env=env, instance_id=parked_instance_id)
    handover = measure_parked_handover(warm_pool, clock, env=env, instance_id=parked_instance_id)
    return {
        "cold_boot_seconds": cold,
        "parked_handover_seconds": handover,
        "saved_seconds": cold - handover,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="measure_boot",
        description="Cold-Boot vs. Parked-Handover messen (Issue #909)",
    )
    parser.add_argument("--json", action="store_true", help="Ergebnis als JSON auf stdout")
    parser.add_argument("--env", default=None)
    parser.add_argument("--instance-id", dest="instance_id", default="measure909")
    args = parser.parse_args(argv)

    prov = _load_provisioner_module()
    try:
        cfg = prov.load_config()
    except prov.ConfigError as exc:
        print("config error: %s" % exc, file=sys.stderr)
        return 2

    provisioner = prov.Provisioner(cfg)
    pool = ParkedPool(provisioner)

    cold_id = "%s-cold" % args.instance_id
    result = run_measurement(provisioner, pool, env=args.env, cold_instance_id=cold_id,
                             parked_instance_id=args.instance_id)

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("cold_boot_seconds=%.3f" % result["cold_boot_seconds"])
        print("parked_handover_seconds=%.3f" % result["parked_handover_seconds"])
        print("saved_seconds=%.3f" % result["saved_seconds"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
