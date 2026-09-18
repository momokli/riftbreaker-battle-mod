#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""attack-cycle — 7-Minuten-Angriffszyklus (PoC, ersetzt den 2-min-Order-Scheduler).

Ziel (PoC): anstatt jede gekaufte Welle 2 min nach dem Kauf einzeln zu feuern,
laeuft ein fester Zyklus, der beim Bau des HQ startet:

  round start (HQ gebaut)
    └─ alle `interval` Sekunden (Default 7 min) feuert EINE "natuerliche" Welle
       mit steigendem Level 1..9 (cap 9). Der Spieler kann waehrend des Fensters
       Wellen "kaufen" (`-send waveN`): jede wird SOFORT bezahlt (try_spend) und
       in eine Queue gestapelt; beim naechsten Zyklus-Tick werden sie GLEICHZEITIG
       mit der natuerlichen Welle gefeuert.

Ablauf je Zyklus-Tick:
  fire natural(level) + pending[]  ueber POST /activate_mission_flow
  level = min(level + 1, max_level), pending = [], next_attack_at += interval

Kauf (`POST /queue_send {"name":"waveN"}`):
  1. Cost-Tabelle (Spiegel der client-mod .ent-Preise) → cost
  2. POST /try_spend {"amount":"<cost>"} → SOFORT bezahlt (nur wenn Guthaben reicht)
  3. ok → level in pending stapeln; insufficient → 402 (nicht gestapelt)

Rein stdlib (HTTP via urllib), kein Netz-Dep im Test. Server-seitig (Sidecar),
NICHT in der Website, NICHT in der DLL. Pattern wie deploy/match-loop (pollt
get_state) + tools/wave-scheduler (Queue + activate_mission_flow).
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional

# Cost-Tabelle (Spiegel der client-mod .ent-Preise; Stand main #770/#205).
# Nur die Preise sind hier relevant; der Logic-Pfad ist ein einziges Template
# (siehe DEFAULT_LOGIC_TEMPLATE). level -> cost (Carbonium, Display-Einheiten).
WAVE_COST = {
    1: 300,
    2: 700,
    3: 1400,
    4: 2450,
    5: 4000,
    6: 5350,
    7: 7600,
    8: 9650,
    9: 10500,
}
NAME_TO_LEVEL = {f"wave{lvl}": lvl for lvl in WAVE_COST}

DEFAULT_MAX_LEVEL = 9
DEFAULT_INTERVAL_S = 420.0  # 7 min
DEFAULT_BRIDGE_URL = "http://127.0.0.1:9001"
DEFAULT_CONTROL_PORT = 9102

# Logic-Pfad je Level (Spiegel der SEND-MENU-Presets im Cockpit, die live
# Wellen spawnen). NICHT logic/dom/* — das loest nur "attack incoming" aus,
# ohne Spawn (pauseAttacks=true in sandbox). wave9 teilt den Pool mit wave8 (#658).
WAVE_LOGIC = {
    1: "logic/missions/survival/attack_level_1_entry.logic",
    2: "logic/missions/survival/attack_level_2_entry.logic",
    3: "logic/missions/survival/attack_level_3_id_1.logic",
    4: "logic/missions/survival/attack_level_4_id_1.logic",
    5: "logic/missions/survival/attack_level_5_id_1.logic",
    6: "logic/missions/survival/attack_level_6_id_1.logic",
    7: "logic/missions/survival/attack_level_7_id_1.logic",
    8: "logic/missions/survival/attack_level_8_id_1.logic",
    9: "logic/missions/survival/attack_level_8_id_1.logic",
}


def parse_hq_alive(raw: str) -> bool:
    """True, wenn get_state ok:true und hq_hp > 0 (HQ gebaut + lebt)."""
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return False
    if not isinstance(obj, dict) or not obj.get("ok"):
        return False
    hp = obj.get("hq_hp")
    return isinstance(hp, (int, float)) and hp > 0


def parse_send_level(body: str) -> Optional[int]:
    """Extrahiert das Wave-Level aus einem /queue_send-Body.

    Akzeptiert `{"name":"waveN"}` (send-tailer) oder `{"level":N}` (manuell).
    Liefert None bei ungueltigem/unbekanntem Namen.
    """
    try:
        obj = json.loads(body) if isinstance(body, str) else body
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None

    if "level" in obj:
        try:
            lvl = int(obj["level"])
        except (ValueError, TypeError):
            return None
        return lvl if lvl in WAVE_COST else None

    name = obj.get("name")
    if isinstance(name, str):
        return NAME_TO_LEVEL.get(name)

    return None


class AttackCycle:
    """Reine Zustandsmaschine + HTTP (injizierbarer Poster/Clock fuer Tests)."""

    def __init__(
        self,
        base_url: str,
        interval_s: float = DEFAULT_INTERVAL_S,
        max_level: int = DEFAULT_MAX_LEVEL,
        wave_logic: Optional[Dict[int, str]] = None,
        timeout: float = 5.0,
        _poster: Optional[Callable[[str, bytes], tuple]] = None,
        _clock: Callable[[], float] = time.monotonic,
    ):
        self.base_url = base_url.rstrip("/")
        self.interval_s = interval_s
        self.max_level = max_level
        self.wave_logic = wave_logic or WAVE_LOGIC
        self.timeout = timeout
        self._poster = _poster or self._http_post
        self._clock = _clock

        self._lock = threading.Lock()
        self.active = False
        self.level = 1
        self.next_attack_at: Optional[float] = None
        self.pending: list = []
        self.last_fire: Optional[Dict[str, Any]] = None

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

    def _post_json(self, path: str, payload: Dict[str, Any]) -> tuple:
        return self._poster(path, json.dumps(payload).encode("utf-8"))

    # --- get_state / HQ-Erkennung ----------------------------------------
    def _hq_alive(self) -> bool:
        status, body = self._poster("/get_state", b"{}")
        if not 200 <= status < 300:
            return False
        return parse_hq_alive(body)

    # --- try_spend (Kauf) -------------------------------------------------
    def _spend(self, cost: int) -> tuple:
        status, body = self._poster("/try_spend", json.dumps({"amount": str(cost)}).encode("utf-8"))
        ok = False
        try:
            ok = bool(json.loads(body).get("ok"))
        except (ValueError, TypeError, AttributeError):
            ok = False
        return status, ok, body

    # --- Kauf (aus /queue_send) ------------------------------------------
    def buy(self, level: int) -> tuple:
        """Bezahlt SOFORT via try_spend und stapelt die Welle in pending.

        Liefert (http_status, payload_dict).
        """
        if level not in WAVE_COST:
            return 400, {"ok": False, "reason": "unknown_wave"}
        cost = WAVE_COST[level]
        status, ok, body = self._spend(cost)
        if not 200 <= status < 300:
            return 502, {"ok": False, "reason": "bridge_unreachable", "status": status}
        if not ok:
            return 402, {"ok": False, "reason": "insufficient", "detail": body[:160]}

        with self._lock:
            self.pending.append(level)
            queue_len = len(self.pending)
        print(f"[attack-cycle] buy wave{level} -> gestapelt (cost={cost}, queue={queue_len})", flush=True)
        return 200, {"ok": True, "queued_level": level, "queue_length": queue_len}

    # --- Feuern -----------------------------------------------------------
    def _fire(self, level: int) -> None:
        logic = self.wave_logic.get(level, self.wave_logic.get(self.max_level, ""))
        status, body = self._post_json(
            "/activate_mission_flow",
            {"logic": logic, "mode": "default"},
        )
        print(f"[attack-cycle] fire wave{level} logic={logic} -> HTTP {status} {body[:120]}", flush=True)

    # --- Ein Poll/Tick-Schritt -------------------------------------------
    def step(self) -> Optional[str]:
        """Ein Iterationsschritt. Liefert eine Aktion ("started"/"attack") oder None."""
        now = self._clock()

        if not self.active:
            if self._hq_alive():
                with self._lock:
                    self.active = True
                    self.level = 1
                    self.next_attack_at = now + self.interval_s
                print(f"[attack-cycle] HQ gebaut -> Zyklus gestartet (Level 1 in {self.interval_s:.0f}s)", flush=True)
                return "started"
            return None

        with self._lock:
            if self.next_attack_at is None or now < self.next_attack_at:
                return None
            natural_level = self.level
            sent_levels = list(self.pending)
            self.pending = []
            self.level = min(self.level + 1, self.max_level)
            self.next_attack_at = self.next_attack_at + self.interval_s

        self._fire(natural_level)
        for lvl in sent_levels:
            self._fire(lvl)
        with self._lock:
            self.last_fire = {"natural_level": natural_level, "sent_levels": sent_levels, "t": now}
        print(f"[attack-cycle] attack: natural={natural_level} + sent={sent_levels}", flush=True)
        return "attack"

    # --- Status -----------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        now = self._clock()
        with self._lock:
            return {
                "active": self.active,
                "level": self.level,
                "seconds_to_next_attack": (
                    max(0.0, self.next_attack_at - now) if self.next_attack_at is not None else None
                ),
                "pending": list(self.pending),
                "interval_s": self.interval_s,
                "max_level": self.max_level,
                "last_fire": self.last_fire,
            }

    # --- Status an die Bridge pushen (WebUI) ----------------------------
    def push_status(self) -> None:
        """Pusht den Status-JSON an die Bridge (POST /attack_status)."""
        try:
            self._post_json("/attack_status", self.status())
        except Exception:
            pass


class ControlHandler(BaseHTTPRequestHandler):
    cycle: AttackCycle = None  # gesetzt von build_control_server()

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # weniger Rauschen

    def _write_json(self, status: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/status":
            self._write_json(200, self.cycle.status())
        else:
            self._write_json(404, {"ok": False, "reason": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/queue_send":
            self._write_json(404, {"ok": False, "reason": "not_found"})
            return
        length = int(self.headers.get("content-length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        level = parse_send_level(raw.decode("utf-8") or "{}")
        if level is None:
            self._write_json(400, {"ok": False, "reason": "invalid_request"})
            return
        status, payload = self.cycle.buy(level)
        self._write_json(status, payload)


def build_control_server(bind: str, port: int, cycle: AttackCycle) -> ThreadingHTTPServer:
    handler_cls = type("BoundControlHandler", (ControlHandler,), {"cycle": cycle})
    return ThreadingHTTPServer((bind, port), handler_cls)


def run(
    cycle: AttackCycle,
    control_bind: str,
    control_port: int,
    poll_interval: float = 1.0,
    once: bool = False,
    _sleep: Callable[[float], None] = time.sleep,
) -> AttackCycle:
    httpd = build_control_server(control_bind, control_port, cycle)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    print(
        f"[attack-cycle] control auf http://{control_bind}:{control_port} (GET /status, POST /queue_send)",
        flush=True,
    )
    try:
        while True:
            cycle.step()
            cycle.push_status()
            if once:
                break
            _sleep(poll_interval)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        thread.join(timeout=2.0)
    return cycle


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="RBBattle Attack-Cycle (PoC)")
    p.add_argument(
        "--bridge-url",
        default=os.environ.get("RBB_BRIDGE_URL") or DEFAULT_BRIDGE_URL,
        help="Bridge-Basis-URL (ohne Pfad; Default: RBB_BRIDGE_URL oder http://127.0.0.1:9001)",
    )
    p.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL_S, help="Zyklus-Intervall in Sekunden (Default 420 = 7 min)"
    )
    p.add_argument("--max-level", type=int, default=DEFAULT_MAX_LEVEL, help="Max. natuerliches Level (Default 9)")
    p.add_argument("--control-bind", default="0.0.0.0", help="Bind-Adresse des Control-Servers")
    p.add_argument(
        "--control-port", type=int, default=DEFAULT_CONTROL_PORT, help="Port des Control-Servers (Default 9102)"
    )
    p.add_argument("--poll-interval", type=float, default=1.0)
    p.add_argument("--timeout", type=float, default=5.0, help="HTTP-Timeout je Request")
    p.add_argument("--once", action="store_true", help="Einen Poll ausfuehren, dann beenden")
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    cycle = AttackCycle(
        args.bridge_url,
        interval_s=args.interval,
        max_level=args.max_level,
        timeout=args.timeout,
    )
    run(cycle, args.control_bind, args.control_port, poll_interval=args.poll_interval, once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
