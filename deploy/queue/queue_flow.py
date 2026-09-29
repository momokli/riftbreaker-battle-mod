#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-queue — kalte VS-Doppel-Provisionierung (Issue #998, US2/US5).

Der :class:`QueueCoordinator` verkettet den reinen Matchmaking-Kern
(:mod:`queue_core`) mit den Nachbarn:

    Paarung -> ZWEI frische Kalt-Instanzen (Welt A + Welt B)
            -> Referee-Lobby (`POST /lobby {player, world}` je Spieler)
            -> Match-Record (Teilnehmer/Welten/Instanzen/Endpoints)
    `finish` -> Ergebnis nachtragen + kaltes Cleanup (beide Instanzen stoppen)

**Kalt heisst:** es gibt KEINEN Warm-Pool — jede Paarung provisioniert zwei
neue Instanzen und stoppt sie nach dem Match wieder (kein Parked-VS-Reuse).
Kein MMR/Ranking: der Match-Record haelt nur Teilnehmer + Ergebnis.

Fehler brechen laut ab (kein halber Zustand): scheitert die zweite
Provisionierung oder die Referee-Lobby, werden die bereits gestarteten
Instanzen zurueckgerollt (kalt `stop`) und das Match als ``failed`` markiert.

Nur Standardbibliothek (stdlib), kein venv/pip. Alle Clients sind injizierbar
(Tests: Fakes, kein Netz).
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from identity import canonicalize
from queue_core import (
    Match,
    QueueCore,
    QueueError,
    STATE_FAILED,
    STATE_FINISHED,
    STATE_READY,
    RESULTS,
)


def instance_id_for(match_id: int, world: str) -> str:
    """Deterministische Instanz-ID einer Match-Welt (provisioner-konform).

    ``queue-<match_id>-<world>`` (lowercase) erfuellt ``INSTANCE_ID_RE``
    (``^[A-Za-z0-9_.-]{1,40}$``); zwei Welten -> zwei verschiedene IDs.
    """
    return "queue-%d-%s" % (int(match_id), str(world).lower())


def _http_json(
    base_url: str,
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 5.0,
    token: str = "",
    opener: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Ein JSON-Request auf einen lokalen Dienst; Fehler -> :class:`QueueError`.

    Mapping: Verbindungsfehler -> ``503 unreachable``; HTTP-Fehler -> ``status``
    + ``reason`` aus dem Antwort-Body (Fallback ``http_error``).
    """
    url = "%s%s" % (base_url.rstrip("/"), path)
    data: Optional[bytes] = None
    headers: Dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = opener or urllib.request.urlopen
    try:
        with opener(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if hasattr(exc, "read") else ""
        reason = "http_error"
        detail = "HTTP %s" % exc.code
        try:
            parsed = json.loads(raw) if raw.strip() else {}
            if isinstance(parsed, dict):
                reason = parsed.get("reason") or reason
                detail = parsed.get("detail") or detail
        except ValueError:
            pass
        raise QueueError(reason, detail, int(exc.code))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise QueueError("unreachable", "%s %s: %s" % (method, path, exc), 503)
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        raise QueueError("bad_response", "%s %s: kein JSON (%s)" % (method, path, exc), 502)
    if status >= 400:
        reason = parsed.get("reason", "http_error") if isinstance(parsed, dict) else "http_error"
        raise QueueError(reason, "%s %s -> HTTP %s" % (method, path, status), status)
    return parsed if isinstance(parsed, dict) else {"value": parsed}


class ProvisionerClient(object):
    """HTTP-Client auf den Provisioner (``POST /start`` + ``POST /stop``).

    Erfuellt die Duck-Type-Schnittstelle, die der Koordinator erwartet:
    ``start(env, mode, instance_id, world)`` und ``stop(instance_id, env)``.
    Die Provisionierung ist KALT: jeder Start erzeugt eine frische Instanz.
    """

    def __init__(self, base_url: str, token: str = "", timeout: float = 30.0,
                 opener: Optional[Callable[..., Any]] = None) -> None:
        self.base_url = base_url
        self.token = token
        self.timeout = timeout
        self._opener = opener

    def start(self, env: Optional[str], mode: str, instance_id: str,
              world: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"env": env, "mode": mode, "instance_id": instance_id}
        if world is not None:
            payload["world"] = world
        return _http_json(self.base_url, "POST", "/start", payload, self.timeout,
                          self.token, self._opener)

    def stop(self, instance_id: str, env: Optional[str] = None) -> Dict[str, Any]:
        return _http_json(self.base_url, "POST", "/stop",
                          {"env": env, "instance_id": instance_id}, self.timeout,
                          self.token, self._opener)


class RefereeClient(object):
    """HTTP-Client auf den Referee (``POST /lobby {player, world}``)."""

    def __init__(self, base_url: str, token: str = "", timeout: float = 5.0,
                 opener: Optional[Callable[..., Any]] = None) -> None:
        self.base_url = base_url
        self.token = token
        self.timeout = timeout
        self._opener = opener

    def lobby(self, player: str, world: str) -> Dict[str, Any]:
        return _http_json(self.base_url, "POST", "/lobby",
                          {"player": player, "world": world}, self.timeout,
                          self.token, self._opener)


class QueueCoordinator(object):
    """Matchmaking + kalte Provisionierung + Referee-Lobby + Match-Record.

    ``core`` — :class:`queue_core.QueueCore` (rein).
    ``provisioner`` — Objekt mit ``start(env, mode, instance_id, world)`` und
    ``stop(instance_id, env)`` (Produktion: :class:`ProvisionerClient`; Tests:
    Fake).
    ``referee`` — Objekt mit ``lobby(player, world)``.
    ``clock`` — injizierbar (Tests: Fake).
    ``state_dir`` — optionales Verzeichnis fuer den Match-Record (JSON, atomar).
    """

    def __init__(
        self,
        core: QueueCore,
        provisioner: Any,
        referee: Any,
        clock: Callable[[], float] = time.monotonic,
        env: Optional[str] = None,
        state_dir: Optional[str] = None,
        provision_mode: str = "solo_self",
    ) -> None:
        self.core = core
        self.provisioner = provisioner
        self.referee = referee
        self.clock = clock
        self.env = env
        self.state_dir = state_dir
        self.provision_mode = provision_mode
        self._cleaned: set = set()
        self._load_state()

    # -- Persistenz (US5) --------------------------------------------------
    def _state_path(self) -> Optional[str]:
        if not self.state_dir:
            return None
        return os.path.join(self.state_dir, "queue-state.json")

    def _load_state(self) -> None:
        path = self._state_path()
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        core_state = payload.get("core")
        if isinstance(core_state, dict):
            self.core.load_state(core_state)
        cleaned = payload.get("cleaned")
        if isinstance(cleaned, list):
            self._cleaned = set(int(x) for x in cleaned)

    def _persist(self) -> None:
        path = self._state_path()
        if not path:
            return
        os.makedirs(self.state_dir, exist_ok=True)
        payload = {"core": self.core.to_state(), "cleaned": sorted(self._cleaned)}
        fd, tmp = tempfile.mkstemp(dir=self.state_dir, prefix=".queue-state-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # -- Join/Leave --------------------------------------------------------
    def join(self, identitaet: str, mode: str = "vs",
             team_size: Optional[int] = None) -> Dict[str, Any]:
        """Spieler einreihen; bei vollstaendiger Paarung kalt provisionieren.

        Rueckgabe: ``{"status":"queued","position":n}`` bzw.
        ``{"status":"matched","match":{…}}`` (nach erfolgreicher Provisionierung).
        """
        identity = canonicalize(identitaet) or identitaet
        self.core.enqueue(identity, mode=mode, team_size=team_size)
        self._persist()
        match = self.core.pair()
        if match is None:
            return {
                "status": "queued",
                "identitaet": identity,
                "position": self.core.position(identity),
                "match": None,
            }
        self._provision(match)
        return {
            "status": "matched",
            "identitaet": identity,
            "position": None,
            "match": match.to_dict(),
        }

    def leave(self, identitaet: str) -> Dict[str, Any]:
        """Wartenden Spieler entfernen (bereits gepaart -> lauter Fehler)."""
        identity = canonicalize(identitaet) or identitaet
        entry = self.core.leave(identity)
        self._persist()
        return {"identitaet": entry.identitaet}

    # -- Provisionierung (kalt, A/B) ---------------------------------------
    def _provision(self, match: Match) -> None:
        started: List[str] = []
        try:
            for team in match.teams:
                iid = instance_id_for(match.match_id, team.world)
                result = self.provisioner.start(
                    self.env, self.provision_mode, iid, team.world
                )
                endpoint = self._endpoint_from(result)
                started.append(iid)
                for player in team.players:
                    self.core.set_assignment(match, player, instance=iid, endpoint=endpoint)
            # Referee-Lobby: je Spieler genau ein /lobby mit seiner Welt.
            for assignment in match.assignments():
                side = getattr(match, "_assignment_side", {}).get(assignment.identitaet, {})
                self.referee.lobby(assignment.identitaet, assignment.world)
            self.core.mark_ready(match)
            self._persist()
        except Exception as exc:  # noqa: BLE001 - Rollback + lauter Fehler
            for iid in started:
                try:
                    self.provisioner.stop(iid, self.env)
                except Exception:  # noqa: BLE001 - best-effort Rollback
                    pass
            self.core.mark_failed(match.match_id, detail=str(exc))
            self._persist()
            if isinstance(exc, QueueError):
                raise QueueError("provision_failed", str(exc), exc.status)
            raise QueueError("provision_failed", str(exc), 503)

    @staticmethod
    def _endpoint_from(result: Any) -> Optional[str]:
        if not isinstance(result, dict):
            return None
        ports = result.get("ports")
        if isinstance(ports, dict):
            gns = ports.get("gns")
            if isinstance(gns, str) and gns:
                return gns
        for key in ("gns_endpoint", "endpoint"):
            value = result.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    # -- Finish / Cleanup --------------------------------------------------
    def finish(self, match_id: int, result: Optional[str] = None) -> Dict[str, Any]:
        """Ergebnis nachtragen + kaltes Cleanup. Idempotent.

        ``result`` optional aus ``winnerA``/``winnerB``/``draw``. Ohne ``result``
        wird nur gestoppt (Ergebnis bleibt ``null``). Ein zweiter Aufruf stoppt
        nicht erneut.
        """
        match = self.core.get_match(match_id)
        if match is None:
            raise QueueError("unknown_match", "Match %s unbekannt" % match_id, 409)
        if result is not None and result not in RESULTS:
            raise QueueError("bad_result", "result muss einer von %s sein"
                             % (", ".join(RESULTS),), 400)
        self.core.finish(match_id, result)
        if match.match_id not in self._cleaned:
            self._cleanup(match)
            self._cleaned.add(match.match_id)
        self._persist()
        return match.to_dict()

    def _cleanup(self, match: Match) -> None:
        side = getattr(match, "_assignment_side", {}) or {}
        errors = []
        for assignment in match.assignments():
            iid = side.get(assignment.identitaet, {}).get("instance")
            if not iid:
                continue
            try:
                self.provisioner.stop(iid, self.env)
            except Exception as exc:  # noqa: BLE001
                errors.append("%s: %s" % (iid, exc))
        if errors:
            raise QueueError("cleanup_failed", "; ".join(errors), 503)

    # -- Snapshot ----------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        return self.core.status()


def build_coordinator(queue_config: Any, clock: Callable[[], float] = time.monotonic) -> QueueCoordinator:
    """Koordinator aus einer ``QueueServiceConfig`` bauen (Produktionsverdrahtung)."""
    core = QueueCore(
        team_size=getattr(queue_config, "team_size", 1),
        allow_teams=getattr(queue_config, "allow_teams", False),
        clock=clock,
    )
    provisioner = ProvisionerClient(
        queue_config.provisioner_url,
        token=getattr(queue_config, "provisioner_token", ""),
        timeout=getattr(queue_config, "provisioner_timeout", 30.0),
    )
    referee = RefereeClient(
        queue_config.referee_url,
        token=getattr(queue_config, "referee_token", ""),
        timeout=getattr(queue_config, "timeout", 5.0),
    )
    return QueueCoordinator(
        core,
        provisioner,
        referee,
        clock=clock,
        env=getattr(queue_config, "env", None),
        state_dir=getattr(queue_config, "state_dir", None),
    )