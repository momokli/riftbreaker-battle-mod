#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_deploy_wiring.py - statischer Wiring-Contract-Check fuer deploy.yml.

Prueft textbasiert (stdlib, KEIN PyYAML - keine zusaetzliche Runner-Abhaengigkeit),
dass der gate-lose CD-Workflow seine minimale Struktur behaelt:

  1. push-Trigger auf `main` (CD bei jedem Merge auf main).
  2. workflow_dispatch-Trigger vorhanden (manueller Re-Deploy; keine Inputs).
  3. SSH-Deploy-Step vorhanden (`ssh ... rbd`) - die Deploy-Mechanik.
  4. `permissions: contents: read` unveraendert (Least privilege).
  5. `concurrency: group: cd-deploy` unveraendert (kein paralleler Deploy).

Historisch (Issue #238): Die frueher hier gepruefte Park-Verdrahtung
(actions/checkout, `force`/`timeout`-Inputs, `deploy_gate.py`-Gate-Step,
`player_count.py`) wurde mit e8f7783 BEWUSST aus deploy.yml entfernt - die CD
deployt sofort bei jedem Merge auf main. Ob das Gate wieder eingefuehrt wird
(#238/#327), ist offen und wird von diesem Check NICHT entschieden: er
verbietet die Park-Stufe nicht, fordert sie aber auch nicht mehr.

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
PUSH_KEY = re.compile(r"\s*push\s*:")
DISPATCH_KEY = re.compile(r"\s*workflow_dispatch\s*:")
PERMISSIONS_KEY = re.compile(r"\s*permissions\s*:")
CONTENTS_READ = re.compile(r"\s+contents\s*:\s*read\s*$")
CONCURRENCY_KEY = re.compile(r"\s*concurrency\s*:")
CONCURRENCY_GROUP_CD = re.compile(r"\s+group\s*:\s*[\"']?cd-deploy[\"']?\s*$")


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


def _block(lines, key_pattern):
    """Zeilen des Blocks, der mit key_pattern beginnt (inkl. Key-Zeile) oder None."""
    idx = _index_of(lines, lambda line: key_pattern.match(line) is not None)
    if idx is None:
        return None
    return lines[idx:_block_end(lines, idx)]


def check_wiring(path=DEFAULT_WORKFLOW):
    """Liefert eine Liste fehlender Invarianten (leer = alles ok)."""
    problems = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as exc:
        return ["Workflow nicht lesbar (%s): %s" % (path, exc)]

    # 1. push-Trigger auf main (CD bei jedem Merge auf main).
    push = _block(lines, PUSH_KEY)
    if push is None:
        problems.append("push-Trigger fehlt.")
    elif not any(re.search(r"\bbranches\b.*\bmain\b", line) for line in push):
        problems.append("push-Trigger ohne 'branches: [main]'.")

    # 2. workflow_dispatch-Trigger (manueller Re-Deploy; keine Inputs).
    if _block(lines, DISPATCH_KEY) is None:
        problems.append("workflow_dispatch-Trigger fehlt.")

    # 3. SSH-Deploy-Step - die Deploy-Mechanik (`ssh ... rbd`).
    if _index_of(lines, lambda line: SSH_LINE.search(line) is not None) is None:
        problems.append("SSH-Deploy-Step fehlt (kein 'ssh ... rbd').")

    # 4. permissions: contents: read (Least privilege).
    perms = _block(lines, PERMISSIONS_KEY)
    if perms is None or not any(CONTENTS_READ.match(line) for line in perms):
        problems.append(
            "permissions 'contents: read' fehlt (Least privilege)."
        )

    # 5. concurrency: group: cd-deploy (kein paralleler Deploy; gemeinsame
    #    Gruppe mit deploy-release.yml, Issue #290).
    conc = _block(lines, CONCURRENCY_KEY)
    if conc is None or not any(CONCURRENCY_GROUP_CD.match(line) for line in conc):
        problems.append("concurrency-Gruppe 'cd-deploy' fehlt.")

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
