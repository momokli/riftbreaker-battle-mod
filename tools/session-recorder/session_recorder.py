#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-session-recorder — persistente Session-Mitschnitte (Issue #280).

Tailt den Lua-Log des Dedicated-Servers (``exor_logs.txt`` im Wine-Prefix) und
schreibt JEDE ``[RBBATTLE] event=...``-Zeile als JSONL fort — pro Spiel-Session
eine Datei. Damit sind Metriken (Session-Dauer, Wave-Count, Economy, HQ-Death,
Player-Join, Match START/END) nach einem Container-Restart nicht verloren und
vom Host aus query-bar (``tail``/``grep``/``jq``).

Kernpunkte:
  * **Session-Boundaries:** eine Session öffnet mit dem ersten Event (typisch
    ``event=mod_load``/``event=setup``, spätestens ``event=commence``) und
    schließt mit ``event=match_end``. Jede Session bekommt eine eigene ID
    (UTC-Zeitstempel + Zufallssuffix). ``event=hq_dead`` wird als Event
    mitgeschrieben (Metrik HQ-Death), ist aber keine Session-Grenze.
  * **Restart-fest:** Cursor (Dateioffset) + aktiver Session-Zustand liegen im
    Ausgabeverzeichnis. Ein Container-Restart (Log wird neu/leer geschrieben)
    ergibt keinen Verlust: erkannte Truncation liest ab Offset 0, die offene
    Session wird fortgeführt, bereits geschriebene JSONL bleibt unangetastet.
  * **Leichte Aggregation:** je Session eine ``<id>.summary.json``
    (Dauer, Event-Count, Waves, HQ-HP-Minimum, HQ-Death).

Nur Standardbibliothek (läuft im Sidecar ``python:3.12-slim`` und als
Host-Werkzeug). Keine Spiel-Änderung, kein Mod-Release nötig.
"""

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone

STATE_FILE = ".state.json"
INDEX_FILE = "index.jsonl"

LOG_MARKER = "[RBBATTLE]"
RE_MARKER = re.compile(r"\[\s*RBBATTLE\s*\]\s*(?P<rest>.*)$")
RE_EVENT = re.compile(r"\bevent=(?P<event>\S+)")
RE_FIELD = re.compile(r"\b(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<val>[^\s\"]+)")
RE_INT = re.compile(r"^-?\d+$")
# Player-JOIN-Signatur (Muster tools/solo-feed/feed.py, #157).
RE_JOIN_CREATE = re.compile(r"OnNetPlayerCreateRequest\s+'[^']*':'([^']+)'")
RE_JOIN_PLAYER = re.compile(r"ServerGameplayState: Player '[^']*':'([^']+)'")


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def default_session_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:4]


def parse_event_fields(rest):
    """Zerlegt den Text nach ``[RBBATTLE]`` in Event-Name + key=value-Felder."""
    m = RE_EVENT.search(rest)
    if not m:
        return None, {}
    fields = {}
    for fm in RE_FIELD.finditer(rest):
        if fm.group("key") == "event":
            continue  # Event-Name steckt bereits in `event`
        fields[fm.group("key")] = fm.group("val")
    return m.group("event"), fields


def classify(line, player_events=True):
    """Eine Log-Zeile -> Record-Dict (ohne Session/seq) oder ``None``.

    `[RBBATTLE]`-Zeilen ergeben ``event=<name>``; optional werden Player-JOIN-
    Zeilen als ``event=player_join`` mitgeschnitten (LEAVE hat aktuell keine
    verlässliche Log-Signatur → bewusst nicht geraten).
    """
    m = RE_MARKER.search(line)
    if m:
        event, fields = parse_event_fields(m.group("rest"))
        if not event:
            return None
        return {"event": event, "fields": fields, "raw": line.rstrip("\n")}
    if player_events:
        jm = RE_JOIN_CREATE.search(line) or RE_JOIN_PLAYER.search(line)
        if jm:
            return {
                "event": "player_join",
                "fields": {"player": jm.group(1)},
                "raw": line.rstrip("\n"),
            }
    return None


def _as_int(value):
    if value is None:
        return None
    return int(value) if RE_INT.match(str(value)) else None


class SessionRecorder:
    """Session-Zustandsmaschine + JSONL-Ausgabe (deterministisch testbar)."""

    def __init__(self, out_dir, log_names=None, player_events=True,
                 clock=utc_now_iso, id_factory=default_session_id):
        self.out_dir = out_dir
        self.log_names = list(log_names or [])
        self.player_events = player_events
        self._clock = clock
        self._id_factory = id_factory
        os.makedirs(out_dir, exist_ok=True)
        self._state_path = os.path.join(out_dir, STATE_FILE)
        self._state = {"active": None, "cursors": {}}
        self._fh = None
        self._load_state()

    # -- Zustand -----------------------------------------------------------
    def _load_state(self):
        if not os.path.isfile(self._state_path):
            return
        try:
            with open(self._state_path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
        except (OSError, ValueError):
            return
        if isinstance(loaded, dict):
            self._state = {"active": loaded.get("active"), "cursors": loaded.get("cursors", {}) or {}}
        active = self._state.get("active")
        if active:
            self._open_file(active["session_id"], mode="a")

    def _save_state(self):
        tmp = self._state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._state, fh, sort_keys=True)
        os.replace(tmp, self._state_path)

    def _open_file(self, session_id, mode="a"):
        if self._fh is not None:
            self._fh.close()
        self._fh = open(os.path.join(self.out_dir, session_id + ".jsonl"), mode, encoding="utf-8")

    def _ensure_fh(self):
        """Dateihandle der aktiven Session offen halten (auch nach close())."""
        active = self._state.get("active")
        if self._fh is None and active:
            self._open_file(active["session_id"], mode="a")

    def _write_record(self, record):
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    # -- Sessions ----------------------------------------------------------
    @property
    def active_session_id(self):
        active = self._state.get("active")
        return active["session_id"] if active else None

    def get_cursor(self, path):
        return self._state["cursors"].get(path)

    def set_cursor(self, path, offset, size):
        self._state["cursors"][path] = {"offset": offset, "size": size}

    def _ensure_session(self):
        active = self._state.get("active")
        if active:
            return active
        sid = self._id_factory()
        active = {
            "session_id": sid,
            "started_ts": self._clock(),
            "seq": 0,
            "event_count": 0,
            "waves": 0,
            "commence": 0,
            "hq_hp_min": None,
            "hq_dead": False,
            "first_event_ts": None,
            "last_event_ts": None,
        }
        self._state["active"] = active
        self._open_file(sid, mode="a")
        self._write_record({
            "type": "session_start",
            "session_id": sid,
            "ts": active["started_ts"],
            "source_logs": self.log_names,
        })
        self._save_state()
        return active

    def _append_event(self, rec):
        active = self._ensure_session()
        self._ensure_fh()
        active["seq"] += 1
        ts = self._clock()
        active["event_count"] += 1
        active["last_event_ts"] = ts
        if active["first_event_ts"] is None:
            active["first_event_ts"] = ts
        fields = rec.get("fields", {})
        if rec["event"] == "wave" and fields.get("status") == "start":
            active["waves"] += 1
        if rec["event"] == "commence":
            active["commence"] += 1
        if rec["event"] == "hq_hp":
            hp = _as_int(fields.get("hp"))
            if hp is not None and (active["hq_hp_min"] is None or hp < active["hq_hp_min"]):
                active["hq_hp_min"] = hp
        if rec["event"] == "hq_dead":
            active["hq_dead"] = True
        self._write_record({
            "type": "event",
            "session_id": active["session_id"],
            "seq": active["seq"],
            "ts": ts,
            "event": rec["event"],
            "fields": fields,
            "raw": rec["raw"],
        })

    def finalize_session(self, reason="match_end"):
        active = self._state.get("active")
        if not active:
            return None
        self._ensure_fh()
        ts = self._clock()
        sid = active["session_id"]
        self._write_record({
            "type": "session_end",
            "session_id": sid,
            "seq": active["seq"],
            "ts": ts,
            "reason": reason,
            "started_ts": active["started_ts"],
            "event_count": active["event_count"],
        })
        summary = {
            "session_id": sid,
            "started_ts": active["started_ts"],
            "ended_ts": ts,
            "duration_s": _duration_s(active["started_ts"], ts),
            "event_count": active["event_count"],
            "waves": active["waves"],
            "commence": active["commence"],
            "hq_hp_min": active["hq_hp_min"],
            "hq_dead": active["hq_dead"],
            "end_reason": reason,
            "jsonl": sid + ".jsonl",
        }
        with open(os.path.join(self.out_dir, sid + ".summary.json"), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        with open(os.path.join(self.out_dir, INDEX_FILE), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(summary, ensure_ascii=False, sort_keys=True) + "\n")
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        self._state["active"] = None
        self._save_state()
        return summary

    # -- Verarbeitung ------------------------------------------------------
    def feed(self, line):
        rec = classify(line, self.player_events)
        if rec is None:
            return None
        if rec["event"] == "match_end":
            # Einzige Session-Grenze: match_end schließt die offene Session.
            # Ohne offene Session (z.B. Mitschnitt startet mitten im Match)
            # gibt es nichts zu schließen → ignorieren statt Phantom-Session.
            if self._state.get("active") is None:
                return None
            self._append_event(rec)
            reason = rec.get("fields", {}).get("reason") or "match_end"
            self.finalize_session(reason=reason)
            return rec
        # Setup-Events vor dem ersten Match öffnen die Session.
        self._ensure_session()
        self._append_event(rec)
        self._save_state()
        return rec

    def close(self):
        if self._fh is not None:
            self._fh.flush()
            self._fh.close()
            self._fh = None
        self._save_state()


def _duration_s(start_iso, end_iso):
    try:
        start = datetime.strptime(start_iso, "%Y-%m-%dT%H:%M:%S.%fZ")
        end = datetime.strptime(end_iso, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return None
    return round((end - start).total_seconds(), 3)


class LogTailer:
    """Liest neue Zeilen aus Logdateien, robust gegen Append/Truncation/Rotation."""

    def __init__(self, recorder, paths, from_start=True):
        self.recorder = recorder
        self.paths = list(paths)
        self.from_start = from_start
        self._pending = {p: b"" for p in self.paths}

    def _cursor_for(self, path):
        current = self._size(path)
        prev = self.recorder.get_cursor(path)
        if prev is None:
            return 0 if self.from_start else current
        if current < int(prev.get("offset", 0)):
            # Datei wurde kleiner → Truncation/Rotation (neuer Serverlauf).
            self._pending[path] = b""
            return 0
        return int(prev.get("offset", 0))

    @staticmethod
    def _size(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def poll_once(self):
        processed = 0
        for path in self.paths:
            if not os.path.isfile(path):
                continue
            offset = self._cursor_for(path)
            try:
                with open(path, "rb") as fh:
                    fh.seek(offset)
                    data = fh.read()
            except OSError:
                continue
            if not data:
                continue
            # Unvollständige Schlusszeile bleibt im Puffer (kein Offset-Sprung).
            data = self._pending.get(path, b"") + data
            idx = data.rfind(b"\n") + 1
            complete, self._pending[path] = data[:idx], data[idx:]
            consumed = offset + len(complete)
            for line in complete.decode("utf-8", errors="replace").splitlines():
                if self.recorder.feed(line) is not None:
                    processed += 1
            self.recorder.set_cursor(path, consumed, self._size(path))
            self.recorder._save_state()
        return processed


def default_log_paths(wine_prefix, wine_user):
    base = os.path.join(wine_prefix, "drive_c", "users", wine_user)
    return [
        os.path.join(base, "Documents", "The Riftbreaker", "exor_logs.txt"),
        os.path.join(base, "AppData", "LocalLow", "The Riftbreaker - Dedicated Server", "exor_logs.txt"),
    ]


def run(paths, out_dir, once=False, poll_interval=1.0, player_events=True, from_start=True,
        stop_after=None, clock=utc_now_iso, id_factory=default_session_id, _sleep=time.sleep):
    recorder = SessionRecorder(out_dir, log_names=paths, player_events=player_events,
                               clock=clock, id_factory=id_factory)
    tailer = LogTailer(recorder, paths, from_start=from_start)
    idle_rounds = 0
    try:
        while True:
            processed = tailer.poll_once()
            if processed:
                idle_rounds = 0
            else:
                idle_rounds += 1
            if once:
                break
            if stop_after is not None and idle_rounds >= stop_after:
                break
            _sleep(poll_interval)
    finally:
        recorder.close()
    return recorder


def build_parser():
    p = argparse.ArgumentParser(description="RBBattle Session-Recorder (Issue #280)")
    p.add_argument("--log", action="append", default=[], metavar="PATH",
                   help="Logdatei (mehrfach). Ohne Angabe aus --wine-prefix/--wine-user abgeleitet.")
    p.add_argument("--wine-prefix", default="/data/.wine", help="Wine-Prefix (Default: /data/.wine)")
    p.add_argument("--wine-user", default="steamuser", help="Wine-User (Default: steamuser)")
    p.add_argument("--out-dir", default="/data/sessions", help="Zielverzeichnis (JSONL)")
    p.add_argument("--from-end", action="store_true",
                   help="Beim ersten Start nur neue Zeilen lesen (Default: von Anfang an)")
    p.add_argument("--no-player-events", action="store_true", help="Player-JOIN-Zeilen nicht mitschneiden")
    p.add_argument("--once", action="store_true", help="Verfügbare Zeilen verarbeiten, dann beenden")
    p.add_argument("--poll-interval", type=float, default=1.0)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    paths = args.log or default_log_paths(args.wine_prefix, args.wine_user)
    run(paths, args.out_dir, once=args.once, poll_interval=args.poll_interval,
        player_events=not args.no_player_events, from_start=not args.from_end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
