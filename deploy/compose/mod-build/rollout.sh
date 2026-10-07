#!/bin/sh
# ============================================================================
# rollout.sh — mod-build init container (Issue #1093).
#
# Portiert den Mod-Block der Ansible-Rolle `riftbreaker-server`
# (tasks/main.yml) + die Retention (tasks/backup-retention.yml, #312) nach
# POSIX sh. Baut das Battle-Mod-Zip aus dem AKTUELLEN Checkout und rollt es
# in das Game-Volume aus:
#
#   1. rbbattle.zip bauen (scripts/package.sh; Content-Root = client-mod/)
#   2. Marker `rbbattle.md5` (md5 des Zips) lesen — Marker == Zip-md5 UND
#      Ziel-Mod-Ordner vorhanden -> No-Op (idempotent)
#   3. Bisherigen Mod-Stand als `rbbattle-<ts>.tar.gz` AUSSERHALB `mods/` sichern
#   4. #212-Fremd-Mod-Guard: jeder andere Ordner mit `*.manifest` in `mods/`
#      wird nach `<backup>/stray-<ts>/` weggeschoben, danach erneut geprueft;
#      bleibt einer -> harter Abbruch
#   5. Zip nach `<mods>/rbbattle` entpacken; Marker schreiben
#   6. Retention (#312): je nur die N neuesten `rbbattle-*.tar.gz` und
#      `stray-*/` behalten (per NAMENS-Zeitstempel, lexikographisch); `mods/`
#      wird dabei NIE angefasst
#
# Fail-loud: jeder Verstoss -> Exit != 0. Idempotent: der zweite Lauf ohne
# Aenderung ist ein sauberer No-Op (kein Backup, kein Re-Entpacken).
#
# Env (Details: deploy/compose/mod-build/README.md):
#   RBB_REF          Build-Ref (Default: "dev") -> als RBB_BUILD_REF an package.sh
#   RBB_GAME_DIR     Game-Volume (Default: /game)
#   RBB_MODS_DIR     mods/ (Default: $RBB_GAME_DIR/mods)
#   RBB_MOD_DIR      Ziel-Mod-Ordner (Default: $RBB_MODS_DIR/rbbattle)
#   RBB_BACKUP_DIR   Backup-Ziel AUSSERHALB mods/ (Default: /backups)
#   RBB_BACKUP_KEEP  je Gruppe behaltene Backups (Default: 5)
#   RBB_SRC          Checkout, read-only (Default: /src)
#   RBB_WORK         schreibbare Build-Kopie des Checkouts (Default: /work)
#   RBB_MARKER_FILE  Marker-Pfad (Default: $RBB_GAME_DIR/rbbattle.md5)
# ============================================================================
set -eu

log()  { printf '[mod-build] %s\n' "$*"; }
fail() { printf '[mod-build] FEHLER: %s\n' "$*" >&2; exit 1; }

RBB_REF="${RBB_REF:-dev}"
src="${RBB_SRC:-/src}"
work="${RBB_WORK:-/work}"
game_dir="${RBB_GAME_DIR:-/game}"
mods_dir="${RBB_MODS_DIR:-$game_dir/mods}"
mod_dir="${RBB_MOD_DIR:-$mods_dir/rbbattle}"
backup_dir="${RBB_BACKUP_DIR:-/backups}"
backup_keep="${RBB_BACKUP_KEEP:-5}"
marker_file="${RBB_MARKER_FILE:-$game_dir/rbbattle.md5}"

# ts: NAMENS-Zeitstempel wie ansible_date_time.iso8601_basic_short
# (YYYYMMDDTHHMMSS) — lexikographisch == chronologisch (Retention sortiert
# ueber den Namen, NIE ueber mtime, #312).
ts() { date -u +%Y%m%dT%H%M%S; }

# md5_of <file> -> md5 hex (md5sum oder BSD md5)
md5_of() {
    if command -v md5sum >/dev/null 2>&1; then
        md5sum "$1" | awk '{print $1}'
    elif command -v md5 >/dev/null 2>&1; then
        md5 -q "$1"
    else
        fail "weder md5sum noch md5 verfuegbar"
    fi
}

# norm_dir <path>: trailing slashes entfernen (fuer den Backup-Guard)
norm_dir() {
    d="$1"
    while [ "$d" != "/" ] && [ "${d%/}" != "$d" ]; do
        d="${d%/}"
    done
    printf '%s' "$d"
}

# extract_zip <zip> <dest>  — unzip, sonst python3-Fallback
extract_zip() {
    if command -v unzip >/dev/null 2>&1; then
        unzip -q -o "$1" -d "$2"
    elif command -v python3 >/dev/null 2>&1; then
        python3 - "$1" "$2" <<'PY'
import sys, zipfile
zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])
PY
    else
        fail "weder unzip noch python3 verfuegbar"
    fi
}

# normalize_zip <zip>  — schreibt das Zip mit FESTEN Metadaten in place neu.
# Warum: package.sh packt via `cp -R` + Zip und der Zip-Header speichert die
# (volatilen) Datei-mtimes -> der Zip-md5 ist zwischen Laeufen instabil. Der
# Marker-No-Op (Schritt 2) wuerde dann NIE greifen. Fuer echte Idempotenz
# normalisieren wir die Zip-Metadaten (feste Zeitstempel, sortierte Eintraege,
# feste Rechte 0644); der INHALT und die entpackte Mod bleiben identisch, nur
# der md5 des Zips ist jetzt stabil bei gleichem Quellstand.
normalize_zip() {
    command -v python3 >/dev/null 2>&1 || fail "python3 wird fuer normalize_zip gebraucht"
    python3 - "$1" "$1.norm" <<'PY'
import sys, zipfile
src, dst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(src) as zi:
    names = sorted(n for n in zi.namelist() if not n.endswith("/"))
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for n in names:
            data = zi.read(n)
            info = zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zo.writestr(info, data)
PY
    mv "$1.norm" "$1"
}

# ---------------------------------------------------------------------------
# Guard: Backup-Ziel darf nicht mods/ sein oder darunter liegen (#212/#312).
# Sonst wuerde ein Backup-Ordner/-Tarball in mods/ als eigene Mod geladen.
# Zuerst pruefen (fail fast), bevor irgendetwas geschrieben/geloescht wird.
# ---------------------------------------------------------------------------
mods_dir_n="$(norm_dir "$mods_dir")"
backup_dir_n="$(norm_dir "$backup_dir")"
case "$backup_dir_n" in
    "$mods_dir_n" | "$mods_dir_n"/*)
        fail "RBB_BACKUP_DIR ($backup_dir) ist mods/ oder liegt darunter — dort wird NICHTS geschrieben/geloescht (#212). Backup-Ziel korrigieren."
        ;;
esac

[ -d "$src" ] || fail "RBB_SRC ($src) fehlt — Checkout read-only nach $src mounten"
mkdir -p "$game_dir" "$mods_dir" "$backup_dir"
marker_parent="$(dirname "$marker_file")"
mkdir -p "$marker_parent"

log "ref=$RBB_REF game=$game_dir mods=$mods_dir mod=$mod_dir backup=$backup_dir keep=$backup_keep"

# ---------------------------------------------------------------------------
# 1. Zip bauen (aus dem aktuellen Checkout).
# /src ist read-only gemountet; package.sh schreibt nach <root>/dist. Deshalb
# den Checkout in ein schreibbares Arbeitsverzeichnis kopieren und DORT bauen
# (Semantik unveraendert: package.sh aus dem Checkout, cwd = Repo-Root).
# ---------------------------------------------------------------------------
log "kopiere Checkout $src -> $work (ohne .git)"
rm -rf "$work"
mkdir -p "$work"
tar -C "$src" --exclude=./.git -cf "$work/.source.tar" .
tar -C "$work" -xf "$work/.source.tar"
rm -f "$work/.source.tar"
[ -f "$work/scripts/package.sh" ] || fail "scripts/package.sh fehlt in $work — falscher Checkout gemountet?"

log "baue rbbattle.zip"
( cd "$work" && RBB_BUILD_REF="$RBB_REF" bash scripts/package.sh )

zip_file="$work/dist/rbbattle.zip"
[ -f "$zip_file" ] || fail "Build lieferte kein $zip_file"
# Zip-Metadaten normalisieren -> md5 stabil bei gleichem Quellstand (No-Op).
# Der INHALT (und die entpackte Mod) bleibt unveraendert.
normalize_zip "$zip_file"
zip_md5="$(md5_of "$zip_file")"
log "Zip: $zip_file (md5=$zip_md5)"

# ---------------------------------------------------------------------------
# 2. Installierten Stand (Marker) lesen und Update-Bedarf bestimmen.
# No-Op, wenn Marker == Zip-md5 UND der Ziel-Ordner steht (nicht isdir/leer).
# ---------------------------------------------------------------------------
marker=""
if [ -f "$marker_file" ]; then
    marker="$(cat "$marker_file")"
fi

mod_dir_ok=false
if [ -d "$mod_dir" ] && find "$mod_dir" -name '*.manifest' -type f 2>/dev/null | grep -q .; then
    mod_dir_ok=true
fi

update_needed=false
if [ "$marker" != "$zip_md5" ] || [ "$mod_dir_ok" != true ]; then
    update_needed=true
fi
log "Marker='$marker' mod_dir_ok=$mod_dir_ok -> update_needed=$update_needed"

# ---------------------------------------------------------------------------
# 3./4. #212-Fremd-Mod-Guard (laeuft unabhaengig vom Update-Bedarf, wie die Rolle).
# Fremd-Ordner mit *.manifest in mods/ wuerden ZUSAETZLICH geladen
# (Versionskonflikt + doppelte Handler). Ausserhalb des Ziel-Mods -> wegschieben,
# dann erneut pruefen; bleibt einer -> harter Abbruch.
# ---------------------------------------------------------------------------
# scan_strays: alle Ordner unter mods/ mit einem *.manifest (rekursiv), OHNE
# den Ziel-Mod (mod_dir) und ohne mods/ selbst.
scan_strays() {
    find "$mods_dir" -name '*.manifest' -type f 2>/dev/null \
        | while IFS= read -r mf; do
            d="$(dirname "$mf")"
            case "$d" in
                "$mod_dir" | "$mod_dir"/*) ;;   # das Ziel-Mod selbst -> ok
                "$mods_dir") ;;                  # Manifest direkt in mods/ -> nicht mods/ selbst verschieben
                *) printf '%s\n' "$d" ;;         # Fremd-Ordner mit Manifest
            esac
        done \
        | sort -u
}

strays="$(scan_strays)"
if [ -n "$strays" ]; then
    stray_target="$backup_dir/stray-$(ts)"
    log "GUARD: Fremd-Mod-Ordner in $mods_dir gefunden -> wegschieben nach $stray_target"
    mkdir -p "$stray_target"
    printf '%s\n' "$strays" | while IFS= read -r d; do
        [ -n "$d" ] || continue
        # `removes`-Semantik der Rolle: nur wegschieben, was noch da ist.
        [ -e "$d" ] || continue
        if mv "$d" "$stray_target/"; then
            log "  weggeschoben: $d"
        else
            log "  WARN: konnte nicht wegschieben: $d"
        fi
    done

    strays_after="$(scan_strays)"
    [ -z "$strays_after" ] || fail "GUARD: in $mods_dir liegt trotz Wegschieben noch ein Fremd-Ordner mit *.manifest ($strays_after) — Deploy abgebrochen (#212)."
    log "GUARD OK: nur $(basename "$mod_dir") traegt eine *.manifest in $mods_dir"
fi

# ---------------------------------------------------------------------------
# 5. Bei Bedarf: alten Stand sichern, dann neu ausrollen + Marker schreiben.
# ---------------------------------------------------------------------------
if [ "$update_needed" = true ]; then
    if [ -d "$mod_dir" ]; then
        backup_path="$backup_dir/rbbattle-$(ts).tar.gz"
        log "backup: $mod_dir -> $backup_path"
        tar -czf "$backup_path" -C "$mods_dir" "$(basename "$mod_dir")"
    fi

    log "rollout: entpacke $(basename "$zip_file") -> $mod_dir"
    rm -rf "$mod_dir"
    mkdir -p "$mod_dir"
    extract_zip "$zip_file" "$mod_dir"

    printf '%s' "$zip_md5" > "$marker_file"
    log "Marker geschrieben: $marker_file = $zip_md5"
else
    log "No-Op: Mod-Stand aktuell (Marker == Zip-md5, Ordner ok)"
fi

# ---------------------------------------------------------------------------
# 6. Retention (#312) — ausschliesslich in $backup_dir, NIE in mods/.
# Nur die N neuesten `rbbattle-*.tar.gz` (files) bzw. `stray-*` (dirs) bleiben;
# "neueste" deterministisch ueber den NAMENS-Zeitstempel (lexikographisch).
# ---------------------------------------------------------------------------
# negative/ungueltige keep-Werte auf 0 klemmen (nie negativ = "alles weg").
case "$backup_keep" in
    '' | *[!0-9]*) backup_keep=0 ;;
esac

retention_group() { # <f|d> <name-pattern>
    r_type="$1"
    r_pat="$2"
    r_list="$(find "$backup_dir" -maxdepth 1 -type "$r_type" -name "$r_pat" | sort)"
    [ -n "$r_list" ] || return 0
    r_count="$(printf '%s\n' "$r_list" | grep -c .)"
    if [ "$r_count" -gt "$backup_keep" ]; then
        printf '%s\n' "$r_list" | head -n "$((r_count - backup_keep))" | while IFS= read -r r_p; do
            [ -n "$r_p" ] || continue
            log "Retention: entferne $(basename "$r_p")"
            rm -rf "$r_p"
        done
    fi
}

retention_group f 'rbbattle-*.tar.gz'
retention_group d 'stray-*'

log "OK — Lauf beendet (update_needed=$update_needed)"
