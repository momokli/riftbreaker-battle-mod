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

import json
import logging
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from identity import canonicalize
from queue_core import (
    STATE_PROVISIONING,
    STATE_READY,
    Match,
    QueueCore,
    QueueError,
    RESULTS,
    derive_result,
)

LOG = logging.getLogger("queue-flow")


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
    """HTTP-Client auf den Referee (``POST /lobby`` + ``POST /ready``)."""

    def __init__(self, base_url: str, token: str = "", timeout: float = 5.0,
                 opener: Optional[Callable[..., Any]] = None) -> None:
        self.base_url = base_url
        self.token = token
        self.timeout = timeout
        self._opener = opener

    def lobby(self, player: str, world: str,
              match_id: Optional[int] = None) -> Dict[str, Any]:
        """Spieler im Referee registrieren (``POST /lobby {player, world}``, Bearer).

        ``match_id`` (Issue #1028) wird additiv mitgeschickt, damit der Referee
        sie in ``GET /state`` als ``teams.<W>.match_id`` zurueckgibt und die
        Queue ihr Ergebnis eindeutig zuordnen kann.
        """
        payload: Dict[str, Any] = {"player": player, "world": world}
        if match_id is not None:
            payload["match_id"] = int(match_id)
        return _http_json(self.base_url, "POST", "/lobby",
                          payload, self.timeout, self.token, self._opener)

    def ready(self, world: str) -> Dict[str, Any]:
        """Welt beim Referee bereit melden (``POST /ready {world}``, Bearer).

        Der zweite Ready (A und B) loest mit ``TOURNAMENT_AUTO_GO=true`` den
        GO-Broadcast an beide Bridges aus; der Referee armiert GO nur bei
        ``BothReady``. Idempotent (``AlreadyReady``).
        """
        return _http_json(self.base_url, "POST", "/ready",
                          {"world": world}, self.timeout,
                          self.token, self._opener)

    def state(self) -> Optional[Dict[str, Any]]:
        """Autoritativen Referee-Zustand lesen (``GET /state``, auth-frei, #1028).

        Lesen ist unkritisch: Fehler/Timeout liefern ``None`` statt zu werfen,
        damit ein Reconciler-Tick bei einem Referee-Ausfall keinen Zustand
        verliert und keinen Abbruch ausloest.
        """
        try:
            return _http_json(self.base_url, "GET", "/state", None,
                              self.timeout, "", self._opener)
        except QueueError:
            return None


class QueueCoordinator(object):
    """Matchmaking + kalte Provisionierung + Referee-Lobby + Match-Record.

    ``core`` — :class:`queue_core.QueueCore` (rein).
    ``provisioner`` — Objekt mit ``start(env, mode, instance_id, world)`` und
    ``stop(instance_id, env)`` (Produktion: :class:`ProvisionerClient`; Tests:
    Fake).
    ``referee`` — Objekt mit ``lobby(player, world)`` und ``ready(world)``.
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
        # #998-Verifier: der Dienst laeuft hinter einem ``ThreadingHTTPServer``;
        # zwei gleichzeitige Join/Pair/Leave/Finish/Status koennen sonst doppelt
        # paaren oder den Kernzustand korrumpieren. Reentrant, weil einzelne
        # Operationen intern bereits lesen + schreiben.
        self._lock = threading.RLock()
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
        with self._lock:
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
        with self._lock:
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
            # Referee-Lobby: je Spieler genau ein /lobby mit seiner Welt
            # (+ Queue-match_id, #1028: wird in /state echoisiert).
            for assignment in match.assignments():
                self.referee.lobby(assignment.identitaet, assignment.world,
                                   match.match_id)
            # Ready-Egress (Issue #1025): NACH beiden /lobby-Aufrufen (sonst
            # Referee-404 not_found), dann je DISTINCT Welt genau EIN `ready`.
            # Der zweite Ready loest beim Referee (AUTO_GO) den gemeinsamen
            # GO-Broadcast an beide Bridges aus. Nur bei vollstaendiger Paarung
            # erreichbar (ein erster `join` = queued -> kein Ready). Ready ist
            # idempotent (`AlreadyReady`); scheitert ein Ready, greift der
            # bestehende Rollback (beide Instanzen stop + mark_failed); ein
            # Retry braucht ein `POST /rematch` (Operator/Cockpit) — kein
            # stiller Doppel-Start.
            for world in sorted({a.world for a in match.assignments()}):
                self.referee.ready(world)
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
        nicht erneut. Das Ergebnis wird VOR dem Cleanup persistiert: scheitert
        ``provisioner.stop`` (eine Instanz), bleibt ``result`` gesetzt, der
        Match wird NICHT als „cleaned" markiert und der naechste Aufruf
        wiederholt nur den Stop (Fail-safe, #1028 US3).
        """
        with self._lock:
            match = self.core.get_match(match_id)
            if match is None:
                raise QueueError("unknown_match", "Match %s unbekannt" % match_id, 409)
            if result is not None and result not in RESULTS:
                raise QueueError("bad_result", "result muss einer von %s sein"
                                 % (", ".join(RESULTS),), 400)
            self.core.finish(match_id, result)
            # Ergebnis (und ggf. state=finished) sichern, BEVOR gestoppt wird.
            self._persist()
            if match.match_id not in self._cleaned:
                self._cleanup(match)
                self._cleaned.add(match.match_id)
                self._persist()
            return match.to_dict()

    def _cleanup(self, match: Match) -> None:
        """Beide kalt gestarteten Instanzen stoppen (fail-fast).

        Scheitert ein Stop, sofort ``cleanup_failed`` werfen: der Match wird
        nicht als „cleaned" markiert und der naechste Aufruf wiederholt den
        Stop. ``provisioner.stop`` muss idempotent sein.
        """
        side = match.side
        for assignment in match.assignments():
            iid = side.get(assignment.identitaet, {}).get("instance")
            if not iid:
                continue
            try:
                self.provisioner.stop(iid, self.env)
            except Exception as exc:  # noqa: BLE001 - Fail-safe: Retry beim naechsten Tick
                raise QueueError("cleanup_failed", "%s: %s" % (iid, exc), 503)

    # -- Reconciliation (Pull, #1028 US2/US3) ------------------------------
    @staticmethod
    def _referee_match_ids(state: Dict[str, Any]) -> set:
        """Explizite Queue-``match_id``s aus ``teams.{A,B}.match_id`` (#1028)."""
        ids = set()
        teams = state.get("teams")
        if isinstance(teams, dict):
            for world in ("A", "B"):
                team = teams.get(world)
                if isinstance(team, dict):
                    mid = team.get("match_id")
                    if isinstance(mid, int):
                        ids.add(mid)
        return ids

    @staticmethod
    def _referee_player_pair(state: Dict[str, Any]) -> Optional[set]:
        """Spielernamen beider Welten (Fallback-Zuordnung, wenn ``match_id`` fehlt)."""
        teams = state.get("teams")
        if not isinstance(teams, dict):
            return None
        names = []
        for world in ("A", "B"):
            team = teams.get(world)
            player = team.get("player") if isinstance(team, dict) else None
            names.append(player if isinstance(player, str) and player else None)
        if len(names) == 2 and all(names):
            return set(names)
        return None

    def reconcile(self) -> Dict[str, Any]:
        """Aktive Matches gegen den autoritativen Referee-Zustand abgleichen (#1028).

        Liest (auth-frei) ``GET /state``; bei ``phase=finished`` wird das
        Ergebnis abgeleitet und ``finish`` (idempotent) ausgeloest. Ist der
        Referee nicht erreichbar, passiert nichts (nur Log, kein
        Zustandsverlust). Scheitert das Cleanup, bleibt das Ergebnis gesetzt und
        der naechste Tick wiederholt nur den Stop (US3). Match ohne bekannte
        Zuordnung wird uebersprungen + geloggt (kein Tick-Abbruch).
        """
        with self._lock:
            matches = self.core.matches_snapshot()
            pending_finish = [
                m for m in matches if m.state in (STATE_PROVISIONING, STATE_READY)
            ]
            # Abgeschlossen, aber noch nicht „cleaned": nur der Stop wird
            # wiederholt (Ergebnis bleibt gesetzt, US3). Braucht keinen Referee.
            pending_cleanup = [
                m for m in matches
                if m.state not in (STATE_PROVISIONING, STATE_READY)
                and m.match_id not in self._cleaned
            ]
            if not pending_finish and not pending_cleanup:
                return {"reconciled": 0, "finished": []}
            finished: List[int] = []
            for match in pending_cleanup:
                try:
                    self.finish(match.match_id)  # Ergebnis bleibt; nur Stop retry
                except QueueError as exc:
                    LOG.warning(
                        "reconcile: Cleanup-Retry(%s) fehlgeschlagen (%s) — "
                        "naechster Tick", match.match_id, exc,
                    )
                    continue
                finished.append(match.match_id)
            if not pending_finish:
                return {"reconciled": len(pending_cleanup), "finished": finished}
            try:
                state = self.referee.state()
            except Exception as exc:  # noqa: BLE001 - nicht-fatal
                LOG.warning("reconcile: Referee-Zustand nicht lesbar (%s)", exc)
                return {"reconciled": len(pending_finish), "finished": finished,
                        "error": "referee_unavailable"}
            if not isinstance(state, dict):
                LOG.warning("reconcile: Referee-Zustand unbrauchbar — Tick uebersprungen")
                return {"reconciled": len(pending_finish), "finished": finished,
                        "error": "referee_unavailable"}
            phase = state.get("phase")
            winner = state.get("winner")
            match_ids = self._referee_match_ids(state)
            player_pair = self._referee_player_pair(state)
            for match in pending_finish:
                match_id = match.match_id
                mapped = match_id in match_ids
                if not mapped and player_pair is not None and \
                        set(match.participants()) == player_pair:
                    mapped = True
                    LOG.info(
                        "reconcile: Match %s ueber Spielernamen zugeordnet "
                        "(kein match_id-Echo)", match_id,
                    )
                if not mapped:
                    LOG.warning(
                        "reconcile: Match %s keiner Referee-Welt zugeordnet "
                        "— uebersprungen", match_id,
                    )
                    continue
                result = derive_result(phase, winner)
                if result is None:
                    continue  # noch nicht finished
                try:
                    self.finish(match_id, result=result)
                except QueueError as exc:
                    LOG.warning(
                        "reconcile: finish(%s) fehlgeschlagen (%s) — Retry im "
                        "naechsten Tick", match_id, exc,
                    )
                    continue
                finished.append(match_id)
            return {"reconciled": len(pending_finish), "finished": finished}

    # -- Snapshot ----------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        with self._lock:
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
