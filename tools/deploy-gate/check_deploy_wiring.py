#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_deploy_wiring.py - statischer Wiring-Contract-Check fuer deploy.yml (#238).

Prueft textbasiert (stdlib, KEIN PyYAML - keine zusaetzliche Runner-Abhaengigkeit),
dass die Deploy-Parkierung (Issue #238) im CD-Workflow korrekt verdrahtet ist:

  1. Checkout-Step vorhanden (actions/checkout) - sonst liegt tools/deploy-gate
     gar nicht auf dem Runner.
  2. workflow_dispatch-Trigger mit Inputs `force` + `timeout`.
  3. Gate-Step (deploy_gate.py) liegt VOR dem SSH-Deploy-Step (ssh ... rbd).
  4. Der Gate-Step ruft den Provider player_count.py auf.

Exit-Codes:
  0 = alle Invarianten erfuellt
  1 = mindestens eine Invariante fehlt (praezise Meldung auf stderr)

Aufruf:
  python3 tools/deploy-gate/check_deploy_wiring.py [WORKFLOW-PFAD]
  (Default: .github/workflows/deploy.yml relativ zum Repo-Root)
"""

import os
import re
import sys

DEFAULT_WORKFLOW = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".github", "workflows", "deploy.yml",
)

SSH_LINE = re.compile(r"\bssh\b.*\brbd\b")


def _is_comment(line):
    """YAML-Kommentarzeile (echte Befehle nie, auch nicht inline eingerueckt)."""
    return line.lstrip().startswith("#")


def _index_of(lines, predicate):
    for idx, line in enumerate(lines):
        if not _is_comment(line) and predicate(line):
            return idx
    return None


def check_wiring(path=DEFAULT_WORKFLOW):
    """Liefert eine Liste fehlender Invarianten (leer = alles ok)."""
    problems = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as exc:
        return ["Workflow nicht lesbar (%s): %s" % (path, exc)]

    # 1. Checkout-Step (sonst fehlen tools/deploy-gate/* auf dem Runner).
    if not any("actions/checkout" in line for line in lines):
        problems.append("Checkout-Step fehlt (actions/checkout).")

    # 2. workflow_dispatch-Trigger mit Inputs force + timeout.
    has_dispatch = any(re.match(r"\s*workflow_dispatch\s*:", line) for line in lines)
    if not has_dispatch:
        problems.append("workflow_dispatch-Trigger fehlt.")
    else:
        if not any(re.match(r"^\s{2,}force\s*:", line) for line in lines):
            problems.append("workflow_dispatch Input 'force' fehlt.")
        if not any(re.match(r"^\s{2,}timeout\s*:", line) for line in lines):
            problems.append("workflow_dispatch Input 'timeout' fehlt.")

    # 3. Gate-Step liegt VOR dem SSH-Deploy-Step.
    gate_idx = _index_of(lines, lambda line: "deploy_gate.py" in line)
    ssh_idx = _index_of(lines, lambda line: SSH_LINE.search(line) is not None)
    if gate_idx is None:
        problems.append("Gate-Step fehlt (kein Aufruf von deploy_gate.py).")
    if ssh_idx is None:
        problems.append("SSH-Deploy-Step fehlt (kein 'ssh ... rbd').")
    if gate_idx is not None and ssh_idx is not None and gate_idx >= ssh_idx:
        problems.append("Gate-Step liegt nicht VOR dem SSH-Deploy-Step.")

    # 4. Der Gate-Step ruft den Provider player_count.py auf.
    if gate_idx is not None:
        end = ssh_idx if ssh_idx is not None else len(lines)
        gate_block = lines[gate_idx:end]
        if not any("player_count.py" in line for line in gate_block):
            problems.append("Gate-Step ruft player_count.py nicht auf.")

    return problems


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else DEFAULT_WORKFLOW
    problems = check_wiring(path)
    if problems:
        print("deploy-wiring: FEHLER - %s" % path, file=sys.stderr)
        for problem in problems:
            print("  - %s" % problem, file=sys.stderr)
        return 1
    print("deploy-wiring: ok - %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
