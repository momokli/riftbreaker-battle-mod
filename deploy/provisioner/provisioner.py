#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-provisioner — Dedi-Instanz on-demand starten/stoppen (Issue #908).

Kalter Pfad: fuer einen Spielwunsch genau EINE Dedicated-Server-Instanz
run-scoped starten (Container + Netz + Named Volumes + Verzeichnisse nach dem
Boot-Test-Muster ``riftbreaker-dedicated-<env>-<instance>``) und anschliessend
wieder restfrei stoppen. Bewusst getrennt vom Host-Agenten
``deploy/server-control`` (der steuert einen BESTEHENDEN Container) und vom
Warm-Pool/„Parked" (eigene Issues #909/#910).

Sicherheits-/Robustheitsregeln:

  * ``docker`` wird IMMER als Argumentliste aufgerufen (kein ``shell=True``).
  * Container-/Netz-/Volume-Namen stammen AUSSCHLIESSLICH aus ``InstanceSpec``
    (deterministisch aus ``(env, instance_id)``), NIE aus Nutzereingabe;
    ``instance_id`` wird per Regex validiert.
  * ``start`` prueft fail-loud VOR dem ersten Container: Bridge-Port frei,
    genug Plattenplatz, Image vorhanden. Jeder Fehler nach begonnener Ressource
    rollt ALLES Begonnene zurueck und bricht laut ab — nie halb gestartet.
  * ``stop`` ist idempotent: fehlende Ressourcen sind kein Fehler.

Nur Standardbibliothek — laeuft ohne venv/pip auf dem Host.
"""

import argparse
import dataclasses
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

LOG = logging.getLogger("provisioner")

# instance_id: run-scoped Suffix analog rbbattle_run_suffix (github.run_id).
INSTANCE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
BYTES_PER_GB = 1024 ** 3


class ConfigError(Exception):
    """Konfiguration unbrauchbar — fail-closed (CLI-Exit 2)."""


class DockerError(Exception):
    """``docker``-Aufruf fehlgeschlagen."""


class ProvisionError(Exception):
    """Provisionierung nicht moeglich (Preflight/Rollback) — laut abbrechen."""


# ---------------------------------------------------------------------------
# US3 — Config (JSON + Env, analog SERVER_CONTROL_*)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Config:
    """Provisioner-Konfiguration. Defaults = Env-Defaults."""

    env: str = "test"
    image: str = ""
    base_dir: str = "/srv"
    docker: str = "docker"
    timeout: int = 60
    health_deadline: float = 180.0
    health_interval: float = 3.0
    min_free_gb: float = 10.0
    bridge_port_base: int = 30000
    bridge_container_port: int = 9001
    config_cfg: str = "/opt/rbmods/compose/rift-{env}/riftbreaker/config/config.cfg"
    rbtools_dir: str = "/opt/rbmods/rbtools/{env}"
    game_source: str = "/srv/rift-{env}/game"
    image_build_dir: str = ""
    instance_id: str = "local"


# JSON-Datei-Keys -> Config-Feld. Env-Variablen ueberschreiben die Datei.
_JSON_KEYS = {
    "env": "env",
    "image": "image",
    "base_dir": "base_dir",
    "docker": "docker",
    "timeout": "timeout",
    "health_deadline": "health_deadline",
    "health_interval": "health_interval",
    "min_free_gb": "min_free_gb",
    "bridge_port_base": "bridge_port_base",
    "bridge_container_port": "bridge_container_port",
    "config_cfg": "config_cfg",
    "rbtools_dir": "rbtools_dir",
    "game_source": "game_source",
    "image_build_dir": "image_build_dir",
    "instance_id": "instance_id",
}

_ENV_KEYS = {
    "PROVISIONER_ENV": "env",
    "PROVISIONER_IMAGE": "image",
    "PROVISIONER_BASE_DIR": "base_dir",
    "PROVISIONER_DOCKER": "docker",
    "PROVISIONER_TIMEOUT": "timeout",
    "PROVISIONER_HEALTH_DEADLINE": "health_deadline",
    "PROVISIONER_HEALTH_INTERVAL": "health_interval",
    "PROVISIONER_MIN_FREE_GB": "min_free_gb",
    "PROVISIONER_BRIDGE_PORT_BASE": "bridge_port_base",
    "PROVISIONER_BRIDGE_CONTAINER_PORT": "bridge_container_port",
    "PROVISIONER_CONFIG_CFG": "config_cfg",
    "PROVISIONER_RBTOOLS_DIR": "rbtools_dir",
    "PROVISIONER_GAME_SOURCE": "game_source",
    "PROVISIONER_IMAGE_BUILD_DIR": "image_build_dir",
    "PROVISIONER_INSTANCE_ID": "instance_id",
}

_INT_FIELDS = ("timeout", "bridge_port_base", "bridge_container_port")
_FLOAT_FIELDS = ("health_deadline", "health_interval", "min_free_gb")


def _coerce(field: str, value: Any) -> Any:
    if field in _INT_FIELDS:
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ConfigError("Feld '%s' muss eine ganze Zahl sein (war %r)" % (field, value))
    if field in _FLOAT_FIELDS:
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ConfigError("Feld '%s' muss eine Zahl sein (war %r)" % (field, value))
    return str(value).strip()


def load_config(env: Optional[Dict[str, str]] = None, path: Optional[str] = None) -> Config:
    """Config aus optionaler JSON-Datei (``PROVISIONER_CONFIG``) + Env.

    Env ueberschreibt Dateiwerte. ``PROVISIONER_IMAGE`` ist Pflicht (fail-closed,
    :class:`ConfigError` nennt die Variable).
    """
    env = os.environ if env is None else env
    values: Dict[str, Any] = {}

    if path is None:
        path = env.get("PROVISIONER_CONFIG") or ""
    if path:
        if not os.path.exists(path):
            raise ConfigError("PROVISIONER_CONFIG zeigt auf fehlende Datei: %s" % path)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except ValueError as exc:
            raise ConfigError("PROVISIONER_CONFIG ist kein gueltiges JSON: %s" % exc)
        if not isinstance(payload, dict):
            raise ConfigError("PROVISIONER_CONFIG muss ein JSON-Objekt sein")
        for key, value in payload.items():
            field = _JSON_KEYS.get(key)
            if field is None:
                raise ConfigError("PROVISIONER_CONFIG enthaelt unbekannten Schluessel: %s" % key)
            values[field] = _coerce(field, value)

    for var, field in _ENV_KEYS.items():
        raw = env.get(var)
        if raw is not None and str(raw).strip() != "":
            values[field] = _coerce(field, raw)

    cfg = Config(**values) if values else Config()

    if not cfg.image:
        raise ConfigError(
            "PROVISIONER_IMAGE fehlt — ohne Image startet der Provisioner NICHT "
            "(fail-closed). Image per Env oder PROVISIONER_CONFIG setzen."
        )
    if not INSTANCE_ID_RE.match(cfg.instance_id):
        raise ConfigError(
            "PROVISIONER_INSTANCE_ID '%s' ist ungueltig (erlaubt: %s)"
            % (cfg.instance_id, INSTANCE_ID_RE.pattern)
        )
    if not (1 <= cfg.bridge_container_port <= 65535):
        raise ConfigError(
            "bridge_container_port %r ist ungueltig (erlaubt: 1..65535)"
            % (cfg.bridge_container_port,)
        )
    return cfg


# ---------------------------------------------------------------------------
# US2 — InstanceSpec: deterministische run-scoped Namen/Ports/Pfade
# ---------------------------------------------------------------------------


class InstanceSpec(object):
    """Deterministische Run-Ressourcen aus ``(env, instance_id)``.

    Rein rechnend, ohne Side-Effects und ohne Docker-Zugriff. Alle Namen, die
    spaeter an ``docker`` gehen, stammen aus dieser Klasse.
    """

    def __init__(self, env: str, instance_id: str, cfg: Config) -> None:
        if not isinstance(instance_id, str) or not INSTANCE_ID_RE.match(instance_id):
            raise ProvisionError(
                "instance_id '%s' ist ungueltig (erlaubt: %s)"
                % (instance_id, INSTANCE_ID_RE.pattern)
            )
        if not env or not INSTANCE_ID_RE.match(env):
            raise ProvisionError("env '%s' ist ungueltig" % env)

        self.env = env
        self.instance_id = instance_id
        self.container = "riftbreaker-dedicated-%s-%s" % (env, instance_id)
        self.compose_project = "rb-%s-%s" % (env, instance_id)
        self.network = "%s_default" % self.compose_project
        self.wine_volume = "rb-%s-wine-%s" % (env, instance_id)
        self.saves_volume = "rb-%s-saves-%s" % (env, instance_id)
        run_root = os.path.join(cfg.base_dir, "rift-%s-%s" % (env, instance_id))
        self.run_root = run_root
        self.game_dir = os.path.join(run_root, "game")
        self.backups_dir = os.path.join(run_root, "backups")
        self.sessions_dir = os.path.join(run_root, "sessions")
        self.bridge_port_base = cfg.bridge_port_base
        self.bridge_port = cfg.bridge_port_base + (self._numeric_suffix() % 20000)
        # Reale Image-Quellen (Platzhalter {env} wird durch das Env-Segment ersetzt).
        self.bridge_container_port = cfg.bridge_container_port
        self.config_cfg = cfg.config_cfg.replace("{env}", env)
        self.rbtools_dir = cfg.rbtools_dir.replace("{env}", env)
        self.game_source = cfg.game_source.replace("{env}", env)

    def _numeric_suffix(self) -> int:
        """Stabiler Zahlenwert fuer die Port-Ableitung.

        Rein numerische ``instance_id`` (github.run_id) wird direkt genutzt
        (Plan-Beispiel: 12345 -> 42345). Nicht-numerische Suffixe (z. B.
        ``local``) bekommen einen deterministischen Wert via ``crc32``.
        """
        if self.instance_id.isdigit():
            return int(self.instance_id)
        import zlib

        return zlib.crc32(self.instance_id.encode("utf-8")) & 0xFFFFFFFF

    def health_url(self) -> str:
        return "http://127.0.0.1:%d/health" % self.bridge_port

    def to_dict(self) -> Dict[str, Any]:
        return {
            "env": self.env,
            "instance_id": self.instance_id,
            "container": self.container,
            "compose_project": self.compose_project,
            "network": self.network,
            "wine_volume": self.wine_volume,
            "saves_volume": self.saves_volume,
            "run_root": self.run_root,
            "game_dir": self.game_dir,
            "backups_dir": self.backups_dir,
            "sessions_dir": self.sessions_dir,
            "bridge_port": self.bridge_port,
            "bridge_container_port": self.bridge_container_port,
            "config_cfg": self.config_cfg,
            "rbtools_dir": self.rbtools_dir,
            "game_source": self.game_source,
        }


# ---------------------------------------------------------------------------
# US1 — DockerCli: duenner, injizierbarer Docker-Wrapper (Argumentliste)
# ---------------------------------------------------------------------------


class DockerCli(object):
    """Duenner Wrapper um die ``docker``-CLI (Argumentliste, nie eine Shell)."""

    def __init__(self, binary: str = "docker", timeout: int = 60) -> None:
        self.binary = binary
        self.timeout = timeout

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

    def run(self, args: Sequence[str]) -> Tuple[int, str, str]:
        return self._run(args)

    def run_or_fail(self, args: Sequence[str]) -> Tuple[int, str, str]:
        rc, out, err = self._run(args)
        if rc != 0:
            raise DockerError(
                "docker %s: %s" % (" ".join(args), err.strip() or "exit %d" % rc)
            )
        return rc, out, err

    def inspect(self, name: str) -> Dict[str, Any]:
        rc, out, err = self._run(["inspect", name])
        if rc != 0:
            raise DockerError("docker inspect %s: %s" % (name, err.strip() or "exit %d" % rc))
        try:
            payload = json.loads(out)
        except ValueError as exc:
            raise DockerError("docker inspect lieferte kein JSON: %s" % exc)
        if not payload:
            raise DockerError("docker inspect lieferte keinen Container")
        return payload[0]

    def inspect_optional(self, name: str) -> Optional[Dict[str, Any]]:
        """Wie ``inspect``, aber fehlende Ressource -> ``None`` (kein Fehler)."""
        rc, out, _err = self._run(["inspect", name])
        if rc != 0:
            return None
        try:
            payload = json.loads(out)
        except ValueError:
            return None
        if not payload:
            return None
        return payload[0]

    def ps_all(self, filter_label: Optional[str] = None) -> List[str]:
        args = ["ps", "-a", "--format", "{{.Names}}"]
        if filter_label:
            args += ["--filter", "label=%s" % filter_label]
        rc, out, _err = self._run(args)
        if rc != 0:
            return []
        return [line.strip() for line in out.splitlines() if line.strip()]

    def port(self, container: str) -> Dict[str, str]:
        rc, out, _err = self._run(["port", container])
        if rc != 0:
            return {}
        mapping: Dict[str, str] = {}
        for line in out.splitlines():
            line = line.strip()
            if "->" in line:
                key, value = line.split("->", 1)
                mapping[key.strip()] = value.strip()
        return mapping

    # -- Existenz (fuer idempotentes Teardown) -----------------------------
    def network_exists(self, name: str) -> bool:
        rc, _out, _err = self._run(["network", "inspect", name])
        return rc == 0

    def volume_exists(self, name: str) -> bool:
        rc, _out, _err = self._run(["volume", "inspect", name])
        return rc == 0

    def image_exists(self, ref: str) -> bool:
        rc, _out, _err = self._run(["image", "inspect", ref])
        return rc == 0

    # -- Schreiber (Toleranz bei Loeschen = ``|| true`` aus dem Boot-Test) --
    def start(self, container: str) -> Tuple[int, str, str]:
        return self._run(["start", container])

    def stop(self, container: str) -> Tuple[int, str, str]:
        return self._run(["stop", container])

    def rm(self, container: str) -> Tuple[int, str, str]:
        return self._run(["rm", "-f", container])

    def network_create(self, name: str) -> Tuple[int, str, str]:
        return self._run(["network", "create", name])

    def network_rm(self, name: str) -> Tuple[int, str, str]:
        return self._run(["network", "rm", name])

    def volume_create(self, name: str) -> Tuple[int, str, str]:
        return self._run(["volume", "create", name])

    def volume_rm(self, name: str) -> Tuple[int, str, str]:
        return self._run(["volume", "rm", name])


# ---------------------------------------------------------------------------
# Health-Probe
# ---------------------------------------------------------------------------


def http_health_ok(url: str, timeout: float = 5.0) -> bool:
    """``GET <url>`` -> True nur bei HTTP 200 UND Body ``{"ok": true}``."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                return False
            body = response.read().decode("utf-8", "replace")
        payload = json.loads(body)
    except (urllib.error.URLError, OSError, ValueError):
        return False
    return bool(isinstance(payload, dict) and payload.get("ok") is True)


# ---------------------------------------------------------------------------
# US4/US5/US6 — Provisioner
# ---------------------------------------------------------------------------


class Provisioner(object):
    """Fachlogik: start (fail-loud, idempotent, Rollback), stop, status."""

    def __init__(
        self,
        cfg: Config,
        docker: Optional[DockerCli] = None,
        spec_factory: Callable[..., InstanceSpec] = InstanceSpec,
        health_probe: Optional[Callable[[str], bool]] = None,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.docker = docker or DockerCli(cfg.docker, cfg.timeout)
        self.spec_factory = spec_factory
        self.health_probe = health_probe or http_health_ok
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep

    # -- oeffentlich -------------------------------------------------------
    def start(
        self,
        env: Optional[str] = None,
        mode: str = "solo",
        instance_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        env = env or self.cfg.env
        instance_id = instance_id if instance_id is not None else self.cfg.instance_id
        spec = self.spec_factory(env, instance_id, self.cfg)

        existing = self.docker.inspect_optional(spec.container)
        if existing is not None:
            # Idempotenz: es gibt den Container schon -> KEIN zweiter.
            status = (existing.get("State") or {}).get("Status") or ""
            if status != "running":
                rc, _out, err = self.docker.start(spec.container)
                if rc != 0:
                    raise ProvisionError(
                        "vorhandenen Container %s nicht startbar: %s" % (spec.container, err.strip())
                    )
            if not self._wait_healthy(spec):
                raise ProvisionError(
                    "Instanz %s laeuft, aber %s liefert kein ok innerhalb der Frist"
                    % (instance_id, spec.health_url())
                )
            return self._status_dict(spec, created=False)

        self._preflight(spec)

        created: Dict[str, Any] = {"container": False, "network": False, "volumes": [], "dirs": []}
        try:
            self._create_dirs(spec, created)
            self._create_network(spec, created)
            self._create_volumes(spec, created)
            self._create_container(spec, created, mode)
            if not self._wait_healthy(spec):
                raise ProvisionError(
                    "Health-Timeout: %s liefert kein ok innerhalb von %ss"
                    % (spec.health_url(), self.cfg.health_deadline)
                )
        except Exception:
            self._rollback(spec, created)
            raise
        return self._status_dict(spec, created=True)

    def stop(
        self, instance_id: Optional[str] = None, env: Optional[str] = None
    ) -> Dict[str, Any]:
        env = env or self.cfg.env
        instance_id = instance_id if instance_id is not None else self.cfg.instance_id
        spec = self.spec_factory(env, instance_id, self.cfg)

        removed = {"container": False, "network": False, "volumes": False, "dirs": False}

        # Reihenfolge wie Boot-Test-Cleanup: Container -> Netz -> Volumes -> Pfade.
        if self.docker.inspect_optional(spec.container) is not None:
            removed["container"] = True
        self.docker.rm(spec.container)

        if self.docker.network_exists(spec.network):
            removed["network"] = True
        self.docker.network_rm(spec.network)

        volumes_present = False
        for volume in (spec.wine_volume, spec.saves_volume):
            if self.docker.volume_exists(volume):
                volumes_present = True
            self.docker.volume_rm(volume)
        removed["volumes"] = volumes_present

        dirs_present = False
        for directory in (spec.game_dir, spec.backups_dir, spec.sessions_dir):
            if os.path.isdir(directory):
                dirs_present = True
                shutil.rmtree(directory, ignore_errors=True)
        if os.path.isdir(spec.run_root) and not os.listdir(spec.run_root):
            os.rmdir(spec.run_root)
        removed["dirs"] = dirs_present

        return {"instance": spec.instance_id, "removed": removed}

    def status(
        self, instance_id: Optional[str] = None, env: Optional[str] = None
    ) -> Dict[str, Any]:
        env = env or self.cfg.env
        instance_id = instance_id if instance_id is not None else self.cfg.instance_id
        spec = self.spec_factory(env, instance_id, self.cfg)
        info = self.docker.inspect_optional(spec.container)
        running = bool(info) and ((info.get("State") or {}).get("Status") == "running")
        return {
            "running": running,
            "health": self._health_label(spec, running),
            "ports": self._ports(spec),
            "container": spec.container,
        }

    # -- intern ------------------------------------------------------------
    def _preflight(self, spec: InstanceSpec) -> None:
        if self._port_in_use(spec.bridge_port):
            raise ProvisionError(
                "Bridge-Port %d ist belegt (127.0.0.1) — Instanz %s nicht startbar"
                % (spec.bridge_port, spec.instance_id)
            )
        free = self._free_bytes(spec.run_root)
        need = int(self.cfg.min_free_gb * BYTES_PER_GB)
        if free < need:
            raise ProvisionError(
                "zu wenig Plattenplatz unter %s: %.2f GB frei, %.2f GB noetig"
                % (spec.run_root, free / BYTES_PER_GB, self.cfg.min_free_gb)
            )
        if not self.docker.image_exists(self.cfg.image):
            raise ProvisionError("Image fehlt: %s (docker image inspect -> rc!=0)" % self.cfg.image)
        missing = []
        if not os.path.isdir(spec.game_source):
            missing.append("game_source=%s" % spec.game_source)
        if not os.path.isfile(spec.config_cfg):
            missing.append("config_cfg=%s" % spec.config_cfg)
        if not os.path.isdir(spec.rbtools_dir):
            missing.append("rbtools_dir=%s" % spec.rbtools_dir)
        if missing:
            raise ProvisionError(
                "Quellen fehlen (Mounts wuerden ins Leere zeigen): %s" % ", ".join(missing)
            )

    @staticmethod
    def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        try:
            probe.connect((host, port))
            return True
        except OSError:
            return False
        finally:
            probe.close()

    @staticmethod
    def _free_bytes(path: str) -> int:
        probe = path
        while probe and not os.path.exists(probe):
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        if not probe or not os.path.exists(probe):
            probe = os.sep
        return shutil.disk_usage(probe).free

    def _create_dirs(self, spec: InstanceSpec, created: Dict[str, Any]) -> None:
        for directory in (spec.game_dir, spec.backups_dir, spec.sessions_dir):
            if not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)
                created["dirs"].append(directory)

    def _create_network(self, spec: InstanceSpec, created: Dict[str, Any]) -> None:
        if not self.docker.network_exists(spec.network):
            self.docker.run_or_fail(["network", "create", spec.network])
            created["network"] = True

    def _create_volumes(self, spec: InstanceSpec, created: Dict[str, Any]) -> None:
        for volume in (spec.wine_volume, spec.saves_volume):
            if not self.docker.volume_exists(volume):
                self.docker.run_or_fail(["volume", "create", volume])
                created["volumes"].append(volume)

    def _create_container(self, spec: InstanceSpec, created: Dict[str, Any], mode: str) -> None:
        args = [
            "run",
            "-d",
            "--name", spec.container,
            "--network", spec.network,
            "--label", "rb.provisioner.env=%s" % spec.env,
            "--label", "rb.provisioner.instance=%s" % spec.instance_id,
            "-p", "127.0.0.1:%d:%d" % (spec.bridge_port, spec.bridge_container_port),
            "-p", "127.0.0.1::6321/udp",
            "-v", "%s:/opt/riftbreaker" % spec.game_source,
            "-v", "%s:/data/.wine" % spec.wine_volume,
            "-v", "%s:/data/saves" % spec.saves_volume,
            "-v", "%s:/data/config/config.cfg:ro" % spec.config_cfg,
            "-v", "%s:/opt/rbtools:ro" % spec.rbtools_dir,
            "-e", "RIFTBREAKER_MODE=%s" % mode,
            "-e", "RBB_BRIDGE_BIND=0.0.0.0",
            "-e", "RBB_BRIDGE_PORT=%d" % spec.bridge_container_port,
            "-e", "WINEESYNC=0",
            "-e", "WINEFSYNC=0",
            self.cfg.image,
        ]
        self.docker.run_or_fail(args)
        created["container"] = True

    def _wait_healthy(self, spec: InstanceSpec) -> bool:
        deadline = self.clock() + self.cfg.health_deadline
        while True:
            if self.health_probe(spec.health_url()):
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(self.cfg.health_interval)

    def _health_label(self, spec: InstanceSpec, running: bool) -> str:
        if not running:
            return "unreachable"
        return "healthy" if self.health_probe(spec.health_url()) else "starting"

    @staticmethod
    def _gns_from_mapping(mapping: Dict[str, str]) -> Optional[str]:
        """GNS-UDP-Host-Endpoint der Instanz aus dem ``6321/udp``-Mapping (#929).

        Der Publizier-Aufruf ``-p 127.0.0.1::6321/udp`` vergibt einen
        ephemeren Host-Port; ``docker port`` liefert z. B.
        ``{"6321/udp": "127.0.0.1:32768"}``. Fehlt der Eintrag, ``None`` —
        kein Crash (der Port ist additiv, nicht Pflicht).
        """
        value = mapping.get("6321/udp")
        return value or None

    def _ports(self, spec: InstanceSpec) -> Dict[str, Any]:
        mapping = self.docker.port(spec.container)
        return {
            "bridge": spec.bridge_port,
            "gns": self._gns_from_mapping(mapping),
            "docker": mapping,
        }

    def _status_dict(self, spec: InstanceSpec, created: bool) -> Dict[str, Any]:
        info = self.docker.inspect_optional(spec.container)
        running = bool(info) and ((info.get("State") or {}).get("Status") == "running")
        return {
            "instance": spec.instance_id,
            "container": spec.container,
            "running": running,
            "health": self._health_label(spec, running),
            "ports": self._ports(spec),
            "created": created,
        }

    def _rollback(self, spec: InstanceSpec, created: Dict[str, Any]) -> None:
        """Alle in DIESEM Aufruf erzeugten Ressourcen wieder entfernen.

        Best effort: ein Fehler beim Aufraeumen wird geloggt, aber nicht zum
        neuen Fehler (der urspruengliche ``ProvisionError`` bleibt sichtbar).
        """
        if created.get("container"):
            try:
                self.docker.rm(spec.container)
            except DockerError as exc:  # pragma: no cover - nur Log
                LOG.warning("Rollback Container %s: %s", spec.container, exc)
        for volume in created.get("volumes", []):
            try:
                self.docker.volume_rm(volume)
            except DockerError as exc:  # pragma: no cover - nur Log
                LOG.warning("Rollback Volume %s: %s", volume, exc)
        if created.get("network"):
            try:
                self.docker.network_rm(spec.network)
            except DockerError as exc:  # pragma: no cover - nur Log
                LOG.warning("Rollback Netz %s: %s", spec.network, exc)
        for directory in created.get("dirs", []):
            shutil.rmtree(directory, ignore_errors=True)
        if os.path.isdir(spec.run_root) and not os.listdir(spec.run_root):
            try:
                os.rmdir(spec.run_root)
            except OSError:  # pragma: no cover - nur Log
                pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="provisioner",
        description="Riftbreaker-Dedi-Instanz on-demand starten/stoppen (Issue #908)",
    )
    parser.add_argument("--check", action="store_true", help="nur Konfiguration pruefen und beenden")
    sub = parser.add_subparsers(dest="command")

    start = sub.add_parser("start", help="eine Instanz starten (idempotent)")
    start.add_argument("--env", default=None)
    start.add_argument("--mode", default="solo")
    start.add_argument("--instance-id", dest="instance_id", default=None)

    stop = sub.add_parser("stop", help="eine Instanz restfrei stoppen (idempotent)")
    stop.add_argument("--env", default=None)
    stop.add_argument("--instance-id", dest="instance_id", default=None)

    status = sub.add_parser("status", help="Status einer Instanz ausgeben")
    status.add_argument("--env", default=None)
    status.add_argument("--instance-id", dest="instance_id", default=None)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("PROVISIONER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        cfg = load_config()
    except ConfigError as exc:
        LOG.error("%s", exc)
        print("config error: %s" % exc, file=sys.stderr)
        return 2

    if args.check:
        print(
            "configuration OK (env=%s image=%s base_dir=%s min_free_gb=%s health_deadline=%ss)"
            % (cfg.env, cfg.image, cfg.base_dir, cfg.min_free_gb, cfg.health_deadline)
        )
        return 0

    if not args.command:
        parser.print_help()
        return 2

    provisioner = Provisioner(cfg)
    try:
        if args.command == "start":
            result = provisioner.start(args.env, args.mode, args.instance_id)
        elif args.command == "stop":
            result = provisioner.stop(args.instance_id, args.env)
        else:
            result = provisioner.status(args.instance_id, args.env)
    except (ProvisionError, DockerError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())