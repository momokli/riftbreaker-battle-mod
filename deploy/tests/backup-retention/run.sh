#!/usr/bin/env bash
# Hermetischer Selbsttest der Backup-/Stray-Retention (Issue #312).
#
# Prüft die ECHTE Task-Datei deploy/roles/riftbreaker-server/tasks/
# backup-retention.yml gegen ein Wegwerf-Fixture (tmpdir):
#   Fall 1:  7 Tarballs + 7 Strays, keep=5 -> genau die 2 ältesten je Gruppe
#            weg, die 5 neuesten bleiben, Fremd-Datei unangetastet.
#   Fall 2:  mods/-Fixture (Guard #212) unverändert.
#   Fall 2b: Backup-Dir == mods/ -> Guard bricht LAUT ab (fail loud, #212).
#   Fall 3:  zweiter Lauf -> changed=0 (idempotent).
#   Fall 4:  --check entfernt NICHTS real.
#   Fall 5:  keep=0 (per -e) -> alle Kandidaten weg (konfigurierbar).
#   Fall 6:  Negativ-Probe (red-before-green) des stat-Guards: die Kopie der
#            Task-Datei OHNE die when-Bedingungen des stat-Guards fasst einen
#            NICHT existierenden Backup-Pfad per `find` an und meldet die
#            Modulwarnung "is not a directory" (rc bleibt 0) bzw. schlägt hart
#            fehl — der geschützte Lauf tut das nicht. Die Negativ-Variante wird
#            vorab als gültiges YAML validiert und ein YAML-Parser-Fehler gilt
#            NIE als "Guard erkannt". Ohne den Guard wäre die Hermetik
#            (check-mode-paths) auf Ansible-Versionen mit hart fehlschlagendem
#            `find` gebrochen.
#
# Kein Host, kein SSH, kein Vault, kein Docker. Läuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
play="$here/main.yml"
task="$repo/deploy/roles/riftbreaker-server/tasks/backup-retention.yml"

fail() { echo "::error::$1"; exit 1; }

base="$(mktemp -d)"
trap 'rm -rf "$base"' EXIT

# Zeitstempel aufsteigend (lexikographisch == chronologisch).
TS=(20260101T000000 20260102T000000 20260103T000000 20260104T000000 \
    20260105T000000 20260106T000000 20260107T000000)

make_fixture() {  # <root>
  local d="$1" ts
  mkdir -p "$d/backups" "$d/mods/rbbattle"
  printf 'sentinel' > "$d/mods/keep-me.txt"
  printf 'x' > "$d/mods/rbbattle/rbbattle.manifest"
  printf 'x' > "$d/mods/family.txt"
  for ts in "${TS[@]}"; do
    : > "$d/backups/rbbattle-$ts.tar.gz"
    mkdir -p "$d/backups/stray-$ts"
    : > "$d/backups/stray-$ts/payload"
  done
  # Fremd-Datei im Backup-Dir: darf NIE angefasst werden.
  : > "$d/backups/README.txt"
}

count_tarballs() { find "$1/backups" -maxdepth 1 -type f -name 'rbbattle-*.tar.gz' | wc -l; }
count_strays()   { find "$1/backups" -maxdepth 1 -type d -name 'stray-*' | wc -l; }

run() {  # <root> [extra-args...]
  local root="$1"; shift
  ansible-playbook "$play" \
    -e "test_fixture_root=$root" -e "test_mods_root=$root/mods" "$@" 2>&1
}

echo "== Fall 1: 7 Tarballs + 7 Strays, keep=5 -> die 2 aeltesten je Gruppe weg =="
fix1="$base/fix1"; make_fixture "$fix1"
run "$fix1" >/dev/null
[ "$(count_tarballs "$fix1")" -eq 5 ] \
  || fail "erwartet 5 Tarballs, sind $(count_tarballs "$fix1")."
[ "$(count_strays "$fix1")" -eq 5 ] \
  || fail "erwartet 5 Strays, sind $(count_strays "$fix1")."
for ts in 20260101T000000 20260102T000000; do
  [ ! -e "$fix1/backups/rbbattle-$ts.tar.gz" ] || fail "alter Tarball $ts haette entfernt werden muessen."
  [ ! -e "$fix1/backups/stray-$ts" ] || fail "alter Stray $ts haette entfernt werden muessen."
done
for ts in 20260103T000000 20260104T000000 20260105T000000 20260106T000000 20260107T000000; do
  [ -f "$fix1/backups/rbbattle-$ts.tar.gz" ] || fail "neuer Tarball $ts fehlt (deterministische Auswahl verletzt)."
  [ -d "$fix1/backups/stray-$ts" ] || fail "neuer Stray $ts fehlt (deterministische Auswahl verletzt)."
done
[ -f "$fix1/backups/README.txt" ] || fail "Fremd-Datei README.txt wurde angefasst."
echo "   OK (5/5 behalten, 2/2 aelteste weg, Fremd-Datei unberuehrt)"

echo "== Fall 2: mods/-Fixture unveraendert (Guard #212) =="
[ -f "$fix1/mods/keep-me.txt" ] \
  && [ -f "$fix1/mods/rbbattle/rbbattle.manifest" ] \
  && [ -f "$fix1/mods/family.txt" ] \
  || fail "mods/-Fixture wurde veraendert (Guard #212 verletzt)."
echo "   OK"

echo "== Fall 2b: Backup-Dir == mods/ -> Guard bricht LAUT ab (fail loud, #212) =="
ov="$base/overlap"; mkdir -p "$ov"
set +e
out="$(run "$ov" -e "riftbreaker_backup_dir=$ov" -e "riftbreaker_mods_dir=$ov" 2>&1)"
rc=$?
set -e
if [ "$rc" -eq 0 ]; then
  printf '%s\n' "$out"
  fail "Overlap (backup_dir == mods) hat NICHT abgebrochen."
fi
grep -q "RETENTION-GUARD" <<<"$out" \
  || { printf '%s\n' "$out"; fail "Overlap-Abbruch ohne RETENTION-GUARD-Meldung."; }
echo "   OK"

echo "== Fall 3: zweiter Lauf -> changed=0 (idempotent) =="
out="$(run "$fix1")"
grep -qE 'changed=0([^0-9]|$)' <<<"$out" \
  || { printf '%s\n' "$out"; fail "zweiter Lauf war nicht idempotent (kein changed=0)."; }
echo "   OK"

echo "== Fall 4: --check entfernt NICHTS real =="
fix2="$base/fix2"; make_fixture "$fix2"
run "$fix2" --check >/dev/null
[ "$(count_tarballs "$fix2")" -eq 7 ] \
  || fail "--check hat Tarballs real entfernt (noch $(count_tarballs "$fix2"))."
[ "$(count_strays "$fix2")" -eq 7 ] \
  || fail "--check hat Strays real entfernt (noch $(count_strays "$fix2"))."
echo "   OK (7/7 unveraendert)"

echo "== Fall 5: keep=0 (per -e) -> alle Kandidaten weg (konfigurierbar) =="
fix3="$base/fix3"; make_fixture "$fix3"
run "$fix3" -e riftbreaker_backup_keep=0 >/dev/null
[ "$(count_tarballs "$fix3")" -eq 0 ] || fail "keep=0 liess Tarballs liegen."
[ "$(count_strays "$fix3")" -eq 0 ] || fail "keep=0 liess Strays liegen."
[ -f "$fix3/backups/README.txt" ] || fail "Fremd-Datei wurde bei keep=0 angefasst."
echo "   OK"

echo "== Fall 6 (Negativ-Probe): stat-Guard ist load-bearing (fehlender Pfad) =="
absent="$base/absent"   # bewusst NICHT angelegt

echo "   -- geschuetzter Lauf gegen fehlenden Pfad: sauber, fasst ihn NICHT an --"
set +e
gout="$(run "$absent")"
grc=$?
set -e
if [ "$grc" -ne 0 ]; then
  printf '%s\n' "$gout"
  fail "geschuetzter Lauf gegen fehlenden Pfad ist nicht gruen (stat-Guard kaputt)."
fi
if grep -qi "is not a directory" <<<"$gout"; then
  printf '%s\n' "$gout"
  fail "geschuetzter Lauf hat den fehlenden Pfad dennoch angefasst (stat-Guard unwirksam)."
fi

echo "   -- ungeschuetzter Lauf (stat-Guard entfernt) MUSS ihn anfassen --"
ng="$base/noguard"
mkdir -p "$ng"
cp "$task" "$ng/backup-retention.yml"
# NUR die stat-Guard-when-Bedingungen entfernen; die Task-Zeilen (stat:/path:/
# register:) bleiben intakt -> valides YAML. Dann laeuft `find` ungeschuetzt
# auf dem fehlenden Pfad.
sed -i '/when: riftbreaker_backup_dir_stat\.stat\.isdir/d' "$ng/backup-retention.yml"
# Negativ-Variante muss gueltiges YAML sein: ein Parse-Fehler darf nie als
# "Guard erkannt" durchgehen.
python3 -c "import yaml; list(yaml.safe_load_all(open('$ng/backup-retention.yml')))" \
  || fail "Negativ-Variante ist kein gueltiges YAML (Testaufbau kaputt)."
if grep -qiE 'yaml\.(parser|scanner|composer)\.' "$ng/backup-retention.yml"; then
  fail "Negativ-Variante enthaelt YAML-Parser-Fehler (Testaufbau kaputt)."
fi
cat > "$ng/play.yml" <<'YAML'
- hosts: localhost
  connection: local
  gather_facts: false
  vars:
    riftbreaker_backup_dir: "{{ ng_root }}/nope"
    riftbreaker_mods_dir: "{{ ng_root }}/mods"
    riftbreaker_backup_keep: 1
  tasks:
    - include_tasks: backup-retention.yml
YAML
set +e
out="$(ansible-playbook "$ng/play.yml" -e "ng_root=$base/ngroot" 2>&1)"
rc=$?
set -e
# Ein Parser-Fehler in der Ausgabe bedeutet: der Testaufbau ist kaputt — niemals
# als "Guard erkannt" verbuchen.
if grep -qiE 'yaml\.(parser|scanner|composer)\.' <<<"$out"; then
  printf '%s\n' "$out"
  fail "Negativ-Probe: YAML-Parser-Fehler (Testaufbau kaputt) — kein Guard-Nachweis."
fi
# Rot-before-green: ohne Guard MUSS der fehlende Pfad sichtbar werden. Der
# ungeschuetzte `find` auf dem fehlenden Pfad liefert rc=0, aber die Warnung
# "is not a directory" (bzw. auf manchen Ansible-Versionen einen harten
# Modulfehler). Fehlt beides, ist der Guard-Nachweis nicht erbracht.
if grep -qi "is not a directory" <<<"$out"; then
  echo "   Negativ-Probe erkannt (find meldet 'is not a directory' ohne stat-Guard)."
elif [ "$rc" -ne 0 ]; then
  echo "   Negativ-Probe erkannt (ungeschuetzter Lauf schlaegt hart fehl, rc=$rc)."
else
  printf '%s\n' "$out"
  fail "Negativ-Probe: ungeschuetzter Lauf fasst den fehlenden Pfad NICHT an (Test erkennt den fehlenden stat-Guard nicht)."
fi

echo "OK: Backup-/Stray-Retention begrenzt deterministisch auf die N neuesten, ist idempotent, --check-fest, konfigurierbar (keep=0), fasst mods/ nie an (#212, inkl. fail-loud-Overlap-Guard) und der stat-Guard ist load-bearing: ohne die when-Bedingungen fasst `find` den fehlenden Pfad an ('is not a directory') — die Negativ-Variante ist dabei gueltiges YAML."
