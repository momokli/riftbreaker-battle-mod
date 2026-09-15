#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-buy-order-bridge — Buy-Orders vom Player-Mod an den Referee (PoC #518).

Tailt den Lua-Log des Dedicated-Servers (``exor_logs.txt``) und POSTet jede
``[RBBATTLE] event=buy_order ...``-Zeile an ``POST /buy_order`` des
Tournament-Servers. Die Queue liegt dort (``GET /buy_orders``); dieser Bridge
macht NUR die Zustellung — kein State, keine autoritative Wirkung (die macht der
Operator/Referee/DLL, siehe `docs/research/api-deep-dive.md` §1).

Nur Standardbibliothek (läuft als Sidecar). Offline-Selbsttest: ``--self-test``.
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
RE_FIELD = re.compile(r"\b(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<val>[^\s\"]+)")
RE_INT = re.compile(r"^-?\d+$")


def parse_buy_order(line):
    """Log-Zeile -> Felder-Dict oder ``None``. Liefert nur ``event=buy_order``."""
    m = RE_MARKER.search(line)
    if not m:
        return None
    em = RE_EVENT.search(m.group("rest"))
    if not em or em.group("event") != "buy_order":
        return None
    fields = {}
    for fm in RE_FIELD.finditer(m.group("rest")):
        if fm.group("key") == "event":
            continue
        fields[fm.group("key")] = fm.group("val")
    return fields


def _as_int(value, default):
    if value is None:
        return default
    return int(value) if RE_INT.match(str(value)) else default


def build_payload(fields):
    """Felder-Dict -> Request-Body für ``POST /buy_order``."""
    return {
        "world": fields.get("world") or "A",
        "amount": _as_int(fields.get("amount"), 0),
        "resource": fields.get("resource") or "carbonium",
        "item": fields.get("item") or "unknown",
    }


def forward(payload, base_url, timeout=5.0):
    """POSTet eine Buy-Order; Rückgabe ``(status, body)``."""
    url = base_url.rstrip("/") + "/buy_order"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        return None, "unreachable: %s" % e.reason


def run(path, base_url, once=False, poll_interval=1.0, from_end=False, err=sys.stderr):
    """Tailt `path` und leitet neue ``buy_order``-Zeilen an `base_url` weiter."""
    offset = 0
    if from_end:
        try:
            offset = os.path.getsize(path)
        except OSError:
            offset = 0

    while True:
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                data = fh.read()
        except OSError:
            data = b""
        if data:
            lines = data.decode("utf-8", errors="replace").splitlines()
            offset += len(data)
            for line in lines:
                fields = parse_buy_order(line)
                if fields is None:
                    continue
                payload = build_payload(fields)
                status, body = forward(payload, base_url)
                print(
                    "buy_order forwarded status=%s item=%s resp=%s" % (status, payload["item"], body),
                    file=err,
                )
        if once:
            return
        time.sleep(poll_interval)


def build_parser():
    p = argparse.ArgumentParser(description="RBBattle Buy-Order-Bridge (PoC #518)")
    p.add_argument("--log", default=None, metavar="PATH", help="Lua-Log des Dedicated-Servers (exor_logs.txt)")
    p.add_argument("--url", default=None, metavar="URL", help="Tournament-Server-Basis (z. B. http://127.0.0.1:8080)")
    p.add_argument("--once", action="store_true", help="Verfügbare Zeilen verarbeiten, dann beenden")
    p.add_argument("--from-end", action="store_true", help="Beim ersten Start nur neue Zeilen lesen")
    p.add_argument("--poll-interval", type=float, default=1.0)
    p.add_argument("--self-test", action="store_true", help="Parser-/Payload-Selbsttest ohne Netz/Datei")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.self_test:
        sample = "[RBBATTLE] event=buy_order world=A amount=10 resource=carbonium item=boss order_id=1"
        fields = parse_buy_order(sample)
        assert fields is not None, "sample buy_order wurde nicht erkannt"
        payload = build_payload(fields)
        assert payload == {"world": "A", "amount": 10, "resource": "carbonium", "item": "boss"}, payload
        assert parse_buy_order("[RBBATTLE] event=mod_load version=1 status=ok") is None
        print("self-test ok: %s" % json.dumps(payload, sort_keys=True))
        return 0

    if not args.log or not args.url:
        print("--log und --url sind erforderlich (oder --self-test)", file=sys.stderr)
        return 2

    run(args.log, args.url, once=args.once, poll_interval=args.poll_interval, from_end=args.from_end)
    return 0


if __name__ == "__main__":
    sys.exit(main())
