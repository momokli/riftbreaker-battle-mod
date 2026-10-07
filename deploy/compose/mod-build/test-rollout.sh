#!/bin/bash
# ============================================================================
# test-rollout.sh — hermetischer Selbsttest fuer rollout.sh (Issue #1093).
#
# Kein Netz, kein Docker, kein echtes package.sh: alles in einem tmpdir.
# Ein Stub-`package.sh` kopiert ein vorbereitetes Fixture-Zip nach dist/.
#
# Geprueft werden genau die geforderten Faelle:
#   * warm (Marker == Zip-md5 -> No-Op, kein Re-Entpacken, kein neues Backup)
#   * backup-outside-mods (alter Stand als rbbattle-<ts>.tar.gz ausserhalb mods/)
#   * #212-Fremd-Mod-Guard (weggeschoben UND harter Fail, wenn nicht verschiebbar)
#   * Retention (nur die N neuesten behalten; mods/ unberuehrt)
#   * Guard: Backup-Dir darf nicht mods/ sein / darunter liegen
#
# Aufruf:  bash deploy/compose/mod-build/test-rollout.sh
# ============================================================================
set -eu

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
ROLLOUT="$SELF_DIR/rollout.sh"
[ -f "$ROLLOUT" ] || { echo "rollout.sh nicht gefunden: $ROLLOUT" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

CHECKS=0
t_pass() { CHECKS=$((CHECKS + 1)); printf '  ok   %s\n' "$1"; }
t_fail() { printf '  FAIL %s\n' "$1" >&2; exit 1; }
t_sect() { printf '\n== %s\n' "$1"; }

# run_rbc <logfile> <env...>  — fuehrt rollout via `sh` aus (kein exec-Bit noetig)
run_rbc() {
    r_log="$1"
    shift
    set +e
    env "$@" sh "$ROLLOUT" >"$r_log" 2>&1
    r_rc=$?
    set -e
    return "$r_rc"
}

# md5_of <file> — md5 hex (md5sum oder BSD md5). package.sh baut seit #1101
# deterministisch, der rohe Zip-md5 ist daher stabil — keine Normalisierung mehr.
md5_of() {
    if command -v md5sum >/dev/null 2>&1; then
        md5sum "$1" | awk '{print $1}'
    elif command -v md5 >/dev/null 2>&1; then
        md5 -q "$1"
    else
        echo "test: weder md5sum noch md5 verfuegbar" >&2
        exit 1
    fi
}

make_zip() { # <zipfile> <dir>
    z="$1"
    d="$2"
    rm -f "$z"
    if command -v zip >/dev/null 2>&1; then
        ( cd "$d" && zip -q -r "$z" . )
    elif command -v python3 >/dev/null 2>&1; then
        python3 - "$d" "$z" <<'PY'
import os, sys, zipfile
d, z = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
    for root, _dirs, files in os.walk(d):
        for name in files:
            p = os.path.join(root, name)
            f.write(p, os.path.relpath(p, d))
PY
    else
        echo "test: weder 'zip' noch 'python3' verfuegbar" >&2
        exit 1
    fi
}

GUID='{96745BE8-78FD-4C30-9718-D57AA40B9C09}'

make_fixture() { # <zipfile> <ver>
    fver="$2"
    fdir="$TMP/.fixture-$fver"
    rm -rf "$fdir"
    mkdir -p "$fdir/lua"
    printf 'manifest %s\n' "$fver" > "$fdir/$GUID.manifest"
    printf 'autoexec %s\n' "$fver" > "$fdir/lua/rbbattle_autoexec.lua"
    printf 'readme %s\n' "$fver" > "$fdir/README.md"
    if [ "$fver" = "v2" ]; then printf 'extra\n' > "$fdir/extra.txt"; fi
    make_zip "$1" "$fdir"
}

new_scenario() { # <name>  -> setzt R (Szenario-Wurzel)
    R="$TMP/$1"
    mkdir -p "$R/src/scripts" "$R/src/.fixture" "$R/game/mods" "$R/backups"
    cat > "$R/src/scripts/package.sh" <<'STUB'
#!/bin/bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$root/dist"
cp "$root/.fixture/rbbattle.zip" "$root/dist/rbbattle.zip"
echo "[stub-package] wrote $root/dist/rbbattle.zip"
STUB
    chmod +x "$R/src/scripts/package.sh"
    make_fixture "$R/src/.fixture/rbbattle.zip" v1
    S="$R/src"; G="$R/game"; B="$R/backups"; W="$R/work"
}

count_files() { find "$1" -maxdepth 1 -type f -name "$2" 2>/dev/null | grep -c . || true; }
count_dirs()  { find "$1" -maxdepth 1 -type d -name "$2" 2>/dev/null | grep -c . || true; }

# ---------------------------------------------------------------------------
t_sect "1) warm: erst-Deploy, dann No-Op bei gleichem Zip (idempotent)"
new_scenario warm
if run_rbc "$R/run1.log" RBB_SRC="$S" RBB_GAME_DIR="$G" RBB_BACKUP_DIR="$B" RBB_WORK="$W" RBB_REF=dev; then rc=0; else rc=$?; fi
[ "$rc" -eq 0 ] || t_fail "run1 exit=$rc ($(tail -n 3 "$R/run1.log" | tr '\n' ' '))"
[ -f "$G/mods/rbbattle/$GUID.manifest" ] || t_fail "run1: Mod-Manifest fehlt"
[ -f "$G/rbbattle.md5" ] || t_fail "run1: Marker fehlt"
marker1="$(cat "$G/rbbattle.md5")"
[ "$marker1" = "$(md5_of "$R/src/.fixture/rbbattle.zip")" ] || t_fail "Marker != Zip-md5"
[ "$(count_files "$B" 'rbbattle-*.tar.gz')" -eq 0 ] || t_fail "run1: unerwartetes Backup beim Erst-Deploy"
t_pass "run1: ausgerollt + Marker gesetzt, kein Backup (kein alter Stand)"

# Sentinel: würde der zweite Lauf re-entpacken, wäre er weg.
printf 'sentinel\n' > "$G/mods/rbbattle/SENTINEL"
if run_rbc "$R/run2.log" RBB_SRC="$S" RBB_GAME_DIR="$G" RBB_BACKUP_DIR="$B" RBB_WORK="$W" RBB_REF=dev; then rc=0; else rc=$?; fi
[ "$rc" -eq 0 ] || t_fail "run2 (warm) exit=$rc"
[ -f "$G/mods/rbbattle/SENTINEL" ] || t_fail "run2 hat den Mod-Ordner neu entpackt (kein No-Op!)"
[ "$(cat "$G/rbbattle.md5")" = "$marker1" ] || t_fail "run2: Marker geaendert"
[ "$(count_files "$B" 'rbbattle-*.tar.gz')" -eq 0 ] || t_fail "run2: warm-Lauf hat ein Backup erzeugt"
grep -q 'No-Op' "$R/run2.log" || t_fail "run2: kein No-Op geloggt"
t_pass "run2 (warm): No-Op — kein Re-Entpacken, kein Backup, Marker unveraendert"

# ---------------------------------------------------------------------------
t_sect "2) backup-outside-mods: geaenderter Build -> alter Stand als tar.gz ausserhalb mods/"
new_scenario backup
if run_rbc "$R/run1.log" RBB_SRC="$S" RBB_GAME_DIR="$G" RBB_BACKUP_DIR="$B" RBB_WORK="$W" RBB_REF=dev; then rc=0; else rc=$?; fi
[ "$rc" -eq 0 ] || t_fail "backup run1 exit=$rc"
printf 'hand-edit\n' > "$G/mods/rbbattle/HANDEDIT"        # alter Stand
make_fixture "$R/src/.fixture/rbbattle.zip" v2             # neuer Build
if run_rbc "$R/run2.log" RBB_SRC="$S" RBB_GAME_DIR="$G" RBB_BACKUP_DIR="$B" RBB_WORK="$W" RBB_REF=dev; then rc=0; else rc=$?; fi
[ "$rc" -eq 0 ] || t_fail "backup run2 exit=$rc"
[ -f "$G/mods/rbbattle/extra.txt" ] || t_fail "neuer Stand (extra.txt) nicht ausgerollt"
[ "$(cat "$G/rbbattle.md5")" = "$(md5_of "$R/src/.fixture/rbbattle.zip")" ] || t_fail "Marker nicht auf neuen Zip-md5 gesetzt"
tb="$(find "$B" -maxdepth 1 -type f -name 'rbbattle-*.tar.gz' | head -n1)"
[ -n "$tb" ] || t_fail "kein Backup-Tarball erzeugt"
[ -z "$(find "$G/mods" -name '*.tar.gz' 2>/dev/null)" ] || t_fail "Backup liegt IN mods/ (Guard-Verstoss)"
tar -tzf "$tb" | grep -q 'rbbattle/HANDEDIT' || t_fail "Backup enthaelt den alten Stand nicht"
t_pass "Backup '$tb' ausserhalb mods/, enthaelt alten Stand; neuer Stand ausgerollt"

# ---------------------------------------------------------------------------
t_sect "3) #212-Fremd-Mod-Guard: Fremd-Ordner wird weggeschoben"
new_scenario guard
if run_rbc "$R/run1.log" RBB_SRC="$S" RBB_GAME_DIR="$G" RBB_BACKUP_DIR="$B" RBB_WORK="$W" RBB_REF=dev; then rc=0; else rc=$?; fi
[ "$rc" -eq 0 ] || t_fail "guard run1 exit=$rc"
mkdir -p "$G/mods/foreign/lua"
printf 'x\n' > "$G/mods/foreign/$GUID.manifest"
printf 'y\n' > "$G/mods/foreign/lua/x.lua"
if run_rbc "$R/run2.log" RBB_SRC="$S" RBB_GAME_DIR="$G" RBB_BACKUP_DIR="$B" RBB_WORK="$W" RBB_REF=dev; then rc=0; else rc=$?; fi
[ "$rc" -eq 0 ] || t_fail "guard run2 exit=$rc ($(tail -n 3 "$R/run2.log" | tr '\n' ' '))"
[ ! -e "$G/mods/foreign" ] || t_fail "Fremd-Ordner liegt noch in mods/"
stray="$(find "$B" -maxdepth 1 -type d -name 'stray-*' | head -n1)"
[ -n "$stray" ] || t_fail "kein stray-Ordner angelegt"
[ -d "$stray/foreign" ] || t_fail "Fremd-Ordner nicht in den stray-Ordner verschoben"
[ -f "$G/mods/rbbattle/$GUID.manifest" ] || t_fail "Ziel-Mod beschaedigt"
t_pass "Fremd-Ordner weggeschoben nach $(basename "$stray")/, Ziel-Mod unversehrt"

# ---------------------------------------------------------------------------
t_sect "4) #212-Fremd-Mod-Guard: nicht verschiebbar -> harter Fail (Exit != 0)"
new_scenario guardfail
mkdir -p "$G/mods/foreign"
printf 'x\n' > "$G/mods/foreign/$GUID.manifest"
# fake `date` -> fixer Zeitstempel, damit das stray-Ziel vorhersagbar ist
fake="$R/fakebin"; mkdir -p "$fake"
printf '#!/bin/sh\necho 20240101T000000\n' > "$fake/date"; chmod +x "$fake/date"
# Zielpfad als Ordner mit gleichnamiger DATEI -> `mv` kann den Ordner nicht dort ablegen
mkdir -p "$B/stray-20240101T000000"
: > "$B/stray-20240101T000000/foreign"
if run_rbc "$R/run.log" PATH="$fake:$PATH" RBB_SRC="$S" RBB_GAME_DIR="$G" RBB_BACKUP_DIR="$B" RBB_WORK="$W" RBB_REF=dev; then rc=0; else rc=$?; fi
[ "$rc" -ne 0 ] || t_fail "guard-fail: erwartete Exit != 0, bekam 0"
[ -e "$G/mods/foreign" ] || t_fail "guard-fail: Fremd-Ordner verschwand trotz nicht moeglichem Verschieben"
grep -qi 'GUARD' "$R/run.log" || t_fail "guard-fail: keine GUARD-Meldung im Log"
t_pass "nicht verschiebbarer Fremd-Ordner -> harter Abbruch (#212), Ordner bleibt"

# ---------------------------------------------------------------------------
t_sect "5) Retention (#312): je nur die 5 neuesten behalten; mods/ unberuehrt"
new_scenario retention
if run_rbc "$R/run1.log" RBB_SRC="$S" RBB_GAME_DIR="$G" RBB_BACKUP_DIR="$B" RBB_WORK="$W" RBB_REF=dev RBB_BACKUP_KEEP=5; then rc=0; else rc=$?; fi
[ "$rc" -eq 0 ] || t_fail "retention run1 exit=$rc"
i=1
while [ "$i" -le 7 ]; do
    : > "$B/rbbattle-2020010${i}T000000.tar.gz"
    mkdir -p "$B/stray-2020010${i}T000000"
    i=$((i + 1))
done
printf 'keep\n' > "$B/keepme.txt"
if run_rbc "$R/run2.log" RBB_SRC="$S" RBB_GAME_DIR="$G" RBB_BACKUP_DIR="$B" RBB_WORK="$W" RBB_REF=dev RBB_BACKUP_KEEP=5; then rc=0; else rc=$?; fi
[ "$rc" -eq 0 ] || t_fail "retention run2 exit=$rc"
[ "$(count_files "$B" 'rbbattle-*.tar.gz')" -eq 5 ] || t_fail "Tarballs: erwartete 5, hatte $(count_files "$B" 'rbbattle-*.tar.gz')"
[ "$(count_dirs "$B" 'stray-*')" -eq 5 ] || t_fail "Strays: erwartete 5, hatte $(count_dirs "$B" 'stray-*')"
[ ! -e "$B/rbbattle-20200101T000000.tar.gz" ] || t_fail "aeltester Tarball nicht entfernt"
[ ! -e "$B/stray-20200101T000000" ] || t_fail "aeltester Stray nicht entfernt"
[ -e "$B/rbbattle-20200107T000000.tar.gz" ] || t_fail "neuester Tarball faelschlich entfernt"
[ -e "$B/keepme.txt" ] || t_fail "unbeteiligte Datei entfernt (Retention zu gierig)"
[ -f "$G/mods/rbbattle/$GUID.manifest" ] || t_fail "mods/ wurde von der Retention angefasst"
t_pass "Retention: 5/5 behalten, aelteste weg, keepme.txt + mods/ unberuehrt"

# ---------------------------------------------------------------------------
t_sect "6) Fail-loud: Backup-Dir == mods/ bzw. darunter -> harter Abbruch"
new_scenario backupguard
if run_rbc "$R/run.log" RBB_SRC="$S" RBB_GAME_DIR="$G" RBB_BACKUP_DIR="$G/mods" RBB_WORK="$W" RBB_REF=dev; then rc=0; else rc=$?; fi
[ "$rc" -ne 0 ] || t_fail "backup==mods: erwartete Exit != 0, bekam 0"
if run_rbc "$R/run2.log" RBB_SRC="$S" RBB_GAME_DIR="$G" RBB_BACKUP_DIR="$G/mods/backups" RBB_WORK="$W" RBB_REF=dev; then rc=0; else rc=$?; fi
[ "$rc" -ne 0 ] || t_fail "backup unter mods/: erwartete Exit != 0, bekam 0"
t_pass "Backup-Dir in/unter mods/ bricht laut ab"

printf '\nAlle %d Checks OK.\n' "$CHECKS"
