#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-parked — Warm-Pool „Parked Solo" (Issue #909).

Was ist ein *geparkter* Server?
-------------------------------

Ein Dedicated-Server, dessen **Container + Mod-Load + Bridge bereits laufen**
(Cold-Boot ist bezahlt und abgeschlossen), dessen Welt aber **angehalten** ist:
``POST /pause_game`` hat die Simulation gestoppt, es laeuft **kein** Welt-Tick.
Der Handover an ein echtes Spiel ist dann nur noch ``POST /resume_game``
(sub-sekundig) statt eines kompletten Cold-Boots.

Invariante „kein Weltfortschritt"
---------------------------------

Ein ``PARKED``-Server treibt die Simulation **nicht** voran. Zwischen
``pause_game()`` (Uebergang -> ``PARKED``) und ``resume_game()`` (Uebergang ->
``CLAIMED``) darf keine Spielzeit vergehen, kein Angriffswellen-Tick laufen und
kein Rundenzaehler steigen. Jede Operation, die den geparkten Zustand
verlaesst, ist explizit und messbar:

    WARMING --pause_game--> PARKED --resume_game--> CLAIMED
    CLAIMED --end_game/round_reset--> RECYCLING --pause_game--> PARKED
    {JEDER, PARKED} --stop--> STOPPED

Fehler brechen **laut** ab (:class:`ParkedError`) statt einen halben Zustand zu
hinterlassen. Der Pool verlaesst sich auf den Provisioner aus
``deploy/provisioner`` (Issue #908) fuer Start/Stop — er dupliziert **keine**
Docker-Logik. Nur Standardbibliothek (stdlib), kein venv/pip.
"""

from __future__ import annotations

import dataclasses
import enum
import itertools
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

LOG = logging.getLogger("parked")


class ParkedError(Exception):
    """Parked-Operation nicht moeglich — laut abbrechen (kein halber Zustand)."""


class BridgeError(ParkedError):
    """Bridge nicht erreichbar oder lieferte keine brauchbare Antwort."""


class ParkedState(enum.Enum):
    """Lebenszyklus eines Pool-Eintrags."""

    WARMING = "warming"      # Container startet, noch nicht geparkt
    PARKED = "parked"        # laeuft, Sim angehalten (kein Weltfortschritt)
    CLAIMED = "claimed"      # an ein Spiel uebergeben (Sim laeuft)
    RECYCLING = "recycling"  # nach Spielende: end_game/round_reset laufen
    STOPPED = "stopped"      # Container gestoppt bzw. entfernt


# ---------------------------------------------------------------------------
# BridgeClient — duenner stdlib-HTTP-Client auf die Bridge (injizierbar)
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
        self._opener = opener or urllib.request.urlopen

    # -- Transport ---------------------------------------------------------
    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None):
        url = "%s%s" % (self.base_url, path)
        data: Optional[bytes] = None
        headers: Dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raise BridgeError("%s %s -> HTTP %s" % (method, path, exc.code))
        except (urllib.error.URLError, OSError, ValueError) as exc:
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

    # -- API ---------------------------------------------------------------
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

    def get_state(self) -> Dict[str, Any]:
        return self._post("/get_state")


# ---------------------------------------------------------------------------
# ParkedEntry
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ParkedEntry:
    """Ein Pool-Eintrag (eine dedizierte Instanz + ihr Parked-Zustand)."""

    instance_id: str
    env: str
    container: str
    bridge_url: str
    cycle_url: Optional[str] = None
    gns_endpoint: Optional[str] = None
    # Issue #994: der beim Warm-Up fixierte Container-Modus
    # (`solo_self` | `solo_persona:<name>`). Default = heutiges Verhalten.
    mode: str = "solo_self"
    state: ParkedState = ParkedState.WARMING
    parked_since: Optional[float] = None
    claimed_at: Optional[float] = None
    rounds: int = 0

    def to_dict(self, now: Optional[float] = None) -> Dict[str, Any]:
        parked_seconds: Optional[float] = None
        if now is not None and self.state == ParkedState.PARKED and self.parked_since is not None:
            parked_seconds = now - self.parked_since
        return {
            "instance": self.instance_id,
            "env": self.env,
            "container": self.container,
            "bridge_url": self.bridge_url,
            "cycle_url": self.cycle_url,
            "gns_endpoint": self.gns_endpoint,
            "mode": self.mode,
            "state": self.state.value,
            "rounds": self.rounds,
            "parked_seconds": parked_seconds,
        }


# ---------------------------------------------------------------------------
# ParkedPool
# ---------------------------------------------------------------------------


class ParkedPool(object):
    """Warm-Pool ueber dem Provisioner (#908) und der Bridge.

    ``provisioner`` — Objekt mit ``start(env, mode, instance_id)`` /
    ``stop(instance_id, env)`` (i. d. R. ``provisioner.Provisioner``).
    ``bridge_factory`` — ``callable(base_url) -> BridgeClient`` (injizierbar).
    ``clock`` / ``sleep`` — injizierbar (Tests: Fake, echt: ``time``).
    """

    # Marker-Dateien liegen unter ``state_dir``: ``claimed-<env>-<instance>``.
    # Nur ``[A-Za-z0-9_.-]`` im Namen (Pfad-Traversal-Schutz fuer Discovery-Daten).
    _MARKER_SAFE = re.compile(r"[^A-Za-z0-9_.-]")

    def __init__(
        self,
        provisioner: Any,
        bridge_factory: Optional[Callable[[str], Any]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        state_dir: Optional[str] = None,
    ) -> None:
        self.provisioner = provisioner
        self.bridge_factory = bridge_factory or (lambda url: BridgeClient(url))
        self.clock = clock
        self.sleep = sleep
        self.state_dir = self._resolve_state_dir(state_dir)
        self._entries: Dict[Tuple[str, str], ParkedEntry] = {}
        self._ids = itertools.count(1)

    # -- intern ------------------------------------------------------------
    def _default_env(self) -> str:
        cfg = getattr(self.provisioner, "cfg", None)
        return getattr(cfg, "env", None) or "test"

    # -- persistente Claim-Markierung (#969 B1) ---------------------------
    def _resolve_state_dir(self, state_dir: Optional[str]) -> str:
        """State-Verzeichnis fuer die Claim-Marker bestimmen.

        Reihenfolge: explizit uebergeben > ``PARKED_STATE_DIR`` >
        ``<provisioner.cfg.base_dir>/parked-state/<env>`` (Default ``/srv``).
        So ueberlebt die Claim-Info einen Dienst-Restart, ohne dass der Pool
        ein komplettes JSON-State-File schreiben/laden muss.
        """
        if state_dir:
            return str(state_dir)
        env_dir = os.environ.get("PARKED_STATE_DIR")
        if env_dir and env_dir.strip():
            return env_dir.strip()
        cfg = getattr(self.provisioner, "cfg", None)
        base = getattr(cfg, "base_dir", None) or "/srv"
        return os.path.join(str(base), "parked-state", self._default_env())

    @classmethod
    def _marker_name(cls, env: str, instance_id: str) -> str:
        safe_env = cls._MARKER_SAFE.sub("_", str(env))[:64]
        safe_instance = cls._MARKER_SAFE.sub("_", str(instance_id))[:64]
        return "claimed-%s-%s" % (safe_env, safe_instance)

    def _marker_path(self, env: str, instance_id: str) -> str:
        return os.path.join(self.state_dir, self._marker_name(env, instance_id))

    def _mark_claimed(self, env: str, instance_id: str) -> None:
        """Claim-Marker atomar schreiben (temp + ``os.replace``).

        Enthaelt ``{env, instance, claimed_at}`` als JSON; das macht
        :meth:`claimed_instances` robust (kein Parsen des Dateinamens).
        """
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            path = self._marker_path(env, instance_id)
            payload = {
                "env": env,
                "instance": instance_id,
                "claimed_at": self.clock(),
            }
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(tmp, path)
        except OSError as exc:  # pragma: no cover - nur Log, kein harter Abbruch
            LOG.warning("claim-Marker fuer %s (env=%s) nicht schreibbar: %s", instance_id, env, exc)

    def _clear_claimed(self, env: str, instance_id: str) -> None:
        """Claim-Marker entfernen (idempotent; fehlt er, ist es kein Fehler)."""
        try:
            os.remove(self._marker_path(env, instance_id))
        except FileNotFoundError:
            return
        except OSError as exc:  # pragma: no cover - nur Log
            LOG.warning("claim-Marker fuer %s (env=%s) nicht entfernbar: %s", instance_id, env, exc)

    def _is_claimed_marker(self, env: str, instance_id: str) -> bool:
        """True, wenn ein persistenter Claim-Marker fuer ``(env, instance)`` existiert."""
        try:
            return os.path.exists(self._marker_path(env, instance_id))
        except OSError:  # pragma: no cover - defensiv
            return False

    def claimed_instances(self, env: Optional[str] = None) -> Set[str]:
        """Instance-IDs mit Claim-Marker der EIGENEN ``env`` (persistiert).

        Grundlage fuer :meth:`ParkedController._next_id`: eine frische Instanz
        darf keine ID wiederverwenden, die noch von einem (nach einem Restart
        untracked) geclaimten Container belegt ist.
        """
        env = env or self._default_env()
        result: Set[str] = set()
        try:
            names = os.listdir(self.state_dir)
        except OSError:
            return result
        for name in names:
            if not name.startswith("claimed-") or name.endswith(".tmp"):
                continue
            try:
                with open(os.path.join(self.state_dir, name), encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("env") == env and payload.get("instance"):
                result.add(str(payload["instance"]))
        return result

    def _next_id(self) -> str:
        return "parked-%d" % next(self._ids)

    def _bridge_url(self, result: Dict[str, Any]) -> str:
        ports = result.get("ports") or {}
        bridge_port = ports.get("bridge")
        if bridge_port:
            return "http://127.0.0.1:%d" % int(bridge_port)
        url = result.get("bridge_url")
        if url:
            return str(url)
        raise ParkedError("Provisioner-Ergebnis ohne Bridge-Port: %r" % (result,))

    def _cycle_url(self, result: Dict[str, Any]) -> str:
        """Attack-Cycle-Control-URL der Instanz (#966) — analog ``_bridge_url``.

        Quelle: ``result["ports"]["attack_cycle"]`` (Host-Port, den der
        Provisioner pro Instanz ableitet); Fallback die fertige URL
        ``result["cycle_url"]``/``result["attack_cycle_url"]``. Fehlt beides,
        ist das ein harter Fehler (gleiche Haerte wie ``_bridge_url``).
        """
        ports = result.get("ports") or {}
        cycle_port = ports.get("attack_cycle")
        if cycle_port:
            return "http://127.0.0.1:%d" % int(cycle_port)
        url = result.get("cycle_url") or result.get("attack_cycle_url")
        if url:
            return str(url)
        raise ParkedError("Provisioner-Ergebnis ohne Attack-Cycle-Port: %r" % (result,))

    @staticmethod
    def _gns_endpoint(result: Dict[str, Any]) -> Optional[str]:
        """GNS-UDP-Host-Endpoint der Instanz aus ``ports["gns"]`` (#929).

        Der Provisioner liefert das 6321/udp-Mapping als ``"127.0.0.1:32768"``
        (oder ``None``). Tolerant fuer eine strukturierte Form
        ``{"host":…,"port":…}``; fehlt das Feld, ``None`` (kein Crash).
        """
        ports = result.get("ports") or {}
        gns = ports.get("gns")
        if isinstance(gns, dict):
            port = gns.get("port")
            if not port:
                return None
            return "%s:%s" % (gns.get("host") or "127.0.0.1", port)
        if gns:
            return str(gns)
        return None

    def _refresh_gns_endpoint(self, entry: ParkedEntry) -> None:
        """Endpoint zum Claim-Zeitpunkt FRISCH lesen (der Host-UDP-Port wechselt
        bei einem Container-Neustart). Fehler/fehlende Provisioner-Methode sind
        best-effort: der zuletzt bekannte Wert bleibt stehen."""
        status_fn = getattr(self.provisioner, "status", None)
        if not callable(status_fn):
            return
        try:
            result = status_fn(instance_id=entry.instance_id, env=entry.env)
        except Exception as exc:  # noqa: BLE001 - best-effort, kein harter Abbruch
            LOG.warning("gns_endpoint refresh fuer %s: %s", entry.instance_id, exc)
            return
        # Erfolgreicher Status ist die frische Quelle: auch ein fehlendes
        # 6321/udp-Mapping (None) ersetzt den warm_up-Wert.
        entry.gns_endpoint = self._gns_endpoint(result)
        # Cycle-URL ebenfalls frisch halten (best-effort, #966): fehlt sie im
        # Status, bleibt der zuletzt bekannte Wert stehen.
        try:
            entry.cycle_url = self._cycle_url(result)
        except ParkedError:
            pass

    def _bridge(self, url: str) -> Any:
        return self.bridge_factory(url)

    def _require(self, env: Optional[str], instance_id: Optional[str], state: ParkedState,
                 action: str) -> ParkedEntry:
        env = env or self._default_env()
        if not instance_id:
            raise ParkedError("%s braucht eine instance_id" % action)
        entry = self._entries.get((env, instance_id))
        if entry is None:
            raise ParkedError("%s: unbekannte Instanz %s (env=%s)" % (action, instance_id, env))
        if entry.state != state:
            raise ParkedError(
                "%s: Instanz %s ist %s, erwartet %s"
                % (action, instance_id, entry.state.value, state.value)
            )
        return entry

    # -- API ---------------------------------------------------------------
    def warm_up(self, env: Optional[str] = None, instance_id: Optional[str] = None,
                mode: str = "solo_self") -> ParkedEntry:
        """Instanz starten und parken. Idempotent: ist sie schon ``PARKED``,
        passiert **kein** zweiter Start.

        ``mode`` (Issue #994) ist der Modus, in dem der Container warm laeuft
        (`solo_self` Default = heutiges Verhalten; `solo_persona:<name>`). Er
        wird beim Provisionieren fixiert; ein Claim muss ihn treffen.
        """
        env = env or self._default_env()
        instance_id = instance_id or self._next_id()
        key = (env, instance_id)
        existing = self._entries.get(key)
        if existing is not None:
            if existing.state == ParkedState.PARKED:
                return existing
            raise ParkedError(
                "warm_up: Instanz %s ist %s (nicht PARKED) — abgelehnt"
                % (instance_id, existing.state.value)
            )

        entry = ParkedEntry(instance_id=instance_id, env=env, container="", bridge_url="",
                            mode=mode, state=ParkedState.WARMING)
        self._entries[key] = entry
        try:
            result = self.provisioner.start(env=env, mode=mode, instance_id=instance_id)
        except Exception as exc:
            entry.state = ParkedState.STOPPED
            raise ParkedError("warm_up: start von %s fehlgeschlagen: %s" % (instance_id, exc))

        entry.container = result.get("container") or ""
        entry.bridge_url = self._bridge_url(result)
        entry.cycle_url = self._cycle_url(result)
        entry.gns_endpoint = self._gns_endpoint(result)

        try:
            self._bridge(entry.bridge_url).pause_game()
        except Exception as exc:
            # Rollback: kein halb gestarteter, ungeparkter Server bleibt stehen.
            try:
                self.provisioner.stop(instance_id=instance_id, env=env)
            except Exception as stop_exc:  # pragma: no cover - nur Log
                LOG.warning("Rollback stop %s: %s", instance_id, stop_exc)
            entry.state = ParkedState.STOPPED
            raise ParkedError("warm_up: pause_game von %s fehlgeschlagen: %s" % (instance_id, exc))

        entry.state = ParkedState.PARKED
        entry.parked_since = self.clock()
        # Der Pool besitzt diese Instanz jetzt: ein (ggf. verwaister) Claim-
        # Marker einer frueheren Generation darf sie nicht mehr schuetzen.
        self._clear_claimed(env, instance_id)
        return entry

    def claim(self, env: Optional[str] = None, instance_id: Optional[str] = None,
              resume: bool = True, mode: Optional[str] = None) -> Dict[str, Any]:
        """Geparkte Instanz an ein Spiel uebergeben und die Handover-Dauer messen.

        ``mode`` (Issue #994) ist der ANGEFORDERTE Modus. Der Container-Modus
        ist bereits beim Warm-Up fixiert; weicht der Request ab, wird NICHT
        still umgeschaltet, sondern lauthupen (``mode_mismatch``). ``mode=None``
        (Default) prueft nichts — heutiges Verhalten.

        ``resume=True`` (Default, rueckwaerts-kompatibel): ``resume_game`` und
        Zeitmessung des Handovers (Welt laeuft danach). ``resume=False``: die
        Instanz wird uebergeben, die Welt bleibt aber PAUSIERT (``#931``
        Kapsel-Flow: der Spieler sieht ein pausiertes Spiel; erst ``ready``
        resumed). In beiden Faellen wird die Bridge-Health geprueft und der
        Eintrag -> ``CLAIMED``; ``handover_seconds`` ist bei ``resume=False``
        ~0 (kein Resume gemessen).
        """
        entry = self._require(env, instance_id, ParkedState.PARKED, "claim")
        # Issue #994: der angeforderte Modus muss zum warm laufenden Container
        # passen (kein stilles Umschalten; spiegelt die Provisioner-Regel).
        if mode is not None and mode != entry.mode:
            raise ParkedError(
                "mode_mismatch: Instanz %s laeuft in '%s', angefordert '%s'"
                % (entry.instance_id, entry.mode, mode)
            )
        # Der Host-UDP-Port kann seit dem warm_up gewechselt haben -> frisch lesen.
        self._refresh_gns_endpoint(entry)
        bridge = self._bridge(entry.bridge_url)
        if not bridge.health_ok():
            raise ParkedError("claim: Bridge %s nicht healthy" % entry.bridge_url)
        start = self.clock()
        if resume:
            try:
                bridge.resume_game()
            except Exception as exc:
                raise ParkedError(
                    "claim: resume_game von %s fehlgeschlagen: %s" % (entry.instance_id, exc)
                )
        handover = self.clock() - start
        entry.state = ParkedState.CLAIMED
        entry.claimed_at = self.clock()
        entry.parked_since = None
        # Persistente Claim-Markierung (#969 B1): ueberlebt einen Dienst-Restart,
        # damit Reconcile den laufenden geclaimten Container NIE stoppt.
        self._mark_claimed(entry.env, entry.instance_id)
        return {
            "instance": entry.instance_id,
            "env": entry.env,
            "bridge_url": entry.bridge_url,
            "cycle_url": entry.cycle_url,
            "gns_endpoint": entry.gns_endpoint,
            "state": entry.state.value,
            "resumed": bool(resume),
            "handover_seconds": handover,
        }

    def recycle(self, env: Optional[str] = None, instance_id: Optional[str] = None,
                keep_warm: bool = True, result: Optional[str] = None) -> ParkedEntry:
        """Nach Spielende aufraeumen: ``round_reset`` (+ ``pause_game``) -> wieder
        ``PARKED``. ``end_game`` wird NUR aufgerufen, wenn ``result`` mitgegeben ist
        (``win``/``lose``): die Bridge verlangt ein Pflicht-``result`` und lehnt
        ``end_game(None)`` immer mit HTTP 400 ``invalid_request`` ab. Ohne Ergebnis ist
        ``round_reset`` + ``pause_game`` der gueltige, weltunabhaengige Recycle-Pfad.
        Bei ``keep_warm=False`` stattdessen ``stop`` -> ``STOPPED``."""
        entry = self._require(env, instance_id, ParkedState.CLAIMED, "recycle")
        entry.state = ParkedState.RECYCLING
        bridge = self._bridge(entry.bridge_url)
        try:
            if result is not None:
                bridge.end_game(result)
            bridge.round_reset()
            if keep_warm:
                bridge.pause_game()
        except Exception as exc:
            raise ParkedError("recycle: %s fehlgeschlagen: %s" % (entry.instance_id, exc))

        entry.rounds += 1
        if keep_warm:
            entry.state = ParkedState.PARKED
            entry.parked_since = self.clock()
            entry.claimed_at = None
        else:
            try:
                self.provisioner.stop(instance_id=entry.instance_id, env=entry.env)
            except Exception as exc:
                raise ParkedError("recycle: stop von %s fehlgeschlagen: %s" % (entry.instance_id, exc))
            entry.state = ParkedState.STOPPED
            entry.parked_since = None
            entry.claimed_at = None
            entry.gns_endpoint = None
            entry.cycle_url = None
        # Recycle beendet den Claim -> Marker entfernen (PARKED wie STOPPED).
        self._clear_claimed(entry.env, entry.instance_id)
        return entry

    def reap(self, max_park_seconds: float) -> List[ParkedEntry]:
        """Auslaufschutz: geparkte Instanzen, die laenger als ``max_park_seconds``
        geparkt sind, sauber stoppen.

        Ein ``stop``-Fehler bei EINER Instanz bricht die Schleife NICHT ab:
        alle uebrigen ueberfaelligen Instanzen werden trotzdem gestoppt (der
        Auslaufschutz greift vollstaendig). Am Ende wird ein aggregierter
        :class:`ParkedError` geworfen, wenn mindestens ein ``stop`` scheiterte.
        """
        now = self.clock()
        stopped: List[ParkedEntry] = []
        errors: List[str] = []
        for entry in list(self._entries.values()):
            if entry.state != ParkedState.PARKED or entry.parked_since is None:
                continue
            if (now - entry.parked_since) <= max_park_seconds:
                continue
            try:
                self.provisioner.stop(instance_id=entry.instance_id, env=entry.env)
            except Exception as exc:
                errors.append("reap: stop von %s fehlgeschlagen: %s" % (entry.instance_id, exc))
                continue
            entry.state = ParkedState.STOPPED
            entry.parked_since = None
            entry.gns_endpoint = None
            entry.cycle_url = None
            self._clear_claimed(entry.env, entry.instance_id)
            stopped.append(entry)
        if errors:
            raise ParkedError("; ".join(errors))
        return stopped

    def status(self) -> List[Dict[str, Any]]:
        """Snapshot aller Pool-Eintraege (als einfache dicts)."""
        now = self.clock()
        return [entry.to_dict(now) for entry in self._entries.values()]

    # -- Reconciliation (#969) --------------------------------------------
    def _discover(self, env: str) -> List[Dict[str, Any]]:
        """Container der EIGENEN ``env`` auflisten (Discovery-Fundament).

        Bevorzugt :meth:`Provisioner.list_instances` (#969). Fehlt die Methode,
        wird defensiv ueber ``provisioner.docker.ps_all`` + ``inspect_optional``
        gelesen (gleiches Ergebnis, nur ohne Provisioner-Wrapper). Ein echter
        Daemon-Fehler beim ``ps``/``inspect`` wird dabei NICHT geschluckt — er
        steigt auf und wird von :meth:`reconcile` als Discovery-Ausfall gewertet.
        """
        list_fn = getattr(self.provisioner, "list_instances", None)
        if callable(list_fn):
            return list(list_fn(env=env) or [])

        docker = getattr(self.provisioner, "docker", None)
        ps_all = getattr(docker, "ps_all", None)
        if not callable(ps_all):
            raise ParkedError("reconcile: Provisioner ohne list_instances/docker.ps_all")
        rows: List[Dict[str, Any]] = []
        for name in ps_all("rb.provisioner.env=%s" % env):
            # Ein ECHTER inspect-Fehler (Daemon kaputt) wird laut propagiert:
            # ``reconcile`` faengt ihn als Discovery-Ausfall ab (#969 B2) und
            # evictet NICHT. Nur ein wirklich fehlender Container -> None.
            info = docker.inspect_optional(name)
            if info is None:
                continue
            labels = (info.get("Config") or {}).get("Labels") or {}
            state = info.get("State") or {}
            status = state.get("Status") or ""
            rows.append(
                {
                    "container": name,
                    "env": labels.get("rb.provisioner.env") or env,
                    "instance": labels.get("rb.provisioner.instance"),
                    "status": status,
                    "running": status == "running",
                    "started_at": state.get("StartedAt"),
                }
            )
        return rows

    def _evict(self, env: str, instance_id: str) -> None:
        """Tracking-Eintrag rein in-memory entfernen (kein Docker-Aufruf)."""
        self._entries.pop((env, instance_id), None)

    def _remove_orphan(
        self,
        env: str,
        instance_id: str,
        group: Dict[str, Any],
        status: str,
        result: Dict[str, Any],
    ) -> None:
        """Einen Orphan per vollem ``provisioner.stop`` entfernen.

        Die Bridge wird dabei NIE beruehrt (kein ``pause_game``/``resume_game``)
        — ``stop`` loest Container/Sidecars/Netz/Volumes/Pfade auf. Ein
        ``stop``-Fehler bricht die Schleife nicht ab: er wird aggregiert und
        der naechste Orphan weiter entfernt (wie :meth:`reap`).
        """
        label = "orphan:%s (status=%s)" % (
            group.get("container") or instance_id,
            status or "unknown",
        )
        LOG.info("reconcile: %s", label)
        try:
            self.provisioner.stop(instance_id=instance_id, env=env)
        except Exception as exc:  # noqa: BLE001 - ein Fehler stoppt die Schleife nicht
            result["errors"].append(
                "reconcile: stop von %s fehlgeschlagen: %s" % (instance_id, exc)
            )
            return
        result["removed"].append(instance_id)
        if not group.get("running"):
            result["zombies"].append(instance_id)
        # Defensiv: ein entfernter Container soll auch keinen Claim-Marker
        # zuruecklassen (Marker'd Container werden hier eigentlich nie erreicht).
        self._clear_claimed(env, instance_id)

    def reconcile(self) -> Dict[str, Any]:
        """Pool gegen den echten Docker-Zustand abgleichen (#969).

        Reines Mengendifferenzieren (Discovery − Tracking) innerhalb der EIGENEN
        ``env``; fremde Envs werden nie angefasst. Entfernt werden:

          * nicht getrackte Container (Restart-Leak / Orphan),
          * getrackte Eintraege, deren Container nicht laeuft (Created-Zombie /
            Exited / Dead), samt Eviction des stale Eintrags,
          * getrackte ``STOPPED``-Eintraege, deren Container noch existiert.

        Getrackte ``PARKED``/``CLAIMED``/``RECYCLING`` mit laufendem Container
        bleiben unberuehrt. Getrackte Eintraege ohne Container (Crash) werden
        evicted — ohne ``stop``. Ueberfaellige ``PARKED``-Instanzen bleiben
        Sache von :meth:`reap` (kennt ``parked_since``).

        Persistente Claim-Marker (#969 B1): jeder discoverte **und** getrackte
        Eintrag mit Marker wird **nie** gestoppt/evictet — so ueberlebt ein
        laufendes, geclaimtes Spiel einen Dienst-Restart. Faellt die Discovery
        aus (#969 B2), bricht der Lauf **ohne** Eviction ab
        (``discovery_failed=True``) — sonst wuerde die Reverse-Luecke alle
        getrackten Eintraege vergessen und ``_fill`` einen Leak erzeugen.

        Idempotent: ein zweiter Lauf findet nichts mehr. Rueckgabe::

            {"removed": [...], "evicted": [...], "zombies": [...],
             "kept": int, "skipped_foreign": int,
             "discovery_failed": bool, "errors": [str]}
        """
        env = self._default_env()
        result: Dict[str, Any] = {
            "removed": [],
            "evicted": [],
            "zombies": [],
            "kept": 0,
            "skipped_foreign": 0,
            "discovery_failed": False,
            "errors": [],
        }
        try:
            rows = self._discover(env)
        except Exception as exc:  # noqa: BLE001 - Discovery best-effort
            # Lauter Discovery-Ausfall (#969 B2): NICHT evicten, NICHT stop.
            result["errors"].append("reconcile: discovery fehlgeschlagen: %s" % exc)
            result["discovery_failed"] = True
            LOG.warning("reconcile (%s): discovery fehlgeschlagen, kein Eingriff: %s", env, exc)
            return result

        # Container je (env, instance) gruppieren. Sidecars tragen dieselben
        # rb.provisioner.*-Labels; ihre Status sind fuer den Instanzzustand
        # gleichwertig (ein Stack laeuft oder nicht).
        groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in rows:
            row_env = row.get("env") or env
            if row_env != env:
                result["skipped_foreign"] += 1
                continue
            instance = row.get("instance")
            if not instance:
                continue
            group = groups.setdefault(
                (row_env, instance),
                {"container": "", "statuses": [], "running": False},
            )
            if not group["container"]:
                group["container"] = row.get("container") or ""
            status = row.get("status") or ""
            group["statuses"].append(status)
            if row.get("running") or status == "running":
                group["running"] = True

        for (row_env, instance_id), group in groups.items():
            status = group["statuses"][0] if group["statuses"] else ""
            tracked = self._entries.get((row_env, instance_id))
            if self._is_claimed_marker(row_env, instance_id):
                # Persistenter Claim (#969 B1): der Container gehoert einem
                # laufenden Spiel (ggf. aus einer VORHERIGEN Prozess-Generation)
                # -> nie stoppen/evicten.
                result["kept"] += 1
                continue
            if tracked is None or tracked.state == ParkedState.STOPPED:
                # Orphan: nicht getrackt (Restart-Leak) bzw. getrackt STOPPED
                # mit noch vorhandenem Container.
                self._remove_orphan(row_env, instance_id, group, status, result)
            elif not group["running"]:
                # Created-Zombie/Exited/Dead, waehrend der Eintrag PARKED/
                # CLAIMED/WARMING behauptet -> stale Eintrag evicten + weg.
                self._evict(row_env, instance_id)
                result["evicted"].append(instance_id)
                self._remove_orphan(row_env, instance_id, group, status, result)
            else:
                result["kept"] += 1

        # Reverse-Luecke: getrackter Eintrag ohne Container (z. B. Crash) ->
        # evict, KEIN stop (nichts da). Nur die eigene env. Eintraege mit
        # Claim-Marker bleiben (Claim-Container kann transient fehlen).
        discovered = set(groups.keys())
        for key in list(self._entries.keys()):
            if key[0] != env or key in discovered:
                continue
            if self._is_claimed_marker(key[0], key[1]):
                continue
            self._evict(key[0], key[1])
            result["evicted"].append(key[1])

        if result["errors"]:
            LOG.warning("reconcile (%s): %s", env, "; ".join(result["errors"]))
        else:
            LOG.info(
                "reconcile (%s): removed=%d evicted=%d kept=%d skipped_foreign=%d",
                env,
                len(result["removed"]),
                len(result["evicted"]),
                result["kept"],
                result["skipped_foreign"],
            )
        return result
