#!/usr/bin/env python3
"""Carbonium-Write-Case (PoC #373): runter bis 0, dann rauf bis max.

Demonstriert den WRITE-Kanal des Dedicated IO Interface end-to-end:
POST /get_state + POST /add_resource der IO-Bridge (rbbridge.dll ->
PlayerService::AddResourceAmount), ohne Lua/Log.

Ablauf:
  1) runter:  -STEP je INTERVAL Sekunde, bis carbonium == 0 (Klemmung).
  2) rauf:    +STEP je INTERVAL Sekunde, bis max (kein Zuwachs mehr -> Kappung).

Konfiguration ueber Umgebungsvariablen:
  RBB_BRIDGE_URL  Bridge-Basis-URL (Default http://127.0.0.1:9001, planet).
  RBB_STEP        Schrittweite in Carbonium (Default 10).
  RBB_INTERVAL    Abstand in Sekunden (Default 1).

Werte sind int64-Fixed-Point x10^6; die Ausgabe zeigt Rohwert und
Display-Wert (Carbonium, auf 1 Nachkommastelle).
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

URL = os.environ.get("RBB_BRIDGE_URL", "http://127.0.0.1:9001")
STEP = float(os.environ.get("RBB_STEP", "10"))
INTERVAL = float(os.environ.get("RBB_INTERVAL", "1"))
SCALE = 1_000_000.0


def post(path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else b""
    req = urllib.request.Request(URL + path, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def carbonium():
    return int(post("/get_state")["carbonium"])


def add(amount):
    return post("/add_resource", {"amount": str(amount)})


def fmt(raw):
    return "%d (%.1f)" % (raw, raw / SCALE)


def main():
    try:
        cur = carbonium()
    except (urllib.error.URLError, KeyError, ValueError) as exc:
        print("Fehler: Bridge %s nicht erreichbar/lesbar (%s)" % (URL, exc))
        return 1

    print("start:  carbonium=%s" % fmt(cur))

    # Phase 1: runter bis 0.
    while cur > 0:
        r = add(-STEP)
        time.sleep(INTERVAL)
        nxt = carbonium()
        print("down   %12d -> %12d  (add %g, ret=%s)" % (cur, nxt, -STEP, r.get("ret")))
        if nxt >= cur:  # Klemmung/kein Fortschritt -> Sicherheitsstop
            break
        cur = nxt

    print("bottom: carbonium=%s" % fmt(cur))

    # Phase 2: rauf bis max (kappt automatisch, kein Zuwachs mehr).
    while True:
        r = add(STEP)
        time.sleep(INTERVAL)
        nxt = carbonium()
        print("up     %12d -> %12d  (add %g, ret=%s)" % (cur, nxt, STEP, r.get("ret")))
        if nxt <= cur:  # max erreicht (Wert waechst nicht mehr)
            print("max:    carbonium=%s" % fmt(nxt))
            break
        cur = nxt

    print("done:   carbonium=%s" % fmt(carbonium()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
