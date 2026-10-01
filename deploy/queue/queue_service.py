#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-queue-service — HTTP-Dienst der Casual-Queue (Issue #998, US3).

Der Dienst ueber :mod:`queue_flow` (:class:`QueueCoordinator`): er paart Spieler
(1v1, FIFO) und provisioniert bei einer Paarung **kalt** zwei frische VS-Welten
(A/B), registriert beide Spieler im Referee (`POST /lobby`) und fuehrt den
Match-Record. Kein Warm-Pool, kein MMR.

Endpunkte (JSON; Bearer-Token PFLICHT, wenn ``QUEUE_TOKEN`` gesetzt):

  * ``GET  /health``         -> ``200 {"ok":true,"env":…}``
  * ``GET  /queue/status``   -> Queue + Matches (Snapshot)
  * ``POST /queue/join``     -> ``{"identitaet","mode":"vs"}``; erst ``position``,
    bei vollstaendiger Paarung die Match-Antwort (A/B + Endpoints)
  * ``POST /queue/leave``    -> ``{"identitaet"}`` (nur wartende Spieler)
  * ``POST /queue/finish``   -> ``{"match_id","result"?}``; Ergebnis + kaltes Cleanup

Fehlerformat einheitlich ``{"ok":false,"reason":"<code>","detail":"…"}``;
``401`` ohne/mit falschem Bearer, ``400`` falscher Modus/Body, ``404``
unbekannte Route, ``405`` falsche Methode, ``409`` unbekannter Match/already
matched, ``503`` Provisioner/Referee nicht erreichbar.
"""

from __future__ import annotations

import argparse
import dataclasses
import hmac
import json
import logging
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Sequence
from urllib.parse import urlsplit

from queue_core import QueueError
from queue_flow import QueueCoordinator, build_coordinator

LOG = logging.getLogger("queue-service")

MAX_BODY_BYTES = 65536


class QueueConfigError(Exception):
    """Dienst-Konfiguration unbrauchbar — der Dienst darf so nicht starten."""


def _positive_number(env: Dict[str, str], var: str, default: Any, cast: Any) -> Any:
    """Env-Wert lesen: fehlend/leer -> Default; ungueltig/<=0 -> Fehler (fail-closed)."""
    raw = env.get(var)
    if raw is None or str(raw).strip() == "":
        return cast(default)
    try:
        value = cast(str(raw).strip())
    except (TypeError, ValueError):
        raise QueueConfigError("%s muss eine Zahl sein (war %r)" % (var, raw))
    if not value > 0:
        raise QueueConfigError("%s muss > 0 sein (war %r)" % (var, raw))
    return value


def _flag(env: Dict[str, str], var: str, default: bool = False) -> bool:
    raw = env.get(var)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _non_negative_number(env: Dict[str, str], var: str, default: Any, cast: Any) -> Any:
    """Env-Wert lesen: fehlend/leer -> Default; ungueltig/<0 -> Fehler (fail-closed).

    ``0`` ist erlaubt (bedeutet je nach Feld „aus"); negative Werte nicht.
    """
    raw = env.get(var)
    if raw is None or str(raw).strip() == "":
        return cast(default)
    try:
        value = cast(str(raw).strip())
    except (TypeError, ValueError):
        raise QueueConfigError("%s muss eine Zahl sein (war %r)" % (var, raw))
    if value < 0:
        raise QueueConfigError("%s muss >= 0 sein (war %r)" % (var, raw))
    return value


@dataclasses.dataclass
class QueueServiceConfig:
    """Dienst-Konfiguration aus ``QUEUE_*``."""

    env: str = "dev"
    bind: str = "127.0.0.1"
    port: int = 9221
    token: str = ""
    provisioner_url: str = "http://127.0.0.1:8094"
    provisioner_token: str = ""
    provisioner_timeout: float = 30.0
    referee_url: str = "http://127.0.0.1:8081"
    referee_token: str = ""
    timeout: float = 5.0
    state_dir: str = ""
    team_size: int = 1
    allow_teams: bool = False
    # Auto-Finish-Reconciler (#1028): Intervall in Sekunden; 0 = aus.
    reconcile_interval_s: float = 5.0
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "QueueServiceConfig":
        env = os.environ if env is None else env
        name = (env.get("QUEUE_ENV") or env.get("RIFT_ENV") or "dev").strip()
        return cls(
            env=name or "dev",
            bind=(env.get("QUEUE_BIND") or "127.0.0.1").strip() or "127.0.0.1",
            port=int(_positive_number(env, "QUEUE_PORT", 9221, int)),
            token=(env.get("QUEUE_TOKEN") or "").strip(),
            provisioner_url=(env.get("QUEUE_PROVISIONER_URL") or "http://127.0.0.1:8094").strip(),
            provisioner_token=(env.get("QUEUE_PROVISIONER_TOKEN") or "").strip(),
            provisioner_timeout=float(_positive_number(env, "QUEUE_PROVISIONER_TIMEOUT", 30.0, float)),
            referee_url=(env.get("QUEUE_REFEREE_URL") or "http://127.0.0.1:8081").strip(),
            referee_token=(env.get("QUEUE_REFEREE_TOKEN") or "").strip(),
            timeout=float(_positive_number(env, "QUEUE_TIMEOUT", 5.0, float)),
            state_dir=(env.get("QUEUE_STATE_DIR") or "").strip(),
            team_size=int(_positive_number(env, "QUEUE_TEAM_SIZE", 1, int)),
            allow_teams=_flag(env, "QUEUE_ALLOW_TEAMS", False),
            reconcile_interval_s=float(
                _non_negative_number(env, "QUEUE_RECONCILE_INTERVAL_S", 5.0, float)),
            log_level=(env.get("QUEUE_LOG_LEVEL") or "INFO").strip() or "INFO",
        )


def _match_payload(match: Dict[str, Any]) -> Dict[str, Any]:
    """Relay-taugliche Match-Antwort: id + assignments (mit target=Endpoint)."""
    participants = match.get("participants", []) or []
    assignments = [
        {
            "identitaet": p.get("identitaet"),
            "world": p.get("world"),
            "instance": p.get("instance"),
            "target": p.get("endpoint"),
        }
        for p in participants
    ]
    return {
        "match_id": match.get("match_id"),
        "mode": match.get("mode"),
        "state": match.get("state"),
        "result": match.get("result"),
        "participants": participants,
        "assignments": assignments,
    }


class Handler(BaseHTTPRequestHandler):
    """HTTP-Schicht: Auth, Routing, JSON."""

    server_version = "rbbattle-queue/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def coordinator(self) -> QueueCoordinator:
        return self.server.coordinator  # type: ignore[attr-defined]

    @property
    def token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    @property
    def env(self) -> str:
        return self.server.env  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        LOG.info("%s - %s", self.address_string(), fmt % args)

    # -- Helfer ------------------------------------------------------------
    def _send_json(self, status: int, payload: Dict[str, Any],
                   extra: Optional[Dict[str, str]] = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        expected = self.token
        if not expected:
            return True  # kein Token konfiguriert -> lokal offen (Tests/dev)
        header = self.headers.get("Authorization") or ""
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        provided = header[len(prefix):].strip().encode("utf-8")
        return hmac.compare_digest(provided, expected.encode("utf-8"))

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise QueueError("bad_request", "Request-Body zu gross", 400)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise QueueError("bad_request", "Body ist kein gueltiges JSON", 400)
        if not isinstance(payload, dict):
            raise QueueError("bad_request", "Body muss ein JSON-Objekt sein", 400)
        return payload

    # -- Routing -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        path = urlsplit(self.path).path.rstrip("/") or "/"
        try:
            if not self._authorized():
                self._send_json(
                    401, {"ok": False, "reason": "unauthorized"},
                    {"WWW-Authenticate": "Bearer"},
                )
                return

            if method == "GET" and path == "/health":
                self._send_json(200, {"ok": True, "env": self.env})
            elif method == "GET" and path == "/queue/status":
                snap = self.coordinator.status()
                self._send_json(200, dict({"ok": True}, **snap))
            elif method == "POST" and path == "/queue/join":
                payload = self._read_json()
                identitaet = payload.get("identitaet")
                if not isinstance(identitaet, str) or not identitaet.strip():
                    raise QueueError("bad_request", "identitaet fehlt", 400)
                mode = payload.get("mode", "vs")
                if mode != "vs":
                    raise QueueError("bad_mode", "mode muss 'vs' sein (war %r)" % (mode,), 400)
                team_size = payload.get("team_size")
                if team_size is not None:
                    try:
                        team_size = int(team_size)
                    except (TypeError, ValueError):
                        raise QueueError("bad_request", "team_size muss eine Zahl sein", 400)
                out = self.coordinator.join(identitaet, mode=mode, team_size=team_size)
                if out.get("status") == "matched":
                    self._send_json(200, {
                        "ok": True,
                        "status": "matched",
                        "identitaet": out.get("identitaet"),
                        "match": _match_payload(out["match"]),
                    })
                else:
                    self._send_json(200, {
                        "ok": True,
                        "status": "queued",
                        "identitaet": out.get("identitaet"),
                        "position": out.get("position"),
                    })
            elif method == "POST" and path == "/queue/leave":
                payload = self._read_json()
                identitaet = payload.get("identitaet")
                if not isinstance(identitaet, str) or not identitaet.strip():
                    raise QueueError("bad_request", "identitaet fehlt", 400)
                out = self.coordinator.leave(identitaet)
                self._send_json(200, {"ok": True, "identitaet": out["identitaet"]})
            elif method == "POST" and path == "/queue/finish":
                payload = self._read_json()
                match_id = payload.get("match_id")
                if match_id is None:
                    raise QueueError("bad_request", "match_id fehlt", 400)
                try:
                    match_id = int(match_id)
                except (TypeError, ValueError):
                    raise QueueError("bad_request", "match_id muss eine Zahl sein", 400)
                record = self.coordinator.finish(match_id, result=payload.get("result"))
                self._send_json(200, {"ok": True, "match": _match_payload(record)})
            elif path in ("/health", "/queue/status", "/queue/join",
                          "/queue/leave", "/queue/finish"):
                self._send_json(405, {"ok": False, "reason": "method_not_allowed", "method": method})
            else:
                self._send_json(404, {"ok": False, "reason": "not_found", "path": path})
        except QueueError as exc:
            self._send_json(exc.status, {"ok": False, "reason": exc.reason, "detail": exc.detail})
        except Exception:  # noqa: BLE001 - letzte Reihe, nie 500 ohne Body
            LOG.exception("unerwarteter Fehler")
            self._send_json(500, {"ok": False, "reason": "internal_error"})


def build_server(config: QueueServiceConfig, coordinator: QueueCoordinator) -> ThreadingHTTPServer:
    """Server instanziieren (ohne ``serve_forever``) — so auch von Tests nutzbar."""
    httpd = ThreadingHTTPServer((config.bind, config.port), Handler)
    httpd.daemon_threads = True
    httpd.coordinator = coordinator  # type: ignore[attr-defined]
    httpd.token = config.token  # type: ignore[attr-defined]
    httpd.env = config.env  # type: ignore[attr-defined]
    return httpd


def _reconciler_loop(coordinator: QueueCoordinator, interval: float,
                     stop_event: threading.Event) -> None:
    """Daemon-Takt: regelmaessig ``coordinator.reconcile()`` (#1028).

    Fehler sind nicht-fatal (Log + naechster Tick). ``stop_event`` beendet die
    Schleife sauber (kein blockierender Schlaf).
    """
    while not stop_event.wait(interval):
        try:
            outcome = coordinator.reconcile()
            if outcome.get("finished"):
                LOG.info("reconcile: %s", outcome)
        except Exception:  # noqa: BLE001 - Dienst darf nie am Tick sterben
            LOG.exception("reconcile fehlgeschlagen")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="rbbattle queue service")
    parser.add_argument("--check", action="store_true", help="nur Konfiguration pruefen und beenden")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("QUEUE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = QueueServiceConfig.from_env()
    except QueueConfigError as exc:
        LOG.error("%s", exc)
        return 2

    if args.check:
        LOG.info(
            "Konfiguration OK (bind=%s port=%s env=%s provisioner=%s referee=%s "
            "state_dir=%s reconcile_interval_s=%s)",
            config.bind, config.port, config.env, config.provisioner_url,
            config.referee_url, config.state_dir or "-", config.reconcile_interval_s,
        )
        return 0

    coordinator = build_coordinator(config)
    httpd = build_server(config, coordinator)

    # Auto-Finish-Reconciler (#1028): Daemon-Thread liest den Referee-Zustand
    # und finisht abgeschlossene Matches idempotent. 0 = aus.
    stop_event = threading.Event()
    if config.reconcile_interval_s > 0:
        threading.Thread(
            target=_reconciler_loop,
            args=(coordinator, config.reconcile_interval_s, stop_event),
            daemon=True,
        ).start()
        LOG.info("Reconciler aktiv (alle %.1fs)", config.reconcile_interval_s)

    def _shutdown(signum: int, _frame: Any) -> None:
        LOG.info("Signal %s empfangen — fahre herunter", signum)
        stop_event.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    LOG.info("queue-service laeuft auf %s:%s (env=%s)", config.bind, config.port, config.env)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
