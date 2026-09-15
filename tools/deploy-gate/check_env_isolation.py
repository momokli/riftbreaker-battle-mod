#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_env_isolation.py — statischer Env-Isolations-Audit (Issue #483, US2).

Prueft textbasiert (stdlib, KEIN PyYAML — keine zusaetzliche Runner-
Abhaengigkeit, Muster von check_deploy_wiring.py), dass jede Variable in den
Env-Override-Dateien klassifiziert ist und die `per_env`-Pflichten erfuellt
sind. Luecke -> exit != 0 (Marker `ENV-ISOLATION-GATE`).

Geprueft wird gegen deploy/env-schema.yml:
  1. Schema sauber: `shared`-Eintrag ohne Begruendung -> Fehler.
  2. `per_env: <var>: [envs]` — jede gelistete Env-Datei MUSS die Variable
     explizit definieren (sonst erbt sie still den dev-Wert aus host_vars).
  3. Jede Top-Level-Variable in ALLEN Var-Quellen (host_vars/planet/vars.yml,
     prod-vars.yml, test-vars.yml) MUSS im Schema klassifiziert sein
     (`per_env` oder `shared`) — sonst rot. Gerade host_vars-only-Keys sind
     die Bug-Klasse des Issues ("was nicht in prod steht, erbt still dev").

`dev` hat keine eigene Override-Datei; dev IST die Basis
(inventory/host_vars/planet/vars.yml). Die Distinctness der per-env-Pfade
gegen den dev-Basiswert asserted zusaetzlich deploy/tasks/env-assert.yml.

Exit-Codes:
  0 = alle Invarianten erfuellt
  1 = mindestens eine Invariante fehlt (praezise Meldung auf stderr)

Aufruf:
  python3 tools/deploy-gate/check_env_isolation.py [--repo-root DIR] [--env NAME]
"""

import argparse
import os
import re
import sys

MARKER = "ENV-ISOLATION-GATE"

VALID_ENVS = ("dev", "prod", "test")
ENV_FILES = {
    "dev": os.path.join("deploy", "inventory", "host_vars", "planet", "vars.yml"),
    "prod": os.path.join("deploy", "prod-vars.yml"),
    "test": os.path.join("deploy", "test-vars.yml"),
}
SCHEMA_REL = os.path.join("deploy", "env-schema.yml")

TOP_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):")
SCHEMA_SECTION = re.compile(r"^(per_env|shared):\s*$")
PER_ENV_ENTRY = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_]*):\s*\[(.*)\]\s*$")
SHARED_ENTRY = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*$")


def default_repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def top_level_keys(path):
    """Top-Level-Keys einer YAML-Datei (Spalte 0, `key:`), Kommentare ignoriert."""
    keys = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return None
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        match = TOP_KEY.match(line)
        if match:
            keys.append(match.group(1))
    return keys


def parse_schema(path):
    """-> (per_env: {var: [envs]}, shared: {var: reason}, problems: [str])."""
    per_env, shared, problems = {}, {}, []
    section = None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as exc:
        return {}, {}, ["Schema nicht lesbar (%s): %s" % (path, exc)]

    for line in lines:
        if line.lstrip().startswith("#") or not line.strip():
            continue
        if line.startswith("---"):
            continue
        sec = SCHEMA_SECTION.match(line)
        if sec:
            section = sec.group(1)
            continue
        if TOP_KEY.match(line) and not line.startswith(" "):
            # Anderer Top-Level-Block (z. B. unbekanntes Feld) — ignorieren.
            continue
        if section == "per_env":
            match = PER_ENV_ENTRY.match(line)
            if match:
                var = match.group(1)
                envs = [e.strip() for e in match.group(2).split(",") if e.strip()]
                per_env[var] = envs
        elif section == "shared":
            match = SHARED_ENTRY.match(line)
            if match:
                var, reason = match.group(1), match.group(2)
                shared[var] = reason

    # Basis-Validierung des Schemas.
    for var, envs in per_env.items():
        if not envs:
            problems.append("per_env '%s' nennt keine Env." % var)
        for env in envs:
            if env not in VALID_ENVS:
                problems.append("per_env '%s' nennt ungueltige Env '%s'." % (var, env))
    for var, reason in shared.items():
        cleaned = reason.strip().strip("'\"")
        if not cleaned:
            problems.append("shared '%s' ohne Begruendung." % var)
    return per_env, shared, problems


def check(repo_root, env=None):
    problems = []
    schema_path = os.path.join(repo_root, SCHEMA_REL)
    per_env, shared, schema_problems = parse_schema(schema_path)
    problems.extend(schema_problems)

    # Dateien einlesen (Top-Level-Keys).
    keys = {}
    for name, rel in ENV_FILES.items():
        path = os.path.join(repo_root, rel)
        parsed = top_level_keys(path)
        if parsed is None:
            problems.append("Vars-Datei nicht lesbar: %s" % rel)
            parsed = []
        keys[name] = parsed

    classified = set(per_env) | set(shared)

    # 2. per_env-Pflicht: jede gelistete Env muss die Variable explizit setzen.
    for var, envs in sorted(per_env.items()):
        for env_name in envs:
            if var not in keys.get(env_name, []):
                problems.append(
                    "per_env '%s' fehlt in %s (%s) — Env wuerde den dev-Wert erben."
                    % (var, ENV_FILES.get(env_name, env_name), env_name)
                )

    # 3. Jeder Top-Level-Key aus ALLEN Var-Quellen muss klassifiziert sein —
    #    auch die dev-Basis (host_vars): ein Key, der nur dort steht, wuerde
    #    sonst still in jeder anderen Env den dev-Wert erben (Kern von #483).
    for env_name in ENV_FILES:
        for var in keys.get(env_name, []):
            if var not in classified:
                problems.append(
                    "Variable '%s' in %s ist in keiner Schema-Klasse (per_env/shared)."
                    % (var, ENV_FILES[env_name])
                )

    if env is not None and env not in VALID_ENVS:
        problems.append("ungueltige --env '%s' (erwartet dev|prod|test)." % env)
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="Env-Isolations-Audit (Issue #483)")
    parser.add_argument("--repo-root", default=default_repo_root(),
                        help="Repo-Root (Default: zwei Ebenen ueber diesem Skript).")
    parser.add_argument("--env", default=None,
                        help="Env-Kontext (dev|prod|test) — nur fuer die Meldung.")
    args = parser.parse_args(argv)

    problems = check(args.repo_root, env=args.env)
    prefix = "%s (env=%s):" % (MARKER, args.env or "?")
    if problems:
        print("%s FEHLER — %d Verletzung(en)" % (prefix, len(problems)), file=sys.stderr)
        for problem in problems:
            print("  - %s" % problem, file=sys.stderr)
        return 1
    print("%s ok — Schema + per-env-Pflicht erfuellt." % prefix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
