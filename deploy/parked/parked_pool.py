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
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

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

    def __init__(
        self,
        provisioner: Any,
        bridge_factory: Optional[Callable[[str], Any]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provisioner = provisioner
        self.bridge_factory = bridge_factory or (lambda url: BridgeClient(url))
        self.clock = clock
        self.sleep = sleep
        self._entries: Dict[Tuple[str, str], ParkedEntry] = {}
        self._ids = itertools.count(1)

    # -- intern ------------------------------------------------------------
    def _default_env(self) -> str:
        cfg = getattr(self.provisioner, "cfg", None)
        return getattr(cfg, "env", None) or "test"

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
    def warm_up(self, env: Optional[str] = None, instance_id: Optional[str] = None) -> ParkedEntry:
        """Instanz starten und parken. Idempotent: ist sie schon ``PARKED``,
        passiert **kein** zweiter Start."""
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
                            state=ParkedState.WARMING)
        self._entries[key] = entry
        try:
            result = self.provisioner.start(env=env, instance_id=instance_id)
        except Exception as exc:
            entry.state = ParkedState.STOPPED
            raise ParkedError("warm_up: start von %s fehlgeschlagen: %s" % (instance_id, exc))

        entry.container = result.get("container") or ""
        entry.bridge_url = self._bridge_url(result)

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
        return entry

    def claim(self, env: Optional[str] = None, instance_id: Optional[str] = None) -> Dict[str, Any]:
        """Geparkte Instanz an ein Spiel uebergeben (``resume_game``) und die
        Handover-Dauer messen."""
        entry = self._require(env, instance_id, ParkedState.PARKED, "claim")
        bridge = self._bridge(entry.bridge_url)
        if not bridge.health_ok():
            raise ParkedError("claim: Bridge %s nicht healthy" % entry.bridge_url)
        start = self.clock()
        try:
            bridge.resume_game()
        except Exception as exc:
            raise ParkedError("claim: resume_game von %s fehlgeschlagen: %s" % (entry.instance_id, exc))
        handover = self.clock() - start
        entry.state = ParkedState.CLAIMED
        entry.claimed_at = self.clock()
        entry.parked_since = None
        return {
            "instance": entry.instance_id,
            "env": entry.env,
            "bridge_url": entry.bridge_url,
            "state": entry.state.value,
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
            stopped.append(entry)
        if errors:
            raise ParkedError("; ".join(errors))
        return stopped

    def status(self) -> List[Dict[str, Any]]:
        """Snapshot aller Pool-Eintraege (als einfache dicts)."""
        now = self.clock()
        return [entry.to_dict(now) for entry in self._entries.values()]
