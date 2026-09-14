#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-server-control — Host-Agent fuer den Dedicated-Server (Issue #424).

Kleiner HTTP-Dienst (Standardbibliothek), der den Dedicated-Server-CONTAINER
steuert und ueberwacht — **Plane B**. Bewusst getrennt vom In-Game-I/O-Kanal
(Plane A: ``pipe_bridge`` -> ``rbbridge.dll``): der Agent wrappt ``docker`` auf
dem Host und funktioniert deshalb auch dann, wenn das Spiel haengt oder gerade
neu startet. Genau dann braucht man den Restart.

Endpunkte (alle Bearer-geschuetzt):

  * ``GET  /server/status``       -> ``{state, restarting, health, uptime, started_at}``
                                     (aus ``docker inspect``)
  * ``GET  /server/logs?tail=N``  -> ``{lines: [...], tail: N}`` (``docker logs --tail``)
  * ``POST /server/restart``      -> ``docker restart <container>``
  * ``POST /server/start``        -> ``docker start <container>``
  * ``POST /server/stop``         -> ``docker stop <container>``
  * ``POST /server/config``       -> ``config.cfg`` rendern
                                     (``mode``/``mission``/``difficulty``/``seed``/
                                     ``mission_save``) + Container-Restart

Sicherheit (Haertung wie die Tournament-API, Issue #298):

  * Bind ausschliesslich auf ``127.0.0.1`` — Caddy proxyt dorthin.
  * **JEDER** Request braucht ``Authorization: Bearer <token>`` und wird mit
    ``hmac.compare_digest`` geprueft. Kein offener Restart-Endpunkt.
  * Ohne konfigurierten Token startet der Dienst **nicht** (fail-closed).
  * ``docker`` wird immer als Argumentliste aufgerufen (kein ``shell=True``),
    der Container-Name kommt aus der Konfiguration, nie aus dem Request.
  * Config-Werte werden auf Zeichen ohne ``"``/Steuerzeichen/Newlines geprueft
    (sonst koennte ein Wert die ``config.cfg``-Struktur aufbrechen).

Nur Standardbibliothek — laeuft ohne venv/pip auf dem Host.
"""

import argparse
import hmac
import json
import logging
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlsplit

LOG = logging.getLogger("server-control")

MAX_TAIL = 5000
DEFAULT_TAIL = 200
CONFIG_VALUE_RE = re.compile(r"^[^\x00-\x1f\x7f\"]{1,200}$")
TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\|\s*default\(\s*'([^']*)'\s*\)\s*)?\}\}")


class ConfigError(Exception):
    """Konfiguration unbrauchbar — der Dienst darf so nicht starten."""


class DockerError(Exception):
    """``docker``-Aufruf fehlgeschlagen."""


class BadRequest(Exception):
    """Request-Inhalt unbrauchbar (-> HTTP 400)."""


class ConfigUnavailable(Exception):
    """Config-Rendering nicht moeglich (Vorlage/Basiswerte fehlen) -> HTTP 503."""


def parse_rfc3339(value: str) -> Optional[datetime]:
    """Docker-Zeitstempel (RFC3339, bis ns) robust nach aware UTC parsen."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Docker liefert 9 Nachkommastellen (ns); fromisoformat kann nur 6.
    match = re.match(r"^(.*\.\d{6})\d+(\+.*)$", text)
    if match:
        text = match.group(1) + match.group(2)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_uptime(seconds: Optional[float]) -> Optional[str]:
    """Sekunden als kompakte ``1d 02:03:04``-Form, None wenn unbekannt."""
    if seconds is None or seconds < 0:
        return None
    total = int(seconds)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    clock = "%02d:%02d:%02d" % (hours, minutes, secs)
    return "%dd %s" % (days, clock) if days else clock


def render_fallback(template_text: str, variables: Dict[str, Any]) -> str:
    """Enge ``{{ var }}``-Ersetzung — Fallback ohne Jinja2.

    Deckt genau die Platzhalterformen ab, die ``config.cfg.j2`` benutzt
    (``{{ name }}`` und ``{{ name | default('...') }}``).
    """

    def _sub(match: "re.Match[str]") -> str:
        name, fallback = match.group(1), match.group(2)
        if name in variables and variables[name] is not None:
            return str(variables[name])
        return fallback if fallback is not None else ""

    return TEMPLATE_VAR_RE.sub(_sub, template_text)


def render_template(template_text: str, variables: Dict[str, Any]) -> str:
    """``config.cfg.j2`` rendern.

    Bevorzugt echtes Jinja2-Rendering (der Host hat Jinja2 ohnehin, weil Ansible
    dort laeuft); faellt sonst auf ``render_fallback`` zurueck. Beide Wege lesen
    dieselbe deployte Vorlage — es gibt nur EINE Quelle.
    """
    try:
        import jinja2  # type: ignore
    except ImportError:
        return render_fallback(template_text, variables)

    env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
    return env.from_string(template_text).render(**variables)


def validate_config_value(field: str, value: Any) -> str:
    """Config-Wert gegen Aufbrechen der ``config.cfg``-Struktur absichern."""
    if not isinstance(value, str):
        raise BadRequest("Feld '%s' muss ein String sein" % field)
    if not CONFIG_VALUE_RE.match(value):
        raise BadRequest(
            "Feld '%s' enthaelt unerlaubte Zeichen (Anfuehrungszeichen, "
            "Steuerzeichen oder Newlines sind nicht erlaubt)" % field
        )
    return value


class DockerCli(object):
    """Duenner Wrapper um die ``docker``-CLI (Argumentliste, nie eine Shell)."""

    def __init__(self, binary: str = "docker", timeout: int = 60) -> None:
        self.binary = binary
        self.timeout = timeout
        self._lock = threading.Lock()

    def _run(self, args: Sequence[str]) -> Tuple[int, str, str]:
        cmd = [self.binary] + list(args)
        LOG.info("docker %s", " ".join(args))
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise DockerError("'%s' nicht gefunden: %s" % (self.binary, exc))
        except subprocess.TimeoutExpired:
            raise DockerError("docker-Aufruf nach %ss abgebrochen" % self.timeout)
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        return proc.returncode, out, err

    def inspect(self, container: str) -> Dict[str, Any]:
        rc, out, err = self._run(["inspect", container])
        if rc != 0:
            raise DockerError("docker inspect %s: %s" % (container, err.strip() or "exit %d" % rc))
        try:
            payload = json.loads(out)
        except ValueError as exc:
            raise DockerError("docker inspect lieferte kein JSON: %s" % exc)
        if not payload:
            raise DockerError("docker inspect lieferte keinen Container")
        return payload[0]

    def logs(self, container: str, tail: int) -> str:
        rc, out, err = self._run(["logs", "--tail", str(tail), container])
        if rc != 0:
            raise DockerError("docker logs %s: %s" % (container, err.strip() or "exit %d" % rc))
        # docker logs schreibt Container-stdout/-stderr auf getrennte Streams;
        # der stderr-Kanal traegt hier echte Logzeilen, nicht nur Fehler.
        combined = out + err
        if combined and not combined.endswith("\n"):
            combined += "\n"
        return combined

    def lifecycle(self, action: str, container: str) -> Tuple[int, str, str]:
        """``docker restart|start|stop <container>`` — serialisiert."""
        if action not in ("restart", "start", "stop"):
            raise DockerError("unbekannte Aktion: %s" % action)
        with self._lock:
            return self._run([action, container])


class ServerControl(object):
    """Fachlogik: Status, Logs, Lifecycle, Config-Rendering."""

    def __init__(self, cfg: Dict[str, Any], docker: Optional[DockerCli] = None) -> None:
        self.cfg = cfg
        self.docker = docker or DockerCli(cfg["docker_bin"], cfg["timeout"])

    # -- Status ------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        data = self.docker.inspect(self.cfg["container"])
        state = data.get("State") or {}
        started_at = state.get("StartedAt") or ""
        started = parse_rfc3339(started_at)
        running = (state.get("Status") or "") == "running"
        uptime_seconds = None
        if running and started is not None:
            uptime_seconds = max(0.0, (datetime.now(timezone.utc) - started).total_seconds())
        health = "none"
        health_obj = state.get("Health") or {}
        if isinstance(health_obj, dict) and health_obj.get("Status"):
            health = health_obj["Status"]
        return {
            "container": self.cfg["container"],
            "state": state.get("Status") or "unknown",
            "restarting": bool(state.get("Restarting", False)),
            "health": health,
            "uptime": format_uptime(uptime_seconds),
            "uptime_seconds": int(uptime_seconds) if uptime_seconds is not None else None,
            "started_at": started_at or None,
        }

    # -- Logs --------------------------------------------------------------
    def logs(self, tail: int) -> Dict[str, Any]:
        text = self.docker.logs(self.cfg["container"], tail)
        lines = text.splitlines() if text else []
        return {"container": self.cfg["container"], "tail": tail, "lines": lines}

    # -- Lifecycle ---------------------------------------------------------
    def lifecycle(self, action: str) -> Dict[str, Any]:
        rc, _out, err = self.docker.lifecycle(action, self.cfg["container"])
        if rc != 0:
            raise DockerError("docker %s %s: %s" % (action, self.cfg["container"], err.strip()))
        return {"ok": True, "action": action, "container": self.cfg["container"]}

    # -- Config ------------------------------------------------------------
    def config(self, payload: Dict[str, Any], restart: bool = True) -> Dict[str, Any]:
        applied: Dict[str, str] = {}
        mapping = (
            ("mode", "riftbreaker_server_campaign"),
            ("mission", "riftbreaker_server_mission"),
            ("difficulty", "riftbreaker_server_difficulty"),
        )
        overrides: Dict[str, Any] = {}
        for field, var in mapping:
            if payload.get(field) is not None:
                value = validate_config_value(field, payload[field])
                overrides[var] = value
                applied[field] = value

        extras: List[str] = []
        for field in ("seed", "mission_save"):
            if payload.get(field) is not None:
                value = validate_config_value(field, payload[field])
                applied[field] = value
                extras.append('set %s "%s"' % (field, value))

        variables = self._config_vars()
        variables.update(overrides)

        template_path = self.cfg["config_template"]
        if not template_path:
            raise ConfigUnavailable("SERVER_CONTROL_CONFIG_TEMPLATE ist nicht gesetzt")
        if not os.path.exists(template_path):
            raise ConfigUnavailable("config.cfg-Vorlage fehlt: %s" % template_path)
        with open(template_path, "r", encoding="utf-8") as handle:
            template_text = handle.read()
        rendered = render_template(template_text, variables)
        if extras:
            if not rendered.endswith("\n"):
                rendered += "\n"
            rendered += "\n".join(extras) + "\n"

        self._write_config(rendered)
        result: Dict[str, Any] = {
            "ok": True,
            "applied": applied,
            "config_path": self.cfg["config_path"],
            "restarted": False,
        }
        if restart:
            result.update(self.lifecycle("restart"))
            result["restarted"] = True
        return result

    def _config_vars(self) -> Dict[str, Any]:
        path = self.cfg.get("config_vars")
        if not path:
            raise ConfigUnavailable("SERVER_CONTROL_CONFIG_VARS ist nicht gesetzt")
        if not os.path.exists(path):
            raise ConfigUnavailable("config.cfg-Basiswerte fehlen: %s" % path)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except ValueError as exc:
            raise ConfigUnavailable("config.cfg-Basiswerte sind kein gueltiges JSON: %s" % exc)

    def _write_config(self, text: str) -> None:
        """Atomar schreiben; 0644, damit steamuser im Container lesen kann."""
        path = self.cfg.get("config_path")
        if not path:
            raise ConfigUnavailable("SERVER_CONTROL_CONFIG_PATH ist nicht gesetzt")
        directory = os.path.dirname(path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            tmp = "%s.tmp.%d" % (path, os.getpid())
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except OSError as exc:
            raise ConfigUnavailable("config.cfg nicht schreibbar (%s): %s" % (path, exc.strerror or exc))


class Handler(BaseHTTPRequestHandler):
    """HTTP-Schicht: Auth, Routing, JSON."""

    server_version = "rbbattle-server-control/1.0"
    protocol_version = "HTTP/1.1"

    # -- Helfer ------------------------------------------------------------
    @property
    def control(self) -> ServerControl:
        return self.server.control  # type: ignore[attr-defined]

    @property
    def token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        LOG.info("%s - %s", self.address_string(), fmt % args)

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
        header = self.headers.get("Authorization") or ""
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        return hmac.compare_digest(header[len(prefix):].strip(), self.token)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 65536:
            raise BadRequest("Request-Body zu gross")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise BadRequest("Body ist kein gueltiges JSON")
        if not isinstance(payload, dict):
            raise BadRequest("Body muss ein JSON-Objekt sein")
        return payload

    # -- Routing -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        split = urlsplit(self.path)
        path = split.path.rstrip("/") or "/"
        query = parse_qs(split.query)

        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"}, {"WWW-Authenticate": "Bearer"})
            return

        try:
            if method == "GET" and path == "/server/status":
                self._send_json(200, self.control.status())
            elif method == "GET" and path == "/server/logs":
                self._send_json(200, self.control.logs(self._tail(query)))
            elif method == "POST" and path in (
                "/server/restart",
                "/server/start",
                "/server/stop",
            ):
                self._send_json(200, self.control.lifecycle(path.rsplit("/", 1)[-1]))
            elif method == "POST" and path == "/server/config":
                self._send_json(200, self.control.config(self._read_json()))
            elif path in (
                "/server/status",
                "/server/logs",
                "/server/restart",
                "/server/start",
                "/server/stop",
                "/server/config",
            ):
                self._send_json(405, {"error": "method_not_allowed", "method": method})
            else:
                self._send_json(404, {"error": "not_found", "path": path})
        except BadRequest as exc:
            self._send_json(400, {"error": "bad_request", "detail": str(exc)})
        except ConfigUnavailable as exc:
            LOG.warning("Config nicht verfuegbar: %s", exc)
            self._send_json(503, {"error": "config_unavailable", "detail": str(exc)})
        except DockerError as exc:
            LOG.warning("docker-Fehler: %s", exc)
            self._send_json(502, {"error": "docker_error", "detail": str(exc)})
        except Exception:  # noqa: BLE001 - letzte Reihe, nie 500 ohne Body
            # Nur ins Log, NICHT in die Antwort: keine internen Details leaken.
            LOG.exception("unerwarteter Fehler")
            self._send_json(500, {"error": "internal_error"})

    @staticmethod
    def _tail(query: Dict[str, List[str]]) -> int:
        raw = (query.get("tail") or [""])[0] or str(DEFAULT_TAIL)
        try:
            value = int(raw)
        except ValueError:
            raise BadRequest("tail muss eine Zahl sein")
        return max(1, min(value, MAX_TAIL))


def build_server(cfg: Dict[str, Any]) -> ThreadingHTTPServer:
    """Server instanziieren (ohne serve_forever) — so auch von Tests nutzbar."""
    httpd = ThreadingHTTPServer((cfg["bind"], cfg["port"]), Handler)
    httpd.daemon_threads = True
    httpd.control = ServerControl(cfg)  # type: ignore[attr-defined]
    httpd.token = cfg["token"]  # type: ignore[attr-defined]
    return httpd


def load_config(env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Konfiguration aus der Umgebung; fail-closed ohne Token/Container."""
    env = os.environ if env is None else env
    token = (env.get("SERVER_CONTROL_TOKEN") or "").strip()
    container = (env.get("SERVER_CONTROL_CONTAINER") or "").strip()
    if not token:
        raise ConfigError(
            "SERVER_CONTROL_TOKEN fehlt — der Agent startet ohne Bearer-Token NICHT "
            "(kein offener Restart-Endpunkt). Token als Vault-Secret setzen."
        )
    if not container:
        raise ConfigError("SERVER_CONTROL_CONTAINER fehlt")
    try:
        port = int(env.get("SERVER_CONTROL_PORT") or "8091")
        timeout = int(env.get("SERVER_CONTROL_TIMEOUT") or "60")
    except ValueError:
        raise ConfigError("SERVER_CONTROL_PORT/TIMEOUT muessen Zahlen sein")
    return {
        "bind": (env.get("SERVER_CONTROL_BIND") or "127.0.0.1").strip(),
        "port": port,
        "token": token,
        "container": container,
        "docker_bin": (env.get("SERVER_CONTROL_DOCKER") or "docker").strip(),
        "timeout": timeout,
        "config_path": env.get("SERVER_CONTROL_CONFIG_PATH") or "",
        "config_template": env.get("SERVER_CONTROL_CONFIG_TEMPLATE") or "",
        "config_vars": env.get("SERVER_CONTROL_CONFIG_VARS") or "",
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="rbbattle server-control host agent")
    parser.add_argument("--check", action="store_true", help="nur Konfiguration pruefen und beenden")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("SERVER_CONTROL_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        cfg = load_config()
    except ConfigError as exc:
        LOG.error("%s", exc)
        return 2

    if args.check:
        LOG.info("Konfiguration OK (bind=%s port=%s container=%s)", cfg["bind"], cfg["port"], cfg["container"])
        return 0

    httpd = build_server(cfg)
    LOG.info(
        "server-control laeuft auf %s:%s (Container %s)", cfg["bind"], cfg["port"], cfg["container"]
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
