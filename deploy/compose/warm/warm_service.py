#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-warm-service — "warmed capsule" claim source (Issue #1093).

Der **statische** Compose-Dedicated (``compose.yaml`` -> ``dedicated``) laeuft
ohnehin dauerhaft. Dieser Dienst stellt ihn als **eine** warme, geparkte
Kapsel bereit und implementiert dafuer das **Subset** des Parked-Service-Protokolls
(#928), das die Kapsel braucht — **ohne** Provisioner und **ohne** Pool:

  * ``GET  /health``   -> ``200 {"ok":true,"env":…}`` (Liveness des Dienstes)
  * ``GET  /status``   -> ``{instance,env,state,gns_endpoint,bridge_url}``
  * ``POST /claim``    -> Instanz uebergeben; ``resume=false`` laesst die Welt
                          PAUSIERT (#931, Solo-Kapsel), Default ``true``
  * ``POST /recycle``  -> nach Rundenende ``round_reset`` + ``pause_game`` ->
                          wieder ``PARKED``; bei ``result`` zusaetzlich ``end_game``

Vorbild/Contract-Quelle: ``deploy/parked/parked_service.py`` (HTTP-Surface,
Fehlerformat, Bearer) und ``deploy/parked/parked_pool.py`` (Bridge-Aufrufe).
Erwartet/gesendet von ``deploy/capsule/capsule_flow.py`` (``ParkedServiceClient``
+ ``BridgeControl``).

Der Zustandsraum ist bewusst minimal (PARKED <-> CLAIMED); der Dienst ist
**single-instance** — es gibt nichts zu poolen. Nur Standardbibliothek; die
Docker-/Welt-Arbeit macht die Bridge (``pause_game``/``resume_game``/…), die
per injizierbarer ``bridge_factory`` angesprochen wird (kein Docker-Code hier).

Fehlerformat einheitlich ``{"ok":false,"reason":"<code>","detail":"…"}``;
``401`` ohne/mit falschem Bearer, ``404`` unbekannte Route, ``405`` falsche
Methode, ``409`` falscher Zustand, ``503`` Bridge nicht erreichbar/healthy.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import hmac
import json
import logging
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Sequence
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit

LOG = logging.getLogger("warm-service")

# Schutzgrenze fuer den Request-Body (wie parked_service).
MAX_BODY_BYTES = 65536


class WarmConfigError(Exception):
    """Dienst-Konfiguration unbrauchbar — der Dienst darf so nicht starten."""


class ServiceError(Exception):
    """Fachlicher HTTP-Fehler (Statuscode + reason + detail)."""

    def __init__(self, status: int, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.status = status
        self.reason = reason
        self.detail = detail


class BridgeError(Exception):
    """Bridge nicht erreichbar oder lieferte keine brauchbare Antwort."""


# ---------------------------------------------------------------------------
# Config (WARM_*, fail-closed)
# ---------------------------------------------------------------------------


def _positive_number(env: Dict[str, str], var: str, default: Any, cast: Any) -> Any:
    """Env-Wert lesen: fehlend/leer -> Default; ungueltig/<=0 -> Fehler.

    Fail-closed wie ``parked_service._positive_number``: eine kaputte Zahl bricht
    den Start ab, statt still einen Default zu nehmen.
    """
    raw = env.get(var)
    if raw is None or str(raw).strip() == "":
        return cast(default)
    try:
        value = cast(str(raw).strip())
    except (TypeError, ValueError):
        raise WarmConfigError("%s muss eine Zahl sein (war %r)" % (var, raw))
    # ``not value > 0`` faengt auch ``NaN``/``inf``-Fuellwerte ab.
    if not value > 0:
        raise WarmConfigError("%s muss > 0 sein (war %r)" % (var, raw))
    return value


_TRUE_TOKENS = ("1", "true", "yes", "on")
_FALSE_TOKENS = ("0", "false", "no", "off")


def _bool_value(env: Dict[str, str], var: str, default: bool) -> bool:
    """Boolean-Env lesen: fehlend/leer -> Default; unbekannt -> Fehler (fail-closed)."""
    raw = env.get(var)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    token = str(raw).strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    raise WarmConfigError("%s muss ein Boolean sein (war %r)" % (var, raw))


@dataclasses.dataclass
class WarmServiceConfig:
    """Dienst-Konfiguration aus ``WARM_*`` (eine statische Instanz)."""

    env: str = "local"
    bind: str = "0.0.0.0"
    port: int = 9201
    # HTTP-Bridge des statischen Dedicated (Steuerung).
    bridge_url: str = "http://dedicated:9001"
    # GNS-UDP-Host-Endpoint der Instanz — das Ziel, das der RELAY dialt.
    gns_endpoint: str = "127.0.0.1:6322"
    # Anzeigename/Identitaet der einzigen Instanz.
    instance: str = "solo"
    token: str = ""
    # Beim Start die Welt pausieren (Bridge ``pause_game``) -> geparkt.
    park_on_start: bool = True
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "WarmServiceConfig":
        env = os.environ if env is None else env
        return cls(
            env=(env.get("WARM_ENV") or "local").strip() or "local",
            bind=(env.get("WARM_BIND") or "0.0.0.0").strip() or "0.0.0.0",
            port=int(_positive_number(env, "WARM_PORT", 9201, int)),
            bridge_url=(env.get("WARM_BRIDGE_URL") or "http://dedicated:9001").strip() or "http://dedicated:9001",
            gns_endpoint=(env.get("WARM_GNS_ENDPOINT") or "127.0.0.1:6322").strip() or "127.0.0.1:6322",
            instance=(env.get("WARM_INSTANCE") or "solo").strip() or "solo",
            token=(env.get("WARM_TOKEN") or "").strip(),
            park_on_start=_bool_value(env, "WARM_PARK_ON_START", True),
            log_level=(env.get("WARM_LOG_LEVEL") or "INFO").strip() or "INFO",
        )


# ---------------------------------------------------------------------------
# BridgeClient — duenner stdlib-HTTP-Client (Spiegel von parked_pool.BridgeClient)
# ---------------------------------------------------------------------------


class BridgeClient(object):
    """Duenner HTTP-Client auf die rbbattle-Bridge.

    ``POST <base>/<path>`` bzw. ``GET <base>/health``; Antwort ist JSON.
    Ueber ``opener`` injizierbar (Tests ersetzen Netztransporte komplett).
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 5.0,
        opener: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = opener or urllib_request.urlopen

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None):
        url = "%s%s" % (self.base_url, path)
        data: Optional[bytes] = None
        headers: Dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib_request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                raw = response.read().decode("utf-8", "replace")
        except urllib_error.HTTPError as exc:
            raise BridgeError("%s %s -> HTTP %s" % (method, path, exc.code))
        except (urllib_error.URLError, OSError, ValueError) as exc:
            raise BridgeError("%s %s fehlgeschlagen: %s" % (method, path, exc))
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except ValueError as exc:
            raise BridgeError("Antwort von %s ist kein JSON: %s" % (path, exc))
        if status >= 400:
            raise BridgeError("%s %s -> HTTP %s" % (method, path, status))
        return status, parsed

    def _post(self, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        _status, parsed = self._request("POST", path, payload)
        if isinstance(parsed, dict) and parsed.get("ok") is False:
            raise BridgeError("%s meldet ok=false: %s" % (path, parsed))
        return parsed

    def health_ok(self) -> bool:
        """``GET /health`` -> True nur bei HTTP 200 und Body ``{"ok": true}``."""
        try:
            status, parsed = self._request("GET", "/health")
        except BridgeError:
            return False
        return status == 200 and isinstance(parsed, dict) and parsed.get("ok") is True

    def pause_game(self) -> Dict[str, Any]:
        return self._post("/pause_game")

    def resume_game(self) -> Dict[str, Any]:
        return self._post("/resume_game")

    def round_reset(self) -> Dict[str, Any]:
        return self._post("/round_reset")

    def end_game(self, result: Optional[str] = None) -> Dict[str, Any]:
        payload = {"result": result} if result is not None else None
        return self._post("/end_game", payload)


# ---------------------------------------------------------------------------
# WarmController — eine statische Instanz (kein Provisioner, kein Pool)
# ---------------------------------------------------------------------------


class WarmState(enum.Enum):
    """Lebenszyklus der EINEN statischen Instanz."""

    PARKED = "parked"  # Welt angehalten (pause_game aktiv), claimbar
    CLAIMED = "claimed"  # an ein Spiel uebergeben (Welt nur bei resume=True an)


class WarmController(object):
    """Zustand + Bridge-Aufrufe fuer genau eine statische Instanz.

    ``bridge_factory`` — ``callable(base_url) -> BridgeClient`` (injizierbar,
    Tests ersetzen den Netztransport). Der Dienst kennt die Instanz-Identitaet
    (``instance``/``env``/``gns_endpoint``/``bridge_url``) aus der Config; es
    gibt keinen Provisioner und keinen Pool.
    """

    def __init__(
        self,
        config: WarmServiceConfig,
        bridge_factory: Optional[Callable[[str], Any]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.config = config
        self._bridge_factory = bridge_factory or (lambda url: BridgeClient(url))
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        # Die statische Instanz ist per Default claimbar (PARKED). ``start()``
        # parkt die Welt aktiv, wenn ``park_on_start`` gesetzt ist.
        self.state = WarmState.PARKED
        self.claims = 0
        self.recycles = 0
        self.rounds = 0

    # -- intern ------------------------------------------------------------
    def _bridge(self) -> Any:
        return self._bridge_factory(self.config.bridge_url)

    def _require_instance(self, instance_id: Optional[str], action: str) -> None:
        """Optionalen ``instance_id`` gegen die EINE Instanz pruefen (fail-loud)."""
        if instance_id is not None and str(instance_id) != self.config.instance:
            raise ServiceError(
                409,
                "unknown_instance",
                "%s: unbekannte Instanz %r (hier: %r)" % (action, instance_id, self.config.instance),
            )

    # -- oeffentliche Operationen -----------------------------------------
    def start(self) -> None:
        """``WARM_PARK_ON_START``: die Welt beim Start pausieren -> ``PARKED``.

        Fail-loud: scheitert ``pause_game``, wird die Ausnahme durchgereicht
        (``main`` beendet dann laut) — der Dienst behauptet nie, geparkt zu sein,
        wenn er es nicht ist.
        """
        if not self.config.park_on_start:
            return
        self._bridge().pause_game()
        with self._lock:
            self.state = WarmState.PARKED

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "instance": self.config.instance,
                "env": self.config.env,
                "state": self.state.value,
                "gns_endpoint": self.config.gns_endpoint,
                "bridge_url": self.config.bridge_url,
            }

    def claim(self, resume: bool = True, instance_id: Optional[str] = None) -> Dict[str, Any]:
        """Instanz uebergeben. ``resume=False`` laesst die Welt PAUSIERT (#931)."""
        with self._lock:
            self._require_instance(instance_id, "claim")
            if self.state != WarmState.PARKED:
                raise ServiceError(
                    409, "none_parked", "Instanz %s ist %s (nicht PARKED)" % (self.config.instance, self.state.value)
                )
            bridge = self._bridge()
            if not bridge.health_ok():
                raise ServiceError(503, "bridge_unhealthy", "Bridge %s nicht healthy" % self.config.bridge_url)
            if resume:
                try:
                    bridge.resume_game()
                except Exception as exc:  # noqa: BLE001 - laut auf 409 mappen
                    raise ServiceError(409, "not_claimable", str(exc))
            self.state = WarmState.CLAIMED
            self.claims += 1
            return {
                "instance": self.config.instance,
                "env": self.config.env,
                "bridge_url": self.config.bridge_url,
                "gns_endpoint": self.config.gns_endpoint,
                "state": self.state.value,
                "resumed": bool(resume),
            }

    def recycle(
        self,
        result: Optional[str] = None,
        keep_warm: bool = True,
        instance_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Nach Spielende ``round_reset`` (+ ``pause_game``) -> wieder ``PARKED``.

        ``result`` (``win``/``lose``) wird wie im Parked-Pool durchgereicht und
        loest ``end_game(result)`` aus; ohne ``result`` ist ``round_reset`` +
        ``pause_game`` der gueltige Pfad (kein ``end_game(None)``).

        ``keep_warm=False`` ist fuer eine **statische** Instanz nicht darstellbar
        (kein Stop ohne Provisioner) -> lautes ``409 cold_not_supported`` statt
        eines stillen No-Ops.
        """
        with self._lock:
            self._require_instance(instance_id, "recycle")
            if self.state != WarmState.CLAIMED:
                raise ServiceError(
                    409,
                    "not_recyclable",
                    "Instanz %s ist %s (nicht CLAIMED)" % (self.config.instance, self.state.value),
                )
            if not keep_warm:
                raise ServiceError(
                    409,
                    "cold_not_supported",
                    "statische Instanz kann nicht gestoppt werden (keep_warm=false)",
                )
            bridge = self._bridge()
            try:
                if result is not None:
                    bridge.end_game(result)
                bridge.round_reset()
                bridge.pause_game()
            except Exception as exc:  # noqa: BLE001 - laut auf 409 mappen
                raise ServiceError(409, "not_recyclable", str(exc))
            self.state = WarmState.PARKED
            self.recycles += 1
            self.rounds += 1
            return {
                "instance": self.config.instance,
                "state": self.state.value,
                "rounds": self.rounds,
            }


# ---------------------------------------------------------------------------
# Handler — HTTP-Schicht: Auth, Routing, JSON (Spiegel von parked_service)
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    """HTTP-Schicht: Auth, Routing, JSON."""

    server_version = "rbbattle-warm/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def controller(self) -> WarmController:
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
        path = urlsplit(self.path).path.rstrip("/") or "/"
        try:
            # Auth IM try-Block: der Handler bricht nie ohne Antwort ab.
            if not self._authorized():
                self._send_json(401, {"ok": False, "reason": "unauthorized"}, {"WWW-Authenticate": "Bearer"})
                return

            if method == "GET" and path == "/health":
                self._send_json(200, {"ok": True, "env": self.env})
            elif method == "GET" and path == "/status":
                self._send_json(200, dict({"ok": True}, **self.controller.status()))
            elif method == "POST" and path == "/claim":
                payload = self._read_json()
                resume = payload.get("resume", True)
                if not isinstance(resume, bool):
                    raise ServiceError(400, "bad_request", "resume muss ein JSON-Boolean sein")
                result = self.controller.claim(resume=resume, instance_id=payload.get("instance_id"))
                self._send_json(200, dict({"ok": True}, **result))
            elif method == "POST" and path == "/recycle":
                payload = self._read_json()
                keep_warm = payload.get("keep_warm", True)
                if not isinstance(keep_warm, bool):
                    raise ServiceError(400, "bad_request", "keep_warm muss ein JSON-Boolean sein")
                result = self.controller.recycle(
                    result=payload.get("result"),
                    keep_warm=keep_warm,
                    instance_id=payload.get("instance_id"),
                )
                self._send_json(200, dict({"ok": True}, **result))
            elif path in ("/health", "/status", "/claim", "/recycle"):
                self._send_json(405, {"ok": False, "reason": "method_not_allowed", "method": method})
            else:
                self._send_json(404, {"ok": False, "reason": "not_found", "path": path})
        except ServiceError as exc:
            self._send_json(exc.status, {"ok": False, "reason": exc.reason, "detail": exc.detail})
        except Exception:  # noqa: BLE001 - letzte Reihe, nie 500 ohne Body
            LOG.exception("unerwarteter Fehler")
            self._send_json(500, {"ok": False, "reason": "internal_error"})


def build_server(config: WarmServiceConfig, controller: WarmController) -> ThreadingHTTPServer:
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
    parser = argparse.ArgumentParser(description="rbbattle warm-service (static single instance)")
    parser.add_argument("--check", action="store_true", help="nur Konfiguration pruefen und beenden")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("WARM_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = WarmServiceConfig.from_env()
    except WarmConfigError as exc:
        LOG.error("%s", exc)
        return 2

    if args.check:
        LOG.info(
            "Konfiguration OK (bind=%s port=%s env=%s instance=%s bridge=%s)",
            config.bind,
            config.port,
            config.env,
            config.instance,
            config.bridge_url,
        )
        return 0

    controller = WarmController(config)
    httpd = build_server(config, controller)
    try:
        controller.start()
    except Exception as exc:  # noqa: BLE001 - Start-Park ist fail-loud
        LOG.error("Start-Park (pause_game) fehlgeschlagen: %s", exc)
        httpd.server_close()
        return 3

    def _shutdown(signum: int, _frame: Any) -> None:
        LOG.info("Signal %s empfangen — fahre herunter", signum)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    LOG.info(
        "warm-service laeuft auf %s:%s (env=%s, instance=%s, bridge=%s)",
        config.bind,
        config.port,
        config.env,
        config.instance,
        config.bridge_url,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
