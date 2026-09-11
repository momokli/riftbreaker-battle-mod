#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
player_count.py - Spielerzahl-Provider aus dem Dedicated-Server-Log (Issue #238).

Ermittelt die aktuelle Spielerzahl des Riftbreaker-Dedicated-Servers aus dem
Container-Log (dieselbe Quelle wie tools/solo-feed/feed.py) und schreibt genau
EINE Ganzzahl >= 0 auf stdout:

  0     = Server leer (letztes Signal PauseGame, kein Join danach) -> Deploy frei.
  n > 0 = n Spieler online (Netto-Joins seit dem letzten PauseGame) -> parken.

Signal-Quelle (disable_steam=1 -> kein Steam-Query, kein RCON konfiguriert;
siehe docs/ASSUMPTIONS.md Fakt 4):

  Join   : OnNetPlayerCreateRequest '...':'<name>'
           ServerGameplayState: Player '...':'<name>'
  Leer   : GameplayState::PauseGame   (server_pause_game_when_empty=1)
  Resume : GameplayState::ResumeGame  (bei ConnectReq / Join)

Algorithmus (juengstes Pause-/Resume-Signal entscheidet):

  * letztes Signal PauseGame, kein Join danach       -> 0
  * Join(s) nach dem letzten PauseGame               -> Anzahl Joins (>= 1)
  * letztes Signal ResumeGame, kein Join danach      -> 1 (Resume = ConnectReq)
  * kein verwertbares Signal (nur unbekannte Zeilen) -> kein Wert, Exit != 0

Kein Leave-Signal im Log -> der Zaehler ist ein Over-Estimate (bewusst
konservativ: lieber parken als blind deployen).

Exit-Codes:
  0 = belastbare Spielerzahl auf stdout
  1 = kein verwertbares Signal (unsicher)
  2 = Log-Kommando/-Datei fehlgeschlagen

Aufruf:
  python3 tools/deploy-gate/player_count.py [--log FILE | --log-cmd CMD] [--tail N]

Nur Standardbibliothek (Python 3.7+), kein pip-Paket noetig.
"""

import argparse
import os
import re
import subprocess
import sys

# Leer-/Pause-Signal (0 Spieler, sofern kein Join danach).
PAUSE = "pause"
# Resume-Signal (ConnectReq / eintreffender Client) - impliziert mind. 1 Spieler.
RESUME = "resume"
# Join-Signal (Spieler erzeugt/verbunden).
JOIN = "join"

# Join-Muster identisch zu tools/solo-feed/feed.py (dieselben Log-Zeilen).
RE_JOIN_CREATE = re.compile(r"OnNetPlayerCreateRequest\s+'[^']*':'([^']+)'")
RE_JOIN_PLAYER = re.compile(r"ServerGameplayState: Player '[^']*':'([^']+)'")

DEFAULT_CONTAINER = "riftbreaker-dedicated"
DEFAULT_TAIL = 400
LOG_CMD_TIMEOUT_S = 30.0

EXIT_OK = 0
EXIT_NO_SIGNAL = 1
EXIT_IO_ERROR = 2


def classify_line(line):
    """Ordnet eine Log-Zeile einem Signal zu: PAUSE, RESUME, JOIN oder None.

    Reihenfolge: Boundary-Signale (Pause/Resume) zuerst, dann Join-Muster.
    Rein textbasiert - kein Timestamp-Parsing noetig (Zeilen sind chronologisch).
    """
    if "GameplayState::PauseGame" in line:
        return PAUSE
    if "GameplayState::ResumeGame" in line:
        return RESUME
    if RE_JOIN_CREATE.search(line) or RE_JOIN_PLAYER.search(line):
        return JOIN
    return None


def count_players(lines):
    """Leitet die Spielerzahl aus chronologischen Log-Zeilen ab.

    Liefert eine Ganzzahl >= 0 oder None (kein verwertbares Signal -> unsicher).
    """
    last_boundary = None
    joins_since_pause = 0
    for line in lines:
        event = classify_line(line)
        if event == PAUSE:
            last_boundary = PAUSE
            joins_since_pause = 0
        elif event == RESUME:
            last_boundary = RESUME
        elif event == JOIN:
            joins_since_pause += 1

    if joins_since_pause > 0:
        # Joins nach dem letzten PauseGame (oder ganz ohne Pause): n Spieler.
        return joins_since_pause
    if last_boundary == PAUSE:
        # Letztes Signal PauseGame, kein Join danach -> leer.
        return 0
    if last_boundary == RESUME:
        # Resume = ConnectReq -> mindestens 1 Spieler.
        return 1
    # Weder Pause/Resume noch Join -> unsicher.
    return None


def resolve_log_cmd(args):
    """Ermittelt das Log-Kommando: --log-cmd > RBM_LOG_CMD > Default --tail N."""
    cmd = args.log_cmd or os.environ.get("RBM_LOG_CMD")
    if cmd:
        return cmd
    tail = args.tail if args.tail and args.tail > 0 else DEFAULT_TAIL
    return "docker logs --tail %d %s" % (tail, DEFAULT_CONTAINER)


def run_log_cmd(cmd):
    """Fuehrt das Log-Kommando aus; liefert stdout-Text oder None bei Fehler."""
    try:
        out = subprocess.check_output(
            cmd, shell=True, stderr=subprocess.DEVNULL, timeout=LOG_CMD_TIMEOUT_S,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return out.decode("utf-8", "replace")


def read_log_text(args):
    """Liefert den Log-Text aus --log (Datei) oder --log-cmd (Kommando) oder None."""
    if args.log:
        try:
            with open(args.log, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return None
    return run_log_cmd(resolve_log_cmd(args))


def build_parser():
    parser = argparse.ArgumentParser(
        description="Spielerzahl aus dem Dedicated-Server-Log (Issue #238): "
                    "eine Ganzzahl >= 0 auf stdout, Exit 0 = belastbar.",
    )
    parser.add_argument(
        "--log",
        default=None,
        help="Log-Datei lesen statt einen Container abzufragen (Fixtures/Tests).",
    )
    parser.add_argument(
        "--log-cmd",
        default=None,
        help="Kommando, das den Log auf stdout schreibt. "
             "Default: 'docker logs --tail <N> riftbreaker-dedicated'. "
             "Alternativ RBM_LOG_CMD.",
    )
    parser.add_argument(
        "--tail",
        type=int,
        default=None,
        help="Anzahl Log-Zeilen fuer das Default-Kommando (Default %d)." % DEFAULT_TAIL,
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    text = read_log_text(args)
    if text is None:
        print(
            "player-count: FEHLER - Log nicht lesbar (--log/--log-cmd/Docker).",
            file=sys.stderr,
        )
        return EXIT_IO_ERROR

    count = count_players(text.splitlines())
    if count is None:
        print(
            "player-count: keine verwertbaren Signale "
            "(kein Pause/Resume/Join) - unsicher.",
            file=sys.stderr,
        )
        return EXIT_NO_SIGNAL

    print(count)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
