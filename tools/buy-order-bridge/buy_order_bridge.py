#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-buy-order-bridge — Buy-Orders vom Player-Mod an den Referee (PoC #518).

Tailt den Lua-Log des Dedicated-Servers (``exor_logs.txt`` im Wine-Prefix) und
POSTet jede ``[RBBATTLE] event=buy_order ...``-Zeile an ``POST /buy_order`` des
Tournament-Servers. Die Queue liegt dort (``GET /buy_orders``); dieser Bridge
macht NUR die Zustellung — kein State, keine autoritative Wirkung (die macht der
Operator/Referee/DLL, siehe `docs/research/api-deep-dive.md` §1).

Robustheit: Retry/Backoff bei 5xx/Netzfehlern (4xx wird verworfen), tolerant
gegen Log-Rotation/Truncation, mehrere Kandidat-Pfade (Wine-User variiert).
Nur Standardbibliothek (läuft im Sidecar ``python:3.12-slim``).

Offline-Selbsttest: ``--self-test``.
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

HTTP_TIMEOUT = 5.0
BACKOFF_BASE = 1.0
BACKOFF_MAX = 30.0
TAIL_POLL_S = 0.5


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


def default_log_paths(wine_prefix, wine_user):
    """Die beiden üblichen ``exor_logs.txt``-Pfade im Wine-Prefix."""
    base = os.path.join(wine_prefix, "drive_c", "users", wine_user)
    return [
        os.path.join(base, "Documents", "The Riftbreaker", "exor_logs.txt"),
        os.path.join(base, "AppData", "LocalLow", "The Riftbreaker - Dedicated Server", "exor_logs.txt"),
    ]


def log(msg):
    sys.stdout.write("[buy-order-bridge] " + msg + "\n")
    sys.stdout.flush()


class BuyOrderBridge:
    """Tailt eine oder mehrere Logdateien und postet ``buy_order`` an den Referee."""

    def __init__(self, paths, base_url, from_start=True, env="unknown", ref="unknown"):
        self.paths = list(paths)
        self.base_url = base_url.rstrip("/")
        self.env = env
        self.ref = ref
        self._offset = {p: (0 if from_start else self._size(p)) for p in self.paths}
        self._pending = {p: b"" for p in self.paths}

    @staticmethod
    def _size(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _post(self, payload):
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/buy_order",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")

    def _deliver(self, payload, attempt=0):
        """POST mit Retry/Backoff; 4xx wird verworfen (Konfigurationsfehler)."""
        while True:
            try:
                status, body = self._post(payload)
                if 200 <= status < 300:
                    return True
                log("HTTP {} verworfen: {} {}".format(status, payload, body))
                return False
            except urllib.error.HTTPError as e:
                if e.code >= 500:
                    delay = min(BACKOFF_MAX, BACKOFF_BASE * (2**attempt))
                    log("HTTP {} retry in ~{}s: {}".format(e.code, delay, payload))
                    time.sleep(delay)
                    attempt += 1
                else:
                    log("HTTP {} verworfen: {}".format(e.code, payload))
                    return False
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                delay = min(BACKOFF_MAX, BACKOFF_BASE * (2**attempt))
                log("Netzfehler ({}) retry in ~{}s: {}".format(e, delay, payload))
                time.sleep(delay)
                attempt += 1

    def poll_once(self):
        """Liest neue Zeilen aller Pfade und liefert die Zahl zugestellter Orders."""
        delivered = 0
        for path in self.paths:
            if not os.path.isfile(path):
                continue
            size = self._size(path)
            off = self._offset[path]
            if size < off:
                # Rotation/Truncation: von vorn lesen.
                off = 0
                self._pending[path] = b""
            if size <= off:
                continue
            try:
                with open(path, "rb") as fh:
                    fh.seek(off)
                    data = fh.read()
            except OSError:
                continue
            self._offset[path] = off + len(data)
            data = self._pending[path] + data
            idx = data.rfind(b"\n") + 1
            complete, self._pending[path] = data[:idx], data[idx:]
            for line in complete.decode("utf-8", errors="replace").splitlines():
                fields = parse_buy_order(line)
                if fields is None:
                    continue
                payload = build_payload(fields)
                log("buy_order -> {}".format(payload))
                if self._deliver(payload):
                    delivered += 1
        return delivered

    def run(self, poll_interval=TAIL_POLL_S, once=False):
        while True:
            try:
                self.poll_once()
            except Exception as e:  # Fehlerisolierung: Tail läuft weiter
                log("unerwarteter Fehler: {}".format(e))
            if once:
                break
            time.sleep(poll_interval)


def build_parser():
    p = argparse.ArgumentParser(description="RBBattle Buy-Order-Bridge (PoC #518)")
    p.add_argument(
        "--log",
        action="append",
        default=[],
        metavar="PATH",
        help="Logdatei (mehrfach). Ohne Angabe aus --wine-prefix/--wine-user abgeleitet.",
    )
    p.add_argument("--wine-prefix", default="/data/.wine")
    p.add_argument("--wine-user", default="steamuser")
    p.add_argument(
        "--url",
        default=os.environ.get("RBB_TOURNAMENT_URL", "http://127.0.0.1:8081"),
        help="Tournament-Server-Basis (POST <url>/buy_order)",
    )
    p.add_argument(
        "--from-end",
        action="store_true",
        help="Beim ersten Start nur neue Zeilen lesen (Default: von Anfang an)",
    )
    p.add_argument("--once", action="store_true", help="Verfügbare Zeilen verarbeiten, dann beenden")
    p.add_argument("--poll-interval", type=float, default=TAIL_POLL_S)
    p.add_argument("--self-test", action="store_true", help="Parser-/Payload-Selbsttest ohne Netz/Datei")
    # Deploy-Identitaet (Issue #483, US4): je Start-Logzeile mitgeschrieben.
    p.add_argument("--env", default=os.environ.get("RBB_ENV") or "unknown")
    p.add_argument("--ref", default=os.environ.get("RBB_REF") or "unknown")
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

    paths = args.log or default_log_paths(args.wine_prefix, args.wine_user)
    bridge = BuyOrderBridge(paths, args.url, from_start=not args.from_end, env=args.env, ref=args.ref)
    log("start: url={} env={} ref={} logs={}".format(args.url, args.env, args.ref, paths))
    bridge.run(poll_interval=args.poll_interval, once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
