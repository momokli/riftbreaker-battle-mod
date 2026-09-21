#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""attack-cycle — 7-Minuten-Angriffszyklus (PoC, ersetzt den 2-min-Order-Scheduler).

Ziel (PoC): anstatt jede gekaufte Welle 2 min nach dem Kauf einzeln zu feuern,
laeuft ein fester Zyklus, der beim Bau des HQ startet:

  round start (HQ gebaut)
    └─ alle `interval` Sekunden (Default 7 min) feuert der Zyklus die
       Natural-Wellen des aktuellen Levels (1..9, cap 9): je Attack
       `maxAttackCountPerDifficulty[level]` Wellen, ab Level 8/9 zusaetzlich
       ein Boss (attack_boss_dynamic.logic) — Spiegel der Base-Game-DOM
       (DIFFICULTY_RULES, docs/DOM_REPLICA.md). Der Spieler kann waehrend des
       Fensters Wellen "kaufen" (`-send waveN`): jede wird SOFORT bezahlt
       (try_spend) und in eine Queue gestapelt; beim naechsten Zyklus-Tick
       werden sie GLEICHZEITIG mit den Natural-Wellen gefeuert.

Difficulty-Level (Issue #778) laeuft auf einem EIGENEN, vom Wellen-Feuern
entkoppelten Timer — und folgt der Base-Game-Kurve (§3.1 DOM_REPLICA.md):
erster Schritt 1→2 nach `difficulty_interval_first_s` Sekunden (Default 200s),
jeder Folge-Schritt 2→3 … 8→9 nach `difficulty_interval_subsequent_s` (Default
600s, cap 9). Beide Werte sind im Cockpit editierbar. Eine gefeuerte Welle
nutzt schlicht das zu diesem Zeitpunkt aktuelle Level, erhoeht es aber nicht
mehr selbst (Vorbild: tools/wave-scheduler/wave_scheduler.py).

Ablauf:
  Schritt 1→2 nach first Sekunden, danach je +subsequent Sekunden:
    level = min(level + 1, max_level)
  alle `interval` Sekunden: fire natural(level) + bought[] ueber
    POST /activate_mission_flow, bought = []

Kauf (`POST /queue_send {"name":"waveN"}`):
  1. Cost-Tabelle (Spiegel der client-mod .ent-Preise) → cost
  2. POST /try_spend {"amount":"<cost>"} → SOFORT bezahlt (nur wenn Guthaben reicht)
  3. ok → level in pending stapeln; insufficient → 402 (nicht gestapelt)

Rein stdlib (HTTP via urllib), kein Netz-Dep im Test. Server-seitig (Sidecar),
NICHT in der Website, NICHT in der DLL. Pattern wie deploy/match-loop (pollt
get_state) + tools/wave-scheduler (Queue + activate_mission_flow).

Persona (waehlbares Send-Profil): eine optionale Folge von Attacken, je
Attack eine Liste der vom Gegner gekauften Extra-Wellen. ``[[3,5],[7]]`` =
Attack 1 feuert natural + Wave 3 + Wave 5, Attack 2 natural + Wave 7, danach
laeuft die Persona aus (kein Loop). Leere Liste = keine Extra-Wellen.

send-yourself (Routing): ``on`` (Default) feuert eigene Kaeufe lokal (heute);
``off`` zieht das Carbonium trotzdem ab (try_spend), feuert die Welle aber
NICHT lokal, sondern trackt sie als Outgoing-Send (spaeter Server B).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional

# Cost-Tabelle (Spiegel der client-mod .ent-Preise; Stand main #770/#205).
# Nur die Preise sind hier relevant; der Logic-Pfad ist ein einziges Template
# (siehe DEFAULT_LOGIC_TEMPLATE). level -> cost (Carbonium, Display-Einheiten).
WAVE_COST = {
    1: 300,
    2: 700,
    3: 1400,
    4: 2450,
    5: 4000,
    6: 5350,
    7: 7600,
    8: 9650,
    9: 10500,
}
NAME_TO_LEVEL = {f"wave{lvl}": lvl for lvl in WAVE_COST}

DEFAULT_MAX_LEVEL = 9
DEFAULT_INTERVAL_S = 420.0  # 7 min
# Difficulty-Escalation folgt der Base-Game-Kurve (§3.1 DOM_REPLICA.md):
# erster Schritt 1→2 Default 200s, Folge-Schritte 2→3 … 8→9 Default 600s.
# Beide Werte sind im Cockpit editierbar (POST /difficulty_interval).
DEFAULT_DIFFICULTY_INTERVAL_FIRST_S = 200.0  # erster Schritt (1→2)
DEFAULT_DIFFICULTY_INTERVAL_SUBSEQUENT_S = 600.0  # Folge-Schritte (2→3 … 8→9)
DEFAULT_BRIDGE_URL = "http://127.0.0.1:9001"
DEFAULT_CONTROL_PORT = 9102

# Logic-Pfad je Level (Spiegel der SEND-MENU-Presets im Cockpit, die live
# Wellen spawnen). Durchgehend _id_1 = raw spawn (sofort), kein _entry
# ("attack incoming"-Delay). NICHT logic/dom/* — das loest nur "attack incoming"
# aus, ohne Spawn (pauseAttacks=true in sandbox). wave9 teilt den Pool mit wave8 (#658).
WAVE_LOGIC = {
    1: "logic/missions/survival/attack_level_1_id_1.logic",
    2: "logic/missions/survival/attack_level_2_id_1.logic",
    3: "logic/missions/survival/attack_level_3_id_1.logic",
    4: "logic/missions/survival/attack_level_4_id_1.logic",
    5: "logic/missions/survival/attack_level_5_id_1.logic",
    6: "logic/missions/survival/attack_level_6_id_1.logic",
    7: "logic/missions/survival/attack_level_7_id_1.logic",
    8: "logic/missions/survival/attack_level_8_id_1.logic",
    9: "logic/missions/survival/attack_level_8_id_1.logic",
}

# Boss-/Elite-Boss-Logic (rules.bosses bzw. rules.multiplayerWaves[level] ->
# attack_boss_dynamic.logic; see research #750). Ein Aufruf = EIN Boss.
BOSS_LOGIC = "logic/missions/survival/attack_boss_dynamic.logic"

# Creature-Attack-Events (rules.gameEvents, jungle default; docs/research/
# 798-event-level.md §4.2, docs/DOM_REPLICA.md §2 #10). Mini-Kreaturen-Attacks
# (shegret/kermon/phirian), die in der IDLE-Phase (ZWISCHEN den Haupt-Attacks)
# feuern; ihre `attack_strength` eskaliert mit dem Event-Level. Ein Eintrag = ein
# (level-band, strength)-Paar; dieselbe logicFile mit anderem attack_strength
# schaltet logic_switch_on_value_1 auf die haertere Route (der „Bonus ab
# Level 5/6/8"). attack_strength=None = kein Binding (phirian).
#
#   name             Anzeigename (fuer Status/History)
#   logic            logicFile (logic/event/<family>_attack.logic)
#   min_level/max_level  Event-Level-Band (inclusive)
#   attack_strength  "normal"|"hard"|"very_hard"|None
#   weight           GetEventByWeight-Gewicht (weighted random)
CREATURE_ATTACK_EVENTS = [
    {"name": "shegret_attack", "logic": "logic/event/shegret_attack.logic",
     "min_level": 2, "max_level": 4, "attack_strength": "normal", "weight": 3},
    {"name": "shegret_attack", "logic": "logic/event/shegret_attack.logic",
     "min_level": 5, "max_level": 7, "attack_strength": "hard", "weight": 3},
    {"name": "shegret_attack", "logic": "logic/event/shegret_attack.logic",
     "min_level": 8, "max_level": 9, "attack_strength": "very_hard", "weight": 3},
    {"name": "kermon_attack", "logic": "logic/event/kermon_attack.logic",
     "min_level": 4, "max_level": 5, "attack_strength": "normal", "weight": 1},
    {"name": "kermon_attack", "logic": "logic/event/kermon_attack.logic",
     "min_level": 6, "max_level": 7, "attack_strength": "hard", "weight": 1},
    {"name": "kermon_attack", "logic": "logic/event/kermon_attack.logic",
     "min_level": 8, "max_level": 9, "attack_strength": "very_hard", "weight": 1},
    {"name": "phirian_attack", "logic": "logic/event/phirian_attack.logic",
     "min_level": 3, "max_level": 9, "attack_strength": None, "weight": 1},
]
# Event feuert im prepare_spawn-Fenster bei 35% (Base Game: 126s von 360s,
# dom_manager.lua idleTimeEventMul). Unser flacher interval_s ersetzt das
# prepare-Fenster -> Event bei `next_attack_at - fraction * interval_s`.
DEFAULT_EVENT_OFFSET_FRACTION = 0.35

# Wellen-Feuer-Regeln je Difficulty-Level (1..9), Spiegel der Base-Game-DOM
# (client-mod/lua/missions/survival/v2/dom_survival_jungle_rules_default.lua +
# _normal.lua). Escalation/Timing lebt separat im Difficulty-Timer
# (Issue #778/#800); hier nur die FEUER-Dimensionen.
#
#   max_attack_count  rules.maxAttackCountPerDifficulty — Natural-Wellen je Attack
#   boss_min_level    ab diesem Level feuert zusaetzlich EIN Elite-Boss
#                     (rules.multiplayerWaves -> attack_boss_dynamic.logic),
#                     Coop-Schwelle (playersCounter>1): default=2, normal=5
#                     (GetMultiplayerAttackCount: clamp(additionalWaves+1,0,1)).
#                     rules.bosses (boss_attack) und rules.extraWaves
#                     (stronger_attack) tragen gameStates="ATTACK|STREAMING"
#                     (STREAMING ohne NO_STREAMING) -> nur mit Streaming-Session
#                     -> im Non-Streaming-Match NICHT gefeuert.
#
# Refs: docs/DOM_REPLICA.md §2/§3.3, docs/research/boss-spawn-mechanik.md,
# docs/research/213-wave-richtwert.md,
# docs/research/736-wellen-hp-pool-vollstaendig.md,
# docs/research/multiplayer-additional-boss-wave.md.
DIFFICULTY_RULES = {
    "default": {
        "max_attack_count": [1, 2, 2, 3, 3, 3, 3, 3, 4],
        "boss_min_level": 2,
    },
    "normal": {
        "max_attack_count": [1, 2, 2, 2, 2, 2, 3, 3, 3],
        "boss_min_level": 5,
    },
}
DEFAULT_DIFFICULTY_PROFILE = "normal"


def parse_hq_alive(raw: str) -> bool:
    """True, wenn get_state ok:true und hq_hp > 0 (HQ gebaut + lebt)."""
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return False
    if not isinstance(obj, dict) or not obj.get("ok"):
        return False
    hp = obj.get("hq_hp")
    return isinstance(hp, (int, float)) and hp > 0


def parse_send_level(body: str) -> Optional[int]:
    """Extrahiert das Wave-Level aus einem /queue_send-Body.

    Akzeptiert `{"name":"waveN"}` (send-tailer) oder `{"level":N}` (manuell).
    Liefert None bei ungueltigem/unbekanntem Namen.
    """
    try:
        obj = json.loads(body) if isinstance(body, str) else body
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None

    if "level" in obj:
        try:
            lvl = int(obj["level"])
        except (ValueError, TypeError):
            return None
        return lvl if lvl in WAVE_COST else None

    name = obj.get("name")
    if isinstance(name, str):
        return NAME_TO_LEVEL.get(name)

    return None


WAVE_COUNT = 9  # Wellen-Typen (wave1..wave9)


def _normalize_counts(attack) -> Optional[List[int]]:
    """Normalisiert eine Attack auf WAVE_COUNT Counts (wave1..wave9), 0-auffuellen.

    Liefert eine Liste von WAVE_COUNT nicht-negativen ints, oder None bei
    ungueltigem Format.
    """
    if not isinstance(attack, list):
        return None
    out: List[int] = []
    for c in attack[:WAVE_COUNT]:
        if isinstance(c, bool) or not isinstance(c, int) or c < 0:
            return None
        out.append(c)
    return out + [0] * (WAVE_COUNT - len(out))


def _expand_counts(counts) -> List[int]:
    """9-Counts (wave1..wave9) -> Liste von Leveln (count>1 => mehrfach)."""
    levels: List[int] = []
    for wave, count in enumerate(counts or []):
        levels.extend([wave + 1] * count)
    return levels


def _normalize_creature_events(events) -> List[Dict[str, Any]]:
    """Normalisiert eine Creature-Attack-Event-Liste auf den internen Shape.

    Erwartet eine Liste von dicts mit: name (str), logic (str),
    min_level/max_level (int >= 1, min <= max), attack_strength
    ("normal"|"hard"|"very_hard"|None), weight (num > 0). Wirft ValueError bei
    ungueltigem Format.
    """
    if not isinstance(events, (list, tuple)):
        raise ValueError("creature_events muss eine Liste sein")
    out: List[Dict[str, Any]] = []
    for e in events:
        if not isinstance(e, dict):
            raise ValueError("creature_events: Eintrag muss ein Objekt sein")
        name = e.get("name")
        logic = e.get("logic")
        if (not isinstance(name, str) or not name
                or not isinstance(logic, str) or not logic):
            raise ValueError("creature_events: name/logic muessen nicht-leere Strings sein")
        lo = e.get("min_level")
        hi = e.get("max_level")
        if (isinstance(lo, bool) or not isinstance(lo, int)
                or isinstance(hi, bool) or not isinstance(hi, int)
                or lo < 1 or hi < lo):
            raise ValueError("creature_events: min/max_level invalide")
        strength = e.get("attack_strength")
        if strength is not None and (
            not isinstance(strength, str)
            or strength not in ("normal", "hard", "very_hard")
        ):
            raise ValueError(
                "creature_events: attack_strength muss normal/hard/very_hard/None sein"
            )
        w = e.get("weight", 1)
        if isinstance(w, bool) or not isinstance(w, (int, float)) or w <= 0:
            raise ValueError("creature_events: weight muss > 0 sein")
        out.append({
            "name": name,
            "logic": logic,
            "min_level": lo,
            "max_level": hi,
            "attack_strength": strength,
            "weight": float(w),
        })
    return out


def _pick_creature_event(level, events, rng) -> Optional[Dict[str, Any]]:
    """Weighted-random Auswahl eines Creature-Attack-Events fuer `level`.

    Filtert nach min_level <= level <= max_level und gewichtet per weight
    (GetEventByWeight). Liefert None, wenn kein Event fuer dieses Level
    existiert (z. B. Level 1). `rng` ist ein Callable -> float in [0,1)
    (injizierbar fuer deterministische Tests).
    """
    pool = [e for e in events if e["min_level"] <= level <= e["max_level"]]
    if not pool:
        return None
    total = sum(e["weight"] for e in pool)
    r = rng() * total
    acc = 0.0
    for e in pool:
        acc += e["weight"]
        if r < acc:
            return e
    return pool[-1]


def _normalize_difficulty_rules(rules, max_level: int) -> Dict[str, Any]:
    """Normalisiert eine Difficulty-Rules-Struktur auf den internen Shape.

    Erwartet dict mit (optionalen) Schluesseln:
      max_attack_count: Liste nicht-negativer ints (Natural-Wellen je Level;
                        fehlende Eintraege werden mit dem letzten Wert aufgefuellt)
      boss_min_level:   int|None (Boss feuert ab diesem Level)
      extra_min_level:  int|None (Extra-Wellen feuern ab diesem Level)
      extra_count:      int (Anzahl Extra-Wellen je Attack)
      mp_min_level:     int|None (MP-Elite-Boss feuert ab diesem Level)

    Liefert ein normalisiertes dict; wirft ValueError bei ungueltigem Format.
    """
    if not isinstance(rules, dict):
        raise ValueError("difficulty_rules muss ein Objekt sein")

    counts = rules.get("max_attack_count", [1])
    if not isinstance(counts, (list, tuple)) or not counts:
        raise ValueError("max_attack_count muss eine nicht-leere Liste sein")
    norm_counts: List[int] = []
    for c in counts:
        if isinstance(c, bool) or not isinstance(c, int) or c < 0:
            raise ValueError("max_attack_count: nur nicht-negative ints")
        norm_counts.append(c)
    if len(norm_counts) >= max_level:
        norm_counts = norm_counts[:max_level]
    else:
        norm_counts = norm_counts + [norm_counts[-1]] * (max_level - len(norm_counts))

    def _level_or_none(key: str):
        v = rules.get(key)
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise ValueError(f"{key} muss ein int >= 1 oder None sein")
        return v

    return {
        "max_attack_count": norm_counts,
        "boss_min_level": _level_or_none("boss_min_level"),
    }

def _normalize_difficulty_schedule(schedule, n_steps: int) -> Optional[List[float]]:
    """Normalisiert eine Liste von Schrittdauern auf `n_steps` positive floats.

    Fehlende Eintraege werden mit dem letzten Wert aufgefuellt, ueberzaehlige
    abgeschnitten. Liefert None bei ungueltigem Format (nicht-Liste, leer,
    nicht-positive oder nicht-numerische Werte).
    """
    if not isinstance(schedule, (list, tuple)) or not schedule:
        return None
    out: List[float] = []
    for v in schedule:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            return None
        out.append(float(v))
    if len(out) >= n_steps:
        return out[:n_steps]
    out.extend([out[-1]] * (n_steps - len(out)))
    return out


def _difficulty_schedule_from_steps(
    first_step_s: float, subsequent_step_s: float, n_steps: int
) -> List[float]:
    """Kurve aus zwei Schrittdauern: erster Schritt = first_step_s, alle
    Folge-Schritte = subsequent_step_s."""
    return [float(first_step_s)] + [float(subsequent_step_s)] * (n_steps - 1)


def load_personas(path: str) -> Dict[str, List[List[int]]]:
    """Laedt Persona-Definitionen aus einer JSON-Datei.

    Erwartetes Format: ``{"personas": {"<name>": [[c1..c9], ...]}}``.
    Jede Persona ist eine Liste von Attacken; jede Attack ist eine Liste von
    WAVE_COUNT Counts (wave1..wave9). Laeuft aus (kein Loop). Liefert
    ``{name: [[counts], ...]}``. Wirft ValueError bei ungueltigem Format.
    """
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"persona file {path}: top-level muss ein Objekt sein")
    raw = data.get("personas", {})
    if not isinstance(raw, dict):
        raise ValueError(f"persona file {path}: 'personas' muss ein Objekt sein")
    personas: Dict[str, List[List[int]]] = {}
    for name, attacks in raw.items():
        if not isinstance(attacks, list):
            raise ValueError(f"persona '{name}': muss eine Liste sein")
        persona: List[List[int]] = []
        for attack in attacks:
            norm = _normalize_counts(attack)
            if norm is None:
                raise ValueError(f"persona '{name}': ungueltige Attack (erwartet {WAVE_COUNT} Counts)")
            persona.append(norm)
        personas[name] = persona
    return personas


class AttackCycle:
    """Reine Zustandsmaschine + HTTP (injizierbarer Poster/Clock fuer Tests)."""

    def __init__(
        self,
        base_url: str,
        interval_s: float = DEFAULT_INTERVAL_S,
        difficulty_interval_first_s: float = DEFAULT_DIFFICULTY_INTERVAL_FIRST_S,
        difficulty_interval_subsequent_s: float = DEFAULT_DIFFICULTY_INTERVAL_SUBSEQUENT_S,
        difficulty_schedule: Optional[List[float]] = None,
        max_level: int = DEFAULT_MAX_LEVEL,
        wave_logic: Optional[Dict[int, str]] = None,
        persona: Optional[List[List[int]]] = None,
        persona_name: str = "",
        send_yourself: bool = True,
        timeout: float = 30.0,
        difficulty_profile: str = DEFAULT_DIFFICULTY_PROFILE,
        difficulty_rules: Optional[Dict[str, Any]] = None,
        creature_events: Optional[List[Dict[str, Any]]] = None,
        event_offset_fraction: float = DEFAULT_EVENT_OFFSET_FRACTION,
        _poster: Optional[Callable[[str, bytes], tuple]] = None,
        _getter: Optional[Callable[[str], tuple]] = None,
        _clock: Callable[[], float] = time.monotonic,
        _rng: Callable[[], float] = random.random,
    ):
        self.base_url = base_url.rstrip("/")
        self.interval_s = interval_s
        self.max_level = max_level
        self.difficulty_interval_first_s = float(difficulty_interval_first_s)
        self.difficulty_interval_subsequent_s = float(difficulty_interval_subsequent_s)
        # Difficulty-Schedule: Dauer je Schritt (Index 0 = 1→2 … Index n-1 = 8→9).
        # `difficulty_interval_first_s` + `difficulty_interval_subsequent_s` sind
        # die zwei im Cockpit editierbaren Knoepfe; eine explizite volle Liste
        # (`difficulty_schedule`) ueberschreibt die Kurve komplett.
        n_steps = max(1, max_level - 1)
        if difficulty_schedule is not None:
            sched = _normalize_difficulty_schedule(difficulty_schedule, n_steps)
            self.difficulty_schedule = (
                sched
                if sched is not None
                else _difficulty_schedule_from_steps(
                    difficulty_interval_first_s, difficulty_interval_subsequent_s, n_steps
                )
            )
        else:
            self.difficulty_schedule = _difficulty_schedule_from_steps(
                difficulty_interval_first_s, difficulty_interval_subsequent_s, n_steps
            )
        self.wave_logic = wave_logic or WAVE_LOGIC
        self.persona = persona
        self.persona_name = persona_name
        self.send_yourself = send_yourself
        self.timeout = timeout
        # Wellen-Feuer-Regeln (Attack-Count/Boss/Extra/MP), konfigurierbar.
        # Ein explizites `difficulty_rules`-dict ueberschreibt das benannte
        # Profil (`difficulty_profile`).
        rules = difficulty_rules
        if rules is None:
            rules = DIFFICULTY_RULES.get(difficulty_profile, DIFFICULTY_RULES[DEFAULT_DIFFICULTY_PROFILE])
        self.difficulty_profile = difficulty_profile if difficulty_rules is None else "<custom>"
        self.difficulty_rules = _normalize_difficulty_rules(rules, max_level)
        # Creature-Attack-Events (Event-Layer #816): konfigurierbar via
        # `creature_events`, Default = die Base-Game-Bänder (jungle default).
        self.creature_events = _normalize_creature_events(
            creature_events if creature_events is not None else CREATURE_ATTACK_EVENTS
        )
        self.event_offset_s = interval_s * float(event_offset_fraction)
        self._poster = _poster or self._http_post
        self._getter = _getter or self._http_get
        self._clock = _clock
        self._rng = _rng

        self._lock = threading.Lock()
        self.active = False
        self.level = 1
        self.next_attack_at: Optional[float] = None
        self.next_difficulty_at: Optional[float] = None
        self.next_event_at: Optional[float] = None
        self.orders: list = []  # unbezahlte Buy-Orders (order list)
        self.bought: list = []  # bezahlte Wellen (bought queue, feuert als naechstes)
        self.last_fire: Optional[Dict[str, Any]] = None
        self.last_event: Optional[Dict[str, Any]] = None
        self.attack_index = 0  # Anzahl gefeuerter Attacken (Persona-Indexierung)
        self.outgoing: list = []  # getrackte Outgoing-Sends (send_yourself off)
        self.history: list = []  # letzte N gefeuerte Attacken (fuer Attack-Cycle-Tabelle)
        self._reset_epoch = 0

    # --- HTTP (urllib) ----------------------------------------------------
    def _http_post(self, path: str, body: bytes) -> tuple:
        req = urllib.request.Request(
            self.base_url + path,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError) as e:
            return 0, str(e)

    def _http_get(self, path: str) -> tuple:
        req = urllib.request.Request(self.base_url + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError) as e:
            return 0, str(e)

    def _post_json(self, path: str, payload: Dict[str, Any]) -> tuple:
        return self._poster(path, json.dumps(payload).encode("utf-8"))

    # --- get_state / HQ-Erkennung ----------------------------------------
    def _hq_alive(self) -> bool:
        status, body = self._poster("/get_state", b"{}")
        if not 200 <= status < 300:
            return False
        return parse_hq_alive(body)

    # --- try_spend (Kauf) -------------------------------------------------
    def _spend(self, cost: int) -> tuple:
        status, body = self._poster("/try_spend", json.dumps({"amount": str(cost)}).encode("utf-8"))
        ok = False
        try:
            ok = bool(json.loads(body).get("ok"))
        except (ValueError, TypeError, AttributeError):
            ok = False
        return status, ok, body

    # --- Kauf (aus /queue_send) ------------------------------------------
    def buy(self, level: int) -> tuple:
        """Reiht die Welle SOFORT in die Order-Liste ein (ohne try_spend).

        Der Hintergrund-Resolver (_resolve_orders) bezahlt die Order via
        try_spend und verschiebt sie in die bought-Queue. Der HTTP-Handler
        blockiert dadurch nie. Liefert sofort (http_status, payload_dict).
        """
        if level not in WAVE_COST:
            return 400, {"ok": False, "reason": "unknown_wave"}
        cost = WAVE_COST[level]
        with self._lock:
            self.orders.append({"level": level, "cost": cost})
            order_count = len(self.orders)
        print(f"[attack-cycle] order wave{level} (cost={cost}, orders={order_count})", flush=True)
        return 200, {"ok": True, "queued_level": level, "order_count": order_count}

    def _resolve_orders(self) -> None:
        """Bezahlt alle offenen Orders (try_spend) und verschiebt sie nach bought.

        Nur bezahlte Orders landen in bought -> die Welle feuert NUR, wenn der
        Spieler das Carbonium wirklich HATTE (kein Optimistic-Spawn).
        """
        with self._lock:
            orders = list(self.orders)
            self.orders = []
        for o in orders:
            level, cost = o["level"], o["cost"]
            try:
                status, ok, body = self._spend(cost)
                if 200 <= status < 300 and ok:
                    with self._lock:
                        if self.send_yourself:
                            self.bought.append(level)
                        else:
                            self.outgoing.append({"level": level, "cost": cost})
                    if self.send_yourself:
                        print(f"[attack-cycle] wave{level} bezahlt -> bought (cost={cost})", flush=True)
                    else:
                        print(
                            f"[attack-cycle] wave{level} bezahlt -> outgoing "
                            f"(send_yourself=off, Carbonium abgezogen, Welle geht ins Leere)",
                            flush=True,
                        )
                else:
                    print(f"[attack-cycle] wave{level} verworfen (status={status} ok={ok}): {body[:160]}", flush=True)
            except Exception as e:
                print(f"[attack-cycle] wave{level} resolve error: {e}", flush=True)

    def _resolver_loop(self) -> None:
        """Hintergrund-Resolver: bezahlt kontinuierlich offene Orders."""
        while True:
            try:
                self._resolve_orders()
            except Exception:
                pass
            time.sleep(0.5)

    # --- Feuern -----------------------------------------------------------
    def _fire(self, level: int) -> None:
        logic = self.wave_logic.get(level, self.wave_logic.get(self.max_level, ""))
        status, body = self._post_json(
            "/activate_mission_flow",
            {"logic": logic, "mode": "default"},
        )
        print(f"[attack-cycle] fire wave{level} logic={logic} -> HTTP {status} {body[:120]}", flush=True)

    def _fire_boss(self) -> None:
        """Feuert einen Boss/Elite-Boss (attack_boss_dynamic.logic)."""
        status, body = self._post_json(
            "/activate_mission_flow",
            {"logic": BOSS_LOGIC, "mode": "default"},
        )
        print(f"[attack-cycle] fire boss logic={BOSS_LOGIC} -> HTTP {status} {body[:120]}", flush=True)

    def _fire_event(self, logic: str, attack_strength: Optional[str]) -> None:
        """Feuert einen Creature-Attack-Event (logic/event/<family>_attack.logic).

        `attack_strength` (normal/hard/very_hard) wird als Binding-Parameter im
        Database-Payload gesetzt (activate_mission_flow, #814) und schaltet
        logic_switch_on_value_1 auf die haertere Route. None = kein Binding
        (phirian).
        """
        payload: Dict[str, Any] = {"logic": logic, "mode": "default"}
        if attack_strength:
            payload["attack_strength"] = attack_strength
        status, body = self._post_json("/activate_mission_flow", payload)
        print(
            f"[attack-cycle] fire event logic={logic} "
            f"attack_strength={attack_strength or '-'} -> HTTP {status} {body[:120]}",
            flush=True,
        )

    def _wave_plan(self, level: int) -> Dict[str, Any]:
        """Wellen-Komposition fuer EINE Attack auf `level` (Base-Game-DOM).

        Liefert dict mit natural_count (Natural-Wellen) und boss (bool,
        Elite-Boss via rules.multiplayerWaves). Die Schwellen stammen aus
        self.difficulty_rules (konfigurierbar, s. DIFFICULTY_RULES).
        """
        rules = self.difficulty_rules
        counts = rules["max_attack_count"]
        natural_count = counts[min(level - 1, len(counts) - 1)]
        boss = rules["boss_min_level"] is not None and level >= rules["boss_min_level"]
        return {
            "natural_count": natural_count,
            "boss": boss,
        }

    def _difficulty_duration(self, level: int) -> float:
        """Dauer fuer den naechsten Schritt ab `level` (1-basiert): level → level+1."""
        sched = self.difficulty_schedule
        idx = max(0, min(level - 1, len(sched) - 1))
        return sched[idx]

    # --- Ein Poll/Tick-Schritt -------------------------------------------
    def step(self) -> Optional[str]:
        """Ein Iterationsschritt. Liefert eine Aktion ("started"/"attack") oder None."""
        now = self._clock()

        if not self.active:
            if self._hq_alive():
                with self._lock:
                    self.active = True
                    self.level = 1
                    self.next_attack_at = now + self.interval_s
                    self.next_difficulty_at = now + self._difficulty_duration(1)
                    self.next_event_at = now + self.interval_s - self.event_offset_s
                print(
                    f"[attack-cycle] HQ gebaut -> Zyklus gestartet (Level 1, "
                    f"Angriff in {self.interval_s:.0f}s, "
                    f"naechste Difficulty in {self._difficulty_duration(1):.0f}s)",
                    flush=True,
                )
                return "started"
            return None

        # Difficulty-Timer (Issue #778): laeuft unabhaengig vom Wellen-Feuern
        # und folgt der Base-Game-Kurve (erster Schritt kurz, Rest 600s).
        with self._lock:
            while (
                self.next_difficulty_at is not None and now >= self.next_difficulty_at and self.level < self.max_level
            ):
                self.level += 1
                self.next_difficulty_at += self._difficulty_duration(self.level)
                print(f"[attack-cycle] difficulty erhoeht -> level {self.level}", flush=True)

        # Creature-Attack-Event-Layer (#816): ein Event pro Attack-Zyklus, im
        # prepare-Fenster (event_offset_s vor der naechsten Attack). Das Event-
        # Level = aktuelles Difficulty-Level (self.level), synchron wie
        # currentEventLevel im Base Game (event_manager.lua / dom_manager.lua).
        event = None
        event_level = self.level
        with self._lock:
            if self.next_event_at is not None and now >= self.next_event_at:
                event_level = self.level
                event = _pick_creature_event(
                    event_level, self.creature_events, self._rng
                )
                self.next_event_at += self.interval_s
        if event is not None:
            self._fire_event(event["logic"], event["attack_strength"])
            with self._lock:
                self.last_event = {
                    "name": event["name"],
                    "attack_strength": event["attack_strength"],
                    "level": event_level,
                    "t": now,
                }
            print(
                f"[attack-cycle] event: {event['name']} "
                f"strength={event['attack_strength'] or '-'} level={event_level}",
                flush=True,
            )

        with self._lock:
            if self.next_attack_at is None or now < self.next_attack_at:
                return None
            natural_level = self.level
            sent_levels = list(self.bought)
            self.bought = []
            self.next_attack_at = self.next_attack_at + self.interval_s
            self.attack_index += 1
            extra_levels: List[int] = []
            if self.persona and self.attack_index - 1 < len(self.persona):
                extra_levels = _expand_counts(self.persona[self.attack_index - 1])

        plan = self._wave_plan(natural_level)
        for _ in range(plan["natural_count"]):
            self._fire(natural_level)
        if plan["boss"]:
            self._fire_boss()
        for lvl in extra_levels:
            self._fire(lvl)
        for lvl in sent_levels:
            self._fire(lvl)
        with self._lock:
            self.last_fire = {
                "natural_level": natural_level,
                "natural_count": plan["natural_count"],
                "boss": plan["boss"],
                "persona_levels": extra_levels,
                "sent_levels": sent_levels,
                "t": now,
            }
            self.history.append(
                {
                    "attack": self.attack_index,
                    "natural": natural_level,
                    "natural_count": plan["natural_count"],
                    "boss": plan["boss"],
                    "self": sent_levels,
                    "enemy": extra_levels,
                    "t": now,
                }
            )
            self.history = self.history[-10:]  # cap auf die letzten 10 Attacken
        print(
            f"[attack-cycle] attack: natural={natural_level}x{plan['natural_count']} "
            f"boss={plan['boss']} "
            f"+ persona={extra_levels} + sent={sent_levels}",
            flush=True,
        )
        return "attack"

    # --- Status -----------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        now = self._clock()
        with self._lock:
            return {
                "active": self.active,
                "level": self.level,
                "seconds_to_next_attack": (
                    max(0.0, self.next_attack_at - now) if self.next_attack_at is not None else None
                ),
                "seconds_to_next_difficulty": (
                    max(0.0, self.next_difficulty_at - now) if self.next_difficulty_at is not None else None
                ),
                "seconds_to_next_event": (
                    max(0.0, self.next_event_at - now) if self.next_event_at is not None else None
                ),
                "bought": list(self.bought),
                "orders": list(self.orders),
                "persona": self.persona_name or None,
                "attack_index": self.attack_index,
                "send_yourself": self.send_yourself,
                "outgoing": list(self.outgoing),
                "interval_s": self.interval_s,
                "difficulty_interval_first_s": self.difficulty_interval_first_s,
                "difficulty_interval_subsequent_s": self.difficulty_interval_subsequent_s,
                "difficulty_schedule": list(self.difficulty_schedule),
                "max_level": self.max_level,
                "wave_cost": WAVE_COST,
                "next_attack": {
                    "natural": self.level,
                    "self": list(self.bought),
                    "enemy": (
                        _expand_counts(self.persona[self.attack_index])
                        if self.persona and self.attack_index < len(self.persona)
                        else []
                    ),
                },
                "history": list(self.history),
                "last_fire": self.last_fire,
                "last_event": self.last_event,
            }

    # --- Status an die Bridge pushen (WebUI) ----------------------------
    def push_status(self) -> None:
        """Pusht den Status-JSON an die Bridge (POST /attack_status)."""
        try:
            self._post_json("/attack_status", self.status())
        except Exception:
            pass

    # --- Intervall von der Bridge ziehen (WebUI-Config) -----------------
    def sync_interval(self) -> None:
        """Holt das gewuenschte Intervall (POST /attack_interval {}) und
        uebernimmt es, falls es sich geaendert hat (inkl. Countdown-Reset)."""
        try:
            status, body = self._poster("/attack_interval", b"{}")
            if not 200 <= status < 300:
                return
            new = json.loads(body).get("interval_s")
            if not isinstance(new, (int, float)) or new <= 0:
                return
            new = float(new)
            with self._lock:
                if new != self.interval_s:
                    self.interval_s = new
                    if self.active:
                        self.next_attack_at = self._clock() + new
                    print(f"[attack-cycle] interval -> {new:.0f}s", flush=True)
        except Exception:
            pass

    def sync_difficulty_interval(self) -> None:
        """Holt die zwei Difficulty-Schrittdauern (POST /difficulty_interval {})
        und baut daraus den Schedule [first, subsequent, subsequent, …].

        Die Bridge liefert zwei Werte (`difficulty_interval_first_s` fuer Schritt
        1→2, `difficulty_interval_subsequent_s` fuer alle Folge-Schritte
        2→3 … 8→9); optional eine volle Liste (`difficulty_schedule`) als
        komplette Kurve.
        """
        try:
            status, body = self._poster("/difficulty_interval", b"{}")
            if not 200 <= status < 300:
                return
            data = json.loads(body)
            if not isinstance(data, dict):
                return
            n_steps = max(1, self.max_level - 1)
            new_schedule = None
            raw_sched = data.get("difficulty_schedule")
            if isinstance(raw_sched, list):
                new_schedule = _normalize_difficulty_schedule(raw_sched, n_steps)
            if new_schedule is None:
                new_first = data.get("difficulty_interval_first_s")
                new_subsequent = data.get("difficulty_interval_subsequent_s")
                if (
                    isinstance(new_first, (int, float))
                    and not isinstance(new_first, bool)
                    and new_first > 0
                    and isinstance(new_subsequent, (int, float))
                    and not isinstance(new_subsequent, bool)
                    and new_subsequent > 0
                ):
                    new_schedule = _difficulty_schedule_from_steps(
                        float(new_first), float(new_subsequent), n_steps
                    )
            if new_schedule is None:
                return
            with self._lock:
                if new_schedule != self.difficulty_schedule:
                    self.difficulty_schedule = new_schedule
                    self.difficulty_interval_first_s = new_schedule[0]
                    self.difficulty_interval_subsequent_s = (
                        new_schedule[1] if len(new_schedule) > 1 else new_schedule[0]
                    )
                    if self.active:
                        self.next_difficulty_at = self._clock() + self._difficulty_duration(self.level)
                    print(f"[attack-cycle] difficulty_schedule -> {new_schedule}", flush=True)
        except Exception:
            pass

    # --- Reset (WebUI) -----------------------------------------------------
    def reset(self) -> None:
        """Setzt den Zyklus zurueck: warte wieder auf HQ-Bau, Level 1,
        Queue geleert, kein Countdown."""
        with self._lock:
            self.active = False
            self.level = 1
            self.next_attack_at = None
            self.next_difficulty_at = None
            self.next_event_at = None
            self.orders = []
            self.bought = []
            self.attack_index = 0
            self.outgoing = []
            self.history = []
            self.last_fire = None
            self.last_event = None
        print("[attack-cycle] reset -> warte auf HQ-Bau", flush=True)

    def sync_reset(self) -> None:
        """Prueft einen Reset-Request von der Bridge (POST /attack_reset {})."""
        try:
            status, body = self._poster("/attack_reset", b"{}")
            if not 200 <= status < 300:
                return
            epoch = json.loads(body).get("reset_epoch")
            if isinstance(epoch, int) and epoch != self._reset_epoch:
                self._reset_epoch = epoch
                self.reset()
        except Exception:
            pass

    def sync_personas(self) -> None:
        """Pollt GET /personas und uebernimmt aktive Persona + send_yourself.

        Die Bridge ist die Laufzeit-Quelle der Wahrheit; die CLI-Flags
        --persona/--send-yourself sind nur der Start-Fallback. Die aktive
        Persona liefert die Extra-Wellen je Attack (Liste je Attack, mehrere
        Wellen erlaubt), send_yourself das Routing eigener Kaeufe.
        """
        try:
            status, body = self._getter("/personas")
            if not 200 <= status < 300:
                return
            data = json.loads(body)
            if not isinstance(data, dict):
                return
            personas = data.get("personas")
            if not isinstance(personas, dict):
                return
            active = data.get("active") or ""
            levels = None
            if active:
                sends = personas.get(active)
                if isinstance(sends, list):
                    parsed = []
                    ok = True
                    for attack in sends:
                        norm = _normalize_counts(attack)
                        if norm is None:
                            ok = False
                            break
                        parsed.append(norm)
                    if ok:
                        levels = parsed
            send_yourself = bool(data.get("send_yourself", True))
            with self._lock:
                self.persona = levels
                self.persona_name = active if levels is not None else ""
                self.send_yourself = send_yourself
        except Exception:
            pass

    def sync_natural_attack_rules(self) -> None:
        """Pollt GET /natural_attack_rules und uebernimmt die Natural-Attack-
        Dimensionen (Attack-Count/Boss je Level, Creature-Events, Event-Offset).

        Die Bridge haelt die vollstaendig konfigurierbaren Dimensionen
        (Issue #819); CLI-Flags/Profil sind nur der Start-Fallback. Jede
        Dimension wird einzeln validiert und nur bei gueltigem Format
        uebernommen (ungueltig -> alter Wert bleibt).
        """
        try:
            status, body = self._getter("/natural_attack_rules")
            if not 200 <= status < 300:
                return
            data = json.loads(body)
            if not isinstance(data, dict):
                return

            # difficulty_rules: max_attack_count + boss_min_level.
            new_rules = None
            if "max_attack_count" in data:
                try:
                    new_rules = _normalize_difficulty_rules(
                        {
                            "max_attack_count": data.get("max_attack_count"),
                            "boss_min_level": data.get("boss_min_level"),
                        },
                        self.max_level,
                    )
                except ValueError:
                    new_rules = None

            # creature_events: Liste von Event-Bändern.
            new_events = None
            raw_events = data.get("creature_events")
            if isinstance(raw_events, list):
                try:
                    new_events = _normalize_creature_events(raw_events)
                except ValueError:
                    new_events = None

            # event_offset_fraction: Anteil des Intervalls vor der Attack.
            new_fraction = None
            raw_fraction = data.get("event_offset_fraction")
            if (
                isinstance(raw_fraction, (int, float))
                and not isinstance(raw_fraction, bool)
                and 0.0 < raw_fraction < 1.0
            ):
                new_fraction = float(raw_fraction)

            with self._lock:
                if new_rules is not None:
                    self.difficulty_rules = new_rules
                    self.difficulty_profile = "<bridge>"
                if new_events is not None:
                    self.creature_events = new_events
                if new_fraction is not None:
                    self.event_offset_s = self.interval_s * new_fraction
        except Exception:
            pass


class ControlHandler(BaseHTTPRequestHandler):
    cycle: AttackCycle = None  # gesetzt von build_control_server()

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # weniger Rauschen

    def _write_json(self, status: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/status":
            self._write_json(200, self.cycle.status())
        else:
            self._write_json(404, {"ok": False, "reason": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/queue_send":
            self._write_json(404, {"ok": False, "reason": "not_found"})
            return
        length = int(self.headers.get("content-length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        level = parse_send_level(raw.decode("utf-8") or "{}")
        if level is None:
            self._write_json(400, {"ok": False, "reason": "invalid_request"})
            return
        status, payload = self.cycle.buy(level)
        self._write_json(status, payload)


def build_control_server(bind: str, port: int, cycle: AttackCycle) -> ThreadingHTTPServer:
    handler_cls = type("BoundControlHandler", (ControlHandler,), {"cycle": cycle})
    return ThreadingHTTPServer((bind, port), handler_cls)


def run(
    cycle: AttackCycle,
    control_bind: str,
    control_port: int,
    poll_interval: float = 1.0,
    once: bool = False,
    _sleep: Callable[[float], None] = time.sleep,
) -> AttackCycle:
    httpd = build_control_server(control_bind, control_port, cycle)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    resolver = threading.Thread(target=cycle._resolver_loop, daemon=True)
    resolver.start()
    print(
        f"[attack-cycle] control auf http://{control_bind}:{control_port} (GET /status, POST /queue_send)",
        flush=True,
    )
    try:
        while True:
            cycle.step()
            cycle.sync_interval()
            cycle.sync_difficulty_interval()
            cycle.sync_reset()
            cycle.sync_personas()
            cycle.sync_natural_attack_rules()
            cycle.push_status()
            if once:
                break
            _sleep(poll_interval)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        thread.join(timeout=2.0)
    return cycle


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="RBBattle Attack-Cycle (PoC)")
    p.add_argument(
        "--bridge-url",
        default=os.environ.get("RBB_BRIDGE_URL") or DEFAULT_BRIDGE_URL,
        help="Bridge-Basis-URL (ohne Pfad; Default: RBB_BRIDGE_URL oder http://127.0.0.1:9001)",
    )
    p.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL_S, help="Zyklus-Intervall in Sekunden (Default 420 = 7 min)"
    )
    p.add_argument(
        "--difficulty-interval",
        type=float,
        default=DEFAULT_DIFFICULTY_INTERVAL_FIRST_S,
        help=(
            "Dauer des ERSTEN Difficulty-Schritts (1→2) in Sekunden "
            "(Default 200)."
        ),
    )
    p.add_argument(
        "--difficulty-interval-subsequent",
        type=float,
        default=DEFAULT_DIFFICULTY_INTERVAL_SUBSEQUENT_S,
        help=(
            "Dauer jedes Folge-Schritts (2→3 … 8→9) in Sekunden (Default 600)."
        ),
    )
    p.add_argument(
        "--difficulty-schedule",
        default=None,
        help=(
            "Komma-separierte Schrittdauern (1→2,2→3,…8→9) als volle Kurve; "
            "ueberschreibt --difficulty-interval/--difficulty-interval-subsequent. "
            "Default: Base-Game-Kurve 200,600,600,600,600,600,600,600."
        ),
    )
    p.add_argument("--max-level", type=int, default=DEFAULT_MAX_LEVEL, help="Max. natuerliches Level (Default 9)")
    p.add_argument(
        "--difficulty-profile",
        choices=sorted(DIFFICULTY_RULES.keys()),
        default=DEFAULT_DIFFICULTY_PROFILE,
        help=(
            "Wellen-Feuer-Profil (Attack-Count/Boss/Extra/MP, s. DIFFICULTY_RULES). "
            "Default: default."
        ),
    )
    p.add_argument(
        "--persona",
        default=None,
        help="Name der aktiven Persona (Default none = nur Natural Waves); braucht --persona-file",
    )
    p.add_argument(
        "--persona-file",
        default=os.environ.get("RBB_PERSONA_FILE"),
        help="Pfad zur personas.json (Default: RBB_PERSONA_FILE)",
    )
    p.add_argument(
        "--send-yourself",
        choices=["on", "off"],
        default="on",
        help="Eigene Kaeufe lokal feuern (on, Default) oder nur tracken/ins Leere (off)",
    )
    p.add_argument("--control-bind", default="0.0.0.0", help="Bind-Adresse des Control-Servers")
    p.add_argument(
        "--control-port", type=int, default=DEFAULT_CONTROL_PORT, help="Port des Control-Servers (Default 9102)"
    )
    p.add_argument("--poll-interval", type=float, default=1.0)
    p.add_argument("--timeout", type=float, default=30.0, help="HTTP-Timeout je Request")
    p.add_argument("--once", action="store_true", help="Einen Poll ausfuehren, dann beenden")
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)

    persona = None
    persona_name = ""
    if args.persona:
        if not args.persona_file:
            print("[attack-cycle] Fehler: --persona braucht --persona-file (oder RBB_PERSONA_FILE)", flush=True)
            return 2
        try:
            personas = load_personas(args.persona_file)
        except (OSError, ValueError) as e:
            print(f"[attack-cycle] Fehler beim Laden der Persona-Datei: {e}", flush=True)
            return 2
        if args.persona not in personas:
            print(f"[attack-cycle] Fehler: unbekannte Persona '{args.persona}'", flush=True)
            return 2
        persona = personas[args.persona]
        persona_name = args.persona

    difficulty_schedule = None
    if args.difficulty_schedule:
        try:
            difficulty_schedule = [float(x) for x in args.difficulty_schedule.split(",") if x.strip()]
        except ValueError:
            print(
                f"[attack-cycle] Fehler: --difficulty-schedule '{args.difficulty_schedule}' "
                "ist keine komma-separierte Zahlenliste",
                flush=True,
            )
            return 2

    cycle = AttackCycle(
        args.bridge_url,
        interval_s=args.interval,
        difficulty_interval_first_s=args.difficulty_interval,
        difficulty_interval_subsequent_s=args.difficulty_interval_subsequent,
        difficulty_schedule=difficulty_schedule,
        max_level=args.max_level,
        persona=persona,
        persona_name=persona_name,
        send_yourself=(args.send_yourself == "on"),
        difficulty_profile=args.difficulty_profile,
        timeout=args.timeout,
    )
    run(cycle, args.control_bind, args.control_port, poll_interval=args.poll_interval, once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
