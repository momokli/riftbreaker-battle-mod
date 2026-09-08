#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tail_events.py - Live-Tail auf exor_logs.txt, filtert [RBBATTLE]-Zeilen,
gibt sie als JSON aus (Baustein 03: Log-Bridge).

Nur Standardbibliothek (Python 3.7+), kein pip-Paket noetig.

Verwendung:
    python tail_events.py                      # Standardpfad: <USERPROFILE>\\Documents\\The Riftbreaker\\exor_logs.txt
    python tail_events.py --log "D:\\pfad\\exor_logs.txt"

Ausgabeformat (eine Zeile je Ereignis):
    {"event": "bridge_test", "run": "1", "status": "start"}
Zeilen ohne key=value-Paare (z. B. "[RBBATTLE] skeleton ok"):
    {"raw": "skeleton ok"}

Log-Rotation (6 Dateien): wird erkannt (Datei schrumpft), Tail beginnt in
der neuen Datei bei 0.
"""

import argparse
import json
import os
import sys
import time

POLL_S = 0.5


def default_log_path():
    base = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(base, "Documents", "The Riftbreaker", "exor_logs.txt")


def parse_line(line):
    """Wandelt eine Log-Zeile in ein JSON-faehiges dict um.

    "[RBBATTLE] event=wave level=3 status=done spawned=5 skipped=0"
        -> {"event": "wave", "level": "3", "status": "done", "spawned": "5", "skipped": "0"}
    "[RBBATTLE] skeleton ok" -> {"raw": "skeleton ok"}

    Liefert None, wenn die Zeile kein [RBBATTLE]-Praefix traegt.
    """
    line = line.strip()
    idx = line.find("[RBBATTLE]")
    if idx == -1:
        return None
    rest = line[idx + len("[RBBATTLE]"):].strip()
    if not rest:
        return None

    obj = {}
    for token in rest.split():
        if "=" in token:
            key, _, value = token.partition("=")
            if key:
                obj[key] = value
        else:
            # Nacktes Token ohne '=': nur als raw ansehen, wenn die Zeile
            # komplett ohne key=value-Paare ist (z. B. "skeleton ok").
            if not obj:
                return {"raw": rest}
    return obj or {"raw": rest}


def tail(path, out):
    pos = 0
    while True:
        try:
            size = os.path.getsize(path)
        except OSError:
            # Log existiert noch nicht (Spiel laeuft nicht / noch nie geloggt).
            time.sleep(POLL_S)
            continue

        if size < pos:
            # Log-Rotation: Datei wurde ersetzt -> von vorn lesen.
            pos = 0

        if size > pos:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                for raw in f:
                    parsed = parse_line(raw)
                    if parsed is not None:
                        out(json.dumps(parsed, ensure_ascii=False))
                pos = f.tell()

        time.sleep(POLL_S)


def main():
    ap = argparse.ArgumentParser(description="Live-Tail exor_logs.txt -> [RBBATTLE]-Zeilen als JSON")
    ap.add_argument("--log", default=default_log_path(),
                    help="Pfad zur exor_logs.txt (Default: %%USERPROFILE%%\\Documents\\The Riftbreaker\\exor_logs.txt)")
    args = ap.parse_args()

    # UTF-8-Ausgabe auch auf Windows-Konsolen (cp1252) erzwingen.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    print(f"[tail_events] beobachte: {args.log}", flush=True)
    print("[tail_events] warte auf [RBBATTLE]-Zeilen (Strg+C zum Beenden)...", flush=True)
    tail(args.log, lambda s: print(s, flush=True))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[tail_events] beendet.")
