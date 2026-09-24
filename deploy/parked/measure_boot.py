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
import logging
import os
import sys
import time
from typing import Any, Callable, Dict, Optional, Sequence

from parked_pool import ParkedPool
from parked_vs import ParkedVSPool

LOG = logging.getLogger("parked.measure")


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


def measure_vs_handover(pool_vs: Any, clock: Callable[[], float] = time.monotonic,
                        env: Optional[str] = None, instance_id: Optional[str] = None,
                        players: Sequence[str] = ("p1", "p2")) -> Dict[str, float]:
    """2-Beitritt-Handover eines geparkten VS-Servers messen.

    ``t0`` vor ``join(p1)``, ``t1`` nach ``join(p1)``, dann ``join(p2)`` und das
    Ready-Gate (``ready`` beider). Der Handover (``resume_game``) wird erst durch
    das Gate ausgeloest.

    Rueckgabe:
      ``vs_join_seconds``     = ``t1 - t0`` (erster Beitritt)
      ``vs_handover_seconds`` = beide Beitritte + Ready-Gate bis ``CLAIMED``
    """
    t0 = clock()
    pool_vs.join(players[0], env=env, instance_id=instance_id)
    t1 = clock()
    pool_vs.join(players[1], env=env, instance_id=instance_id)
    pool_vs.ready(players[0], env=env, instance_id=instance_id)
    pool_vs.ready(players[1], env=env, instance_id=instance_id)
    t3 = clock()
    return {"vs_join_seconds": t1 - t0, "vs_handover_seconds": t3 - t1}


def run_vs_measurement(provisioner: Any, pool_vs: Any,
                       clock: Callable[[], float] = time.monotonic,
                       env: Optional[str] = None,
                       cold_instance_id: Optional[str] = None,
                       parked_instance_id: Optional[str] = None,
                       players: Sequence[str] = ("p1", "p2")) -> Dict[str, Any]:
    """Cold-Boot vs. 2-Beitritt-Handover messen (VS-Variante von #909).

    ``try/finally``: Cold-Instanz wird IMMER gestoppt und die geparkte
    VS-Instanz IMMER recycelt (``keep_warm=False``) — auch wenn die Messung
    scheitert (kein Container-Leak). ``saved_seconds = cold_boot - vs_handover``.
    """
    cold = measure_cold_boot(provisioner, clock, env=env, instance_id=cold_instance_id)
    try:
        if parked_instance_id is not None:
            pool_vs.warm_up(env=env, instance_id=parked_instance_id)
        vs = measure_vs_handover(pool_vs, clock, env=env, instance_id=parked_instance_id,
                                 players=players)
    finally:
        cleanup(provisioner, pool_vs, env=env, cold_instance_id=cold_instance_id,
                parked_instance_id=parked_instance_id)
    return {
        "cold_boot_seconds": cold,
        "vs_join_seconds": vs["vs_join_seconds"],
        "vs_handover_seconds": vs["vs_handover_seconds"],
        "saved_seconds": cold - vs["vs_handover_seconds"],
    }


def cleanup(provisioner: Any, pool: Any, env: Optional[str] = None,
            cold_instance_id: Optional[str] = None,
            parked_instance_id: Optional[str] = None) -> None:
    """Mess-Ressourcen restfrei abraeumen (NIT 1 — kein Container-Leak).

    Die Cold-Instanz wird gestoppt; die geparkte Instanz wird recycelt
    (``keep_warm=False`` -> ``stop``). Scheitert das Recyceln (z. B. weil der
    ``warm_up`` vorzeitig abbrach), wird die geparkte Instanz direkt gestoppt.
    Fehler werden geloggt, aber NICHT geworfen: der Messfehler (falls es einen
    gab) bleibt sichtbar.
    """
    if cold_instance_id is not None:
        try:
            provisioner.stop(instance_id=cold_instance_id, env=env)
        except Exception as exc:  # pragma: no cover - nur Log
            LOG.warning("cleanup: cold stop %s fehlgeschlagen: %s", cold_instance_id, exc)
    if parked_instance_id is not None:
        try:
            pool.recycle(env=env, instance_id=parked_instance_id, keep_warm=False)
        except Exception as exc:
            LOG.warning("cleanup: recycle %s fehlgeschlagen: %s", parked_instance_id, exc)
            try:
                provisioner.stop(instance_id=parked_instance_id, env=env)
            except Exception as stop_exc:  # pragma: no cover - nur Log
                LOG.warning("cleanup: stop %s fehlgeschlagen: %s", parked_instance_id, stop_exc)


def run_measurement(provisioner: Any, pool: Any, clock: Callable[[], float] = time.monotonic,
                    env: Optional[str] = None, cold_instance_id: Optional[str] = None,
                    parked_instance_id: Optional[str] = None) -> Dict[str, Any]:
    """Cold-Boot vs. Parked-Handover messen und die Ersparnis ausrechnen.

    ``try/finally``: Cold-Instanz wird IMMER gestoppt und die geparkte Instanz
    IMMER recycelt (``keep_warm=False``) — auch wenn die Messung scheitert.
    """
    cold = measure_cold_boot(provisioner, clock, env=env, instance_id=cold_instance_id)
    try:
        warm_pool = pool
        if parked_instance_id is not None:
            warm_pool.warm_up(env=env, instance_id=parked_instance_id)
        handover = measure_parked_handover(warm_pool, clock, env=env, instance_id=parked_instance_id)
    finally:
        cleanup(provisioner, pool, env=env, cold_instance_id=cold_instance_id,
                parked_instance_id=parked_instance_id)
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
    parser.add_argument("--vs", action="store_true",
                        help="VS-Messung (Cold-Boot vs. 2-Beitritt-Handover, Issue #910)")
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

    cold_id = "%s-cold" % args.instance_id
    if args.vs:
        pool_vs = ParkedVSPool(provisioner)
        result = run_vs_measurement(provisioner, pool_vs, env=args.env,
                                    cold_instance_id=cold_id,
                                    parked_instance_id=args.instance_id)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print("cold_boot_seconds=%.3f" % result["cold_boot_seconds"])
            print("vs_join_seconds=%.3f" % result["vs_join_seconds"])
            print("vs_handover_seconds=%.3f" % result["vs_handover_seconds"])
            print("saved_seconds=%.3f" % result["saved_seconds"])
        return 0

    pool = ParkedPool(provisioner)
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
