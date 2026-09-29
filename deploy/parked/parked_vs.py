#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-parked — Warm-Pool „Parked VS" (Issue #910).

Was ist ein *geparkter VS-Server*?
----------------------------------

Ein **einziger** Dedicated-Server, der wie in „Parked Solo" (#909) vorab
hochgefahren und *geparkt* wird (Container + Mod-Load + Bridge laufen, die Welt
ist angehalten), aber fuer ein **Match mit zwei Spielern** bereitgehalten wird.
Die Welt wird **erst dann** fortgesetzt (``POST /resume_game``), wenn:

1. **beide** Spieler beigetreten sind (``join`` × ``required_players``), **und**
2. **beide** Spieler ``ready`` gemeldet haben.

Bis dahin bleibt der darunterliegende ``ParkedEntry`` in ``PARKED`` — **kein
Weltfortschritt**. Das ist das *Ready-Gate*: es ist die tragende Invariante von
#910. Nach Matchende wird die Instanz recycelt (``end_game`` + ``round_reset``
+ ``pause_game``) und ist sofort wieder fuer das naechste Match geparkt
(``recycle(keep_warm=True)``) bzw. sauber gestoppt (``keep_warm=False``).

Diese Schicht **komponiert** :class:`parked_pool.ParkedPool` (kein Docker-/
HTTP-Code wird dupliziert): ``warm_up``/``claim``/``recycle`` werden delegiert.
Nur Standardbibliothek (stdlib), kein venv/pip.

Zustandsmaschine::

    (warm_up) --> WAITING_OPPONENT
    WAITING_OPPONENT   --join(<required)----> WAITING_OPPONENT
    WAITING_OPPONENT   --join(required.)----> WAITING_BOTH_READY
    WAITING_BOTH_READY --ready(nicht alle)---> WAITING_BOTH_READY
    WAITING_BOTH_READY --ready(alle)--> [pool.claim()/resume_game] --> CLAIMED
    CLAIMED --recycle(keep_warm=True)--> RECYCLING --> WAITING_OPPONENT
    CLAIMED --recycle(keep_warm=False)---------------> STOPPED

Fehler brechen **laut** ab (:class:`VSError`) statt einen halben Zustand zu
hinterlassen.
"""

from __future__ import annotations

import dataclasses
import enum
import time
from typing import Any, Callable, Dict, List, Optional, Set

from parked_pool import ParkedError, ParkedPool


class VSError(ParkedError):
    """VS-Operation nicht moeglich — laut abbrechen (kein halber Zustand)."""


class VSState(enum.Enum):
    """Lebenszyklus einer geparkten VS-Instanz."""

    WAITING_OPPONENT = "waiting_opponent"        # < required_players beigetreten
    WAITING_BOTH_READY = "waiting_both_ready"    # alle beigetreten, nicht alle ready
    CLAIMED = "claimed"                          # Handover (resume_game) erfolgt
    RECYCLING = "recycling"                      # end_game/round_reset laufen
    STOPPED = "stopped"                          # Container gestoppt


@dataclasses.dataclass
class VSSession:
    """Ein VS-Match-Slot (eine geparkte Instanz + ihre Beitritte/Ready-Flags)."""

    instance_id: str
    env: str
    state: VSState
    players: List[str] = dataclasses.field(default_factory=list)
    ready: Set[str] = dataclasses.field(default_factory=set)
    required_players: int = 2
    handover_seconds: Optional[float] = None
    claimed_at: Optional[float] = None
    rounds: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance": self.instance_id,
            "env": self.env,
            "state": self.state.value,
            "players": list(self.players),
            "ready": sorted(self.ready),
            "required_players": self.required_players,
            "handover_seconds": self.handover_seconds,
            "rounds": self.rounds,
        }


class ParkedVSPool(object):
    """Warm-Pool fuer ein geparktes VS-Match (zwei Spieler, Ready-Gate).

    ``provisioner`` — Objekt mit ``start``/``stop`` (i. d. R.
    ``provisioner.Provisioner``); wird nur gebraucht, wenn ``pool`` nicht
    uebergeben wird.
    ``pool`` — eine :class:`parked_pool.ParkedPool`; default eine neue, die den
    Provisioner umschliesst.
    ``clock`` / ``sleep`` — injizierbar (Tests: Fake, echt: ``time``).
    ``required_players`` — Anzahl der Spieler, die fuer den Handover beitreten
    muessen (Default 2, der Scope von #910; parametrisierbar).
    """

    def __init__(
        self,
        provisioner: Any = None,
        pool: Optional[ParkedPool] = None,
        bridge_factory: Optional[Callable[[str], Any]] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        required_players: int = 2,
    ) -> None:
        if required_players < 1:
            raise VSError("required_players muss >= 1 sein (ist %r)" % (required_players,))
        if pool is None:
            if provisioner is None:
                raise VSError("ParkedVSPool braucht provisioner oder pool")
            pool = ParkedPool(provisioner, bridge_factory, clock=clock, sleep=sleep)
        self.provisioner = provisioner if provisioner is not None else pool.provisioner
        self.pool = pool
        self.clock = clock
        self.sleep = sleep
        self.required_players = required_players
        self._sessions: Dict[Any, VSSession] = {}

    # -- intern ------------------------------------------------------------
    def _default_env(self) -> str:
        cfg = getattr(self.provisioner, "cfg", None)
        return getattr(cfg, "env", None) or "test"

    def _require_session(self, env: Optional[str], instance_id: Optional[str],
                         action: str) -> VSSession:
        """Session aufloesen. Unbekannt/mehrdeutig -> :class:`VSError`."""
        env = env or self._default_env()
        if instance_id is not None:
            session = self._sessions.get((env, instance_id))
            if session is None:
                raise VSError(
                    "%s: unbekannte (nicht warm_up-te) Instanz %s (env=%s)"
                    % (action, instance_id, env)
                )
            return session
        candidates = [s for (e, _i), s in self._sessions.items() if e == env]
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise VSError("%s: keine warm_up-te Instanz in env=%s" % (action, env))
        raise VSError(
            "%s: %d Instanzen in env=%s — instance_id noetig"
            % (action, len(candidates), env)
        )

    # -- API ---------------------------------------------------------------
    def warm_up(self, env: Optional[str] = None, instance_id: Optional[str] = None) -> VSSession:
        """Instanz starten und parken (delegiert an ``ParkedPool.warm_up``).

        Idempotent: wird eine bereits ``WAITING_*``-Instanz erneut angefragt,
        passiert **kein** zweiter Start. Eine schon gespielte (``CLAIMED``/
        ``STOPPED``) Instanz wird laut abgelehnt.
        """
        env = env or self._default_env()
        if instance_id is not None:
            existing = self._sessions.get((env, instance_id))
            if existing is not None:
                if existing.state in (VSState.WAITING_OPPONENT, VSState.WAITING_BOTH_READY):
                    return existing
                raise VSError(
                    "warm_up: Instanz %s ist %s — kein erneutes Parken"
                    % (instance_id, existing.state.value)
                )
        entry = self.pool.warm_up(env=env, instance_id=instance_id)
        session = VSSession(
            instance_id=entry.instance_id,
            env=entry.env,
            state=VSState.WAITING_OPPONENT,
            required_players=self.required_players,
        )
        self._sessions[(entry.env, entry.instance_id)] = session
        return session

    def join(self, player: str, env: Optional[str] = None,
             instance_id: Optional[str] = None) -> VSSession:
        """Spieler beitritt. Bis ``required_players`` beigetreten sind, bleibt der
        Server ``WAITING_OPPONENT`` (kein ``resume_game``)."""
        session = self._require_session(env, instance_id, "join")
        if session.state not in (VSState.WAITING_OPPONENT, VSState.WAITING_BOTH_READY):
            raise VSError(
                "join: Instanz %s ist %s — Beitritt nicht moeglich"
                % (session.instance_id, session.state.value)
            )
        if player in session.players:
            raise VSError(
                "join: Spieler %s ist der Instanz %s bereits beigetreten"
                % (player, session.instance_id)
            )
        if len(session.players) >= session.required_players:
            raise VSError(
                "join: Instanz %s ist voll (%d/%d) — Spieler %s abgelehnt"
                % (session.instance_id, len(session.players), session.required_players, player)
            )
        session.players.append(player)
        if len(session.players) >= session.required_players:
            session.state = VSState.WAITING_BOTH_READY
        return session

    def ready(self, player: str, env: Optional[str] = None,
              instance_id: Optional[str] = None) -> VSSession:
        """Spieler meldet ``ready``. Sind **alle** beigetretenen Spieler ready,
        wird **genau einmal** ``pool.claim()`` (= ``resume_game``) ausgeloest
        (-> ``CLAIMED``) und die Handover-Dauer gemessen.

        Scheitert der Handover (z. B. Bridge nicht healthy), bleibt der Zustand
        ``WAITING_BOTH_READY``, der Eintrag ``PARKED`` und das Ready-Flag des
        ausloesenden Spielers wird zurueckgerollt (kein halber Zustand) — es
        wird :class:`VSError` geworfen.
        """
        session = self._require_session(env, instance_id, "ready")
        if session.state not in (VSState.WAITING_OPPONENT, VSState.WAITING_BOTH_READY):
            raise VSError(
                "ready: Instanz %s ist %s — kein ready moeglich"
                % (session.instance_id, session.state.value)
            )
        if player not in session.players:
            raise VSError(
                "ready: Spieler %s ist der Instanz %s nicht beigetreten"
                % (player, session.instance_id)
            )
        if player in session.ready:
            raise VSError(
                "ready: Spieler %s hat fuer Instanz %s bereits ready gemeldet"
                % (player, session.instance_id)
            )
        session.ready.add(player)
        if len(session.players) >= session.required_players and len(session.ready) >= session.required_players:
            try:
                result = self.pool.claim(env=session.env, instance_id=session.instance_id)
            except Exception as exc:
                # Rollback: kein halber Handover; Retry mit demselben ready moeglich.
                session.ready.discard(player)
                raise VSError(
                    "ready: Handover (claim) von %s fehlgeschlagen: %s"
                    % (session.instance_id, exc)
                )
            session.state = VSState.CLAIMED
            session.claimed_at = self.clock()
            session.handover_seconds = result.get("handover_seconds")
        return session

    def recycle(self, env: Optional[str] = None, instance_id: Optional[str] = None,
                keep_warm: bool = True, result: Optional[str] = None) -> VSSession:
        """Nach Matchende aufraeumen (delegiert an ``ParkedPool.recycle``).

        ``keep_warm=True`` (Default) -> ``WAITING_OPPONENT`` (Slots geleert,
        sofort wieder fuer das naechste Match geparkt). ``keep_warm=False`` ->
        ``STOPPED``. Nur aus ``CLAIMED`` erlaubt, sonst :class:`VSError`.
        """
        session = self._require_session(env, instance_id, "recycle")
        if session.state != VSState.CLAIMED:
            raise VSError(
                "recycle: Instanz %s ist %s, erwartet %s"
                % (session.instance_id, session.state.value, VSState.CLAIMED.value)
            )
        session.state = VSState.RECYCLING
        try:
            self.pool.recycle(env=session.env, instance_id=session.instance_id,
                              keep_warm=keep_warm, result=result)
        except Exception as exc:
            raise VSError(
                "recycle: %s fehlgeschlagen: %s" % (session.instance_id, exc)
            )
        session.rounds += 1
        session.players = []
        session.ready = set()
        session.handover_seconds = None
        session.claimed_at = None
        session.state = VSState.WAITING_OPPONENT if keep_warm else VSState.STOPPED
        return session

    def status(self) -> List[Dict[str, Any]]:
        """Snapshot aller VS-Sessions (als einfache dicts)."""
        return [session.to_dict() for session in self._sessions.values()]
