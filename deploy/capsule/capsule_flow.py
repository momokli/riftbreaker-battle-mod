#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-capsule — Kapsel-Flow (Issue #931).

Die einzelnen Bausteine des Warm-Servers existieren bereits (#909 Parked-Pool,
#928 Parked-Dienst, Attack-Cycle, Bridge), aber **niemand verkettet sie** zu
einer gespielten Runde. Diese Datei ist der **Kapsel-Koordinator**: er haelt pro
Env hoechstens **eine aktive Kapsel** und uebersetzt zwischen Parked-Pool,
Attack-Cycle und Bridge.

Kapsel-Phasen (eigene, kleine ZSM — NICHT die Parked-States und NICHT die
``deriveSoloPhase``-Anzeige aus #930):

    idle --open--> claimed --ready--> warmup --(Warmup-Ende+HQ)--> running
                     │                                             │
                     │                                   (HQ-Tod/end)▼
                     └────────── finish/end_game ◀────────────── game_over
                                          │
                                    recycling --(round_reset+pause)--> parked
                                                                         │
                                                          open (naechste Runde)

Wichtige Semantik (#931, bindende Design-Entscheidungen):

  * ``open`` claimt die Instanz **ohne** ``resume_game`` (``resume=False``): die
    Welt bleibt nach der Uebergabe PAUSIERT, der Spieler joint in ein pausiertes
    Spiel (Q1). ``ready`` resumed die Welt und startet den Attack-Cycle.
  * ``finish`` ist **operator-/cockpit-getrieben** (Q6, keine Auto-Erkennung).
    Ohne ``result`` laeuft der weltunabhaengige Pfad (``round_reset`` +
    ``pause_game``); ``end_game(None)`` wird NIE gerufen (Q4).
  * ``auto`` gibt den Operator-Override wieder frei (Bridge
    ``POST /pause_game {"op":"auto"}``): die Engine steuert wieder selbst.

Zwei Pause-Begriffe werden bewusst getrennt: die **Welt-Pause**
(``pause_game``/``resume_game``, Bridge) und die **Cycle-Pause** (Attack-Cycle
``PAUSED``). ``claimed`` heißt „Welt pausiert + Cycle ``PAUSED``"; ``warmup``/
``running`` heißen „Welt laeuft + Cycle ``WARMUP``/``RUNNING``".

Nur Standardbibliothek (stdlib), kein venv/pip. Alle Clients sind injizierbar
(Tests: Fakes, kein Netz).
"""

from __future__ import annotations

import dataclasses
import enum
import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional


class CapsuleError(Exception):
    """Kapsel-Operation nicht moeglich — laut abbrechen (kein halber Zustand).

    Traegt eine HTTP-taugliche ``status`` (Default 409) und einen stabilen
    ``reason`` fuer das einheitliche Fehlerformat der Kapsel-API.
    """

    def __init__(self, reason: str, detail: str = "", status: int = 409) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail
        self.status = int(status)


class CapsulePhase(enum.Enum):
    """Lebenszyklus einer Kapsel (eine gespielte Runde)."""

    IDLE = "idle"            # keine Kapsel aktiv
    CLAIMED = "claimed"      # uebergeben, Welt PAUSIERT (Cycle PAUSED)
    WARMUP = "warmup"        # ready empfangen: Welt resumed, Cycle WARMUP
    RUNNING = "running"      # Cycle RUNNING (Wellen feuern)
    GAME_OVER = "game_over"  # Runde beendet (HQ-Tod bzw. end_game)
    RECYCLING = "recycling"  # end_game/round_reset/pause laufen
    PARKED = "parked"        # zurueck im Pool


# Erlaubte Coordinator-getriebene Uebergaenge. Die Phasen warmup/running/
# game_over werden aus dem Cycle-Status ABGELEITET (nicht hier gesetzt); die
# Kanten dienen der Invariante im Koordinator (kein halber Zustand).
_ALLOWED = {
    CapsulePhase.IDLE: {CapsulePhase.CLAIMED},
    CapsulePhase.CLAIMED: {CapsulePhase.WARMUP, CapsulePhase.RECYCLING},
    CapsulePhase.WARMUP: {CapsulePhase.RUNNING, CapsulePhase.GAME_OVER, CapsulePhase.RECYCLING},
    CapsulePhase.RUNNING: {CapsulePhase.GAME_OVER, CapsulePhase.RECYCLING},
    CapsulePhase.GAME_OVER: {CapsulePhase.RECYCLING},
    CapsulePhase.RECYCLING: {CapsulePhase.PARKED},
    CapsulePhase.PARKED: {CapsulePhase.CLAIMED},
}

# Attack-Cycle-`state` -> Kapselphase (Quelle fuer warmup/running/game_over).
_CYCLE_TO_PHASE = {
    "paused": CapsulePhase.CLAIMED,
    "warmup": CapsulePhase.WARMUP,
    "running": CapsulePhase.RUNNING,
    "game_over": CapsulePhase.GAME_OVER,
}


@dataclasses.dataclass
class Capsule:
    """Eine aktive Kapsel (eine Instanz + ihre Rundenphase).

    ``instance_id``-faehig (Q5): die Kapsel kennt ihre Instanz, damit spaeter
    mehrere Kapseln pro Env moeglich sind.
    """

    env: str
    instance_id: str
    bridge_url: str
    gns_endpoint: Optional[str]
    phase: CapsulePhase
    claimed_at: float
    identitaet: Optional[str] = None
    rounds: int = 0
    bridge: Any = None
    cycle: Any = None

    def to_dict(self, now: Optional[float] = None) -> Dict[str, Any]:
        return {
            "phase": self.phase.value,
            "env": self.env,
            "instance": self.instance_id,
            "bridge_url": self.bridge_url,
            "gns_endpoint": self.gns_endpoint,
            "identitaet": self.identitaet,
            "round": self.rounds,
            "claimed_seconds": None if now is None else now - self.claimed_at,
        }


def _http_json(
    base_url: str,
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 5.0,
    opener: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Ein JSON-Request auf einen lokalen Dienst; Fehler -> :class:`CapsuleError`.

    Mapping: ``urllib``-Verbindungsfehler -> ``503 unreachable``; HTTP-Fehler
    -> ``status`` + ``reason`` aus dem Antwort-Body (Fallback ``http_error``).
    """
    url = "%s%s" % (base_url.rstrip("/"), path)
    data: Optional[bytes] = None
    headers: Dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
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
        raise CapsuleError(reason, detail, int(exc.code))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise CapsuleError("unreachable", "%s %s: %s" % (method, path, exc), 503)
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except ValueError as exc:
        raise CapsuleError("bad_response", "%s %s: kein JSON (%s)" % (method, path, exc), 502)
    if status >= 400:
        reason = parsed.get("reason", "http_error") if isinstance(parsed, dict) else "http_error"
        raise CapsuleError(reason, "%s %s -> HTTP %s" % (method, path, status), status)
    return parsed if isinstance(parsed, dict) else {"value": parsed}


class ParkedServiceClient(object):
    """HTTP-Client auf den Parked-Dienst (#928): ``POST /claim`` + ``/recycle``.

    ``claim`` ohne ``resume_game`` (``resume=False``) laesst die Welt pausiert
    (#931); ``recycle`` reicht das optionale ``result`` durch.
    """

    def __init__(self, base_url: str, token: str = "", timeout: float = 5.0,
                 opener: Optional[Callable[..., Any]] = None) -> None:
        self.base_url = base_url
        self.token = token
        self.timeout = timeout
        self._opener = opener

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Token per Header (nie argv/Prozessliste).
        opener = self._opener
        if self.token:
            real_opener = opener or urllib.request.urlopen

            def opener(request, timeout=5.0):  # type: ignore[no-redef]
                request.add_header("Authorization", "Bearer " + self.token)
                return real_opener(request, timeout=timeout)

        return _http_json(self.base_url, "POST", path, payload, self.timeout, opener)

    def claim(self, env: Optional[str] = None, instance_id: Optional[str] = None,
              resume: bool = True) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"resume": bool(resume)}
        if env is not None:
            payload["env"] = env
        if instance_id is not None:
            payload["instance_id"] = instance_id
        return self._post("/claim", payload)

    def recycle(self, env: Optional[str] = None, instance_id: Optional[str] = None,
                keep_warm: bool = True, result: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "instance_id": instance_id,
            "keep_warm": bool(keep_warm),
        }
        if env is not None:
            payload["env"] = env
        if result is not None:
            payload["result"] = result
        return self._post("/recycle", payload)


class CycleClient(object):
    """HTTP-Client auf den Attack-Cycle-Control-Port (``POST /start``, ``GET /status``)."""

    def __init__(self, base_url: str, timeout: float = 3.0,
                 opener: Optional[Callable[..., Any]] = None) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self._opener = opener

    def start(self) -> Dict[str, Any]:
        return _http_json(self.base_url, "POST", "/start", {}, self.timeout, self._opener)

    def status(self) -> Dict[str, Any]:
        return _http_json(self.base_url, "GET", "/status", None, self.timeout, self._opener)


class BridgeControl(object):
    """Minimaler Bridge-Client fuer den Kapsel-Flow: resume + Pause/auto.

    ``round_reset``/``end_game`` laufen ueber den Parked-Dienst (der die Bridge
    bereits kennt); hier nur die welt-nahen Uebergaenge, die die Kapsel selbst
    ausloest.
    """

    def __init__(self, base_url: str, timeout: float = 5.0,
                 opener: Optional[Callable[..., Any]] = None) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self._opener = opener

    def resume_game(self) -> Dict[str, Any]:
        return _http_json(self.base_url, "POST", "/resume_game", {}, self.timeout, self._opener)

    def pause_game(self, op: Optional[str] = None) -> Dict[str, Any]:
        payload = {"op": op} if op is not None else {}
        return _http_json(self.base_url, "POST", "/pause_game", payload, self.timeout, self._opener)

    def set_auto(self) -> Dict[str, Any]:
        """Operator-Override freigeben: ``POST /pause_game {"op":"auto"}``."""
        return self.pause_game("auto")


class CapsuleCoordinator(object):
    """Verkettet Parked-Dienst, Attack-Cycle und Bridge zu einer Runde.

    ``parked`` — Objekt mit ``claim(env, instance_id, resume)`` und
    ``recycle(env, instance_id, keep_warm, result)`` (Produktion:
    :class:`ParkedServiceClient`; Tests: Fake).
    ``cycle_factory`` — ``callable(env) -> CycleClient``.
    ``bridge_factory`` — ``callable(bridge_url) -> BridgeControl``.
    ``clock`` — injizierbar (Tests: Fake).
    """

    def __init__(
        self,
        parked: Any,
        cycle_factory: Callable[[str], Any],
        bridge_factory: Callable[[str], Any],
        clock: Callable[[], float] = time.monotonic,
        env: Optional[str] = None,
    ) -> None:
        self.parked = parked
        self.cycle_factory = cycle_factory
        self.bridge_factory = bridge_factory
        self.clock = clock
        self.env = env
        # Eine aktive Kapsel pro Env (Q5); Schluessel = env.
        self._capsules: Dict[str, Capsule] = {}

    # -- intern ------------------------------------------------------------
    def _default_env(self) -> str:
        return self.env or "default"

    def _map_error(self, exc: Exception, default_reason: str,
                   default_status: int = 503) -> CapsuleError:
        """Fremdfehler (Parked/Cycle/Bridge) auf einen :class:`CapsuleError` mappen.

        Traegt der Fehler schon ``status``/``reason`` (z. B. ``ServiceError`` des
        Parked-Controllers), werden sie uebernommen — sonst der Default.
        """
        if isinstance(exc, CapsuleError):
            return exc
        status = getattr(exc, "status", None) or default_status
        reason = getattr(exc, "reason", None) or default_reason
        return CapsuleError(reason, str(exc), status)

    def _single(self) -> Optional[Capsule]:
        """Die einzige Kapsel, wenn env nicht angegeben ist (sonst mehrdeutig)."""
        if len(self._capsules) == 0:
            return None
        if len(self._capsules) == 1:
            return next(iter(self._capsules.values()))
        raise CapsuleError(
            "env_ambiguous",
            "%d Kapseln aktiv — env noetig" % len(self._capsules),
            409,
        )

    def _require(self, env: Optional[str], action: str) -> Capsule:
        if env is None:
            cap = self._single()
        else:
            cap = self._capsules.get(env)
        if cap is None:
            raise CapsuleError(
                "no_instance",
                "%s: keine aktive Kapsel%s" % (action, "" if env is None else " fuer env=%s" % env),
                409,
            )
        return cap

    def _transition(self, cap: Capsule, target: CapsulePhase) -> Capsule:
        """Phasenuebergang pruefen und setzen; unerlaubt -> ``CapsuleError``."""
        if target is cap.phase:
            return cap
        if target not in _ALLOWED.get(cap.phase, set()):
            raise CapsuleError(
                "wrong_phase",
                "Kapsel %s: Uebergang %s -> %s nicht erlaubt"
                % (cap.instance_id, cap.phase.value, target.value),
                409,
            )
        cap.phase = target
        return cap

    # -- API ---------------------------------------------------------------
    def open(self, env: Optional[str] = None, instance_id: Optional[str] = None,
             identitaet: Optional[str] = None) -> Capsule:
        """Kapsel oeffnen: Parked ``claim`` OHNE resume -> ``claimed`` (Welt pausiert).

        Nur moeglich, wenn fuer die Env keine Kapsel laeuft (``idle``) bzw. die
        letzte Runde ``parked`` ist; sonst ``409 already_open``.
        """
        env = env or self._default_env()
        existing = self._capsules.get(env)
        if existing is not None and existing.phase not in (CapsulePhase.PARKED,):
            raise CapsuleError(
                "already_open",
                "Kapsel fuer env=%s ist %s" % (env, existing.phase.value),
                409,
            )
        try:
            result = self.parked.claim(env=env, instance_id=instance_id, resume=False)
        except Exception as exc:  # noqa: BLE001 - Mapping in CapsuleError
            raise self._map_error(exc, "claim_failed", 503)

        instance = result.get("instance")
        if not instance:
            raise CapsuleError("claim_failed", "Parked-Antwort ohne instance", 502)
        bridge_url = result.get("bridge_url") or ""
        capsule = Capsule(
            env=env,
            instance_id=str(instance),
            bridge_url=str(bridge_url),
            gns_endpoint=result.get("gns_endpoint"),
            phase=CapsulePhase.IDLE,
            claimed_at=self.clock(),
            identitaet=identitaet,
            bridge=self.bridge_factory(str(bridge_url)) if bridge_url else None,
            cycle=self.cycle_factory(env),
        )
        self._capsules[env] = capsule
        self._transition(capsule, CapsulePhase.CLAIMED)
        return capsule

    def ready(self, env: Optional[str] = None) -> Capsule:
        """``ready``: erst Bridge ``resume_game``, dann Cycle ``POST /start``.

        Idempotenter No-op, wenn die Kapsel schon ``warmup``/``running`` ist.
        Ausserhalb ``claimed`` (und nicht warmup/running) -> ``409 wrong_phase``.
        Scheitert ``resume_game``, bleibt die Phase ``claimed`` (Wiederholung
        moeglich). Scheitert ``cycle.start`` nach erfolgreichem Resume, wird die
        Welt best-effort wieder pausiert und die Phase bleibt ``claimed`` — kein
        stiller Teilzustand.
        """
        cap = self._require(env, "ready")
        if cap.phase in (CapsulePhase.WARMUP, CapsulePhase.RUNNING):
            return cap  # idempotent
        if cap.phase != CapsulePhase.CLAIMED:
            raise CapsuleError(
                "wrong_phase",
                "ready: Kapsel %s ist %s, erwartet %s"
                % (cap.instance_id, cap.phase.value, CapsulePhase.CLAIMED.value),
                409,
            )
        if cap.bridge is None:
            raise CapsuleError("no_bridge", "ready: keine Bridge fuer %s" % cap.instance_id, 503)
        try:
            cap.bridge.resume_game()
        except Exception as exc:  # noqa: BLE001
            # Phase bleibt claimed -> erneutes ready moeglich.
            raise self._map_error(exc, "bridge_unreachable", 503)
        try:
            cap.cycle.start()
        except Exception as exc:  # noqa: BLE001
            # Rollback: Welt wieder pausieren, damit kein halber Zustand bleibt.
            try:
                cap.bridge.pause_game()
            except Exception:  # noqa: BLE001 - best-effort Rollback
                pass
            raise self._map_error(exc, "cycle_unreachable", 503)
        cap.phase = CapsulePhase.WARMUP
        return cap

    def finish(self, result: Optional[str] = None, env: Optional[str] = None) -> Capsule:
        """Runde beenden (operator-/cockpit-getrieben, Q6).

        ``result`` (``win``/``lose``) optional: mit ``result`` ruft der Parked-
        Recycle ``end_game(result)``, ohne ``result`` laeuft der weltunabhaengige
        Pfad (``round_reset`` + ``pause_game``) — ``end_game(None)`` wird NIE
        gerufen (Q4). Danach ``phase=parked``, ``rounds`` erhoeht.

        Ein zweiter ``finish`` (schon ``recycling``/``parked``) -> ``409``.
        """
        if result is not None and result not in ("win", "lose"):
            raise CapsuleError("bad_request", "result muss 'win' oder 'lose' sein", 400)
        cap = self._require(env, "finish")
        if cap.phase not in (CapsulePhase.CLAIMED, CapsulePhase.WARMUP,
                             CapsulePhase.RUNNING, CapsulePhase.GAME_OVER):
            raise CapsuleError(
                "wrong_phase",
                "finish: Kapsel %s ist %s" % (cap.instance_id, cap.phase.value),
                409,
            )
        previous = cap.phase
        self._transition(cap, CapsulePhase.RECYCLING)
        try:
            out = self.parked.recycle(
                env=cap.env, instance_id=cap.instance_id, keep_warm=True, result=result
            )
        except Exception as exc:  # noqa: BLE001
            cap.phase = previous  # Retry moeglich, kein halber Zustand
            raise self._map_error(exc, "recycle_failed", 503)
        cap.rounds = int(out.get("rounds", cap.rounds + 1))
        cap.phase = CapsulePhase.PARKED
        return cap

    def auto(self, env: Optional[str] = None) -> Capsule:
        """Operator-Override freigeben: Bridge ``POST /pause_game {"op":"auto"}``."""
        cap = self._require(env, "auto")
        if cap.bridge is None:
            raise CapsuleError("no_bridge", "auto: keine Bridge fuer %s" % cap.instance_id, 503)
        try:
            cap.bridge.set_auto()
        except Exception as exc:  # noqa: BLE001
            raise self._map_error(exc, "bridge_unreachable", 503)
        return cap

    def status(self, env: Optional[str] = None) -> Dict[str, Any]:
        """Phasen-Snapshot: Phase aus Cycle-``state`` + Kapselzustand abgeleitet."""
        if env is None:
            cap = self._single()
        else:
            cap = self._capsules.get(env)
        if cap is None:
            return {
                "phase": CapsulePhase.IDLE.value,
                "env": env or self._default_env(),
                "instance": None,
                "bridge_url": None,
                "gns_endpoint": None,
                "round": 0,
                "cycle": {"state": None},
                "claimed_seconds": None,
            }

        cycle_snap: Dict[str, Any] = {"state": None}
        phase = cap.phase
        if cap.cycle is not None and cap.phase in (
            CapsulePhase.CLAIMED, CapsulePhase.WARMUP,
            CapsulePhase.RUNNING, CapsulePhase.GAME_OVER,
        ):
            try:
                snap = cap.cycle.status()
                if isinstance(snap, dict):
                    cycle_snap = snap
                    mapped = _CYCLE_TO_PHASE.get(str(snap.get("state")))
                    # Aktive Phasen folgen dem Cycle-State (Quelle fuer
                    # warmup/running/game_over); die Kapselphasen recycling/parked
                    # sind oben bereits ausgenommen.
                    if mapped is not None:
                        phase = mapped
            except Exception:  # noqa: BLE001 - Status darf nie brechen
                cycle_snap = {"state": None}

        out = cap.to_dict(now=self.clock())
        out["phase"] = phase.value
        out["cycle"] = cycle_snap
        return out


def build_coordinator(capsule_config: Any, clock: Callable[[], float] = time.monotonic) -> CapsuleCoordinator:
    """Koordinator aus einer ``CapsuleServiceConfig`` bauen (Produktionsverdrahtung)."""
    parked = ParkedServiceClient(
        capsule_config.parked_url, token=capsule_config.parked_token, timeout=capsule_config.timeout
    )
    return CapsuleCoordinator(
        parked,
        cycle_factory=lambda env: CycleClient(capsule_config.cycle_url, timeout=capsule_config.timeout),
        bridge_factory=lambda url: BridgeControl(url, timeout=capsule_config.timeout),
        clock=clock,
        env=capsule_config.env,
    )