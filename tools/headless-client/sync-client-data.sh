#!/usr/bin/env bash
#
# sync-client-data.sh — Riftbreaker-Client-Daten: lan → planet (~13 GB)
#
# Synchronisiert die lokale Riftbreaker-Installation (GOG) von `lan` in das
# Client-Volume auf `planet` für die Headless-Client-Umgebung (/srv/rbclient/game).
#
# Ablauf:
#   1. Vorab-Größencheck (Quelle vs. freier Platz auf planet) — read-only
#   2. rsync (idempotent, -a) mit dokumentierten Includes/Excludes
#   3. Verifikation: Dateianzahl + Gesamtgröße, danach MD5-Stichprobe
#      (jede N-te Datei, Default N=50)
#
# Hosts sind Tailscale-Aliasse aus ~/.ssh/config (lan, planet) — Mesh-first,
# kein Zugriff über Public-IPs. `rsync` läuft über die lokale ssh-Konfiguration.
#
# Includes/Excludes (dokumentiert):
#   Inkludiert: der gesamte Spiel-Ordner (Daten, Binaries, Assets).
#   Exkludiert (Dateisystem-/Laufzeit-Müll, kein Spielinhalt):
#     .DS_Store, Thumbs.db, desktop.ini   — OS-Dateisystem-Artefakte
#     *.log, *.tmp                        — Laufzeit-Logs/Temp-Dateien
#   Hinweis: `--delete` entfernt auf planet NUR Dateien, die auf lan fehlen und
#   nicht exkludiert sind (kein --delete-excluded). Exkludierte Dateien auf dem
#   Ziel bleiben unangetastet.
#
# Nutzung:
#   ./sync-client-data.sh                 Voll-Sync + Verifikation
#   ./sync-client-data.sh --dry-run       Probe-Lauf (kein Transfer, keine Verifikation)
#   ./sync-client-data.sh --delete        Voll-Sync inkl. --delete (Ziel spiegeln)
#   ./sync-client-data.sh --no-verify     Sync ohne Verifikation
#   ./sync-client-data.sh --sample 100    MD5-Stichprobe jede 100. Datei
#   DRY_RUN=1 ./sync-client-data.sh       Legacy-Env-Variante des Dry-Run
#
# Hinweis: Ersttransfer ≈ 13 GB — lange Laufzeit, nicht interaktiv abbrechen.

set -euo pipefail

# --- Konfiguration (per Environment überschreibbar) ---------------------------
SRC_HOST="${SRC_HOST:-lan}"                                     # momo@lan (Homelab)
SRC_DIR="${SRC_DIR:-/home/momo/share/games/The Riftbreaker}"    # Quelle (Leerzeichen ok)
DST_HOST="${DST_HOST:-planet}"                                  # root@planet (Hetzner)
DST_DIR="${DST_DIR:-/srv/rbclient/game}"                        # Client-Volume, Ziel

SSH_BIN="${SSH_BIN:-ssh}"
RSYNC_BIN="${RSYNC_BIN:-rsync}"

# Jede N-te Datei wird per md5sum stichprobenartig geprüft (0 = Stichprobe aus).
SAMPLE_EVERY="${SAMPLE_EVERY:-50}"

# Dokumentierte Excludes — siehe Kopfkommentar & README.
RSYNC_EXCLUDES=(
    "--exclude=.DS_Store"
    "--exclude=Thumbs.db"
    "--exclude=desktop.ini"
    "--exclude=*.log"
    "--exclude=*.tmp"
)

# --- Zustand (Flags) ----------------------------------------------------------
DRY_RUN="${DRY_RUN:-0}"
DELETE=0
VERIFY=1

# --- Hilfe --------------------------------------------------------------------
usage() {
    cat <<'EOF'
Usage: sync-client-data.sh [OPTIONEN]

Sync der Riftbreaker-Client-Daten von lan → planet (~13 GB).

Optionen:
  -n, --dry-run     Probe-Lauf: rsync --dry-run, kein Transfer, keine Verifikation
      --delete      rsync --delete: Dateien entfernen, die auf der Quelle fehlen
      --no-verify   Verifikation (Anzahl/Größe + MD5-Stichprobe) überspringen
  -s, --sample N    MD5-Stichprobe jede N-te Datei (Default 50; 0 = aus)
  -h, --help        Diese Hilfe anzeigen

Environment-Overrides:
  SRC_HOST, SRC_DIR, DST_HOST, DST_DIR, SSH_BIN, RSYNC_BIN, SAMPLE_EVERY, DRY_RUN
EOF
}

# --- Argumente ----------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--dry-run)   DRY_RUN=1 ;;
        --delete)       DELETE=1 ;;
        --no-verify)    VERIFY=0 ;;
        -s|--sample)    SAMPLE_EVERY="${2:?--sample benötigt eine Zahl}"; shift ;;
        -h|--help)      usage; exit 0 ;;
        --)             shift; break ;;
        -*)             printf 'Unbekannte Option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
        *)              printf 'Unerwartetes Argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

# --- Helfer -------------------------------------------------------------------
# Menschlich lesbare Byte-Angabe.
human() {
    awk -v b="${1:-0}" 'BEGIN{
        i=0; v=b;
        while (v>=1024 && i<4) { v/=1024; i++ }
        u[0]="B"; u[1]="KiB"; u[2]="MiB"; u[3]="GiB"; u[4]="TiB";
        printf "%.1f %s\n", v, u[i]
    }'
}

# Byte-Größe eines Verzeichnisses remote ermitteln ("" bei Fehler).
remote_du() { # host dir
    "$SSH_BIN" "$1" "du -sb '$2' 2>/dev/null | cut -f1" || true
}

# Anzahl regulärer Dateien remote ermitteln ("" bei Fehler).
remote_file_count() { # host dir
    "$SSH_BIN" "$1" "cd '$2' && find . -type f | wc -l" || true
}

# Verfügbaren Platz (Bytes) auf dem Dateisystem des Zielpfads ermitteln.
# Läuft zum nächsten existierenden Vorfahren-Pfad hoch, falls das Ziel noch fehlt.
remote_avail() { # host dir
    "$SSH_BIN" "$1" 'bash -s' -- "$2" <<'REMOTE' || true
d="$1"
p="$d"
while [ ! -d "$p" ] && [ "$p" != "/" ]; do p="$(dirname "$p")"; done
df -B1 --output=avail "$p" 2>/dev/null | tail -n 1
REMOTE
}

# MD5-Stichprobe: sortierte relative Pfade, jede N-te Datei, md5sum je Datei.
sample_md5() { # host dir n
    local n="$3"
    "$SSH_BIN" "$1" "cd '$2' && find . -type f | sort | awk 'NR % $n == 1' | \
while IFS= read -r f; do md5sum -- \"\$f\"; done" || true
}

# --- Schritt 1: Vorab-Größencheck --------------------------------------------
size_check() {
    echo "== 1/3 Vorab-Größencheck (read-only) =="

    local src_bytes dst_cur dst_avail
    src_bytes="$(remote_du "$SRC_HOST" "$SRC_DIR")"
    dst_cur="$(remote_du "$DST_HOST" "$DST_DIR")"
    dst_avail="$(remote_avail "$DST_HOST" "$DST_DIR")"

    if [[ -n "$src_bytes" && "$src_bytes" =~ ^[0-9]+$ ]]; then
        echo "  Quelle (${SRC_HOST}): ${src_bytes} Bytes ($(human "$src_bytes"))"
    else
        echo "  WARNUNG: Quellgröße nicht ermittelbar (${SRC_HOST}:${SRC_DIR} erreichbar?)." >&2
    fi

    if [[ -n "$dst_cur" && "$dst_cur" =~ ^[0-9]+$ ]]; then
        echo "  Ziel vorhanden (${DST_HOST}): ${dst_cur} Bytes ($(human "$dst_cur"))"
    else
        echo "  Ziel (${DST_HOST}) noch leer/nicht vorhanden."
    fi

    if [[ -n "$dst_avail" && "$dst_avail" =~ ^[0-9]+$ ]]; then
        echo "  Freier Platz auf Ziel-Filesystem: ${dst_avail} Bytes ($(human "$dst_avail"))"
    else
        echo "  WARNUNG: freier Platz nicht ermittelbar (${DST_HOST})." >&2
    fi

    if [[ -n "$src_bytes" && "$src_bytes" =~ ^[0-9]+$ && \
          -n "$dst_avail"  && "$dst_avail" =~ ^[0-9]+$ && \
          -n "$dst_cur"    && "$dst_cur" =~ ^[0-9]+$ ]]; then
        # Platzbedarf: Quelle minus bereits vorhandener Zielinhalt (Überschneidung).
        local needed=$(( src_bytes > dst_cur ? src_bytes - dst_cur : 0 ))
        if (( needed > dst_avail )); then
            echo "  FEHLER: zu wenig Platz — benötigt ~$(human "$needed"), frei ${dst_avail} Bytes." >&2
            exit 1
        fi
        echo "  Platzbedarf ~$(human "$needed") ≤ frei ${dst_avail} Bytes → OK."
    fi
    echo
}

# --- Schritt 2: rsync ---------------------------------------------------------
run_rsync() {
    local -a opts=(-a --partial --info=progress2 --stats -s "${RSYNC_EXCLUDES[@]}")
    [[ "$DRY_RUN" -eq 1 ]] && opts+=(--dry-run)
    [[ "$DELETE" -eq 1 ]] && opts+=(--delete)

    echo "== 2/3 rsync: ${SRC_HOST}:${SRC_DIR}/ → ${DST_HOST}:${DST_DIR}/ =="
    [[ "$DRY_RUN" -eq 1 ]] && echo "  (Dry-Run — es wird nichts übertragen)"
    echo "  rsync ${opts[*]}"

    "$RSYNC_BIN" "${opts[@]}" "${SRC_HOST}:${SRC_DIR}/" "${DST_HOST}:${DST_DIR}/"
    echo
}

# --- Schritt 3: Verifikation --------------------------------------------------
verify_sync() {
    echo "== 3/3 Verifikation =="

    local sc dc ss ds
    sc="$(remote_file_count "$SRC_HOST" "$SRC_DIR")"
    dc="$(remote_file_count "$DST_HOST" "$DST_DIR")"
    ss="$(remote_du "$SRC_HOST" "$SRC_DIR")"
    ds="$(remote_du "$DST_HOST" "$DST_DIR")"

    echo "  Dateianzahl: Quelle=${sc:-?}  Ziel=${dc:-?}"
    echo "  Größe:       Quelle=$(human "${ss:-0}")  Ziel=$(human "${ds:-0}")"

    local ok=1
    if [[ -z "$sc" || -z "$dc" ]]; then
        echo "  FEHLER: Dateianzahl auf einer Seite nicht ermittelbar." >&2; ok=0
    elif [[ "$sc" != "$dc" ]]; then
        echo "  FEHLER: Dateianzahl weicht ab (Quelle=$sc, Ziel=$dc)." >&2; ok=0
    fi

    if [[ -z "$ss" || -z "$ds" ]]; then
        echo "  FEHLER: Gesamtgröße auf einer Seite nicht ermittelbar." >&2; ok=0
    elif [[ "$ss" != "$ds" ]]; then
        echo "  FEHLER: Gesamtgröße weicht ab (Quelle=$ss, Ziel=$ds Bytes)." >&2; ok=0
    fi

    if (( SAMPLE_EVERY > 0 )); then
        echo "  MD5-Stichprobe (jede ${SAMPLE_EVERY}. Datei) ..."
        local sm dm
        sm="$(sample_md5 "$SRC_HOST" "$SRC_DIR" "$SAMPLE_EVERY")"
        dm="$(sample_md5 "$DST_HOST" "$DST_DIR" "$SAMPLE_EVERY")"
        if [[ -z "$sm" && -z "$dm" ]]; then
            echo "  MD5-Stichprobe: keine Dateien gefunden."
        elif [[ "$sm" == "$dm" ]]; then
            local n_checked
            n_checked="$(printf '%s\n' "$sm" | grep -c . || true)"
            echo "  MD5-Stichprobe OK: ${n_checked} Dateien identisch."
        else
            echo "  FEHLER: MD5-Stichprobe weicht ab:" >&2
            diff <(printf '%s\n' "$sm") <(printf '%s\n' "$dm") >&2 || true
            ok=0
        fi
    fi

    if [[ "$ok" -eq 1 ]]; then
        echo "  Verifikation OK."
    else
        echo "  Verifikation FEHLGESCHLAGEN." >&2
        return 1
    fi
}

# --- Hauptablauf --------------------------------------------------------------
size_check
run_rsync
if [[ "$DRY_RUN" -eq 0 && "$VERIFY" -eq 1 ]]; then
    verify_sync
elif [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Dry-Run: Verifikation übersprungen (kein Transfer)."
fi
echo "Fertig."
