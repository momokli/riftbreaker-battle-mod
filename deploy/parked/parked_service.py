#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-parked-service — Dienst um den Warm-Pool (Issue #928).

Der **fehlende Dienst** ueber ``parked_pool.py`` (#909): ``ParkedPool``
(``warm_up/claim/recycle/reap/status``) existiert, aber **niemand ruft es auf**.
Dieser Dienst

  * haelt **N** Instanzen warm (Zielzustand ``PARKED``),
  * bietet ``claim()`` als Anfrage-Schnittstelle an (fuer den Proxy-Claim #929),
  * recycelt nach Rundenende (``POST /recycle``),
  * reapt periodisch (Auslaufschutz, ``max_park_seconds``) und
  * hat einen Status/Health-Endpoint (PARKED/CLAIMED-Zaehler).

Ein Dienst **pro Env** (systemd ``rbmods-parked-<env>``). Nur
Standardbibliothek; Docker-/Bridge-Arbeit macht der injizierte ``ParkedPool``
(kein Docker-Code hier).

Endpunkte (JSON; Bearer-Token PFLICHT, wenn ``PARKED_TOKEN`` gesetzt):

  * ``GET  /health``   -> ``200 {"ok":true,"env":…}`` (Liveness des Dienstes)
  * ``GET  /status``   -> ``200 {counters, entries}``
  * ``POST /claim``    -> Instanz uebergeben (FIFO, aelteste ``PARKED``)
  * ``POST /recycle``  -> nach Rundenende wieder ``PARKED`` (oder ``STOPPED``);
                          optionales ``result`` (``win``/``lose``) wird an
                          ``pool.recycle`` durchgereicht und loest dort
                          ``end_game(result)`` aus (ohne ``result`` kein ``end_game``)
  * ``POST /reap``     -> manueller Auslaufschutz-Lauf

Fehlerformat einheitlich ``{"ok":false,"reason":"<code>","detail":"…"}``;
``401`` ohne/mit falschem Bearer, ``404`` unbekannte Route, ``409`` falscher
Zustand / kein Slot, ``503`` Bridge/Reap nicht verfuegbar.
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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from parked_pool import ParkedError, ParkedPool, ParkedState

LOG = logging.getLogger("parked-service")

# Rollierendes Fenster fuer den Handover-Durchschnitt.
HANDOVER_WINDOW = 20
# Obergrenze des Warm-Backoffs (verhindert Spin bei kaputtem Provisioner).
DEFAULT_BACKOFF_CAP = 30.0
# Schutzgrenze fuer den Request-Body.
MAX_BODY_BYTES = 65536


class ParkedConfigError(Exception):
    """Dienst-Konfiguration unbrauchbar — der Dienst darf so nicht starten."""


class ServiceError(Exception):
    """Fachlicher HTTP-Fehler (Statuscode + reason + detail)."""

    def __init__(self, status: int, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.status = status
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------
# US1 — Config (PARKED_*, fail-closed)
# ---------------------------------------------------------------------------


def _positive_number(env: Dict[str, str], var: str, default: Any, cast: Any) -> Any:
    """Env-Wert lesen: fehlend/leer -> Default; ungueltig/<=0 -> Fehler.

    Fail-closed wie ``server_control.load_config``: eine kaputte Zahl bricht den
    Start ab, statt still einen Default zu nehmen.
    """
    raw = env.get(var)
    if raw is None or str(raw).strip() == "":
        return cast(default)
    try:
        value = cast(str(raw).strip())
    except (TypeError, ValueError):
        raise ParkedConfigError("%s muss eine Zahl sein (war %r)" % (var, raw))
    # ``not value > 0`` faengt auch ``NaN``/``inf``-Fuellwerte ab.
    if not value > 0:
        raise ParkedConfigError("%s muss > 0 sein (war %r)" % (var, raw))
    return value


@dataclasses.dataclass
class ParkedServiceConfig:
    """Dienst-Konfiguration aus ``PARKED_*``; ``PROVISIONER_*`` werden an
    ``provisioner.load_config`` durchgereicht (nicht hier gelesen)."""

    env: str = "test"
    bind: str = "127.0.0.1"
    port: int = 8092
    pool_size: int = 1
    max_park_seconds: float = 900.0
    reap_interval: float = 30.0
    instance_prefix: str = "parked"
    token: str = ""
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "ParkedServiceConfig":
        env = os.environ if env is None else env
        name = (env.get("PARKED_ENV") or env.get("PROVISIONER_ENV") or "test").strip()
        prefix = (env.get("PARKED_INSTANCE_PREFIX") or "parked").strip() or "parked"
        return cls(
            env=name or "test",
            bind=(env.get("PARKED_BIND") or "127.0.0.1").strip() or "127.0.0.1",
            port=int(_positive_number(env, "PARKED_PORT", 8092, int)),
            pool_size=int(_positive_number(env, "PARKED_POOL_SIZE", 1, int)),
            max_park_seconds=float(_positive_number(env, "PARKED_MAX_PARK_SECONDS", 900.0, float)),
            reap_interval=float(_positive_number(env, "PARKED_REAP_INTERVAL", 30.0, float)),
            instance_prefix=prefix,
            token=(env.get("PARKED_TOKEN") or "").strip(),
            log_level=(env.get("PARKED_LOG_LEVEL") or "INFO").strip() or "INFO",
        )


def _provisioner_module() -> Any:
    """``provisioner`` importieren (Repo-Sibling oder installierter Pfad)."""
    try:
        import provisioner as module  # type: ignore

        return module
    except ImportError:
        here = os.path.dirname(os.path.abspath(__file__))
        candidate = os.path.join(os.path.dirname(here), "provisioner")
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
        import provisioner as module  # type: ignore

        return module


def build_provisioner(config: ParkedServiceConfig, env: Optional[Dict[str, str]] = None) -> Any:
    """Provisioner aus ``PROVISIONER_*`` bauen; fehlendes Image ⇒ laut abbrechen."""
    module = _provisioner_module()
    try:
        provisioner_cfg = module.load_config(env)
    except module.ConfigError as exc:
        raise ParkedConfigError(str(exc))
    return module.Provisioner(provisioner_cfg)


# ---------------------------------------------------------------------------
# US2 — ParkedController (warm halten + periodischer Reap)
# ---------------------------------------------------------------------------


class ParkedController(object):
    """Haelt ``pool_size`` Instanzen ``PARKED`` und reapt periodisch.

    ``warm_up``-Fehler stoppen die Loop NICHT; ein exponentielles Backoff (Cap)
    verhindert Spin und ``warm_failures`` zaehlt. ``clock``/``sleep`` sind
    injizierbar (Tests: Fakes). ``stop()`` bricht nur den Thread ab — laufende
    ``PARKED``-Instanzen werden NICHT gestoppt (das ist ``reap``s Aufgabe).
    """

    def __init__(
        self,
        pool: ParkedPool,
        pool_size: int = 1,
        max_park_seconds: float = 900.0,
        reap_interval: float = 30.0,
        env: Optional[str] = None,
        prefix: str = "parked",
        clock: Any = time.monotonic,
        sleep: Any = time.sleep,
        backoff_cap: float = DEFAULT_BACKOFF_CAP,
    ) -> None:
        self.pool = pool
        self.pool_size = int(pool_size)
        self.max_park_seconds = float(max_park_seconds)
        self.reap_interval = float(reap_interval)
        self.env = env
        self.prefix = prefix
        self.clock = clock
        self.sleep = sleep
        self.backoff_cap = float(backoff_cap)

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._counter = 0
        self._backoff = 1.0
        self._warm_retry_at = 0.0
        self._last_reap = clock()

        # Lifetime-Zaehler (Laufzeit des Dienstes).
        self.claims = 0
        self.recycles = 0
        self.reaps = 0
        self.warm_failures = 0
        self._handovers: List[float] = []

    # -- intern ------------------------------------------------------------
    def _rows(self) -> List[Dict[str, Any]]:
        return self.pool.status()

    def _states(self) -> Dict[str, str]:
        return {row["instance"]: row["state"] for row in self._rows()}

    def _parked_rows(self) -> List[Dict[str, Any]]:
        return [row for row in self._rows() if row["state"] == ParkedState.PARKED.value]

    def _next_id(self) -> str:
        """Frische ``instance_id`` (``<prefix>-<n>``), die der Pool noch nicht kennt.

        So kann ein nach ``warm_up``-Fehler als ``STOPPED`` hinterlassener Slot
        nicht die Wiederverwendung blockieren.
        """
        present = set(self._states())
        while True:
            self._counter += 1
            candidate = "%s-%d" % (self.prefix, self._counter)
            if candidate not in present:
                return candidate

    def _fill(self) -> None:
        """Fehlende ``PARKED``-Slots per ``warm_up`` bis ``pool_size`` fuellen."""
        if self.clock() < self._warm_retry_at:
            return
        while len(self._parked_rows()) < self.pool_size:
            instance_id = self._next_id()
            try:
                self.pool.warm_up(env=self.env, instance_id=instance_id)
            except ParkedError as exc:
                self.warm_failures += 1
                LOG.warning("warm_up %s fehlgeschlagen: %s", instance_id, exc)
                self._warm_retry_at = self.clock() + self._backoff
                self._backoff = min(self._backoff * 2.0, self.backoff_cap)
                return
            self._backoff = 1.0

    def reap_due(self) -> bool:
        return (self.clock() - self._last_reap) >= self.reap_interval

    def _reap(self) -> List[Any]:
        if not self.reap_due():
            return []
        self._last_reap = self.clock()
        self.reaps += 1
        try:
            return self.pool.reap(self.max_park_seconds)
        except ParkedError as exc:
            # Auslaufschutz bleibt best-effort: Loop laeuft weiter.
            LOG.warning("reap: %s", exc)
            return []

    def maintain_once(self) -> None:
        """Ein Wartungslauf: erst auffuellen, dann (falls faellig) reapen."""
        with self._lock:
            self._fill()
            self._reap()

    # -- oeffentliche Operationen -----------------------------------------
    def _oldest_parked(self) -> Optional[str]:
        rows = self._parked_rows()
        if not rows:
            return None

        def key(row: Dict[str, Any]) -> float:
            seconds = row.get("parked_seconds")
            return -1.0 if seconds is None else float(seconds)

        return max(rows, key=key)["instance"]

    def claim(self, env: Optional[str] = None, instance_id: Optional[str] = None) -> Dict[str, Any]:
        """Instanz uebergeben. Ohne ``instance_id`` die aelteste ``PARKED`` (FIFO)."""
        with self._lock:
            if instance_id is None:
                instance_id = self._oldest_parked()
                if instance_id is None:
                    raise ServiceError(409, "none_parked", "keine PARKED-Instanz im Pool")
            try:
                result = self.pool.claim(env=env, instance_id=instance_id)
            except ParkedError as exc:
                detail = str(exc)
                if "healthy" in detail:
                    raise ServiceError(503, "bridge_unhealthy", detail)
                raise ServiceError(409, "not_claimable", detail)
            self._note_handover(result.get("handover_seconds"))
            self.claims += 1
            return result

    def _note_handover(self, seconds: Optional[float]) -> None:
        if seconds is None:
            return
        self._handovers.append(float(seconds))
        if len(self._handovers) > HANDOVER_WINDOW:
            del self._handovers[0 : len(self._handovers) - HANDOVER_WINDOW]

    def recycle(
        self,
        env: Optional[str] = None,
        instance_id: Optional[str] = None,
        keep_warm: bool = True,
        result: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not instance_id:
            raise ServiceError(400, "bad_request", "instance_id fehlt")
        with self._lock:
            try:
                entry = self.pool.recycle(
                    env=env, instance_id=instance_id, keep_warm=bool(keep_warm), result=result
                )
            except ParkedError as exc:
                raise ServiceError(409, "not_recyclable", str(exc))
            self.recycles += 1
            return {"instance": entry.instance_id, "state": entry.state.value, "rounds": entry.rounds}

    def reap(self, max_park_seconds: Optional[float] = None) -> Dict[str, Any]:
        seconds = self.max_park_seconds if max_park_seconds is None else float(max_park_seconds)
        with self._lock:
            try:
                stopped = self.pool.reap(seconds)
            except ParkedError as exc:
                raise ServiceError(503, "reap_failed", str(exc))
            if stopped:
                self.reaps += 1
            return {"stopped": [entry.instance_id for entry in stopped]}

    def counters(self) -> Dict[str, Any]:
        """Zaehler-Snapshot; Invariante park+claim+warm+recyc+stop == total."""
        with self._lock:
            rows = self._rows()
            counts = {state.value: 0 for state in ParkedState}
            for row in rows:
                counts[row["state"]] = counts.get(row["state"], 0) + 1
            if self._handovers:
                average = sum(self._handovers) / len(self._handovers)
                last = self._handovers[-1]
            else:
                average = None
                last = None
            return {
                "parked": counts.get(ParkedState.PARKED.value, 0),
                "claimed": counts.get(ParkedState.CLAIMED.value, 0),
                "warming": counts.get(ParkedState.WARMING.value, 0),
                "recycling": counts.get(ParkedState.RECYCLING.value, 0),
                "stopped": counts.get(ParkedState.STOPPED.value, 0),
                "total": len(rows),
                "claims": self.claims,
                "recycles": self.recycles,
                "reaps": self.reaps,
                "warm_failures": self.warm_failures,
                "handover_last_seconds": last,
                "handover_avg_seconds": average,
            }

    # -- Thread ------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="parked-maintain", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.maintain_once()
            except Exception:  # noqa: BLE001 - Loop darf nie sterben
                LOG.exception("maintain-Ausnahme")
            self._stop.wait(self.reap_interval)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(5.0, self.reap_interval + 2.0))
            self._thread = None


# ---------------------------------------------------------------------------
# US3 — HTTP-API + Auth
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    """HTTP-Schicht: Auth, Routing, JSON."""

    server_version = "rbbattle-parked/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def controller(self) -> ParkedController:
        return self.server.controller  # type: ignore[attr-defined]

    @property
    def token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    @property
    def env(self) -> str:
        return self.server.env  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        LOG.info("%s - %s", self.address_string(), fmt % args)

    # -- Helfer ------------------------------------------------------------
    def _send_json(
        self, status: int, payload: Dict[str, Any], extra: Optional[Dict[str, str]] = None
    ) -> None:
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
        provided = header[len(prefix) :].strip().encode("utf-8")
        return hmac.compare_digest(provided, expected.encode("utf-8"))

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ServiceError(400, "bad_request", "Request-Body zu gross")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ServiceError(400, "bad_request", "Body ist kein gueltiges JSON")
        if not isinstance(payload, dict):
            raise ServiceError(400, "bad_request", "Body muss ein JSON-Objekt sein")
        return payload

    # -- Routing -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        path = urlsplit(self.path).path.rstrip("/") or "/"
        try:
            # Auth IM try-Block: der Handler bricht nie ohne Antwort ab.
            if not self._authorized():
                self._send_json(
                    401, {"ok": False, "reason": "unauthorized"}, {"WWW-Authenticate": "Bearer"}
                )
                return

            if method == "GET" and path == "/health":
                self._send_json(200, {"ok": True, "env": self.env})
            elif method == "GET" and path == "/status":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "env": self.env,
                        "counters": self.controller.counters(),
                        "entries": self.controller.pool.status(),
                    },
                )
            elif method == "POST" and path == "/claim":
                payload = self._read_json()
                result = self.controller.claim(
                    env=payload.get("env"), instance_id=payload.get("instance_id")
                )
                self._send_json(200, dict({"ok": True}, **result))
            elif method == "POST" and path == "/recycle":
                payload = self._read_json()
                result = self.controller.recycle(
                    env=payload.get("env"),
                    instance_id=payload.get("instance_id"),
                    keep_warm=payload.get("keep_warm", True),
                    result=payload.get("result"),
                )
                self._send_json(200, dict({"ok": True}, **result))
            elif method == "POST" and path == "/reap":
                payload = self._read_json()
                result = self.controller.reap(payload.get("max_park_seconds"))
                self._send_json(200, dict({"ok": True}, **result))
            elif path in ("/health", "/status", "/claim", "/recycle", "/reap"):
                self._send_json(405, {"ok": False, "reason": "method_not_allowed", "method": method})
            else:
                self._send_json(404, {"ok": False, "reason": "not_found", "path": path})
        except ServiceError as exc:
            self._send_json(exc.status, {"ok": False, "reason": exc.reason, "detail": exc.detail})
        except Exception:  # noqa: BLE001 - letzte Reihe, nie 500 ohne Body
            LOG.exception("unerwarteter Fehler")
            self._send_json(500, {"ok": False, "reason": "internal_error"})


def build_server(config: ParkedServiceConfig, controller: ParkedController) -> ThreadingHTTPServer:
    """Server instanziieren (ohne ``serve_forever``) — so auch von Tests nutzbar."""
    httpd = ThreadingHTTPServer((config.bind, config.port), Handler)
    httpd.daemon_threads = True
    httpd.controller = controller  # type: ignore[attr-defined]
    httpd.token = config.token  # type: ignore[attr-defined]
    httpd.env = config.env  # type: ignore[attr-defined]
    return httpd


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="rbbattle parked-pool service")
    parser.add_argument("--check", action="store_true", help="nur Konfiguration pruefen und beenden")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("PARKED_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = ParkedServiceConfig.from_env()
    except ParkedConfigError as exc:
        LOG.error("%s", exc)
        return 2

    try:
        provisioner_obj = build_provisioner(config)
    except ParkedConfigError as exc:
        LOG.error("%s", exc)
        return 2

    if args.check:
        LOG.info(
            "Konfiguration OK (bind=%s port=%s env=%s pool_size=%s)",
            config.bind,
            config.port,
            config.env,
            config.pool_size,
        )
        return 0

    pool = ParkedPool(provisioner_obj)
    controller = ParkedController(
        pool,
        pool_size=config.pool_size,
        max_park_seconds=config.max_park_seconds,
        reap_interval=config.reap_interval,
        env=config.env,
        prefix=config.instance_prefix,
    )
    httpd = build_server(config, controller)
    controller.start()

    def _shutdown(signum: int, _frame: Any) -> None:
        LOG.info("Signal %s empfangen — fahre herunter", signum)
        controller.stop()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    LOG.info(
        "parked-service laeuft auf %s:%s (env=%s, pool_size=%s)",
        config.bind,
        config.port,
        config.env,
        config.pool_size,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
