#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermetischer Dienst-E2E-Nachweis #928 (Test-Artefakt, kein Produktcode).

Faehrt den KOMPLETTEN Dienst ueber HTTP: echter ``ParkedController`` + echter
``ThreadingHTTPServer`` auf ``127.0.0.1:0``, echte Requests per ``urllib``,
aber ``FakeProvisioner``/``FakeBridge``/``FakeClock`` (kein Docker/Netz/Spiel).

Ablauf (ein Durchlauf):
  warm (N=2) -> /status parked=2 -> /claim FIFO (handover_seconds)
  -> /recycle -> wieder PARKED -> zweiter /claim ok -> /reap stoppt Ueberfaellige.

Aufruf:
    cd deploy/parked && python3 evidence/hermetic928.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parked_pool import ParkedPool  # noqa: E402
from parked_service import ParkedController, ParkedServiceConfig, build_server  # noqa: E402
from test_parked_service import FakeBridge, FakeClock, FakeProvisioner  # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    mark = "OK " if cond else "FAIL"
    if not cond:
        FAIL.append(label)
    print("### %s %s%s" % (mark, label, (" -> " + detail) if detail else ""))


def main():
    clock = FakeClock(1000.0)
    provisioner = FakeProvisioner()
    bridges = {}

    def factory(url):
        b = bridges.get(url)
        if b is None:
            b = FakeBridge(url, clock)
            bridges[url] = b
        return b

    pool = ParkedPool(provisioner, factory, clock=clock, sleep=lambda _s: None)
    controller = ParkedController(
        pool, pool_size=2, max_park_seconds=900.0, reap_interval=9999.0,
        env="test", prefix="parked", clock=clock, sleep=lambda _s: None,
    )
    config = ParkedServiceConfig(env="test", bind="127.0.0.1", port=0, token="")
    httpd = build_server(config, controller)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def call(method, path, payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (port, path), data=data, method=method)
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    try:
        print("### service on 127.0.0.1:%d" % port)

        # 1) warm bis N=2 (maintain_once), ueber HTTP /status verifizieren
        controller.maintain_once()
        st, body = call("GET", "/status")
        check("status 200", st == 200, str(st))
        check("parked==2", body["counters"]["parked"] == 2, json.dumps(body["counters"]))
        check("total==2", body["counters"]["total"] == 2)
        ids = sorted(e["instance"] for e in body["entries"])
        check("ids parked-1,parked-2", ids == ["parked-1", "parked-2"], str(ids))

        # 2) FIFO: aeltestes zuerst. parked-1 zuerst parken, dann parked-2 ->
        #    parked-1 hat groesseres parked_seconds => aeltestes.
        clock.advance(5.0)  # parked-1 war zuerst da
        # warm_up hat beide im selben Tick geparkt; FIFO = max parked_seconds.
        st, c1 = call("POST", "/claim", {})
        check("claim1 200", st == 200, str(st))
        check("claim1 state claimed", c1.get("state") == "claimed")
        check("claim1 has handover_seconds", isinstance(c1.get("handover_seconds"), float),
              str(c1.get("handover_seconds")))

        # 3) recycle -> wieder PARKED, rounds+1
        st, rec = call("POST", "/recycle", {"instance_id": c1["instance"], "keep_warm": True})
        check("recycle1 200", st == 200, str(st))
        check("recycle1 state parked", rec.get("state") == "parked", json.dumps(rec))
        check("recycle1 rounds==1", rec.get("rounds") == 1)
        st, body = call("GET", "/status")
        check("nach recycle parked==2", body["counters"]["parked"] == 2)
        check("nach recycle claimed==0", body["counters"]["claimed"] == 0)

        # 4) zweiter Claim ok
        st, c2 = call("POST", "/claim", {})
        check("claim2 200", st == 200, str(st))
        check("claim2 state claimed", c2.get("state") == "claimed")
        st, rec2 = call("POST", "/recycle", {"instance_id": c2["instance"], "keep_warm": True})
        check("recycle2 200", st == 200, str(st))
        check("recycle2 state parked", rec2.get("state") == "parked")
        # claim2 nimmt die andere Instanz (FIFO: parked-2), deren rounds ist 1.
        check("recycle2 rounds==1", rec2.get("rounds") == 1, json.dumps(rec2))
        check("recycle2 andere Instanz", rec2.get("instance") != rec.get("instance"),
              "%s vs %s" % (rec.get("instance"), rec2.get("instance")))

        # 5) /reap stoppt Ueberfaellige, laesst frische PARKED unberuehrt
        st, body = call("GET", "/status")
        check("2 PARKED vor reap", body["counters"]["parked"] == 2)
        clock.advance(20.0)
        st, reap = call("POST", "/reap", {"max_park_seconds": 10})
        check("reap 200", st == 200, str(st))
        check("reap stopped 2", len(reap["stopped"]) == 2, json.dumps(reap))
        st, body = call("GET", "/status")
        check("nach reap parked==0", body["counters"]["parked"] == 0)
        check("nach reap stopped==2", body["counters"]["stopped"] == 2)
        cnt = body["counters"]
        inv = (cnt["parked"] + cnt["claimed"] + cnt["warming"] + cnt["recycling"] + cnt["stopped"]) == cnt["total"]
        check("zaehler-invariante", inv, json.dumps(cnt))
        check("claims==2", cnt["claims"] == 2, json.dumps(cnt))
        check("recycles==2", cnt["recycles"] == 2)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2.0)

    print("### RESULT: %s" % ("PASS" if not FAIL else "FAIL: " + ",".join(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
