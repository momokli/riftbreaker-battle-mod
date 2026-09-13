#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_deploy_wiring.py - statischer Wiring-Contract-Check fuer deploy.yml.

Prueft textbasiert (stdlib, KEIN PyYAML - keine zusaetzliche Runner-Abhaengigkeit),
dass der gate-lose CD-Workflow seine minimale Struktur behaelt:

  1. push-Trigger auf `main` (CD bei jedem Merge auf main).
  2. workflow_dispatch-Trigger vorhanden (manueller Re-Deploy; KEINE Inputs).
  3. SSH-Deploy-Step vorhanden (`ssh ... rbd`) - die Deploy-Mechanik.

Hinweis: Die frueher hier gepruefte #238-Park-Verdrahtung (Checkout-Step,
`force`/`timeout`-Inputs, deploy_gate.py-Gate-Step, player_count.py) wurde mit
e8f7783 BEWUSST aus deploy.yml entfernt. Ob das Gate wieder eingefuehrt wird
(#238/#327), ist offen und wird von diesem Check NICHT entschieden.

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


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _block_end(lines, start_idx):
    """Erster Index nach dem Block ab start_idx (Zeile mit gleicher/geringerer
    Einrueckung; Leer- und Kommentarzeilen zaehlen nicht als Blockende)."""
    base = _indent(lines[start_idx])
    for idx in range(start_idx + 1, len(lines)):
        line = lines[idx]
        if not line.strip() or _is_comment(line):
            continue
        if _indent(line) <= base:
            return idx
    return len(lines)


def check_wiring(path=DEFAULT_WORKFLOW):
    """Liefert eine Liste fehlender Invarianten (leer = alles ok)."""
    problems = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as exc:
        return ["Workflow nicht lesbar (%s): %s" % (path, exc)]

    # 1. push-Trigger auf main (CD bei jedem Merge auf main).
    push_idx = _index_of(lines, lambda line: re.match(r"\s*push\s*:", line))
    if push_idx is None:
        problems.append("push-Trigger fehlt.")
    else:
        push_block = lines[push_idx:_block_end(lines, push_idx)]
        if not any(
            re.search(r"\bbranches\b.*\bmain\b", line) for line in push_block
        ):
            problems.append("push-Trigger ohne 'branches: [main]'.")

    # 2. workflow_dispatch-Trigger (manueller Re-Deploy; keine Inputs).
    if not any(re.match(r"\s*workflow_dispatch\s*:", line) for line in lines):
        problems.append("workflow_dispatch-Trigger fehlt.")

    # 3. SSH-Deploy-Step - die Deploy-Mechanik (`ssh ... rbd`).
    if _index_of(lines, lambda line: SSH_LINE.search(line) is not None) is None:
        problems.append("SSH-Deploy-Step fehlt (kein 'ssh ... rbd').")

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
