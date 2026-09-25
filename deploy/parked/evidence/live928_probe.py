#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Live-Repro #928 — recycle-Root-Cause (end_game-Contract vs. no_world).

Startet EINE reale Dedicated-Instanz (Provisioner #908), parkt sie, claimt sie
(resume_game), wartet auf eine laufende Welt und probiert dann `POST /end_game`
mit/ohne `result` direkt gegen die Bridge — plus get_state/round_reset/pause.

Kein Produktcode. Nur Test-Artefakt. Cleanup via provisioner.stop.

    cd deploy/parked
    PROVISIONER_IMAGE=rb-dedicated:fbe485640bfb timeout 900 python3 evidence/live928_probe.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

INSTANCE = os.environ.get("INSTANCE", "live928probe")
IMAGE = os.environ["PROVISIONER_IMAGE"]
os.environ.setdefault("PROVISIONER_ENV", "dev")
os.environ.setdefault("PROVISIONER_INSTANCE_ID", INSTANCE)
os.environ.setdefault("PROVISIONER_HEALTH_DEADLINE", "300")
os.environ.setdefault("PROVISIONER_HEALTH_INTERVAL", "3")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from parked_service import build_provisioner  # noqa: E402


def post(url, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url + path, data=data, method="POST")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return -1, "ERR %s" % exc


def main():
    provisioner = build_provisioner(None, env=os.environ)
    print("### image=%s env=%s instance=%s" % (IMAGE, os.environ["PROVISIONER_ENV"], INSTANCE))
    print("### start ...")
    t0 = time.time()
    result = provisioner.start(env="dev", instance_id=INSTANCE)
    print("### start dt=%.2fs result=%s" % (time.time() - t0, json.dumps(result)))
    port = result["ports"]["bridge"]
    url = "http://127.0.0.1:%d" % int(port)
    print("### bridge url=%s" % url)

    try:
        print("### health = %s" % (post(url, "/health"),))
        print("### pause_game (park) = %s" % (post(url, "/pause_game"),))
        print("### get_state while parked = %s" % (post(url, "/get_state"),))
        print("### resume_game (claim) = %s" % (post(url, "/resume_game"),))

        for wait in (2, 5, 10, 20, 30):
            time.sleep(wait if wait == 2 else wait - (wait // 2))
            st, body = post(url, "/get_state")
            print("### t~%ss get_state = %s %s" % (wait, st, body))

        # --- Kernprobe: end_game-Contract ---
        print("### end_game (no body)        = %s" % (post(url, "/end_game"),))
        print("### end_game {result:null}    = %s" % (post(url, "/end_game", {"result": None}),))
        print("### end_game {result:win}     = %s" % (post(url, "/end_game", {"result": "win"}),))
        print("### end_game {result:bogus}   = %s" % (post(url, "/end_game", {"result": "bogus"}),))
        print("### get_state after end_game  = %s" % (post(url, "/get_state"),))
        print("### round_reset = %s" % (post(url, "/round_reset"),))
        print("### pause_game (re-park) = %s" % (post(url, "/pause_game"),))
        print("### get_state re-parked = %s" % (post(url, "/get_state"),))
    finally:
        print("### stop ...")
        stop = provisioner.stop(instance_id=INSTANCE, env="dev")
        print("### stop = %s" % json.dumps(stop))
        names = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"],
                               stdout=subprocess.PIPE).stdout.decode()
        print("### leftover container present = %s" % ("riftbreaker-dedicated-dev-%s" % INSTANCE in names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
