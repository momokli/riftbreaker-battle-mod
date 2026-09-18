#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wave_scheduler.py - Freeplay-Wellen-Timer (Wave-Scheduling ohne DOM).

Hintergrund: Im Freeplay/Sandbox laeuft die vellen-Engine des Spiels
(`dom_mananger`, `lua/missions/v2/dom_manager.lua`) mit `pauseAttacks = true`
bzw. `wave_strength = sandbox` - es kommen also keine natuerlichen Wellen
(siehe docs/research/515-survival-modes.md §4/§7). Dieses Script baut NICHT
die komplette DOM-State-Machine nach (kein HQ-Upgrade-Pfad, keine
Idle-Events, kein Multiplayer-Wave-Pool - siehe docs/research/515), sondern
nur den Teil, der fuer manuelles Wellen-Timing im Freeplay fehlt: einen
simplen Tick-Loop, der

  1. eine "natuerliche" Welle in einem konfigurierbaren Intervall ausloest
     (Level 1..9, Eskalationsgeschwindigkeit frei einstellbar), UND
  2. extern angemeldete ("gesendete") Wellen sammelt und beim naechsten
     ohnehin faelligen Tick GLEICHZEITIG mit der natuerlichen Welle feuert -
     mit demselben `spawn_point`-Wert, sofern das Feld genutzt wird.

Ausloesen laeuft ausschliesslich ueber die bereits live verifizierte
`POST /activate_mission_flow`-Route des `pipe_bridge` (Baustein 04/#265,
s. server/pipe-bridge/pipe_bridge.c, docs/research/dedicated-io-write-functions.md
#385/#386) - dieses Script ist ein reiner HTTP-Client, kein neuer nativer
Hook und keine Aenderung an DLL/Pipe-Bridge.

Caveats (siehe docs/research/dedicated-io-write-functions.md #385/#386):
  - Nur `logic/dom/attack_level_1_entry.logic` ist live verifiziert. Ob die
    Pfade fuer Level 2..9 (`attack_level_<n>_entry.logic`) real existieren,
    ist NICHT bestaetigt - vor produktivem Einsatz mit hoeheren Leveln erst
    einzeln pruefen (`get_state` nach dem Aufruf: `ok:true` + `mission_flow`
    gesetzt = Pfad existiert).
  - Ob `spawn_point` den tatsaechlichen Spawn-Ort im laufenden Spiel steuert,
    ist ein offener Punkt (#386, "nur mit Player pruefbar"). Klappt es nicht,
    feuern beide Wellen trotzdem gleichzeitig - nur eben ohne garantierten
    gemeinsamen Spawn-Punkt.
  - Die Default-Intervalle/Eskalationszeiten unten sind FREI GEWAEHLTE
    Platzhalter, keine aus dem Spiel extrahierten Vanilla-Werte (die
    zugrundeliegenden DifficultyService-Getter sind C++-seitig, siehe
    docs/research/515-survival-modes.md §8) - per --config anpassen.

Nur Standardbibliothek (wie pipe_client.py / poller_example.py) - kein pip
noetig.
"""

from __future__ import annotations

import argparse
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("wave_scheduler")

DEFAULT_BRIDGE_URL = "http://127.0.0.1:9001"
DEFAULT_LOGIC_TEMPLATE = "logic/dom/attack_level_{level}_entry.logic"
DEFAULT_MAX_LEVEL = 9

# Platzhalter-Zeitwerte (Sekunden), Index = Level (1..9). Frei anpassbar via
# --config (JSON, siehe config.example.json). Keine Vanilla-Werte, s. Docstring.
DEFAULT_INTERVAL_S = {lvl: 90.0 for lvl in range(1, DEFAULT_MAX_LEVEL + 1)}
DEFAULT_TIME_TO_NEXT_LEVEL_S = {lvl: 180.0 for lvl in range(1, DEFAULT_MAX_LEVEL + 1)}


@dataclass
class SchedulerConfig:
    bridge_url: str = DEFAULT_BRIDGE_URL
    logic_template: str = DEFAULT_LOGIC_TEMPLATE
    spawn_point: str = ""
    max_level: int = DEFAULT_MAX_LEVEL
    start_level: int = 1
    start_delay_s: float = 30.0
    tick_s: float = 1.0
    interval_s: Dict[int, float] = field(default_factory=lambda: dict(DEFAULT_INTERVAL_S))
    time_to_next_level_s: Dict[int, float] = field(
        default_factory=lambda: dict(DEFAULT_TIME_TO_NEXT_LEVEL_S)
    )
    dry_run: bool = False
    request_timeout_s: float = 5.0

    def interval_for(self, level: int) -> float:
        return self.interval_s.get(level, DEFAULT_INTERVAL_S[DEFAULT_MAX_LEVEL])

    def time_to_next_level_for(self, level: int) -> float:
        return self.time_to_next_level_s.get(level, DEFAULT_TIME_TO_NEXT_LEVEL_S[DEFAULT_MAX_LEVEL])


def load_config(path: Optional[str], overrides: argparse.Namespace) -> SchedulerConfig:
    cfg = SchedulerConfig()
    if path:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if "interval_s" in raw:
            cfg.interval_s = {int(k): float(v) for k, v in raw["interval_s"].items()}
        if "time_to_next_level_s" in raw:
            cfg.time_to_next_level_s = {
                int(k): float(v) for k, v in raw["time_to_next_level_s"].items()
            }
        for key in ("bridge_url", "logic_template", "spawn_point", "max_level",
                    "start_level", "start_delay_s", "tick_s"):
            if key in raw:
                setattr(cfg, key, raw[key])

    # CLI-Flags haben Vorrang vor der Config-Datei.
    if overrides.bridge_url is not None:
        cfg.bridge_url = overrides.bridge_url
    if overrides.logic_template is not None:
        cfg.logic_template = overrides.logic_template
    if overrides.spawn_point is not None:
        cfg.spawn_point = overrides.spawn_point
    if overrides.start_level is not None:
        cfg.start_level = overrides.start_level
    if overrides.start_delay is not None:
        cfg.start_delay_s = overrides.start_delay
    if overrides.dry_run:
        cfg.dry_run = True
    return cfg


class WaveScheduler:
    """Reiner Timer/Queue-Zustand - KEIN Netzwerk-IO in dieser Klasse.

    `tick()` haelt den Lock nur fuer die Zustandsaenderung; das eigentliche
    Feuern (HTTP-Call) macht der Aufrufer danach ausserhalb des Locks, damit
    ein haengender Request nie die Queue blockiert.
    """

    def __init__(self, cfg: SchedulerConfig, now: float):
        self.cfg = cfg
        self._lock = threading.Lock()
        self.current_level = cfg.start_level
        self.next_wave_at = now + cfg.start_delay_s
        self.next_difficulty_at = now + cfg.start_delay_s + cfg.time_to_next_level_for(cfg.start_level)
        self.pending: List[int] = []
        self.last_fire: Optional[Dict[str, Any]] = None

    def queue_send(self, level: int) -> int:
        """Reiht eine gesendete Welle ein; feuert beim naechsten faelligen Tick
        zusammen mit der naechsten natuerlichen Welle. Gibt die Queue-Laenge
        danach zurueck."""
        with self._lock:
            self.pending.append(level)
            return len(self.pending)

    def tick(self, now: float) -> Optional[Dict[str, Any]]:
        """Prueft Faelligkeiten. Gibt None zurueck, wenn keine Welle faellig
        ist, sonst {"natural_level": int, "sent_levels": [int, ...]}."""
        with self._lock:
            while self.current_level < self.cfg.max_level and now >= self.next_difficulty_at:
                self.current_level += 1
                self.next_difficulty_at += self.cfg.time_to_next_level_for(self.current_level)
                LOG.info("Difficulty erhoeht -> Level %d", self.current_level)

            if now < self.next_wave_at:
                return None

            sent_levels = self.pending
            self.pending = []
            natural_level = self.current_level
            self.next_wave_at = now + self.cfg.interval_for(self.current_level)
            return {"natural_level": natural_level, "sent_levels": sent_levels}

    def set_last_fire(self, info: Dict[str, Any]) -> None:
        with self._lock:
            self.last_fire = info

    def status(self, now: float) -> Dict[str, Any]:
        with self._lock:
            return {
                "current_level": self.current_level,
                "seconds_to_next_wave": max(0.0, self.next_wave_at - now),
                "seconds_to_next_difficulty": max(0.0, self.next_difficulty_at - now),
                "pending": list(self.pending),
                "last_fire": self.last_fire,
                "spawn_point": self.cfg.spawn_point,
                "dry_run": self.cfg.dry_run,
            }


def post_json(url: str, payload: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"content-type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fire_wave(cfg: SchedulerConfig, level: int, *, natural: bool) -> Dict[str, Any]:
    logic = cfg.logic_template.format(level=level)
    payload = {"logic": logic, "mode": "default", "spawn_point": cfg.spawn_point}
    tag = "natural" if natural else "sent"
    if cfg.dry_run:
        LOG.info("[dry-run] wuerde feuern (%s): %s", tag, payload)
        return {"ok": True, "dry_run": True, "logic": logic}
    url = cfg.bridge_url.rstrip("/") + "/activate_mission_flow"
    try:
        result = post_json(url, payload, cfg.request_timeout_s)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        LOG.warning("activate_mission_flow (%s, level=%d) HTTP %d: %s", tag, level, exc.code, body)
        return {"ok": False, "reason": "http_error", "status": exc.code, "body": body}
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        LOG.warning("activate_mission_flow (%s, level=%d) nicht erreichbar: %s", tag, level, exc)
        return {"ok": False, "reason": "unreachable", "error": str(exc)}
    LOG.info("Welle gefeuert (%s, level=%d): %s", tag, level, result)
    return result


class ControlHandler(BaseHTTPRequestHandler):
    scheduler: WaveScheduler = None  # gesetzt von build_control_server()

    def log_message(self, fmt: str, *args: Any) -> None:  # weniger Rauschen
        LOG.debug("control: " + fmt, *args)

    def _write_json(self, status: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/status":
            self._write_json(200, self.scheduler.status(time.monotonic()))
        else:
            self._write_json(404, {"ok": False, "reason": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/queue_wave":
            self._write_json(404, {"ok": False, "reason": "not_found"})
            return
        length = int(self.headers.get("content-length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
            level = int(body["level"])
        except (ValueError, KeyError, TypeError):
            self._write_json(400, {"ok": False, "reason": "invalid_request"})
            return
        queue_len = self.scheduler.queue_send(level)
        LOG.info("Wave-Send eingereiht: level=%d (Queue-Laenge=%d)", level, queue_len)
        self._write_json(200, {"ok": True, "queued_level": level, "queue_length": queue_len})


def build_control_server(bind: str, port: int, scheduler: WaveScheduler) -> ThreadingHTTPServer:
    handler_cls = type("BoundControlHandler", (ControlHandler,), {"scheduler": scheduler})
    return ThreadingHTTPServer((bind, port), handler_cls)


def run(cfg: SchedulerConfig, control_bind: str, control_port: int) -> None:
    now = time.monotonic()
    scheduler = WaveScheduler(cfg, now)

    httpd = build_control_server(control_bind, control_port, scheduler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    LOG.info(
        "Control-Server auf http://%s:%d (GET /status, POST /queue_wave {level:N})",
        control_bind, control_port,
    )
    LOG.info(
        "Bridge=%s, Start-Level=%d, Start-Delay=%.0fs, dry_run=%s",
        cfg.bridge_url, cfg.start_level, cfg.start_delay_s, cfg.dry_run,
    )

    try:
        while True:
            now = time.monotonic()
            due = scheduler.tick(now)
            if due is not None:
                last = fire_wave(cfg, due["natural_level"], natural=True)
                for level in due["sent_levels"]:
                    fire_wave(cfg, level, natural=False)
                scheduler.set_last_fire({
                    "natural_level": due["natural_level"],
                    "sent_levels": due["sent_levels"],
                    "result": last,
                })
            time.sleep(cfg.tick_s)
    except KeyboardInterrupt:
        LOG.info("Beende (Ctrl+C) ...")
    finally:
        httpd.shutdown()
        thread.join(timeout=2.0)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeplay-Wellen-Timer: feuert periodisch activate_mission_flow "
                    "und queued externe 'gesendete' Wellen auf den naechsten Tick."
    )
    parser.add_argument("--config", help="JSON-Config (siehe config.example.json)")
    parser.add_argument("--bridge-url", default=None, help=f"pipe_bridge HTTP-Endpunkt (Default {DEFAULT_BRIDGE_URL})")
    parser.add_argument("--logic-template", default=None,
                         help="Logic-Pfad-Template mit {level} (Default '%s')" % DEFAULT_LOGIC_TEMPLATE)
    parser.add_argument("--spawn-point", default=None,
                         help="spawn_point-Wert (#386), wird an natuerliche UND gesendete "
                              "Wellen desselben Ticks gehaengt")
    parser.add_argument("--start-level", type=int, default=None, help="Start-Difficulty-Level (Default 1)")
    parser.add_argument("--start-delay", type=float, default=None, help="Sekunden bis zur ersten Welle (Default 30)")
    parser.add_argument("--dry-run", action="store_true", help="nur loggen, keine echten HTTP-Calls an pipe_bridge")
    parser.add_argument("--control-bind", default="127.0.0.1", help="Bind-Adresse des lokalen Control-Servers")
    parser.add_argument("--control-port", type=int, default=9101, help="Port des lokalen Control-Servers")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug-Logging")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    cfg = load_config(args.config, args)
    run(cfg, args.control_bind, args.control_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
