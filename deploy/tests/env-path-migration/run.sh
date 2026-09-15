#!/usr/bin/env bash
# Hermetischer Selbsttest der Env-Pfad-Migration (Issue #483, Ziel B).
#
# Prueft die ECHTE Task-Datei deploy/tasks/env-path-migration.yml in einem
# Wegwerf-Verzeichnis (kein Host, kein Vault, keine Prod-Aktion):
#   * alter Pfad vorhanden, neuer fehlt -> `mv` (kein copy) + Symlink Alt->Neu
#   * erneuter Lauf               -> no-op (idempotent, changed=0)
#   * Alt UND Neu vorhanden       -> kein mv (mehrdeutig, nur Warnung)
#
# Zusaetzlich (Befund 1, Verifier-Retry): WIRING-Nachweis — die regulaeren
# Plays deploy/site.yml + deploy/deploy-prod.yml MUESSEN die Migration in den
# `pre_tasks` VOR den Rollen einbinden (automatischer CD → sonst deployt der
# naechste Lauf die neuen Pfade ohne migrierten Stand). Das prueft
# `ansible-playbook --list-tasks` (echter Play-Parse, kein Host-Kontakt).
# Die Negativ-Probe entfernt den include aus einer Fixture-Kopie und MUSS das
# Fehlen dann erkennen.
#
# Läuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
play="$here/main.yml"

fail() { echo "::error::$1"; exit 1; }

base="$(mktemp -d)"
trap 'rm -rf "$base"' EXIT

# Alt-Pfade mit Inhalt (mv-Nachweis: Inhalt muss am NEUEN Pfad liegen).
mkdir -p "$base/old-game" "$base/old-rbtools" "$base/both-old" "$base/both-new" "$base/old-staging"
printf 'game-content' > "$base/old-game/marker.txt"
printf 'bin' > "$base/old-rbtools/rbbridge.dll"
printf 'staging' > "$base/old-staging/rbbridge.dll"

echo "== Lauf 1: mv + Symlink =="
ansible-playbook "$play" -e "test_base=$base"

[ -d "$base/new/game" ] || fail "neuer Pfad new/game fehlt"
[ -e "$base/new/game/marker.txt" ] || fail "mv hat den Inhalt nicht mitgenommen (kein copy-Nachweis)"
[ -L "$base/old-game" ] || fail "alter Pfad ist kein Symlink"
[ "$(readlink "$base/old-game")" = "$base/new/game" ] || fail "Symlink zeigt nicht auf den neuen Pfad"
[ ! -e "$base/old-game/marker.txt" ] || true   # Altpfad liest ueber den Symlink den neuen Inhalt
[ -e "$base/old-game/marker.txt" ] || fail "Symlink-Uebergang kaputt (Altpfad liest neuen Inhalt nicht)"
[ -L "$base/old-rbtools" ] || fail "alter rbtools-Pfad ist kein Symlink"
# Verschachteltes Paar (Befund #496-Review): new/rbtools/.staging unterhalb von
# new/rbtools — der Parent darf NICHT vorab angelegt werden (sonst mv-No-Op).
[ -d "$base/new/rbtools/.staging" ] || fail "verschachtelter neuer Pfad new/rbtools/.staging fehlt"
[ -e "$base/new/rbtools/.staging/rbbridge.dll" ] || fail "mv hat den verschachtelten Inhalt nicht mitgenommen"
[ -L "$base/old-staging" ] || fail "alter staging-Pfad ist kein Symlink"
[ "$(readlink "$base/old-staging")" = "$base/new/rbtools/.staging" ] || fail "staging-Symlink zeigt falsch"

echo "== Mehrdeutig: Alt UND Neu vorhanden -> kein mv =="
[ -d "$base/both-old" ] || fail "mehrdeutiger Alt-Pfad wurde faelschlich verschoben"
[ ! -L "$base/both-old" ] || fail "mehrdeutiger Alt-Pfad wurde faelschlich verlinkt"

echo "== Lauf 2: no-op (idempotent, changed=0) =="
out2="$(ansible-playbook "$play" -e "test_base=$base")"
if ! grep -qE 'changed=0' <<<"$out2"; then
  echo "$out2"
  fail "erneuter Lauf war nicht idempotent (changed != 0)"
fi

echo "OK: Env-Pfad-Migration idempotent (mv + Symlink, kein copy; Issue #483)."

# ---------------------------------------------------------------------------
# Befund 1 (Verifier-Retry): Wiring in den regulaeren Plays.
# ---------------------------------------------------------------------------
migration_task="Env-Pfad-Migration (#483)"

# `--list-tasks` parst das Play (inkl. dynamischem include_tasks) und listet die
# pre_tasks in Ausfuehrungsreihenfolge VOR den Rollen. Kein Host-Kontakt.
list_tasks() {  # <playbook> <inventory> [extra ansible args...]
  local pb="$1" inv="$2"; shift 2
  ansible-playbook -i "$inv" "$pb" --list-tasks "$@" 2>/dev/null
}

check_wiring() {  # <playbook> <inventory> [extra] -> 0 wenn Migration in pre_tasks vor Rollen
  local pb="$1" inv="$2"; shift 2
  local tasks mig_line role_line
  tasks="$(list_tasks "$pb" "$inv" "$@")" || return 1
  mig_line="$(grep -nF "$migration_task" <<<"$tasks" | head -1 | cut -d: -f1)"
  [ -n "$mig_line" ] || return 1
  # Erste Rollen-Task-Zeile (Muster " <role> : <task>") markiert das Ende der pre_tasks.
  role_line="$(grep -nE '^ +[A-Za-z0-9_-]+ : ' <<<"$tasks" | head -1 | cut -d: -f1)"
  [ -n "$role_line" ] || return 1
  [ "$mig_line" -lt "$role_line" ]
}

echo "== Wiring: Migration liegt in pre_tasks VOR den Rollen =="
check_wiring "$repo/deploy/site.yml" "$repo/deploy/inventory" \
  || fail "deploy/site.yml bindet die Env-Pfad-Migration NICHT in den pre_tasks vor den Rollen ein (Befund 1)."
echo "   site.yml: OK"
check_wiring "$repo/deploy/deploy-prod.yml" "$repo/deploy/inventory" -e "@$repo/deploy/prod-vars.yml" \
  || fail "deploy/deploy-prod.yml bindet die Env-Pfad-Migration NICHT in den pre_tasks vor den Rollen ein (Befund 1)."
echo "   deploy-prod.yml: OK"

echo "== Negativ-Probe: ohne include erkennt check_wiring das Fehlen =="
fixture="$(mktemp -d)"
cp -r "$repo/deploy" "$fixture/deploy"
# include-Block (name + include_tasks, 2 Zeilen) aus der site.yml-Kopie entfernen.
python3 - "$fixture/deploy/site.yml" <<'PY'
import sys
p = sys.argv[1]
lines = open(p, encoding="utf-8").read().splitlines(keepends=True)
out, skip = [], 0
for ln in lines:
    if "Env-Pfad-Migration (#483)" in ln:
        skip = 2  # name-Zeile + include_tasks-Zeile
        continue
    if skip:
        skip -= 1
        continue
    out.append(ln)
open(p, "w", encoding="utf-8").writelines(out)
PY
if check_wiring "$fixture/deploy/site.yml" "$fixture/deploy/inventory"; then
  fail "Negativ-Probe: check_wiring meldet OK, obwohl der Migration-include fehlt (Wiring-Test wirkungslos)."
fi
rm -rf "$fixture"
echo "   Negativ-Probe erkannt."

echo "OK: Migration in pre_tasks von site.yml/deploy-prod.yml verdrahtet (Befund 1)."
