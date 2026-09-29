#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chat-announcer — ereignis-/schwellenbasierter Chat-Ansager (Issue #940).

Eigener Sidecar analog ``attack-cycle`` / ``send-tailer``: **liest** den State
(Attack-Cycle ``GET /status``, Fallback Bridge ``GET /attack_status``) und
**entscheidet was/wann** in den In-Game-Chat geht. Der Chat ist append-only,
darum wird ausschliesslich beim **Schwellen-Crossing** gesendet — genau einmal
pro Schwelle und Epoche, **kein 1-Hz-Spam**.

Die Bridge bleibt IO-Primitive: Senden laeuft ueber
``POST /send_chat {"text","type","prefix"}`` (Issue #934).

Der Detektor-Kern :class:`Announcer` ist **rein** (kein IO, injizierbare
``status``/``now``) und liefert eine Liste von Event-Dicts. Die Formatierung ist
an **einer** Stelle gekapselt (:class:`FormatConfig` + ``fmt_*``-Funktionen) —
kein Format-Literal im Event-/Loop-Code.

Rein stdlib (HTTP via urllib).
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# --- Schwellen (Sekunden, absteigend) ----------------------------------------
DEFAULT_WARMUP_THRESHOLDS = (180, 120, 60, 30, 10)
DEFAULT_ATTACK_THRESHOLDS = (60, 30, 10)

# --- Event-Kinds --------------------------------------------------------------
KIND_WARMUP = "warmup"
KIND_WARMUP_GO = "warmup_go"
KIND_ATTACK_NEXT = "attack_next"
KIND_INCOMING = "incoming"
KIND_ROUND_END = "round_end"

# --- Rundenende-Gruende -------------------------------------------------------
REASON_HQ_DESTROYED = "hq_destroyed"
REASON_NO_HQ = "no_hq"

# --- Bridge-Chat-Typen (Pipe-Werte; die Bridge reicht den String durch) --------
CHAT_TYPE_SYSTEM = "system"
CHAT_TYPE_ANNOUNCEMENT = "announcement"


# =============================================================================
# Format-Kapselung (Story 2) — EINZIGE Stelle mit Format-Literalen
# =============================================================================
class FormatConfig:
    """Format-Variante. ``short`` (Default) ist die konservative, ASCII-nahe
    Kurzform ohne Dot-Leader.

    ``aligned`` ist ein **dokumentierter, ungeeichter Platzhalter**: erst nach
    dem Mess-Spike #939 (monospace? Zeilenlaenge/Wrap? Encoding?) umstellen.
    Die Kapselung stellt sicher, dass #939 nur diesen einen Block aendert —
    der Detektor-/Loop-Code enthaelt keine Format-Literale.
    """

    VALID_STYLES = ("short", "aligned")

    def __init__(self, style: str = "short") -> None:
        if style not in self.VALID_STYLES:
            raise ValueError(f"unbekannter style: {style!r} (erwartet {self.VALID_STYLES})")
        self.style = style


def _mmss(seconds: float) -> str:
    s = int(max(0, round(seconds)))
    return f"{s // 60}:{s % 60:02d}"


def fmt_warmup(fmt: FormatConfig, remaining: float) -> str:
    if fmt.style == "aligned":
        # UNGEEICHT (Mess-Spike #939): Dot-Leader-Platzhalter, bewusst nicht
        # als finales Format markiert.
        return f"warmup {'·' * 3} {_mmss(remaining)}"
    return f"warmup {_mmss(remaining)}"


def fmt_warmup_go(fmt: FormatConfig) -> str:
    if fmt.style == "aligned":
        return "== GO =="
    return "GO"


def fmt_attack_next(fmt: FormatConfig, remaining: float) -> str:
    if fmt.style == "aligned":
        return f"next attack {'·' * 3} {int(max(0, round(remaining)))}s"
    return f"next attack in {int(max(0, round(remaining)))}s"


def fmt_incoming(fmt: FormatConfig, counts: Dict[Any, int]) -> str:
    parts = [f"[W{lvl}]x{counts[lvl]}" for lvl in sorted(counts) if counts[lvl] > 0]
    body = " ".join(parts)
    if fmt.style == "aligned":
        return f"incoming {'·' * 3} {body}".rstrip()
    return f"incoming {body}".rstrip()


def fmt_round_end(fmt: FormatConfig, reason: str) -> str:
    label = {
        REASON_HQ_DESTROYED: "HQ destroyed",
        REASON_NO_HQ: "no HQ",
    }.get(reason, reason)
    if fmt.style == "aligned":
        return f"round over {'·' * 3} {label}"
    return f"round over - {label}"


# =============================================================================
# Incoming-Komposition (Story 4)
# =============================================================================
def incoming_counts(last_fire: Optional[Dict[str, Any]]) -> Dict[Any, int]:
    """Komponiert die Wellen-Zaehlung fuer ``incoming …`` aus ``last_fire``:
    naturale Welle (``natural_level``/``natural_count``) + ``persona_levels`` +
    ``sent_levels``. Liefert ``{level: count}``."""
    counts: Dict[Any, int] = {}
    if not isinstance(last_fire, dict):
        return counts

    def add(level: Any, n: int = 1) -> None:
        if level is None:
            return
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 1
        if n <= 0:
            return
        counts[level] = counts.get(level, 0) + n

    natural_n = last_fire.get("natural_count")
    add(last_fire.get("natural_level"), natural_n if natural_n else 1)
    for lvl in last_fire.get("persona_levels") or []:
        add(lvl)
    for lvl in last_fire.get("sent_levels") or []:
        add(lvl)
    return counts


# =============================================================================
# Detektor-Kern (Story 1/3/4) — rein, kein IO
# =============================================================================
def _resolve_threshold(remaining: float, thresholds, announced: set) -> Optional[float]:
    """Waehlt die **groesste ueberschrittene, noch nicht gemeldete** Schwelle
    (``remaining <= t``) und markiert **alle** ueberschrittenen Schwellen als
    gemeldet.

    Damit wird auch bei Poll-Aussetzern (mehrere Schwellen auf einmal
    ueberschritten) genau **eine** Nachricht pro Tick gesendet — die groesste
    — und es gibt keine Rueckstands-Flut in den Folgeticks. Unter normaler
    Kadenz ist ``crossed`` einelementig, jede Schwelle feuert genau einmal.
    """
    crossed = [t for t in thresholds if remaining <= t and t not in announced]
    if not crossed:
        return None
    t = max(crossed)
    announced.update(crossed)
    return t


class Announcer:
    """Schwellen-/Ereignis-Detektor. :meth:`step` ist rein und liefert eine
    Liste von Event-Dicts ``{"kind","text","type"}``; der Aufrufer sendet."""

    def __init__(
        self,
        fmt: Optional[FormatConfig] = None,
        warmup_thresholds=DEFAULT_WARMUP_THRESHOLDS,
        attack_thresholds=DEFAULT_ATTACK_THRESHOLDS,
        chat_type: str = CHAT_TYPE_SYSTEM,
    ) -> None:
        self.fmt = fmt or FormatConfig()
        self.warmup_thresholds = tuple(warmup_thresholds)
        self.attack_thresholds = tuple(attack_thresholds)
        self.chat_type = chat_type

        self._prev_state: Optional[str] = None
        self._attack_index: Optional[Any] = None
        self._warmup_announced: set = set()
        self._attack_announced: set = set()
        self._go_sent = False

    # --- Epochen ----------------------------------------------------------
    def _reset_warmup_epoch(self) -> None:
        self._warmup_announced.clear()
        self._go_sent = False

    def _reset_attack_epoch(self) -> None:
        self._attack_announced.clear()

    def _event(self, kind: str, text: str, chat_type: Optional[str] = None) -> Dict[str, str]:
        return {"kind": kind, "text": text, "type": chat_type or self.chat_type}

    # --- Kern -------------------------------------------------------------
    def step(self, status: Dict[str, Any], now: float) -> List[Dict[str, str]]:
        events: List[Dict[str, str]] = []
        status = status or {}
        state = status.get("state")
        prev = self._prev_state
        attack_index = status.get("attack_index")

        # --- Epochen-Reset (Warmup-Neu / attack_index-Sprung) ---------------
        if state == "warmup" and prev != "warmup":
            self._reset_warmup_epoch()
            self._reset_attack_epoch()

        ai_changed = attack_index is not None and attack_index != self._attack_index
        first_sight = self._attack_index is None

        if state == "paused":
            # Keine Sendung in paused; Buchhaltung dennoch nachziehen.
            if attack_index is not None:
                if ai_changed:
                    self._reset_attack_epoch()
                self._attack_index = attack_index
            self._prev_state = state
            return events

        if ai_changed:
            if not first_sight:
                self._reset_attack_epoch()
                # Eingehend nur bei ECHTEM Feuern (Erhoehung, running).
                new_ai, old_ai = attack_index, self._attack_index
                increased = isinstance(new_ai, (int, float)) and isinstance(old_ai, (int, float)) and new_ai > old_ai
                if increased and state == "running":
                    counts = incoming_counts(status.get("last_fire"))
                    if counts:
                        events.append(self._event(KIND_INCOMING, fmt_incoming(self.fmt, counts)))
            self._attack_index = attack_index

        # --- Warmup-Schwellen + GO -----------------------------------------
        if state == "warmup":
            remaining = status.get("seconds_to_warmup_end")
            if isinstance(remaining, (int, float)):
                if remaining > 0:
                    t = _resolve_threshold(remaining, self.warmup_thresholds, self._warmup_announced)
                    if t is not None:
                        events.append(self._event(KIND_WARMUP, fmt_warmup(self.fmt, t)))
                elif not self._go_sent:
                    self._go_sent = True
                    events.append(self._event(KIND_WARMUP_GO, fmt_warmup_go(self.fmt)))

        # --- GO am warmup->running-Edge ------------------------------------
        if state == "running" and prev == "warmup" and not self._go_sent:
            self._go_sent = True
            events.append(self._event(KIND_WARMUP_GO, fmt_warmup_go(self.fmt)))

        # --- Attack-Schwellen (running) ------------------------------------
        if state == "running":
            remaining = status.get("seconds_to_next_attack")
            if isinstance(remaining, (int, float)):
                t = _resolve_threshold(remaining, self.attack_thresholds, self._attack_announced)
                if t is not None:
                    events.append(self._event(KIND_ATTACK_NEXT, fmt_attack_next(self.fmt, t)))

        # --- Rundenende-Edge ------------------------------------------------
        if state == "game_over" and prev in ("warmup", "running"):
            reason = REASON_NO_HQ if prev == "warmup" else REASON_HQ_DESTROYED
            events.append(
                self._event(KIND_ROUND_END, fmt_round_end(self.fmt, reason), CHAT_TYPE_ANNOUNCEMENT)
            )

        self._prev_state = state
        return events


# =============================================================================
# HTTP-Loop / CLI (Story 5)
# =============================================================================
def _http_get(url: str, timeout: float = 5.0) -> Tuple[int, str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def _http_post_json(url: str, payload: Dict[str, Any], timeout: float = 5.0) -> Tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def _extract_status(raw: str) -> Optional[Dict[str, Any]]:
    """Parst eine Status-Antwort; akzeptiert das Objekt direkt oder unter
    ``status`` verschachtelt."""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    nested = data.get("status")
    if isinstance(nested, dict) and "state" in nested:
        return nested
    return data


class AnnouncerService:
    """Pollt den State und sendet Announcer-Events ueber die Bridge."""

    def __init__(
        self,
        attack_cycle_url: str,
        bridge_url: str,
        interval: float = 1.0,
        dry_run: bool = False,
        style: str = "short",
        chat_type: str = CHAT_TYPE_SYSTEM,
        timeout: float = 5.0,
        once: bool = False,
        _getter=_http_get,
        _poster=_http_post_json,
        _clock=time.time,
        _sleep=time.sleep,
    ) -> None:
        self.attack_cycle_url = attack_cycle_url.rstrip("/")
        self.bridge_url = bridge_url.rstrip("/")
        self.interval = interval
        self.dry_run = dry_run
        self.timeout = timeout
        self.once = once
        self._getter = _getter
        self._poster = _poster
        self._clock = _clock
        self._sleep = _sleep
        self.announcer = Announcer(fmt=FormatConfig(style), chat_type=chat_type)

    # --- State lesen ------------------------------------------------------
    def fetch_status(self) -> Optional[Dict[str, Any]]:
        """GET /status am Attack-Cycle; Fallback GET /attack_status an der Bridge."""
        for url in (f"{self.attack_cycle_url}/status", f"{self.bridge_url}/attack_status"):
            code, body = self._getter(url, self.timeout)
            if 200 <= code < 300:
                status = _extract_status(body)
                if status is not None:
                    return status
            else:
                print(f"[chat-announcer] GET {url}: HTTP {code} {body[:160]}", flush=True)
        return None

    # --- Senden -----------------------------------------------------------
    def send(self, event: Dict[str, str]) -> bool:
        text = event.get("text", "")
        if not text:
            return False
        if self.dry_run:
            print(f"[chat-announcer] DRY-RUN [{event.get('kind')}] {text}", flush=True)
            return True
        code, body = self._poster(
            f"{self.bridge_url}/send_chat",
            {"text": text, "type": event.get("type", CHAT_TYPE_SYSTEM), "prefix": ""},
            self.timeout,
        )
        if 200 <= code < 300:
            print(f"[chat-announcer] sent [{event.get('kind')}] {text}", flush=True)
            return True
        print(f"[chat-announcer] send_chat HTTP {code} {body[:160]} — weiter", flush=True)
        return False

    def poll_once(self) -> List[Dict[str, str]]:
        status = self.fetch_status()
        if status is None:
            print("[chat-announcer] kein Status (GET !=2xx) — weiter", flush=True)
            return []
        events = self.announcer.step(status, self._clock())
        for ev in events:
            self.send(ev)
        return events

    def run(self) -> None:
        while True:
            try:
                self.poll_once()
            except Exception as e:  # Loop darf nie sterben
                print(f"[chat-announcer] poll error: {e!r} — weiter", flush=True)
            if self.once:
                break
            self._sleep(self.interval)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="RBBattle Chat-Announcer (Issue #940)")
    p.add_argument(
        "--attack-cycle-url",
        default="http://127.0.0.1:9102",
        help="Attack-Cycle-Basis-URL (GET /status), Default http://127.0.0.1:9102",
    )
    p.add_argument(
        "--bridge-url",
        default="http://127.0.0.1:9001",
        help="Bridge-Basis-URL (POST /send_chat), Default http://127.0.0.1:9001",
    )
    p.add_argument("--interval", type=float, default=1.0, help="Poll-Intervall in Sekunden (Default 1.0)")
    p.add_argument("--dry-run", action="store_true", help="Nur loggen, nicht senden")
    p.add_argument("--style", default="short", choices=FormatConfig.VALID_STYLES, help="Format-Stil (Default short)")
    p.add_argument("--chat-type", default=CHAT_TYPE_SYSTEM, help="Bridge-Chat-Typ (Default system)")
    p.add_argument("--timeout", type=float, default=5.0, help="HTTP-Timeout je Request")
    p.add_argument("--once", action="store_true", help="Einmal pollen, dann beenden")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    service = AnnouncerService(
        attack_cycle_url=args.attack_cycle_url,
        bridge_url=args.bridge_url,
        interval=args.interval,
        dry_run=args.dry_run,
        style=args.style,
        chat_type=args.chat_type,
        timeout=args.timeout,
        once=args.once,
    )
    service.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())