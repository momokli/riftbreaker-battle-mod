#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-provisioner-service — HTTP-Wrapper um die Provisioner-Fachlogik (#1083).

Der kalte Pfad lief bisher nur als CLI (``provisioner.py``). Die Queue
(:class:`deploy.queue.queue_flow.ProvisionerClient`) spricht aber per HTTP mit
einem Dienst auf ``127.0.0.1:8094`` — der existierte nicht (Issue #1083). Dieser
Dienst ist genau dieser Wrapper: er baut EINMAL ``Provisioner(load_config())``
und stellt die drei oeffentlichen Methoden als JSON-Endpunkte bereit.

Es ist bewusst NUR die HTTP-Schicht (stdlib ``ThreadingHTTPServer``); die gesamte
Fachlogik (Preflight/Rollback/Idempotenz) bleibt in :mod:`provisioner`. Der Dienst
reicht die Rueckgaben der Methoden unveraendert durch — inklusive ``ports`` mit
dem ``gns``-Endpoint, den die Queue defensiv liest.

Endpunkte (JSON; Bearer nur, wenn ``PROVISIONER_TOKEN`` gesetzt):

  * ``GET  /health``                     -> ``200 {"ok":true,"env":…}``
  * ``POST /start``  ``{env?,mode?,instance_id?,world?}`` -> ``Provisioner.start``
  * ``POST /stop``   ``{env?,instance_id}``               -> ``Provisioner.stop``
  * ``GET  /status?instance_id=&env=``                    -> ``Provisioner.status``

Fehlerformat einheitlich ``{"ok":false,"reason":"<code>","detail":"…"}``;
``401`` ohne/mit falschem Bearer, ``404`` unbekannte Route, ``405`` falsche
Methode, ``500`` ``ProvisionError``/``ConfigError``, ``503`` ``DockerError``.
Nie ein stiller ``200`` bei einem Fehler (fail-loud).

Nur Standardbibliothek — laeuft ohne venv/pip.
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
from urllib.parse import parse_qs, urlsplit

# provisioner.py liegt im selben Verzeichnis (Server + Tests). Fuer den Fall,
# dass der Dienst aus einem anderen CWD gestartet wird, den Modulpfad ergaenzen.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from provisioner import (  # noqa: E402
    Config,
    ConfigError,
    DockerError,
    Provisioner,
    ProvisionError,
    load_config,
)

LOG = logging.getLogger("provisioner-service")

# Schutzgrenze fuer den Request-Body.
MAX_BODY_BYTES = 65536


class ProvisionerServiceConfigError(Exception):
    """Dienst-Konfiguration unbrauchbar — der Dienst darf so nicht starten."""


class ServiceError(Exception):
    """Vorzeitiger Antwortabbruch mit Status/Reason (z. B. 400 bad_request)."""

    def __init__(self, status: int, reason: str, detail: str) -> None:
        super(ServiceError, self).__init__("%s: %s" % (reason, detail))
        self.status = status
        self.reason = reason
        self.detail = detail


def _positive_number(env: Dict[str, str], var: str, default: Any, cast: Any) -> Any:
    """Env-Wert lesen: fehlend/leer -> Default; ungueltig/<=0 -> Fehler (fail-closed)."""
    raw = env.get(var)
    if raw is None or str(raw).strip() == "":
        return cast(default)
    try:
        value = cast(str(raw).strip())
    except (TypeError, ValueError):
        raise ProvisionerServiceConfigError("%s muss eine Zahl sein (war %r)" % (var, raw))
    if not value > 0:  # faengt auch NaN/inf ab
        raise ProvisionerServiceConfigError("%s muss > 0 sein (war %r)" % (var, raw))
    return value


@dataclasses.dataclass
class ProvisionerServiceConfig:
    """Dienst-Konfiguration aus ``PROVISIONER_*`` (Bind/Port/Token/Log).

    Der fachliche Teil (Image, Pfade, Port-Bases, …) liegt weiter in
    :class:`provisioner.Config` und wird per :func:`load_config` gelesen.
    """

    env: str = "dev"
    bind: str = "127.0.0.1"
    port: int = 8094
    token: str = ""
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "ProvisionerServiceConfig":
        env = os.environ if env is None else env
        return cls(
            env=(env.get("PROVISIONER_ENV") or "dev").strip() or "dev",
            bind=(env.get("PROVISIONER_BIND") or "127.0.0.1").strip() or "127.0.0.1",
            port=int(_positive_number(env, "PROVISIONER_PORT", 8094, int)),
            token=(env.get("PROVISIONER_TOKEN") or "").strip(),
            log_level=(env.get("PROVISIONER_LOG_LEVEL") or "INFO").strip() or "INFO",
        )


# ---------------------------------------------------------------------------
# HTTP-API + Auth
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    """HTTP-Schicht: Auth, Routing, JSON. Keine Fachlogik."""

    server_version = "rbbattle-provisioner/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def provisioner(self) -> Provisioner:
        return self.server.provisioner  # type: ignore[attr-defined]

    @property
    def token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    @property
    def env(self) -> str:
        return self.server.env  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        LOG.info("%s - %s", self.address_string(), fmt % args)

    # -- Helfer ------------------------------------------------------------
    def _send_json(self, status: int, payload: Dict[str, Any], extra: Optional[Dict[str, str]] = None) -> None:
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
        parts = urlsplit(self.path)
        path = parts.path.rstrip("/") or "/"
        query = parse_qs(parts.query)
        try:
            # Auth IM try-Block: der Handler bricht nie ohne Antwort ab.
            if not self._authorized():
                self._send_json(
                    401,
                    {"ok": False, "reason": "unauthorized"},
                    {"WWW-Authenticate": "Bearer"},
                )
                return

            if method == "GET" and path == "/health":
                self._send_json(200, {"ok": True, "env": self.env})
            elif method == "POST" and path == "/start":
                payload = self._read_json()
                result = self.provisioner.start(
                    env=payload.get("env"),
                    mode=payload.get("mode") or "solo_self",
                    instance_id=payload.get("instance_id"),
                    world=payload.get("world"),
                )
                self._send_json(200, dict({"ok": True}, **result))
            elif method == "POST" and path == "/stop":
                payload = self._read_json()
                result = self.provisioner.stop(instance_id=payload.get("instance_id"), env=payload.get("env"))
                self._send_json(200, dict({"ok": True}, **result))
            elif method == "GET" and path == "/status":
                result = self.provisioner.status(
                    instance_id=_first(query, "instance_id"),
                    env=_first(query, "env"),
                )
                self._send_json(200, dict({"ok": True}, **result))
            elif path in ("/health", "/start", "/stop", "/status"):
                self._send_json(405, {"ok": False, "reason": "method_not_allowed", "method": method})
            else:
                self._send_json(404, {"ok": False, "reason": "not_found", "path": path})
        except ServiceError as exc:
            self._send_json(exc.status, {"ok": False, "reason": exc.reason, "detail": exc.detail})
        except ProvisionError as exc:
            # Fachlich fehlgeschlagen (Preflight/Rollback/Modus) -> 500, laut.
            self._send_json(500, {"ok": False, "reason": "provision_failed", "detail": str(exc)})
        except ConfigError as exc:
            self._send_json(500, {"ok": False, "reason": "config_error", "detail": str(exc)})
        except DockerError as exc:
            # docker nicht erreichbar/Aufruf fehlgeschlagen -> 503.
            self._send_json(503, {"ok": False, "reason": "docker_error", "detail": str(exc)})
        except Exception:  # noqa: BLE001 - letzte Reihe, nie 500 ohne Body
            LOG.exception("unerwarteter Fehler")
            self._send_json(500, {"ok": False, "reason": "internal_error"})


def _first(query: Dict[str, Any], key: str) -> Optional[str]:
    """Ersten Query-Parameter-Wert lesen (``?key=a``); fehlend -> ``None``."""
    values = query.get(key)
    if not values:
        return None
    value = values[0]
    return value if value != "" else None


def build_server(config: ProvisionerServiceConfig, provisioner: Provisioner) -> ThreadingHTTPServer:
    """Server instanziieren (ohne ``serve_forever``) — so auch von Tests nutzbar."""
    httpd = ThreadingHTTPServer((config.bind, config.port), Handler)
    httpd.daemon_threads = True
    httpd.provisioner = provisioner  # type: ignore[attr-defined]
    httpd.token = config.token  # type: ignore[attr-defined]
    httpd.env = config.env  # type: ignore[attr-defined]
    return httpd


def build_provisioner(config: Optional[Config] = None) -> Provisioner:
    """``Provisioner`` aus der Repo-Konfiguration bauen (``load_config()``).

    Fehlt ``PROVISIONER_IMAGE`` o. a., propagiert :class:`ConfigError` (fail-closed).
    """
    return Provisioner(config or load_config())


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="provisioner_service",
        description="HTTP-Wrapper um den Riftbreaker-Provisioner (Issue #1083)",
    )
    parser.add_argument("--check", action="store_true", help="nur Konfiguration pruefen und beenden")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("PROVISIONER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = ProvisionerServiceConfig.from_env()
    except ProvisionerServiceConfigError as exc:
        LOG.error("%s", exc)
        print("config error: %s" % exc, file=sys.stderr)
        return 2

    # Fail-closed auch fuer die Fachkonfiguration: ohne Image kein Dienststart.
    try:
        provisioner = build_provisioner()
    except ConfigError as exc:
        LOG.error("%s", exc)
        print("config error: %s" % exc, file=sys.stderr)
        return 2

    if args.check:
        print(
            "configuration OK (bind=%s port=%s env=%s image=%s)"
            % (config.bind, config.port, config.env, provisioner.cfg.image)
        )
        return 0

    httpd = build_server(config, provisioner)

    def _shutdown(signum: int, _frame: Any) -> None:
        LOG.info("Signal %s empfangen — fahre herunter", signum)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    LOG.info("provisioner-service laeuft auf %s:%s (env=%s)", config.bind, config.port, config.env)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
