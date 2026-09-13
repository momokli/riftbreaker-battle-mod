#!/usr/bin/env python3
"""Carbonium-Write-Case (PoC #373): runter bis min, rauf bis max, oszillierend.

Demonstriert den WRITE-Kanal des Dedicated IO Interface end-to-end:
POST /get_state + POST /add_resource der IO-Bridge (rbbridge.dll ->
PlayerService::AddResourceAmount), ohne Lua/Log.

Ablauf eines Zyklus:
  1) runter:  -STEP je INTERVAL Sekunde, bis min == 0 (Klemmung).
  2) rauf:    +STEP je INTERVAL Sekunde, bis max (Kappung, ret=False).

Konfiguration ueber Umgebungsvariablen:
  RBB_BRIDGE_URL  Bridge-Basis-URL (Default http://127.0.0.1:9001, planet).
  RBB_STEP        Schrittweite in Carbonium (Default 10).
  RBB_INTERVAL    Abstand in Sekunden (Default 1).
  RBB_CYCLES      Anzahl Down+Up-Zyklen (Default 1); "inf" = endlos oszillieren.

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


def drain(cur):
    """runter bis min (0); gibt den neuen Wert zurueck."""
    while cur > 0:
        r = add(-STEP)
        time.sleep(INTERVAL)
        nxt = carbonium()
        print("down   %12d -> %12d  (add %g, ret=%s)" % (cur, nxt, -STEP, r.get("ret")))
        if nxt >= cur:  # Klemmung/kein Fortschritt -> Sicherheitsstop
            break
        cur = nxt
    return cur


def fill(cur):
    """rauf bis max (kappt automatisch); gibt den neuen Wert zurueck."""
    while True:
        r = add(STEP)
        time.sleep(INTERVAL)
        nxt = carbonium()
        print("up     %12d -> %12d  (add %g, ret=%s)" % (cur, nxt, STEP, r.get("ret")))
        if nxt <= cur:  # max erreicht (Wert waechst nicht mehr)
            return nxt
        cur = nxt


def main():
    cycles = os.environ.get("RBB_CYCLES", "1")
    try:
        cur = carbonium()
    except (urllib.error.URLError, KeyError, ValueError) as exc:
        print("Fehler: Bridge %s nicht erreichbar/lesbar (%s)" % (URL, exc))
        return 1

    print("start:  carbonium=%s (cycles=%s, step=%g, interval=%gs)" % (fmt(cur), cycles, STEP, INTERVAL))

    if cycles == "inf":
        i = 0
        while True:
            i += 1
            print("== Zyklus %d ==" % i)
            cur = drain(cur)
            print("min:    carbonium=%s" % fmt(cur))
            cur = fill(cur)
            print("max:    carbonium=%s" % fmt(cur))
    else:
        n = int(cycles)
        for i in range(1, n + 1):
            print("== Zyklus %d/%d ==" % (i, n))
            cur = drain(cur)
            print("min:    carbonium=%s" % fmt(cur))
            cur = fill(cur)
            print("max:    carbonium=%s" % fmt(cur))

    print("done:   carbonium=%s" % fmt(carbonium()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
