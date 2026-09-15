#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""referee_egress.py — Spiel-Log → Referee (Egress, Issue #358 / #268).

Tailt ``exor_logs.txt`` des Dedicated-Servers, parst ``[RBBATTLE]``-Zeilen und
postet die für den Referee relevanten Events an ``POST /referee/event``. Damit
wird der Tournament-Server die autoritative State-Quelle („Solo online", #268):

    exor_logs.txt  ([RBBATTLE]-Zeilen, vom Lua-Mod geschrieben)
        -> Tail + Parse
        -> map_referee_event():
             event=mod_load|setup            -> {"world": W, "type": "ready"}
             event=wave level=N status=done  -> {"world": W, "type": "wave_done", "level": N}
             event=hq_dead                   -> {"world": W, "type": "hq_destroyed"}
        -> POST {server}/referee/event   (Retry/Backoff bei Netzfehlern/5xx)

Bewusst NUR Egress (Spiel → Referee). Der Ingress (Referee → Spiel, `exec`) läuft
über die bestehende Wine-x64-Bridge ``pipe_bridge.exe`` (HTTP :9001, #265); der
Referee entscheidet aus den Events die Commands und legt sie in die Outbox
(``GET /referee/poll``) — der Ingress bleibt unverändert.

Nur Standardbibliothek (läuft im Sidecar ``python:3.12-slim`` und als
Host-Werkzeug). Kein Windows-Bezug, keine Named Pipe.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

LOG_MARKER = "[RBBATTLE]"
RE_MARKER = re.compile(r"\[\s*RBBATTLE\s*\]\s*(?P<rest>.*)$")
RE_EVENT = re.compile(r"\bevent=(?P<event>\S+)")
RE_FIELD = re.compile(r"\b(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<val>[^\s\"']+)")

# Event-Typen, die "Executor bereit" bedeuten (Map geladen / Setup läuft).
# Spiegelt REFEREE_READY_TYPES aus bausteine/07-relay/relay.py (#268).
REFEREE_READY_TYPES = frozenset(["mod_load", "setup"])

HTTP_TIMEOUT = 5.0
BACKOFF_BASE = 1.0
BACKOFF_MAX = 30.0
TAIL_POLL_S = 0.5
QUEUE_MAX = 10000


def parse_rbbattle(line):
    """Eine Log-Zeile -> ``(event, fields)`` oder ``None``.

    ``event`` = Wert von ``event=``; ``fields`` = dict der übrigen
    ``key=value``-Tokens (Werte bleiben Strings; der Mapper castet gezielt).
    """
    m = RE_MARKER.search(line)
    if not m:
        return None
    rest = m.group("rest")
    em = RE_EVENT.search(rest)
    if not em:
        return None
    fields = {}
    for fm in RE_FIELD.finditer(rest):
        key = fm.group("key")
        if key == "event":
            continue
        fields[key] = fm.group("val")
    return em.group("event"), fields


def map_referee_event(event, fields, world="A"):
    """``(event, fields)`` -> Referee-Event-Dict oder ``None`` (spiegelt relay.py).

    - ``wave`` + ``status=done`` + ``level`` -> ``wave_done`` (level int)
    - ``hq_dead`` -> ``hq_destroyed``
    - ``mod_load`` / ``setup`` -> ``ready``
    """
    if event == "wave":
        if str(fields.get("status", "")) != "done":
            return None
        level = fields.get("level")
        if level is None:
            return None
        try:
            level = int(level)
        except (TypeError, ValueError):
            return None
        return {"world": world, "type": "wave_done", "level": level}
    if event == "hq_dead":
        return {"world": world, "type": "hq_destroyed"}
    if event in REFEREE_READY_TYPES:
        return {"world": world, "type": "ready"}
    return None


class RefereeEgress:
    """Tailt eine Logdatei und postet Referee-Events (robust gegen Rotation)."""

    def __init__(self, path, server, world="A", from_start=True,
                 env="unknown", ref="unknown"):
        self.path = path
        self.server = server.rstrip("/")
        self.world = world
        self.from_start = from_start
        # Deploy-Identitaet (Issue #483, US4): dieselbe <env> · <ref> wie
        # Landing/Tournament/Server-Control — je gepostetem Event mitgeschickt.
        self.env = env
        self.ref = ref
        self._offset = 0 if from_start else self._size()
        self._pending = b""

    @staticmethod
    def _size(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _post(self, event):
        body = json.dumps(event).encode("utf-8")
        req = urllib.request.Request(
            self.server + "/referee/event",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")

    def _deliver(self, event, attempt=0):
        """POST mit Retry/Backoff; 4xx wird verworfen (Konfigurationsfehler)."""
        while True:
            try:
                status, _ = self._post(event)
                if 200 <= status < 300:
                    return True
                # 3xx/4xx: kein Retry
                log("egress: HTTP {} verworfen: {}".format(status, event))
                return False
            except urllib.error.HTTPError as e:
                if e.code >= 500:
                    delay = min(BACKOFF_MAX, BACKOFF_BASE * (2**attempt))
                    log("egress: HTTP {} retry in ~{}s: {}".format(e.code, delay, event))
                    time.sleep(delay)
                    attempt += 1
                else:
                    log("egress: HTTP {} verworfen: {}".format(e.code, event))
                    return False
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                delay = min(BACKOFF_MAX, BACKOFF_BASE * (2**attempt))
                log("egress: netzfehler ({}) retry in ~{}s: {}".format(e, delay, event))
                time.sleep(delay)
                attempt += 1

    def poll_once(self):
        """Liest neue Zeilen und liefert die verarbeiteten Referee-Events."""
        delivered = 0
        if not os.path.isfile(self.path):
            return 0
        size = self._size(self.path)
        if size < self._offset:
            # Rotation/Truncation: von vorn lesen.
            self._offset = 0
            self._pending = b""
        if size <= self._offset:
            return 0
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._offset)
                data = fh.read()
        except OSError:
            return 0
        self._offset += len(data)
        data = self._pending + data
        idx = data.rfind(b"\n") + 1
        complete, self._pending = data[:idx], data[idx:]
        for line in complete.decode("utf-8", errors="replace").splitlines():
            parsed = parse_rbbattle(line)
            if parsed is None:
                continue
            event, fields = parsed
            rev = map_referee_event(event, fields, self.world)
            if rev is None:
                continue
            # Deploy-Identitaet (Issue #483, US4): "je JSONL-/Event-Record". Die
            # bestehenden Felder (world/type/level) bleiben unveraendert.
            rev = dict(rev, env=self.env, ref=self.ref)
            log("egress: {} -> {}".format(event, rev))
            if self._deliver(rev):
                delivered += 1
        return delivered

    def run(self, poll_interval=TAIL_POLL_S, once=False):
        while True:
            try:
                self.poll_once()
            except Exception as e:  # Fehlerisolierung: Tail läuft weiter
                log("egress: unerwarteter Fehler: {}".format(e))
            if once:
                break
            time.sleep(poll_interval)


def log(msg):
    sys.stdout.write("[egress] " + msg + "\n")
    sys.stdout.flush()


def default_log_paths(wine_prefix, wine_user):
    base = os.path.join(wine_prefix, "drive_c", "users", wine_user)
    return [
        os.path.join(base, "Documents", "The Riftbreaker", "exor_logs.txt"),
        os.path.join(base, "AppData", "LocalLow", "The Riftbreaker - Dedicated Server", "exor_logs.txt"),
    ]


def build_parser():
    p = argparse.ArgumentParser(description="RBBattle Referee-Egress (Issue #358/#268)")
    p.add_argument(
        "--log",
        action="append",
        default=[],
        metavar="PATH",
        help="Logdatei (mehrfach). Ohne Angabe aus --wine-prefix/--wine-user abgeleitet.",
    )
    p.add_argument("--wine-prefix", default="/data/.wine")
    p.add_argument("--wine-user", default="steamuser")
    p.add_argument("--server", default=os.environ.get("RBB_SERVER", "http://127.0.0.1:8081"))
    p.add_argument("--world", default=os.environ.get("RBB_WORLD", "A"))
    p.add_argument(
        "--from-end", action="store_true", help="Beim ersten Start nur neue Zeilen lesen (Default: von Anfang an)"
    )
    p.add_argument("--once", action="store_true", help="Verfügbare Zeilen verarbeiten, dann beenden")
    # Deploy-Identitaet (Issue #483, US4): je Referee-Event mitgeschickt. Die
    # Compose-Templates setzen RBB_ENV/RBB_REF (Default).
    p.add_argument("--env", default=os.environ.get("RBB_ENV") or "unknown",
                   help="Env der Deploy-Identitaet (Default: RBB_ENV oder unknown)")
    p.add_argument("--ref", default=os.environ.get("RBB_REF") or "unknown",
                   help="Ref der Deploy-Identitaet (Default: RBB_REF oder unknown)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    world = args.world.strip().upper()
    if world not in ("A", "B"):
        print("[egress] Fehler: --world muss A oder B sein (ist: {!r}).".format(world), file=sys.stderr)
        return 2
    paths = args.log or default_log_paths(args.wine_prefix, args.wine_user)
    feeders = [RefereeEgress(p, args.server, world=world, from_start=not args.from_end,
                             env=args.env, ref=args.ref) for p in paths]
    log("start: server={} world={} env={} ref={} logs={}".format(
        args.server, world, args.env, args.ref, paths))
    if args.once:
        for f in feeders:
            f.run(once=True)
        return 0
    # Mehrere Kandidat-Pfade: nur der erste existierende läuft dauerhaft;
    # die anderen poll_once() sind No-Ops, solange die Datei fehlt. Einfach alle
    # im Round-Robin pollen.
    while True:
        for f in feeders:
            f.poll_once()
        time.sleep(TAIL_POLL_S)
    return 0


if __name__ == "__main__":
    sys.exit(main())
