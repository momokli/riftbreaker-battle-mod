#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deploy_gate.py - Deploy-Parkierung: erst ausrollen, wenn 0 Spieler online.

Eigenstaendiges, testbares Modul (Issue #118). Vor jedem Server-Deploy wird
die aktuelle Spielerzahl ermittelt und entschieden, ob sofort deployed wird
oder das Update geparkt wird, bis alle Spieler disconnected sind.

Akzeptanz aus #118:
  - 0 Spieler        -> sofort deployen.
  - Spieler online   -> parken ("geparkt (n Spieler online)"), periodischer
                        Re-Check bis 0, dann deployen.
  - Force            -> sofort deployen (manueller Override).
  - Timeout          -> nichts haengt unbegrenzt; nach Deadline Exit-Code 2.

Spielerzahl-Provider (pluggable): Die tatsaechliche Quelle (RCON/Steam-Query/
Log-Grep des Dedicated Servers) wird per --count-cmd / RBM_COUNT_CMD
eingehaengt. Das Kommando schreibt eine Ganzzahl >= 0 auf stdout. Der
Dev-SP-Server (6321) laeuft mit disable_steam=1 und cli=1; eine verbindliche
RCON-/Query-URL existiert im Repo noch nicht (siehe README) - deshalb ist die
Quelle austauschbar, die Entscheidungslogik darunter ist fest und getestet.

Exit-Codes:
  0 = deploy (leer oder Force)
  1 = Fehler (fehlender Provider / ungueltige Argumente)
  2 = Timeout (geparkt bis Deadline, kein Deploy)

Aufruf (Beispiel):
  python3 tools/deploy-gate/deploy_gate.py \
      --count-cmd '<RCON/Query-Kommando>' --timeout 1800 \
    && ansible-playbook -i deploy/inventory deploy/site.yml --ask-vault-pass

Nur Standardbibliothek (Python 3.7+), kein pip-Paket noetig.
"""

import argparse
import functools
import os
import re
import subprocess
import sys
import time

DEPLOY = "deploy"
PARK = "park"
TIMEOUT = "timeout"

DEFAULT_INTERVAL_S = 10.0
COUNT_CMD_TIMEOUT_S = 30.0


def decide(player_count, force=False):
    """Entscheidungslogik fuer eine bekannte Spielerzahl.

    Liefert (action, detail) mit action in {DEPLOY, PARK}.
    - force:              immer deployen (detail "forced").
    - player_count None:  sicher parken ("unbekannt") - nie blind deployen.
    - 0:                  deployen ("leer").
    - > 0:                parken ("<n> Spieler online").
    """
    if force:
        return (DEPLOY, "forced")
    if player_count is None:
        return (PARK, "unbekannt")
    if player_count < 0:
        raise ValueError("player_count must be >= 0, got %r" % (player_count,))
    if player_count == 0:
        return (DEPLOY, "leer")
    return (PARK, "%d Spieler online" % (player_count,))


def run_count_cmd(cmd):
    """Fuehrt das Provider-Kommando aus und liefert die Spielerzahl (int) oder None.

    Robust: liefert None bei Nicht-Null-Exit, Timeout, leerer oder nicht
    parsbarer Ausgabe (sicheres Parken statt blindem Deploy).
    """
    try:
        out = subprocess.check_output(
            cmd, shell=True, stderr=subprocess.DEVNULL, timeout=COUNT_CMD_TIMEOUT_S,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    text = out.decode("utf-8", "replace").strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        match = re.search(r"\d+", text)
        if not match:
            return None
        value = int(match.group(0))
    if value < 0:
        return None
    return value


def _constant(value):
    """Provider, der immer `value` liefert (Test-/Manual-Override)."""
    def provider():
        return value
    return provider


def wait_until_empty(provider, force=False, timeout=None, interval=DEFAULT_INTERVAL_S,
                     sleep_fn=time.sleep, now_fn=time.time, log=print):
    """Pollt provider, bis leer / Force / Timeout. Liefert finales (action, detail).

    - force: sofort (action=DEPLOY), kein Poll.
    - timeout (s, optional): nach Deadline (action=TIMEOUT) statt unbegrenzt
      haengen; timeout <= 0 oder None = unbegrenzt.
    - log wird fuer jeden Park-Zustand gerufen ("geparkt (n Spieler online)").
    """
    deadline = None
    if timeout is not None and timeout > 0:
        deadline = now_fn() + timeout
    while True:
        count = provider()
        action, detail = decide(count, force=force)
        if action == DEPLOY:
            log("deploy-gate: deploy - %s" % detail)
            return (action, detail)
        log("deploy-gate: geparkt (%s)" % detail)
        if deadline is not None and now_fn() >= deadline:
            log("deploy-gate: timeout nach %ss - geparkt (%s)" % (timeout, detail))
            return (TIMEOUT, detail)
        sleep_fn(interval)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Deploy-Parkierung: erst ausrollen, wenn 0 Spieler online (Issue #118).",
    )
    parser.add_argument(
        "--count-cmd",
        default=None,
        help="Kommando, das die aktuelle Spielerzahl (Ganzzahl >= 0) auf stdout "
             "schreibt (RCON/Steam-Query/Log-Grep). Alternativ RBM_COUNT_CMD.",
    )
    parser.add_argument(
        "--player-count",
        type=int,
        default=None,
        help="Spielerzahl direkt angeben (Test-/Manual-Override, umgeht --count-cmd).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Sofort deployen; ignoriert die Spielerzahl.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Max. Parkdauer in Sekunden; danach Exit-Code 2 (Timeout). "
             "Unset/0 = unbegrenzt. Alternativ RBM_TIMEOUT_S.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_S,
        help="Re-Check-Intervall in Sekunden (Default %s)." % DEFAULT_INTERVAL_S,
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.force:
        action, detail = decide(0, force=True)
        print("deploy-gate: %s - %s" % (action, detail))
        return 0

    if args.player_count is not None:
        provider = _constant(args.player_count)
    else:
        count_cmd = args.count_cmd or os.environ.get("RBM_COUNT_CMD")
        if not count_cmd:
            print(
                "deploy-gate: FEHLER - kein Spielerzahl-Provider "
                "(--count-cmd / --player-count / RBM_COUNT_CMD).",
                file=sys.stderr,
            )
            return 1
        provider = functools.partial(run_count_cmd, count_cmd)

    timeout = args.timeout
    if timeout is None and os.environ.get("RBM_TIMEOUT_S"):
        try:
            timeout = float(os.environ["RBM_TIMEOUT_S"])
        except ValueError:
            print("deploy-gate: FEHLER - RBM_TIMEOUT_S ist keine Zahl.", file=sys.stderr)
            return 1
    if timeout is not None and timeout < 0:
        print("deploy-gate: FEHLER - --timeout darf nicht negativ sein.", file=sys.stderr)
        return 1

    action, detail = wait_until_empty(
        provider, force=False, timeout=timeout, interval=args.interval,
    )
    if action == TIMEOUT:
        print("deploy-gate: TIMEOUT - geparkt (%s), kein Deploy." % detail, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
