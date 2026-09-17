#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""send-tailer — env-agnostischer Send-Ingress (Issue #713).

Tailt die Dedi-Logdatei `exor_logs.txt`, parst aus den Mod-Chat-Log-Zeilen
(`button_chat sent: -send waveN` / `event=chat_send … text=-send waveN`) den
Order-Namen und POSTet ihn an die Bridge (`POST /order`). Die Bridge (oder der
Scheduler) zieht den Preis dann SOFORT ab und feuert die Welle nach 5 min.

Warum ein Tailer statt des C++-Chat-Detours: der Markt-Button sendet Chat
**serverseitig** via `QueueEvent("PlayerChatRequest", …)` — das läuft NICHT
durch den Netz-Handler `OnNetPlayerChatRequest`, an dem der Detour (#549)
hängt. Der Chat-Text steht aber im Log. Dieser Sidecar ist das
env-agnostische Gegenstück zu `deploy/session-recorder` (gleicher LogTailer,
gleiche Pfade), das nur die Mod-Log-Zeilen als Quelle nimmt.

Rein stdlib (kein Netz-Dep im Test; HTTP via urllib). Kein Spielprozess, kein
C++-Zugriff. Ein verlorener POST ist kein Desync: der Spieler kann die Order
jederzeit erneut senden (Retry/Pull-Prinzip, siehe server/protocol.md).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

_SEND_RE = re.compile(r"-send\s+([A-Za-z0-9_]+)")


def parse_send_order(line: str) -> Optional[str]:
    """Extrahiert den Order-Namen aus einer Mod-Chat-Log-Zeile.

    Akzeptiert nur die beiden serverseitigen Mod-Log-Formen (Button + RBSendChat),
    damit getippter Chat (der nicht über diese Log-Pfade läuft) NICHT doppelt
    verarbeitet wird. Liefert den Namen (z. B. "wave1") oder None.
    """
    if not line or "-send" not in line:
        return None
    if "button_chat sent" not in line and "event=chat_send" not in line:
        return None
    m = _SEND_RE.search(line)
    if not m:
        return None
    return m.group(1)


class LogTailer:
    """Liest neue Zeilen aus Logdateien, robust gegen Append/Truncation/Rotation."""

    def __init__(self, feeder, paths, from_start=False):
        self.feeder = feeder
        self.paths = list(paths)
        self.from_start = from_start
        self._pending = {p: b"" for p in self.paths}
        self._cursor = {}

    def _cursor_for(self, path):
        current = self._size(path)
        prev = self._cursor.get(path)
        if prev is None:
            return 0 if self.from_start else current
        if current < prev:
            # Datei wurde kleiner → Truncation/Rotation (neuer Serverlauf).
            self._pending[path] = b""
            return 0
        return prev

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
            data = self._pending.get(path, b"") + data
            idx = data.rfind(b"\n") + 1
            complete, self._pending[path] = data[:idx], data[idx:]
            consumed = offset + len(complete)
            for line in complete.decode("utf-8", errors="replace").splitlines():
                if self.feeder.feed(line) is not None:
                    processed += 1
            self._cursor[path] = consumed
        return processed


class SendTailer:
    """Parst Mod-Chat-Log-Zeilen und POSTet Send-Orders an die Bridge."""

    def __init__(self, bridge_url: str, timeout: float = 5.0, env: str = "unknown", ref: str = "unknown", _poster=None):
        self.bridge_url = bridge_url
        self.timeout = timeout
        self.env = env
        self.ref = ref
        self._poster = _poster or self._http_post

    def _http_post(self, name: str) -> Tuple[int, str]:
        body = json.dumps({"name": name}).encode("utf-8")
        req = urllib.request.Request(
            self.bridge_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError) as e:
            return 0, str(e)

    def feed(self, line: str) -> Optional[str]:
        """Feed eine Log-Zeile. POSTet bei Treffer und liefert den Namen zurück."""
        name = parse_send_order(line)
        if name is None:
            return None
        status, body = self._poster(name)
        if 200 <= status < 300:
            print(f"[send-tailer] order queued: {name} (HTTP {status})", flush=True)
        else:
            print(f"[send-tailer] order {name}: HTTP {status} {body[:160]}", flush=True)
        return name


def default_log_paths(wine_prefix: str, wine_user: str) -> List[str]:
    base = os.path.join(wine_prefix, "drive_c", "users", wine_user)
    return [
        os.path.join(base, "Documents", "The Riftbreaker", "exor_logs.txt"),
        os.path.join(base, "AppData", "LocalLow", "The Riftbreaker - Dedicated Server", "exor_logs.txt"),
    ]


def run(
    paths,
    bridge_url,
    poll_interval=1.0,
    from_start=False,
    once=False,
    env="unknown",
    ref="unknown",
    timeout=5.0,
    _sleep=time.sleep,
    poster=None,
):
    tailer_feed = SendTailer(bridge_url, timeout=timeout, env=env, ref=ref, _poster=poster)
    tailer = LogTailer(tailer_feed, paths, from_start=from_start)
    while True:
        tailer.poll_once()
        if once:
            break
        _sleep(poll_interval)
    return tailer_feed


def build_parser():
    p = argparse.ArgumentParser(description="RBBattle Send-Tailer (Issue #713)")
    p.add_argument(
        "--log",
        action="append",
        default=[],
        metavar="PATH",
        help="Logdatei (mehrfach). Ohne Angabe aus --wine-prefix/--wine-user abgeleitet.",
    )
    p.add_argument("--wine-prefix", default="/data/.wine", help="Wine-Prefix (Default: /data/.wine)")
    p.add_argument("--wine-user", default="steamuser", help="Wine-User (Default: steamuser)")
    p.add_argument(
        "--bridge-url",
        default=os.environ.get("RBB_BRIDGE_URL") or "http://127.0.0.1:9001/order",
        help="Bridge-/order-Endpoint (Default: RBB_BRIDGE_URL oder http://127.0.0.1:9001/order)",
    )
    p.add_argument("--poll-interval", type=float, default=1.0)
    p.add_argument(
        "--from-start", action="store_true", help="Beim ersten Start die ganze Datei lesen (Default: nur neue Zeilen)"
    )
    p.add_argument("--once", action="store_true", help="Verfügbare Zeilen verarbeiten, dann beenden")
    p.add_argument("--timeout", type=float, default=5.0, help="HTTP-Timeout je POST")
    p.add_argument("--env", default=os.environ.get("RBB_ENV") or "unknown")
    p.add_argument("--ref", default=os.environ.get("RBB_REF") or "unknown")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    paths = args.log or default_log_paths(args.wine_prefix, args.wine_user)
    run(
        paths,
        args.bridge_url,
        poll_interval=args.poll_interval,
        from_start=args.from_start,
        once=args.once,
        env=args.env,
        ref=args.ref,
        timeout=args.timeout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
