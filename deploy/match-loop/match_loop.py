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

# VS-HQ-Reporter (Issue #996, US6): Ist ``RBB_REFEREE_URL`` gesetzt, meldet der
# Loop bei HQ-Wertänderung ``POST /report {world, event:"hq_hp", hp}`` und beim
# bestaetigten HQ-Tod zusaetzlich ``event:"hq_dead"`` an den Referee — der ist
# dann die Autoritaet ueber das Match-Ende (kein lokales ``end_game``).
# ``RBB_VS_WORLD`` nennt die eigene Welt (Default "A"). Ohne Referee-URL bleibt
# das SOLO-Verhalten (Latch + end_game + restart_map) bitgleich.
DEFAULT_VS_WORLD = "A"

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
        referee_url: Optional[str] = None,
        vs_world: str = DEFAULT_VS_WORLD,
        _poster: Optional[Callable[[str, bytes], tuple]] = None,
        _clock: Callable[[], float] = time.monotonic,
    ):
        self.base_url = base_url.rstrip("/")
        self.restart_delay = restart_delay
        self.timeout = timeout
        self._poster = _poster or self._http_post
        self._clock = _clock

        # VS-HQ-Reporter (US6, #996). Ohne referee_url = SOLO (unveraendert).
        self.referee_url = (referee_url or "").rstrip("/") or None
        self.vs_world = str(vs_world or DEFAULT_VS_WORLD).strip().upper() or DEFAULT_VS_WORLD

        self.seen_alive = False
        self.defeat_at: Optional[float] = None  # gesetzt bei Defeat, bis Restart
        self.last_reported_hp: Optional[float] = None  # letzter an Referee gemeldeter Wert

    # --- VS-HQ-Reporter (Cross-World, US6/#996) ---------------------------
    def _report(self, payload: Dict[str, object]) -> Optional[Dict[str, object]]:
        """POSTet ein Report-Event an den Referee (nicht-fatal, log-only).

        Rueckgabe: geparste JSON-Antwort oder None (kein/fremder Body).
        """
        if not self.referee_url:
            return None
        try:
            status, body = self._poster(
                self.referee_url + "/report", json.dumps(payload).encode("utf-8")
            )
        except Exception as e:  # nie den Loop blockieren
            print(f"[match-loop] report {payload.get('event')} fehlgeschlagen: {e}", flush=True)
            return None
        parsed: Optional[Dict[str, object]] = None
        try:
            obj = json.loads(body)
            if isinstance(obj, dict):
                parsed = obj
        except (ValueError, TypeError):
            parsed = None
        print(
            f"[match-loop] report {payload.get('event')} -> HTTP {status} "
            f"{str(body)[:120]}",
            flush=True,
        )
        return parsed

    def _report_hq(self, hp: float) -> Optional[Dict[str, object]]:
        """meldet den aktuellen HQ-HP an den Referee (event `hq_hp`)."""
        if not self.referee_url:
            return None
        return self._report({"world": self.vs_world, "event": "hq_hp", "hp": hp})

    def _report_hq_dead(self) -> Optional[Dict[str, object]]:
        """meldet den bestaetigten HQ-Tod an den Referee (event `hq_dead`)."""
        if not self.referee_url:
            return None
        return self._report({"world": self.vs_world, "event": "hq_dead"})

    # --- HTTP (urllib) ----------------------------------------------------
    def _http_post(self, path: str, body: bytes) -> tuple:
        # `path` kann base_url-relativ oder eine volle URL sein (Referee, US6).
        url = path if path.startswith(("http://", "https://")) else self.base_url + path
        req = urllib.request.Request(
            url,
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
        # Phase 2 (SOLO): Defeat erkannt, auf Restart-Delay warten. Im
        # Referee-Modus gibt es diesen Pfad nicht (der Referee ist Autoritaet).
        if self.referee_url is None and self.defeat_at is not None:
            if self._clock() - self.defeat_at >= self.restart_delay:
                self._restart_map()
                self.defeat_at = None
                self.seen_alive = False
                return "restart"
            return None

        view = self._get_state()
        if not view.get("ok"):
            return None  # Welt noch nicht bereit -> nichts tun (Latch unberuehrt)

        if is_alive(view):
            # Referee-Modus: HQ-Wert bei Aenderung melden (kein Spam).
            hp = view.get("hq_hp")
            if (
                self.referee_url
                and isinstance(hp, (int, float))
                and hp != self.last_reported_hp
            ):
                self._report_hq(float(hp))
                self.last_reported_hp = float(hp)
            if not self.seen_alive:
                self.seen_alive = True
                return "alive"
            return None

        # HP null/0: nur ein Defeat, wenn das HQ in dieser Runde schon lebte.
        if self.seen_alive and is_defeat(view):
            if self.referee_url:
                # Der Referee entscheidet das Match-Ende (Sieger). Wir melden
                # den finalen HP (falls ermittelbar) + den bestaetigten Tod und
                # beenden NICHT selbst per end_game/restart_map.
                hp = view.get("hq_hp")
                if isinstance(hp, (int, float)) and float(hp) != self.last_reported_hp:
                    self._report_hq(float(hp))
                    self.last_reported_hp = float(hp)
                resp = self._report_hq_dead()
                if resp and resp.get("match_over"):
                    print(
                        f"[match-loop] match_over (winner={resp.get('winner')}) — "
                        "Referee ist Autoritaet, kein lokaler end_game",
                        flush=True,
                    )
                self.seen_alive = False
                return "defeat"
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
    referee_url: Optional[str] = None,
    vs_world: str = DEFAULT_VS_WORLD,
    _sleep: Callable[[float], None] = time.sleep,
    _poster: Optional[Callable[[str, bytes], tuple]] = None,
    _clock: Callable[[], float] = time.monotonic,
):
    loop = MatchLoop(
        base_url,
        restart_delay=restart_delay,
        timeout=timeout,
        referee_url=referee_url,
        vs_world=vs_world,
        _poster=_poster,
        _clock=_clock,
    )
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
    p.add_argument(
        "--referee-url",
        default=os.environ.get("RBB_REFEREE_URL") or None,
        help=(
            "Referee-Basis-URL fuer den HQ-Reporter (VS, US6/#996). Gesetzt → "
            "POST <url>/report {world,event:hq_hp|hq_dead}; sonst SOLO (end_game/restart_map) "
            "(Default RBB_REFEREE_URL)"
        ),
    )
    p.add_argument(
        "--vs-world",
        default=os.environ.get("RBB_VS_WORLD") or DEFAULT_VS_WORLD,
        help="Eigene Welt im VS (Default RBB_VS_WORLD oder A)",
    )
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
        referee_url=args.referee_url,
        vs_world=args.vs_world,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
