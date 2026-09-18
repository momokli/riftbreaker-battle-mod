#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""match-loop — Solo-Round-Loop (Issue #730): HQ-Status erkennen und bei
Zerstörung das Match beenden + automatisch eine neue Runde starten.

Pollt `POST /get_state` der Bridge und erkennt den Übergang HQ-alive → HQ-tot
**edge-getriggert** über einen `seen-alive`-Latch (sonst würde ein noch nicht
gebautes HQ zu Rundenstart fälschlich als "tot" gewertet und sofort restartet).
Erkennt der Latch den Tod, feuert der Loop:

  1. `POST /end_game {"result":"lose"}`   → LOST-Screen
  2. `restart_delay` Sekunden warten      (Default 10 s)
  3. `POST /restart_map {"op":"reset"}`   → neue Runde (Map-Reload, Economy 0)

Rein stdlib (HTTP via urllib), kein Netz-Dep im Test. Server-seitig, NICHT in
der Website, NICHT in der DLL (die DLL bleibt reine IO-Primitive). Der Sidecar
ist das env-agnostische Gegenstück zu `deploy/send-tailer`/`session-recorder`,
nur dass er **pollt** statt eine Logdatei tailt.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional

# Normalisierte get_state-Sicht: ok + die drei HQ-Felder (hq_hp/hq_hp_max als
# float|None, hq_dead als bool|None). None = "nicht auflösbar" (null im JSON).
HqView = Dict[str, object]


def parse_state(raw: str) -> HqView:
    """Parst den get_state-Body in eine normalisierte HQ-Sicht.

    Robust gegen null/number/bool und gegen Nicht-JSON (liefert ok=False).
    hq_hp/hq_hp_max: number -> float, alles andere (null/fehlend) -> None.
    hq_dead: true -> True, false -> False, sonst (null/fehlend) -> None.
    """
    view: HqView = {"ok": False, "hq_hp": None, "hq_hp_max": None, "hq_dead": None}
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return view
    if not isinstance(obj, dict):
        return view
    view["ok"] = bool(obj.get("ok"))

    hp = obj.get("hq_hp")
    view["hq_hp"] = float(hp) if isinstance(hp, (int, float)) else None

    hp_max = obj.get("hq_hp_max")
    view["hq_hp_max"] = float(hp_max) if isinstance(hp_max, (int, float)) else None

    dead = obj.get("hq_dead")
    view["hq_dead"] = True if dead is True else (False if dead is False else None)
    return view


def is_alive(view: HqView) -> bool:
    """HQ lebt, wenn hp > 0 vorliegt (und nicht tot)."""
    hp = view.get("hq_hp")
    return isinstance(hp, (int, float)) and hp > 0


def is_defeat(view: HqView) -> bool:
    """HQ ist (jetzt) weg/tot: hp == 0 (dead=true) ODER Entity weg (hp null)."""
    return view.get("hq_dead") is True or view.get("hq_hp") is None


class MatchLoop:
    """Pollt get_state und steuert den Defeat→end_game→restart-Ablauf.

    Edge-getriggert: ein Defeat feuert genau EIN end_game und (nach dem Delay)
    genau EIN restart_map. Danach fällt der Latch zurück, bis das HQ der neuen
    Runde wieder lebt (hp > 0).
    """

    def __init__(
        self,
        base_url: str,
        restart_delay: float = 10.0,
        timeout: float = 5.0,
        _poster: Optional[Callable[[str, bytes], tuple]] = None,
        _clock: Callable[[], float] = time.monotonic,
    ):
        self.base_url = base_url.rstrip("/")
        self.restart_delay = restart_delay
        self.timeout = timeout
        self._poster = _poster or self._http_post
        self._clock = _clock

        self.seen_alive = False
        self.defeat_at: Optional[float] = None  # gesetzt bei Defeat, bis Restart

    # --- HTTP (urllib) ----------------------------------------------------
    def _http_post(self, path: str, body: bytes) -> tuple:
        req = urllib.request.Request(
            self.base_url + path,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError) as e:
            return 0, str(e)

    def _get_state(self) -> HqView:
        status, body = self._poster("/get_state", b"{}")
        if not 200 <= status < 300:
            return {"ok": False, "hq_hp": None, "hq_hp_max": None, "hq_dead": None}
        return parse_state(body)

    def _end_game(self) -> None:
        status, body = self._poster("/end_game", json.dumps({"result": "lose"}).encode("utf-8"))
        print(f"[match-loop] end_game(lose) -> HTTP {status} {body[:120]}", flush=True)

    def _restart_map(self) -> None:
        status, body = self._poster("/restart_map", json.dumps({"op": "reset"}).encode("utf-8"))
        print(f"[match-loop] restart_map(reset) -> HTTP {status} {body[:120]}", flush=True)

    # --- ein Poll-Schritt -------------------------------------------------
    def step(self) -> Optional[str]:
        """Ein Iterationsschritt. Liefert eine Aktion ("defeat"/"restart") oder None."""
        # Phase 2: Defeat erkannt, auf Restart-Delay warten.
        if self.defeat_at is not None:
            if self._clock() - self.defeat_at >= self.restart_delay:
                self._restart_map()
                self.defeat_at = None
                self.seen_alive = False
                return "restart"
            return None

        view = self._get_state()
        if not view.get("ok"):
            return None  # Welt noch nicht bereit -> nichts tun

        if is_alive(view):
            if not self.seen_alive:
                self.seen_alive = True
                return "alive"
            return None

        # HP null/0: nur ein Defeat, wenn das HQ in dieser Runde schon lebte.
        if self.seen_alive and is_defeat(view):
            self._end_game()
            self.defeat_at = self._clock()
            self.seen_alive = False
            return "defeat"

        return None


def run(
    base_url: str,
    poll_interval: float = 1.0,
    restart_delay: float = 10.0,
    once: bool = False,
    timeout: float = 5.0,
    _sleep: Callable[[float], None] = time.sleep,
    _poster: Optional[Callable[[str, bytes], tuple]] = None,
    _clock: Callable[[], float] = time.monotonic,
):
    loop = MatchLoop(base_url, restart_delay=restart_delay, timeout=timeout, _poster=_poster, _clock=_clock)
    while True:
        loop.step()
        if once:
            break
        _sleep(poll_interval)
    return loop


def build_parser():
    p = argparse.ArgumentParser(description="RBBattle Match-Loop (Issue #730)")
    p.add_argument(
        "--bridge-url",
        default=os.environ.get("RBB_BRIDGE_URL") or "http://127.0.0.1:9001",
        help="Bridge-Basis-URL (Default: RBB_BRIDGE_URL oder http://127.0.0.1:9001)",
    )
    p.add_argument("--poll-interval", type=float, default=1.0)
    p.add_argument("--restart-delay", type=float, default=10.0, help="Sekunden zwischen LOST und Restart")
    p.add_argument("--once", action="store_true", help="Einen Poll ausführen, dann beenden")
    p.add_argument("--timeout", type=float, default=5.0, help="HTTP-Timeout je Request")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    run(
        args.bridge_url,
        poll_interval=args.poll_interval,
        restart_delay=args.restart_delay,
        once=args.once,
        timeout=args.timeout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
