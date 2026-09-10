#!/usr/bin/env python3
"""RIFT BATTLE — rbbridge-Poller (Referenz-Beispiel für die Bridge-Seite, v1).

Jede Dedi-Welt (A und B) läuft diesen Poller neben der rbbridge/tools-Suite:
Er fragt GET /state des Tournament-Servers ab und übersetzt Zustandswechsel in
Game-Commands über die lokale exec-Schiene (rbbridge exec-Dispatch / den
Pipe-Kanal des Trainers — siehe trainer/README.md, `exec_cmd_client` ist hier
der Platzhalter für den lokalen Command-Runner der jeweiligen Bridge).

Reine Standardbibliothek (urllib) — keine Dependencies. Konfiguration via Env:

  RBBRIDGE_POLL_URL      Tournament-Server, z. B. http://10.0.0.2:8080/state
                         (Default: http://127.0.0.1:8080/state)
  RBBRIDGE_POLL_WORLD    Diese Welt: "A" oder "B" (Default: A)
  RBBRIDGE_POLL_INTERVAL Sekunden zwischen Polls (Default: 2.0)

Ablauf (dokumentiert in docs/TOURNAMENT_API.md):
  1. phase "running" zum ersten Mal gesehen  -> Unpause-Kommando(n)      (Fallback,
     falls der GO-Push des Servers nicht ankam — idempotent; die authoritative
     Liste kommt aus TOURNAMENT_GO_COMMANDS des Servers, Default debug_dom_resume)
  2. round hochgezählt                        -> cmd "round_start <n>"
  3. reveal neu (round > zuletzt gesehen)     -> cmd "reveal" (HUD-Aufdeckung)
  4. phase "finished"                         -> cmd "match_over"
  5. Meldungen der Welt an den Referee        -> POST /report (siehe unten)

Zusätzlich zeigt das Beispiel, wie die Welt ihren Zustand meldet
(send_state-Egress, Issue #13, konzeptionell): `report_wave_start(...)` und
`report_hq_hp(...)` posten an den Tournament-Server. Der Lua-Mod/RE-Layer
ruft diese Funktionen auf, sobald der echte State-Egress verdrahtet ist.
"""

import json
import os
import sys
import time
import urllib.request

POLL_URL = os.environ.get("RBBRIDGE_POLL_URL", "http://127.0.0.1:8080/state")
WORLD = os.environ.get("RBBRIDGE_POLL_WORLD", "A").strip().upper()
INTERVAL = float(os.environ.get("RBBRIDGE_POLL_INTERVAL", "2.0"))


def run_game_command(cmd: str) -> None:
    """Game-Command lokal ausführen (rbbridge exec-Dispatch).

    v1-Platzhalter: Hier ruft die Bridge ihren Command-Runner auf
    (im Ziel-Setup z. B. exec_cmd_client <cmd> gegen die rbbridge-Named-Pipe
    \\\\.\\pipe\\rbbattle — siehe trainer/protocol.md). Bewusst idempotente
    Kommandos verwenden (GO/Unpause doppelt ist unkritisch).

    Quoting (Issue #18): `cmd` ist EIN String; beim Aufruf des Command-Runners
    als EIN Argument übergeben, sonst verliert `exec_cmd_client` die Argumente
    hinter dem ersten Token (`exec_cmd_client rb_wave 3` -> command="rb_wave"
    -> level 1; `exec_cmd_client "rb_wave 3"` -> level 3).
    """
    print(f"[bridge:{WORLD}] exec: {cmd}", flush=True)
    # TODO(RE): durch echten exec_cmd_client-Aufruf ersetzen.
    # Quoting beachten (Issue #18): cmd als EIN Listenelement = ein Argument:
    #   subprocess.run(["exec_cmd_client", cmd], check=False)
    # NICHT: subprocess.run(["exec_cmd_client", *cmd.split()], check=False)


def get_state():
    with urllib.request.urlopen(POLL_URL, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_report(payload: dict) -> None:
    """Meldung der Welt an den Referee (POST /report)."""
    url = POLL_URL.replace("/state", "/report")
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"content-type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp.read()


def report_wave_start(built_value: int) -> None:
    """Wellenstart dieser Welt melden (Lock + Built-Value → Reveal auf dem Server)."""
    post_report({"world": WORLD, "event": "wave_start", "built_value": built_value})


def report_hq_hp(hp: float) -> None:
    """Aktuellen HQ-HP dieser Welt melden (absolut; 0 = HQ-Tod → Match-Ende)."""
    post_report({"world": WORLD, "event": "hq_hp", "hp": hp})


def report_score_update(score: int, resources: dict, wave: int) -> None:
    """Periodischen State-Snapshot melden (send_state-Egress, Issue #13).

    Der rbbridge-State-Heartbeat (score_update) wird hier an den Referee
    weitergeleitet → erscheint in /state und der Web-UI.
    """
    post_report({
        "world": WORLD,
        "event": "score_update",
        "score": score,
        "resources": resources,
        "wave": wave,
    })


def main() -> None:
    if WORLD not in ("A", "B"):
        sys.exit(f"RBBRIDGE_POLL_WORLD muss 'A' oder 'B' sein (ist: {WORLD})")
    print(f"[bridge:{WORLD}] Poller startet gegen {POLL_URL} (Intervall {INTERVAL}s)", flush=True)

    seen_running = False
    seen_round = 0
    seen_finished = False

    while True:
        try:
            state = get_state()
        except Exception as exc:  # noqa: BLE001 — Poller läuft weiter
            print(f"[bridge:{WORLD}] poll-Fehler: {exc}", flush=True)
            time.sleep(INTERVAL)
            continue

        phase = state.get("phase")
        round_no = int(state.get("round") or 0)

        if phase == "running":
            if not seen_running:
                # GO-Fallback (Sync-Start, Issue #22), falls der Push nicht ankam:
                # Unpause der pausierten Welt. Muss der Server-Konfiguration
                # TOURNAMENT_GO_COMMANDS entsprechen (Default debug_dom_resume,
                # DOM-Ebene — SYNC_START.md). Quoting (Issue #18): EIN String.
                run_game_command("debug_dom_resume")
                seen_running = True
            if round_no > seen_round:
                run_game_command(f"round_start {round_no}")
                seen_round = round_no
            if state.get("reveal") and state["reveal"].get("round", 0) > seen_round:
                run_game_command("reveal")
                seen_round = int(state["reveal"]["round"])
        elif phase == "finished":
            if not seen_finished:
                run_game_command("match_over")
                seen_finished = True
        else:
            # Lobby/Ready: Zustand zurücksetzen, falls ein Rematch ansteht
            seen_running = False
            seen_finished = False
            seen_round = 0

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
