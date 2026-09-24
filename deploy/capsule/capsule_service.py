#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-capsule-service — HTTP-Dienst um den Kapsel-Flow (Issue #931).

Der Dienst ueber :mod:`capsule_flow` (:class:`CapsuleCoordinator`): ein Dienst
**pro Env**, der den gesamten Solo-Ablauf verkettet —

    open (claim ohne resume, Welt PAUSIERT) -> ready (resume + Cycle-Start)
    -> finish (end_game/round_reset/recycle -> wieder PARKED) -> auto (Override)

Er treibt den **Parked-Dienst** (#928, ``POST /claim``/``/recycle``), den
**Attack-Cycle** (``POST /start``) und die **Bridge** (``POST /resume_game``,
``POST /pause_game {"op":"auto"}``) — alles per HTTP, kein Docker/Bridge-Code
hier. Nur Standardbibliothek (stdlib), kein venv/pip.

Endpunkte (JSON; Bearer-Token PFLICHT, wenn ``CAPSULE_TOKEN`` gesetzt):

  * ``GET  /health``          -> ``200 {"ok":true,"env":…}``
  * ``GET  /capsule/status``  -> Phasen-/Round-Snapshot (Phase aus Cycle-State)
  * ``POST /capsule/open``    -> Parked ``claim`` OHNE resume; ``phase=claimed``
  * ``POST /capsule/ready``   -> Bridge ``resume_game`` + Cycle ``/start``; ``phase=warmup``
  * ``POST /capsule/finish``  -> Parked ``recycle(result?)``; ``phase=parked``
  * ``POST /capsule/auto``    -> Bridge ``/pause_game {"op":"auto"}`` (Override frei)

Fehlerformat einheitlich ``{"ok":false,"reason":"<code>","detail":"…"}``;
``401`` ohne/mit falschem Bearer, ``404`` unbekannte Route, ``405`` falsche
Methode, ``409`` falsche Phase/keine Instanz, ``503`` Parked/Bridge/Cycle nicht
erreichbar.
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

from capsule_flow import CapsuleCoordinator, CapsuleError, build_coordinator

LOG = logging.getLogger("capsule-service")

# Schutzgrenze fuer den Request-Body.
MAX_BODY_BYTES = 65536


class CapsuleConfigError(Exception):
    """Dienst-Konfiguration unbrauchbar — der Dienst darf so nicht starten."""


def _positive_number(env: Dict[str, str], var: str, default: Any, cast: Any) -> Any:
    """Env-Wert lesen: fehlend/leer -> Default; ungueltig/<=0 -> Fehler (fail-closed)."""
    raw = env.get(var)
    if raw is None or str(raw).strip() == "":
        return cast(default)
    try:
        value = cast(str(raw).strip())
    except (TypeError, ValueError):
        raise CapsuleConfigError("%s muss eine Zahl sein (war %r)" % (var, raw))
    if not value > 0:  # faengt auch NaN/inf ab
        raise CapsuleConfigError("%s muss > 0 sein (war %r)" % (var, raw))
    return value


@dataclasses.dataclass
class CapsuleServiceConfig:
    """Dienst-Konfiguration aus ``CAPSULE_*``."""

    env: str = "dev"
    bind: str = "127.0.0.1"
    port: int = 8093
    token: str = ""
    parked_url: str = "http://127.0.0.1:8095"
    parked_token: str = ""
    cycle_url: str = "http://127.0.0.1:9102"
    timeout: float = 5.0
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "CapsuleServiceConfig":
        env = os.environ if env is None else env
        name = (env.get("CAPSULE_ENV") or env.get("RIFT_ENV") or "dev").strip()
        return cls(
            env=name or "dev",
            bind=(env.get("CAPSULE_BIND") or "127.0.0.1").strip() or "127.0.0.1",
            port=int(_positive_number(env, "CAPSULE_PORT", 8093, int)),
            token=(env.get("CAPSULE_TOKEN") or "").strip(),
            parked_url=(env.get("CAPSULE_PARKED_URL") or "http://127.0.0.1:8095").strip(),
            parked_token=(env.get("CAPSULE_PARKED_TOKEN") or "").strip(),
            cycle_url=(env.get("CAPSULE_CYCLE_URL") or "http://127.0.0.1:9102").strip(),
            timeout=float(_positive_number(env, "CAPSULE_TIMEOUT", 5.0, float)),
            log_level=(env.get("CAPSULE_LOG_LEVEL") or "INFO").strip() or "INFO",
        )


# ---------------------------------------------------------------------------
# HTTP-API + Auth
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    """HTTP-Schicht: Auth, Routing, JSON."""

    server_version = "rbbattle-capsule/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def coordinator(self) -> CapsuleCoordinator:
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
            raise CapsuleError("bad_request", "Request-Body zu gross", 400)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise CapsuleError("bad_request", "Body ist kein gueltiges JSON", 400)
        if not isinstance(payload, dict):
            raise CapsuleError("bad_request", "Body muss ein JSON-Objekt sein", 400)
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
                    401, {"ok": False, "reason": "unauthorized"}, {"WWW-Authenticate": "Bearer"}
                )
                return

            if method == "GET" and path == "/health":
                self._send_json(200, {"ok": True, "env": self.env})
            elif method == "GET" and path == "/capsule/status":
                snap = self.coordinator.status()
                self._send_json(200, dict({"ok": True}, **snap))
            elif method == "POST" and path == "/capsule/open":
                payload = self._read_json()
                cap = self.coordinator.open(
                    env=payload.get("env"),
                    instance_id=payload.get("instance_id"),
                    identitaet=payload.get("identitaet"),
                )
                self._send_json(200, dict({"ok": True}, **cap.to_dict()))
            elif method == "POST" and path == "/capsule/ready":
                payload = self._read_json()
                cap = self.coordinator.ready(env=payload.get("env"))
                self._send_json(200, dict({"ok": True}, **cap.to_dict()))
            elif method == "POST" and path == "/capsule/finish":
                payload = self._read_json()
                cap = self.coordinator.finish(result=payload.get("result"), env=payload.get("env"))
                self._send_json(200, dict({"ok": True}, **cap.to_dict()))
            elif method == "POST" and path == "/capsule/auto":
                payload = self._read_json()
                cap = self.coordinator.auto(env=payload.get("env"))
                self._send_json(200, dict({"ok": True}, **cap.to_dict()))
            elif path in ("/health", "/capsule/status", "/capsule/open",
                          "/capsule/ready", "/capsule/finish", "/capsule/auto"):
                self._send_json(405, {"ok": False, "reason": "method_not_allowed", "method": method})
            else:
                self._send_json(404, {"ok": False, "reason": "not_found", "path": path})
        except CapsuleError as exc:
            self._send_json(exc.status, {"ok": False, "reason": exc.reason, "detail": exc.detail})
        except Exception:  # noqa: BLE001 - letzte Reihe, nie 500 ohne Body
            LOG.exception("unerwarteter Fehler")
            self._send_json(500, {"ok": False, "reason": "internal_error"})


def build_server(config: CapsuleServiceConfig, coordinator: CapsuleCoordinator) -> ThreadingHTTPServer:
    """Server instanziieren (ohne ``serve_forever``) — so auch von Tests nutzbar."""
    httpd = ThreadingHTTPServer((config.bind, config.port), Handler)
    httpd.daemon_threads = True
    httpd.coordinator = coordinator  # type: ignore[attr-defined]
    httpd.token = config.token  # type: ignore[attr-defined]
    httpd.env = config.env  # type: ignore[attr-defined]
    return httpd


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="rbbattle capsule-flow service")
    parser.add_argument("--check", action="store_true", help="nur Konfiguration pruefen und beenden")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("CAPSULE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = CapsuleServiceConfig.from_env()
    except CapsuleConfigError as exc:
        LOG.error("%s", exc)
        return 2

    if args.check:
        LOG.info(
            "Konfiguration OK (bind=%s port=%s env=%s parked=%s cycle=%s)",
            config.bind, config.port, config.env, config.parked_url, config.cycle_url,
        )
        return 0

    coordinator = build_coordinator(config)
    httpd = build_server(config, coordinator)

    def _shutdown(signum: int, _frame: Any) -> None:
        LOG.info("Signal %s empfangen — fahre herunter", signum)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    LOG.info(
        "capsule-service laeuft auf %s:%s (env=%s)", config.bind, config.port, config.env
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())