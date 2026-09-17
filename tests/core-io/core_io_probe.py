#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core-IO-Gate — Live-Probe (C1 Boot) einer laufenden dedicated-Instanz.

Issue #289: Das Kern-Versprechen „We can interact with the running game we
host" war über C1–C4 abgesichert. Nach der Green-Field-Refaktorierung (v2) ist
der Lua-Mod nur noch die Player-HUD-Schicht — die Business-Logik liegt im
Backend (rbbridge.dll C++). C2–C4 (rb_status/rb_wave → Lua-Effekt) testeten
die alte Lua-Architektur und sind eingestampft.

Aktuell prüft das Gate nur noch C1 BOOT/HOST:

  * Container läuft stabil (nicht restarting)
  * Bridge-HTTP ``/health`` antwortet ``ok:true``
  * Mod geladen: ``[RBBATTLE…] event=mod_load …`` im dedi-Log (Praefix-Match,
    das Tag kann seit #631 einen Build-Suffix tragen, "[RBBATTLE:<build>]")

C2–C4 (Ingress-Effekt-Invariante, Egress, Server-Wave) werden wieder
aufgebaut, sobald das Backend echte C++-Read/Write-Pfade hat
(``get_state``/``add_resource``) — dann gegen die Bridge statt gegen
Lua-Commands.

Das Skript ist transport-neutral: es spricht den Zielhost über einen
konfigurierbaren ``--remote``-Präfix an (Default ``ssh planet``) oder direkt
(``--local``).

Exit-Codes: 0 = C1 bestanden, 1 = C1 rot, 2 = Aufruf-/Umgebungsfehler.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from typing import List, Optional, Tuple


class Host:
    """Fuehrt Shell-Kommandos + HTTP gegen den Zielhost aus.

    ``remote`` ist ein Command-Praefix (z. B. ``ssh planet``) oder None fuer
    lokale Ausfuehrung.
    """

    def __init__(self, remote: Optional[str]):
        self.remote = remote.strip() if remote else None

    def _wrap(self, cmd: str) -> List[str]:
        if self.remote:
            return ["bash", "-lc", "{} {}".format(self.remote, shlex.quote(cmd))]
        return ["bash", "-lc", cmd]

    def run(self, cmd: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
        return subprocess.run(self._wrap(cmd), capture_output=True, text=True, timeout=timeout)

    def http_json(
        self, method: str, url: str, payload: Optional[dict] = None, timeout: float = 15.0
    ) -> Tuple[Optional[int], str]:
        """HTTP ueber curl auf dem Zielhost (Ports sind dort an 127.0.0.1 gebunden).

        Der HTTP-Status wird per ``-w`` an den Body gehaengt (Trenner ``@@``),
        damit Quoting ueber eine (oder zwei) Shells keinen Backslash-Format-String
        zerbricht.
        """
        # KEIN fuehrendes '@' (curl liest '-w @file' aus einer Datei!) und kein
        # Backslash-Format (bricht ueber zwei Shells) — daher Marker+Status.
        marker = "COREIO_STATUS="
        fmt = " " + marker + "%{http_code}"
        parts = ["curl", "-sS", "--max-time", str(int(timeout)), "-X", method, "-w", shlex.quote(fmt)]
        if payload is not None:
            parts += ["-H", shlex.quote("Content-Type: application/json"), "-d", shlex.quote(json.dumps(payload))]
        parts.append(shlex.quote(url))
        cmd = " ".join(parts)
        try:
            proc = self.run(cmd, timeout=timeout + 5.0)
        except subprocess.TimeoutExpired:
            return None, "__curl_timeout__"
        raw = proc.stdout or ""
        body, sep, code = raw.rpartition(marker)
        if not sep:
            return None, raw
        try:
            status = int(code.strip())
        except ValueError:
            status = None
        return status, body


def is_rbbattle_line(line: str) -> bool:
    """"[RBBATTLE" ohne schliessende Klammer (Issue #696): seit 08bb0ca traegt

    das Tag einen Build-Suffix ("[RBBATTLE:20260917-203600]" statt
    "[RBBATTLE]") -- der exakte "[RBBATTLE]"-Substring-Match matchte diese
    Zeilen nie mehr und liess C1 in jedem echten Lauf in den Boot-Timeout
    laufen, unabhaengig vom PR-Inhalt. Praefix-Match deckt beide Formate ab.
    """
    return "[RBBATTLE" in line


def fetch_log_lines(host: Host, container: str, tail: int = 4000) -> List[str]:
    proc = host.run("docker logs --tail {} {} 2>&1".format(tail, shlex.quote(container)), timeout=45.0)
    if proc.returncode != 0:
        raise RuntimeError("docker logs fehlgeschlagen (rc={}): {}".format(proc.returncode, proc.stderr.strip()))
    return [ln for ln in proc.stdout.splitlines() if is_rbbattle_line(ln)]


def log(msg: str) -> None:
    print("[core-io] {}".format(msg), flush=True)


def err(msg: str) -> None:
    print("::error::{}".format(msg), flush=True)


def container_state(host: Host, container: str) -> Tuple[str, bool]:
    """Liest (status, restarting) robust aus ``docker inspect`` (JSON, kein
    Format-Template — vermeidet Quoting-Fallen ueber ssh)."""
    proc = host.run("docker inspect {}".format(shlex.quote(container)), timeout=30.0)
    if proc.returncode != 0:
        return "missing", False
    try:
        data = json.loads(proc.stdout)
        state = data[0]["State"]
        return str(state.get("Status", "?")), bool(state.get("Restarting", False))
    except (ValueError, KeyError, IndexError, TypeError):
        return "unknown", False


def wait_for_boot(host: Host, container: str, bridge_url: str, timeout: float) -> bool:
    """C1: Container stabil + Mod geladen + Bridge /health ok. Liefert True/False."""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        st, restarting = container_state(host, container)
        remaining = max(0, int(deadline - time.time()))
        log("C1 boot: container status={} restarting={} ({}s)".format(st, restarting, remaining))
        if st == "running" and not restarting:
            lines = fetch_log_lines(host, container)
            if any("event=mod_load" in ln or "skeleton ok" in ln for ln in lines):
                hstatus, body = host.http_json("GET", bridge_url.replace("/exec", "/health"), timeout=10)
                if hstatus == 200 and '"ok":true' in body:
                    log("C1 boot: OK (Container stabil, Mod geladen, Bridge /health ok)")
                    return True
                last = "Bridge /health: HTTP {} {}".format(hstatus, body[:120])
                log("C1 boot: warte auf Bridge /health — {}".format(last))
            else:
                last = "Mod noch nicht geladen ([RBBATTLE...]-Zeile mit event=mod_load fehlt)"
        time.sleep(8)
    err("C1 boot rot: {} (letzter Stand: {})".format("Timeout nach {}s".format(int(timeout)), last))
    log("--- Container-Log (letzte 60 Zeilen, Diagnose) ---")
    dump = host.run("docker logs --tail 60 {} 2>&1".format(shlex.quote(container)), timeout=45.0)
    for ln in (dump.stdout or "").splitlines():
        log("  " + ln)
    log("--- Ende Container-Log ---")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Core-IO-Gate Live-Probe (C1 Boot)")
    ap.add_argument("--local", action="store_true", help="lokal ausfuehren statt ueber --remote")
    ap.add_argument("--remote", default="ssh planet", help="Command-Praefix fuer den Zielhost (Default: 'ssh planet')")
    ap.add_argument("--container", default="riftbreaker-dedicated-test")
    ap.add_argument("--bridge-url", default="http://127.0.0.1:9003/exec")
    ap.add_argument("--boot-timeout", type=float, default=180)
    args = ap.parse_args()

    host = Host(None if args.local else args.remote)
    log("Ziel: container={} bridge={} remote={!r}".format(args.container, args.bridge_url, host.remote))

    if not wait_for_boot(host, args.container, args.bridge_url, args.boot_timeout):
        print_summary(["C1 boot"], "C1 boot")
        return 1

    print_summary([], "C1 boot")
    return 0


def print_summary(failures: List[str], scope: str) -> None:
    if failures:
        err("Core-IO-Gate ROT ({}): {}".format(scope, "; ".join(failures)))
    else:
        log("Core-IO-Gate GRUEN ({}): C1 Boot (Container stabil, Mod geladen, Bridge /health ok)".format(scope))


if __name__ == "__main__":
    sys.exit(main())
