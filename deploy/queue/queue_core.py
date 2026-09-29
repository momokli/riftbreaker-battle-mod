#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-queue — Matchmaking-Kern (Issue #998, US1).

Reine, I/O-freie Matchmaking-Logik fuer den Casual-Queue-Dienst: Eintraege
(FIFO), generisches Team-/Match-Modell (N Slots je Team, ``teams_per_match=2``)
und die Welt-Zuordnung (Team 0 -> Welt A, Team 1 -> Welt B).

**Aktiv ist nur 1v1** (``team_size == 1``): ``team_size > 1`` wird fail-loud
abgelehnt (``unsupported_team_size``), solange ``allow_teams`` aus ist
(Flag ``QUEUE_ALLOW_TEAMS``, Default off). Das Datenmodell ist trotzdem
generisch N/Team gebaut, damit spaetere Modi es zulassen.

Kein Ranking/MMR (Issue-Abgrenzung): Paarung ist rein FIFO. Der Match-Record
haelt Teilnehmer + Welten + (spaeter nachgetragen) Ergebnis, mehr nicht.

Nur Standardbibliothek (stdlib), kein venv/pip. Keine HTTP-/Docker-Beruehrung —
das ist :mod:`queue_flow` / :mod:`queue_service`.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any, Dict, List, Optional

# Welten eines VS-Matches (Team-Index 0/1 -> A/B). Additiv erweiterbar.
WORLD_A = "A"
WORLD_B = "B"
WORLDS = (WORLD_A, WORLD_B)

# Match-Zustaende (US5): provisioning -> ready -> finished; Fehler -> failed.
STATE_PROVISIONING = "provisioning"
STATE_READY = "ready"
STATE_FAILED = "failed"
STATE_FINISHED = "finished"

# Erlaubte Ergebnis-Werte (kein MMR, nur Sieger/Draw).
RESULTS = ("winnerA", "winnerB", "draw")


class QueueError(Exception):
    """Queue-Operation nicht moeglich — laut abbrechen (kein halber Zustand).

    Traegt eine HTTP-taugliche ``status`` (Default 409) und einen stabilen
    ``reason`` fuer das einheitliche Fehlerformat der Queue-API.
    """

    def __init__(self, reason: str, detail: str = "", status: int = 409) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail
        self.status = int(status)


@dataclasses.dataclass
class QueueEntry:
    """Ein wartender Spieler in der Queue (FIFO-Slot)."""

    identitaet: str
    mode: str
    team_size: int
    seq: int
    enqueued_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identitaet": self.identitaet,
            "mode": self.mode,
            "team_size": self.team_size,
            "seq": self.seq,
            "waiting_seconds": None,
        }


@dataclasses.dataclass
class Assignment:
    """Zuordnung eines Spielers zu Welt + (nach Provisionierung) Instanz/Endpoint."""

    identitaet: str
    world: str
    instance: Optional[str] = None
    endpoint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identitaet": self.identitaet,
            "world": self.world,
            "instance": self.instance,
            "endpoint": self.endpoint,
        }


@dataclasses.dataclass
class Team:
    """Ein Team eines Matches (Index + Welt + seine Spieler)."""

    index: int
    world: str
    team_size: int
    players: List[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "world": self.world,
            "team_size": self.team_size,
            "players": list(self.players),
        }


@dataclasses.dataclass
class Match:
    """Ein gepaartes Match (Record, kein MMR).

    ``assignments`` ist die flache Sicht (je Spieler Welt/Instanz/Endpoint) fuer
    den Relay-Pin-Plan; ``teams`` die generische Team-Sicht.
    """

    match_id: int
    mode: str
    team_size: int
    created_at: float
    teams: List[Team] = dataclasses.field(default_factory=list)
    state: str = STATE_PROVISIONING
    result: Optional[str] = None
    detail: Optional[str] = None

    # -- Sichten -----------------------------------------------------------
    def assignments(self) -> List[Assignment]:
        out: List[Assignment] = []
        for team in self.teams:
            for player in team.players:
                out.append(Assignment(identitaet=player, world=team.world))
        return out

    def world_of(self, identitaet: str) -> Optional[str]:
        for team in self.teams:
            if identitaet in team.players:
                return team.world
        return None

    def participants(self) -> List[str]:
        out: List[str] = []
        for team in self.teams:
            out.extend(team.players)
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "match_id": self.match_id,
            "mode": self.mode,
            "team_size": self.team_size,
            "created_at": self.created_at,
            "state": self.state,
            "result": self.result,
            "detail": self.detail,
            "teams": [t.to_dict() for t in self.teams],
            "participants": [
                {
                    "identitaet": a.identitaet,
                    "world": a.world,
                    "instance": a.instance,
                    "endpoint": a.endpoint,
                }
                for a in self.assignments()
            ],
        }


class QueueCore(object):
    """FIFO-Matchmaking mit Welt-Zuordnung A/B (rein, ohne I/O).

    ``team_size`` — Slots je Team (aktiv 1 = 1v1).
    ``teams_per_match`` — Teams je Match (aktiv 2 = A/B).
    ``allow_teams`` — erlaubt ``team_size > 1`` (Default False; Flag
    ``QUEUE_ALLOW_TEAMS``).
    ``clock`` — injizierbar (Tests: Fake).
    """

    def __init__(
        self,
        team_size: int = 1,
        teams_per_match: int = 2,
        allow_teams: bool = False,
        clock=None,
    ) -> None:
        if team_size < 1:
            raise QueueError("bad_team_size", "team_size muss >= 1 sein", 400)
        if teams_per_match < 2:
            raise QueueError("bad_teams_per_match", "teams_per_match muss >= 2 sein", 400)
        self.team_size = team_size
        self.teams_per_match = teams_per_match
        self.allow_teams = allow_teams
        self.clock = clock or time.monotonic
        self._queue: List[QueueEntry] = []
        self._by_identity: Dict[str, QueueEntry] = {}
        self._matches: Dict[int, Match] = {}
        self._identity_match: Dict[str, int] = {}
        self._next_match_id = 1
        self._seq = 0

    # -- Queue -------------------------------------------------------------
    def enqueue(self, identitaet: str, mode: str = "vs",
                team_size: Optional[int] = None) -> QueueEntry:
        """Spieler einreihen (idempotent fuer denselben Spieler).

        ``team_size`` (Default = Core-Default) > 1 wird fail-loud abgelehnt,
        solange ``allow_teams`` aus ist. Eine bereits gepaarte Identitaet kann
        nicht erneut einreihen (``already_matched``); ein bereits wartender
        Eintrag wird unveraendert zurueckgegeben (kein zweiter Slot).
        """
        if not isinstance(identitaet, str) or not identitaet:
            raise QueueError("bad_request", "identitaet fehlt", 400)
        size = self.team_size if team_size is None else team_size
        if size != 1 and not self.allow_teams:
            raise QueueError(
                "unsupported_team_size",
                "team_size=%r wird nicht unterstuetzt (aktiv nur 1v1)" % (size,),
                400,
            )
        if size < 1:
            raise QueueError("bad_team_size", "team_size muss >= 1 sein", 400)

        if identitaet in self._identity_match:
            raise QueueError(
                "already_matched",
                "Identitaet %s ist bereits in Match %d"
                % (identitaet, self._identity_match[identitaet]),
                409,
            )
        existing = self._by_identity.get(identitaet)
        if existing is not None:
            return existing  # idempotent: kein zweiter Slot

        entry = QueueEntry(
            identitaet=identitaet,
            mode=mode or "vs",
            team_size=size,
            seq=self._seq,
            enqueued_at=self.clock(),
        )
        self._seq += 1
        self._queue.append(entry)
        self._by_identity[identitaet] = entry
        return entry

    def leave(self, identitaet: str) -> QueueEntry:
        """Wartenden Eintrag entfernen. Unbekannt -> ``not_queued``; bereits
        gepaart -> ``already_matched`` (eine Paarung ist bindend)."""
        if identitaet in self._identity_match:
            raise QueueError(
                "already_matched",
                "Identitaet %s ist bereits in Match %d"
                % (identitaet, self._identity_match[identitaet]),
                409,
            )
        entry = self._by_identity.get(identitaet)
        if entry is None:
            raise QueueError("not_queued", "Identitaet %s ist nicht in der Queue" % identitaet, 409)
        self._queue.remove(entry)
        del self._by_identity[identitaet]
        return entry

    def position(self, identitaet: str) -> Optional[int]:
        """1-basierte Warteposition oder ``None`` (nicht in der Queue)."""
        entry = self._by_identity.get(identitaet)
        if entry is None:
            return None
        return self._queue.index(entry) + 1

    # -- Pairing -----------------------------------------------------------
    def pair(self) -> Optional[Match]:
        """Genau ein Match bilden, wenn genug Eintraege da sind (FIFO).

        Braucht ``team_size * teams_per_match`` Eintraege; sonst ``None``
        (pending). Team 0 (Welt A) bekommt die zuerst Eingereihten.
        """
        needed = self.team_size * self.teams_per_match
        if len(self._queue) < needed:
            return None
        taken = self._queue[:needed]
        teams: List[Team] = []
        for index in range(self.teams_per_match):
            world = WORLDS[index] if index < len(WORLDS) else "W%d" % index
            chunk = taken[index * self.team_size:(index + 1) * self.team_size]
            teams.append(
                Team(
                    index=index,
                    world=world,
                    team_size=self.team_size,
                    players=[e.identitaet for e in chunk],
                )
            )
        match = Match(
            match_id=self._next_match_id,
            mode=taken[0].mode,
            team_size=self.team_size,
            created_at=self.clock(),
            teams=teams,
        )
        self._next_match_id += 1
        for entry in taken:
            self._queue.remove(entry)
            del self._by_identity[entry.identitaet]
            self._identity_match[entry.identitaet] = match.match_id
        self._matches[match.match_id] = match
        return match

    # -- Lookup ------------------------------------------------------------
    def get_match(self, match_id: int) -> Optional[Match]:
        return self._matches.get(int(match_id))

    def match_for(self, identitaet: str) -> Optional[Match]:
        match_id = self._identity_match.get(identitaet)
        if match_id is None:
            return None
        return self._matches.get(match_id)

    def entry_for(self, identitaet: str) -> Optional[QueueEntry]:
        return self._by_identity.get(identitaet)

    # -- Match-Mutationen --------------------------------------------------
    def _require_match(self, match_id: int) -> Match:
        match = self._matches.get(int(match_id))
        if match is None:
            raise QueueError("unknown_match", "Match %s unbekannt" % match_id, 409)
        return match

    def set_assignment(self, match: Match, identitaet: str, instance: Optional[str] = None,
                       endpoint: Optional[str] = None) -> None:
        """Instanz/Endpoint eines Teilnehmers am Match-Record nachtragen.

        ``Assignment`` ist eine Sicht auf ``teams``; die Werte werden daher in
        einem seitlichen Dict gehalten, das ``Match.to_dict`` mitliest.
        """
        if identitaet not in match.participants():
            raise QueueError(
                "unknown_participant",
                "%s ist kein Teilnehmer von Match %d" % (identitaet, match.match_id),
                409,
            )
        side = getattr(match, "_assignment_side", None)
        if side is None:
            side = {}
            setattr(match, "_assignment_side", side)
        side[identitaet] = {"instance": instance, "endpoint": endpoint}

    def mark_ready(self, match: Match) -> Match:
        if match.state == STATE_PROVISIONING:
            match.state = STATE_READY
        return match

    def mark_failed(self, match_id: int, detail: str = "") -> Match:
        match = self._require_match(match_id)
        match.state = STATE_FAILED
        match.detail = detail or match.detail
        return match

    def finish(self, match_id: int, result: Optional[str] = None) -> Match:
        """Ergebnis nachtragen (US5, kein MMR). Idempotent.

        ``result`` optional aus ``RESULTS``; ein zweiter Aufruf aendert nichts.
        """
        if result is not None and result not in RESULTS:
            raise QueueError(
                "bad_result",
                "result muss einer von %s sein" % (", ".join(RESULTS),),
                400,
            )
        match = self._require_match(match_id)
        if match.state == STATE_FINISHED:
            return match  # idempotent
        match.state = STATE_FINISHED
        if result is not None:
            match.result = result
        return match

    # -- Snapshots ---------------------------------------------------------
    def queue_snapshot(self) -> List[QueueEntry]:
        return list(self._queue)

    def matches_snapshot(self) -> List[Match]:
        return [self._matches[i] for i in sorted(self._matches)]

    def status(self) -> Dict[str, Any]:
        now = self.clock()
        queue = []
        for entry in self._queue:
            item = entry.to_dict()
            item["waiting_seconds"] = round(now - entry.enqueued_at, 3)
            queue.append(item)
        return {
            "queue": queue,
            "matches": [m.to_dict() for m in self.matches_snapshot()],
            "queued": len(self._queue),
            "matches_count": len(self._matches),
            "team_size": self.team_size,
            "allow_teams": self.allow_teams,
        }

    # -- Persistenz (US5) --------------------------------------------------
    def to_state(self) -> Dict[str, Any]:
        """Serialisierbarer Snapshot (Queue + Matches + Zaehler) fuer die Datei."""
        return {
            "team_size": self.team_size,
            "teams_per_match": self.teams_per_match,
            "allow_teams": self.allow_teams,
            "next_match_id": self._next_match_id,
            "seq": self._seq,
            "queue": [
                {
                    "identitaet": e.identitaet,
                    "mode": e.mode,
                    "team_size": e.team_size,
                    "seq": e.seq,
                    "enqueued_at": e.enqueued_at,
                }
                for e in self._queue
            ],
            "matches": [
                {
                    "match_id": m.match_id,
                    "mode": m.mode,
                    "team_size": m.team_size,
                    "created_at": m.created_at,
                    "state": m.state,
                    "result": m.result,
                    "detail": m.detail,
                    "teams": [t.to_dict() for t in m.teams],
                    "side": getattr(m, "_assignment_side", {}) or {},
                }
                for m in self.matches_snapshot()
            ],
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        """Zustand aus :meth:`to_state` wiederherstellen (ersetzt alles)."""
        if not isinstance(state, dict):
            return
        self._queue = []
        self._by_identity = {}
        self._matches = {}
        self._identity_match = {}
        for raw in state.get("queue", []) or []:
            entry = QueueEntry(
                identitaet=raw["identitaet"],
                mode=raw.get("mode", "vs"),
                team_size=int(raw.get("team_size", self.team_size)),
                seq=int(raw.get("seq", 0)),
                enqueued_at=float(raw.get("enqueued_at", 0.0)),
            )
            self._queue.append(entry)
            self._by_identity[entry.identitaet] = entry
        for raw in state.get("matches", []) or []:
            teams = [
                Team(
                    index=int(t["index"]),
                    world=t["world"],
                    team_size=int(t.get("team_size", self.team_size)),
                    players=list(t.get("players", [])),
                )
                for t in raw.get("teams", [])
            ]
            match = Match(
                match_id=int(raw["match_id"]),
                mode=raw.get("mode", "vs"),
                team_size=int(raw.get("team_size", self.team_size)),
                created_at=float(raw.get("created_at", 0.0)),
                teams=teams,
                state=raw.get("state", STATE_PROVISIONING),
                result=raw.get("result"),
                detail=raw.get("detail"),
            )
            if raw.get("side"):
                setattr(match, "_assignment_side", dict(raw["side"]))
            self._matches[match.match_id] = match
            for player in match.participants():
                self._identity_match[player] = match.match_id
        self._next_match_id = int(state.get("next_match_id", len(self._matches) + 1))
        self._seq = int(state.get("seq", len(self._queue)))


def _assignment_participants(match: Match) -> List[Dict[str, Any]]:
    """Teilnehmer mit seitlichen Instanz/Endpoint-Werten (siehe ``set_assignment``)."""
    side = getattr(match, "_assignment_side", {}) or {}
    out = []
    for a in match.assignments():
        extra = side.get(a.identitaet, {})
        out.append(
            {
                "identitaet": a.identitaet,
                "world": a.world,
                "instance": extra.get("instance"),
                "endpoint": extra.get("endpoint"),
            }
        )
    return out


# ``Match.to_dict`` soll die seitlichen Zuordnungen mitlesen; wir patchen die
# Methode einmal zentral (haelt das Dataclass schlank und testbar).
def _match_to_dict(self: Match) -> Dict[str, Any]:
    return {
        "match_id": self.match_id,
        "mode": self.mode,
        "team_size": self.team_size,
        "created_at": self.created_at,
        "state": self.state,
        "result": self.result,
        "detail": self.detail,
        "teams": [t.to_dict() for t in self.teams],
        "participants": _assignment_participants(self),
    }


Match.to_dict = _match_to_dict  # type: ignore[assignment]