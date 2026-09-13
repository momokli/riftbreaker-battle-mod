#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core-IO-Gate — Live-Probe des Kern-Kanals einer laufenden dedicated-Instanz.

Issue #289: Das Kern-Versprechen „We can interact with the running game we
host" soll automatisiert abgesichert werden. Dieses Skript laeuft gegen den
*gebooteten Test-Stack* (deploy/test-deploy.yml + deploy/test-vars.yml) und
prueft den gesamten Kanal C1–C4 ohne Player:

  C1 BOOT/HOST    Container laeuft stabil + Bridge-HTTP erreichbar + Mod geladen
                  ([RBBATTLE] im dedi-Log).
  C2 INGRESS      POST /exec {"command":"rb_status"} -> exec_result ok:true
                  UND der Effekt steht als Log-Zeile im Game-Log
                  ([RBBATTLE] event=status). **Invariante**: ok:true OHNE
                  Effekt-Logzeile = ROT (genau die Falsch-Gruen-Semantik #288).
  C3 EGRESS       Ein *laufender* Consumer (docker logs -f) sieht das
                  Spiel-Event — nicht nur „die Zeile steht irgendwo".
  C4 SERVER-SPAWN Vom Tournament-Server ausgeloester POST /wave erreicht den
                  Mod (Referee-Aktion -> Bridge -> mod). Der *Spawn-Zaehler*
                  (spawned>0) ist headless NICHT erreichbar (kein Spieler /
                  keine Bord-Spawner) und wird als OFFENER PUNKT gemeldet,
                  nicht hart gegated.

Das Skript ist bewusst transport-neutral: es spricht den Zielhost ueber einen
konfigurierbaren ``--remote``-Praeﬁx an (Default ``ssh planet``) oder direkt
(``--local``), damit dieselbe Logik sowohl im CI-Runner (auf planet) als auch
zur Diagnose von einer Workstation aus laeuft.

Exit-Codes: 0 = alle harten Checks bestanden, 1 = harter Check rot,
2 = Aufruf-/Umgebungsfehler. Der Spawn-Zaehler (C4) beeinflusst den Exit-Code
nicht — er wird als ``::warning::`` ausgegeben (offener Punkt, docs/tests).
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import threading
import time
from collections import Counter
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Reine Klassifikations-Helfer (hermetisch testbar in test_invariant.py)
# ---------------------------------------------------------------------------

EXEC_RESULT_OK = "ok"
EXEC_RESULT_FAIL = "fail"


def parse_exec_response(http_status: Optional[int], body: str) -> dict:
    """Wertet die JSON-Antwort von ``POST <bridge>/exec`` aus.

    Liefert::

        {"http": int|None, "json": dict|None,
         "transport_ok": bool, "exec_ok": bool|None, "reason": str}

    ``transport_ok`` = HTTP 2xx (Zustellung gelungen); ``exec_ok`` = das
    ``ok``-Feld aus dem exec_result (Ausfuehrung im Mod). Beides ist bewusst
    getrennt — eine Bridge kann mit 200 + ``exec_result.ok:false`` antworten
    (Issue #266-Semantik).
    """
    out = {"http": http_status, "json": None, "transport_ok": False,
           "exec_ok": None, "reason": ""}
    try:
        parsed = json.loads(body) if body and body.strip() else None
    except ValueError:
        parsed = None
    out["json"] = parsed
    if isinstance(parsed, dict):
        exec_ok = parsed.get("ok")
        if isinstance(exec_ok, bool):
            out["exec_ok"] = exec_ok
        reason = parsed.get("reason")
        if isinstance(reason, str):
            out["reason"] = reason
    if http_status is not None and 200 <= http_status < 300:
        out["transport_ok"] = True
        # Ein direkter exec_result-ok kann auch in "results" stecken.
        if out["exec_ok"] is None and isinstance(parsed, dict):
            results = parsed.get("results")
            if isinstance(results, list) and results:
                ok_vals = [r.get("ok") for r in results if isinstance(r, dict)]
                if ok_vals:
                    out["exec_ok"] = all(v is True for v in ok_vals)
                    reasons = [r.get("reason") for r in results
                               if isinstance(r, dict) and r.get("reason")]
                    if reasons and not out["reason"]:
                        out["reason"] = str(reasons[0])
    return out


def count_effect(lines: List[str], needle: str) -> int:
    """Zaehlt Log-Zeilen, die das erwartete Effekt-Token enthalten."""
    return sum(1 for ln in lines if needle in ln)


def classify_ingress(resp: dict, effect_delta: int) -> Tuple[str, str]:
    """Die Kern-INVARIANTE (#289/#288).

    ``ok:true`` darf NIE nur „ExecuteCommand wurde aufgerufen" heissen. Rot,
    wenn der Transport scheitert, die Ausfuehrung nicht ok ist, oder ``ok:true``
    ohne neue Effekt-Logzeile kommt (Falsch-Gruen).
    """
    if not resp["transport_ok"]:
        return EXEC_RESULT_FAIL, (
            "Transport rot: HTTP {} (reason={!r})".format(resp["http"], resp["reason"]))
    if resp["exec_ok"] is not True:
        return EXEC_RESULT_FAIL, (
            "exec_result nicht ok (exec_ok={!r}, reason={!r})".format(
                resp["exec_ok"], resp["reason"]))
    if effect_delta <= 0:
        return EXEC_RESULT_FAIL, (
            "FALSCH-GRUEN: exec_result ok:true, aber KEINE neue Effekt-Logzeile "
            "(ok:true heisst hier nur 'ExecuteCommand aufgerufen', vgl. #288)")
    return EXEC_RESULT_OK, "ingress ok:true UND Effekt-Logzeile (+{}) gesehen".format(effect_delta)


_WAVE_SPAWNED_RE = re.compile(r"event=wave\b[^\n]*status=done[^\n]*spawned=(\d+)")
_WAVE_START_RE = re.compile(r"event=wave\b[^\n]*status=start")


def classify_wave(lines: List[str]) -> dict:
    """Wertet die Wave-Effekt-Zeilen des Mods aus.

    - ``reached``      : der Referee-Auftrag kam im Mod an (``status=start``).
    - ``spawned``      : Integer aus ``status=done spawned=N`` oder None.
    - ``terminal``     : ``done`` | ``no_player`` | ``no_border_spawners`` | ``none``.
    - ``spawn_ok``     : spawned ist bekannt und > 0.
    """
    reached = any(_WAVE_START_RE.search(ln) for ln in lines)
    spawned = None
    terminal = "none"
    for ln in lines:
        m = _WAVE_SPAWNED_RE.search(ln)
        if m:
            spawned = int(m.group(1))
            terminal = "done"
        elif "status=no_player" in ln:
            terminal = "no_player"
        elif "status=no_border_spawners" in ln and terminal == "none":
            terminal = "no_border_spawners"
    return {
        "reached": reached,
        "spawned": spawned,
        "terminal": terminal,
        "spawn_ok": spawned is not None and spawned > 0,
    }


def classify_consumer(consumer_started: bool, seen: bool) -> Tuple[str, str]:
    """C3: Egress muss einen *laufenden* Consumer erreichen."""
    if not consumer_started:
        return EXEC_RESULT_FAIL, "Consumer fehlt/lief nicht — Egress nicht nachweisbar"
    if not seen:
        return EXEC_RESULT_FAIL, "laufender Consumer hat KEIN Egress-Event gesehen"
    return EXEC_RESULT_OK, "laufender Consumer hat das Egress-Event gesehen"


# ---------------------------------------------------------------------------
# Transport (remote oder lokal)
# ---------------------------------------------------------------------------

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
        return subprocess.run(self._wrap(cmd), capture_output=True, text=True,
                              timeout=timeout)

    def http_json(self, method: str, url: str, payload: Optional[dict] = None,
                  timeout: float = 15.0) -> Tuple[Optional[int], str]:
        """HTTP ueber curl auf dem Zielhost (Ports sind dort an 127.0.0.1 gebunden).

        Der HTTP-Status wird per ``-w`` an den Body gehaengt (Trenner ``@@``),
        damit Quoting ueber eine (oder zwei) Shells keinen Backslash-Format-String
        zerbricht.
        """
        # shellcheck disable=SC2016  # absichtlich literal: %{http_code} ist curl-Syntax
        # KEIN fuehrendes '@' (curl liest '-w @file' aus einer Datei!) und kein
        # Backslash-Format (bricht ueber zwei Shells) — daher Marker+Status.
        marker = "COREIO_STATUS="
        fmt = " " + marker + "%{http_code}"
        parts = ["curl", "-sS", "--max-time", str(int(timeout)),
                 "-X", method, "-w", shlex.quote(fmt)]
        if payload is not None:
            parts += ["-H", shlex.quote("Content-Type: application/json"),
                      "-d", shlex.quote(json.dumps(payload))]
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


def fetch_log_lines(host: Host, container: str, tail: int = 4000) -> List[str]:
    proc = host.run("docker logs --tail {} {} 2>&1".format(tail, shlex.quote(container)),
                    timeout=45.0)
    if proc.returncode != 0:
        raise RuntimeError(
            "docker logs fehlgeschlagen (rc={}): {}".format(proc.returncode, proc.stderr.strip()))
    return [ln for ln in proc.stdout.splitlines() if "[RBBATTLE]" in ln]


def new_lines(before: List[str], after: List[str]) -> List[str]:
    """Zeilen, die gegenueber ``before`` neu hinzugekommen sind (multiset-Diff).

    Robust gegen ein verschobenes ``--tail``-Fenster (im Gegensatz zu
    Index-Slicing).
    """
    delta = Counter(after) - Counter(before)
    out: List[str] = []
    for ln, n in delta.items():
        out.extend([ln] * n)
    return out


class LogConsumer:
    """C3: laufender Egress-Consumer — streamt ``docker logs -f`` mit."""

    def __init__(self, host: Host, container: str):
        self.host = host
        self.container = container
        self.lines: List[str] = []
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        cmd = "docker logs -f --tail 50 {}".format(shlex.quote(self.container))
        self._proc = subprocess.Popen(self.host._wrap(cmd), stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True,
                                      encoding="utf-8", errors="replace")

        def _reader():
            assert self._proc and self._proc.stdout
            for ln in self._proc.stdout:
                if "[RBBATTLE]" in ln:
                    self.lines.append(ln)

        self._thread = threading.Thread(target=_reader, daemon=True)
        self._thread.start()

    @property
    def started(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def seen(self, needle: str) -> bool:
        return any(needle in ln for ln in self.lines)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print("[core-io] {}".format(msg), flush=True)


def warn(msg: str) -> None:
    print("::warning::{}".format(msg), flush=True)


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
    """C1: Container stabil + Mod geladen. Liefert True/False."""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        st, restarting = container_state(host, container)
        remaining = max(0, int(deadline - time.time()))
        log("C1 boot: container status={} restarting={} ({}s)".format(st, restarting, remaining))
        if st == "running" and not restarting:
            lines = fetch_log_lines(host, container)
            if any("event=mod_load" in ln or "skeleton ok" in ln for ln in lines):
                hstatus, body = host.http_json("GET", bridge_url.replace("/exec", "/health"),
                                               timeout=10)
                if hstatus == 200 and '"ok":true' in body:
                    log("C1 boot: OK (Container stabil, Mod geladen, Bridge /health ok)")
                    return True
                last = "Bridge /health: HTTP {} {}".format(hstatus, body[:120])
                log("C1 boot: warte auf Bridge /health — {}".format(last))
            else:
                last = "Mod noch nicht geladen ([RBBATTLE] fehlt)"
        time.sleep(8)
    err("C1 boot rot: {} (letzter Stand: {})".format("Timeout nach {}s".format(int(timeout)), last))
    log("--- Container-Log (letzte 60 Zeilen, Diagnose) ---")
    dump = host.run("docker logs --tail 60 {} 2>&1".format(shlex.quote(container)), timeout=45.0)
    for ln in (dump.stdout or "").splitlines():
        log("  " + ln)
    log("--- Ende Container-Log ---")
    return False


def check_ingress(host: Host, container: str, bridge_url: str, cmd: str,
                  needle: str, effect_timeout: float) -> Tuple[bool, str]:
    """C2: Ingress-Kommando -> erwarteter Effekt (Invariante)."""
    before = fetch_log_lines(host, container)
    status, body = host.http_json("POST", bridge_url,
                                  {"cmd": "exec", "command": cmd,
                                   "cmd_id": "core-io-{}".format(int(time.time()))},
                                  timeout=30)
    resp = parse_exec_response(status, body)
    log("C2 ingress: POST /exec {!r} -> HTTP {} exec_ok={!r} reason={!r}".format(
        cmd, resp["http"], resp["exec_ok"], resp["reason"]))

    delta = 0
    deadline = time.time() + effect_timeout
    while time.time() < deadline:
        delta = count_effect(new_lines(before, fetch_log_lines(host, container)), needle)
        if delta > 0:
            break
        time.sleep(3)

    verdict, detail = classify_ingress(resp, delta)
    if verdict == EXEC_RESULT_FAIL:
        return False, "C2 ingress {}".format(detail)
    return True, "C2 ingress OK: {}".format(detail)


def check_wave(host: Host, container: str, tournament_url: str,
               effect_timeout: float) -> Tuple[bool, bool, str]:
    """C4: serverseitig ausgeloester Wave-Auftrag erreicht den Mod.

    Rueckgabe: (reached_ok, spawn_ok, detail). ``spawn_ok`` ist der offene
    Punkt (headless kein Spieler) und wird nicht hart gegated.
    """
    before = fetch_log_lines(host, container)
    status, body = host.http_json("POST", tournament_url.rstrip("/") + "/wave",
                                  {"world": "A", "n": 1}, timeout=30)
    log("C4 wave: POST {}/wave -> HTTP {} {}".format(
        tournament_url.rstrip("/"), status, (body or "")[:200]))

    lines_new: List[str] = []
    deadline = time.time() + effect_timeout
    while time.time() < deadline:
        lines_new = new_lines(before, fetch_log_lines(host, container))
        if any(_WAVE_START_RE.search(ln) for ln in lines_new):
            # kurze Nachlaufzeit fuer status=done / no_player
            time.sleep(6)
            lines_new = new_lines(before, fetch_log_lines(host, container))
            break
        time.sleep(3)

    res = classify_wave(lines_new)
    detail = "reached={} terminal={} spawned={}".format(
        res["reached"], res["terminal"], res["spawned"])
    return res["reached"], res["spawn_ok"], detail


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Core-IO-Gate Live-Probe (Issue #289)")
    ap.add_argument("--local", action="store_true",
                    help="lokal ausfuehren statt ueber --remote")
    ap.add_argument("--remote", default="ssh planet",
                    help="Command-Praefix fuer den Zielhost (Default: 'ssh planet')")
    ap.add_argument("--container", default="riftbreaker-dedicated-test")
    ap.add_argument("--bridge-url", default="http://127.0.0.1:9003/exec")
    ap.add_argument("--tournament-url", default="http://127.0.0.1:8091")
    ap.add_argument("--boot-timeout", type=float, default=180)
    ap.add_argument("--effect-timeout", type=float, default=45)
    ap.add_argument("--status-cmd", default="rb_status")
    ap.add_argument("--status-needle", default="event=status")
    ap.add_argument("--skip-wave", action="store_true",
                    help="C4 ueberspringen (z. B. read-only Diagnose gegen Prod)")
    args = ap.parse_args()

    host = Host(None if args.local else args.remote)
    log("Ziel: container={} bridge={} tournament={} remote={!r}".format(
        args.container, args.bridge_url, args.tournament_url, host.remote))

    failures: List[str] = []

    # C1 — Boot/Host
    if not wait_for_boot(host, args.container, args.bridge_url, args.boot_timeout):
        print_summary(["C1 boot"], "C1 boot")
        return 1

    # C3 — laufender Egress-Consumer VOR den Kommandos starten
    consumer = LogConsumer(host, args.container)
    consumer.start()
    time.sleep(3)
    consumer_running = consumer.started  # Zustand VOR stop() einfrieren
    if not consumer_running:
        err("C3 Consumer konnte nicht gestartet werden ({}docker logs -f)".format(
            host.remote + " " if host.remote else ""))

    # C2 — Ingress + Effekt-Invariante
    ok, detail = check_ingress(host, args.container, args.bridge_url,
                               args.status_cmd, args.status_needle, args.effect_timeout)
    log(detail)
    if not ok:
        failures.append(detail)

    # C4 — Server-Aktion (Referee -> Bridge -> Mod); Spawn ist offener Punkt
    if not args.skip_wave:
        reached, spawn_ok, detail = check_wave(host, args.container,
                                               args.tournament_url, args.effect_timeout)
        log("C4 wave: " + detail)
        if not reached:
            failures.append("C4 wave rot: Referee-Auftrag kam nicht im Mod an")
        if not spawn_ok:
            warn("C4 OFFENER PUNKT: Wave erreichte den Mod, aber spawned>0 ist "
                 "headless nicht erreichbar (kein Spieler/Bord-Spawner) — "
                 "Sicht-Beleg nur durch Player-Test (Momo/Matheo).")

    time.sleep(3)
    consumer.stop()

    # C3 — Auswertung (Consumer-Zustand von VOR stop() verwenden)
    c_ok, c_detail = classify_consumer(consumer_running, consumer.seen(args.status_needle))
    log("C3 egress: " + c_detail)
    if c_ok != EXEC_RESULT_OK:
        failures.append("C3 egress " + c_detail)

    print_summary(failures, "core-io")
    return 1 if failures else 0


def print_summary(failures: List[str], scope: str) -> None:
    if failures:
        err("Core-IO-Gate ROT ({}): {}".format(scope, "; ".join(failures)))
    else:
        log("Core-IO-Gate GRUEN ({}): C1 + C2-Invariante + C3-Egress".format(scope))


if __name__ == "__main__":
    sys.exit(main())
