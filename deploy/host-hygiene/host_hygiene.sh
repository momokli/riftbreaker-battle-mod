#!/usr/bin/env bash
# ============================================================
# rbmods-host-hygiene.sh — dangling Docker-Images + rbtools test-* Retention aufräumen (Issue #308/#606)
# ------------------------------------------------------------
# Läuft als systemd-Timer (Rolle deploy/roles/host-hygiene) auf planet.
#
#   docker image prune -f     # NUR dangling (ungetaggte <none>-Layer), KEIN -a
#
# Warum KEIN -a: `docker image prune -a` entfernt ALLE Images ohne laufenden
# Container — inklusive des getaggten Rollback-Stands `rb-dedicated:<alte-sha>`.
# Der Rollback-Stand muss liegen bleiben; `prune` (ohne `-a`) fasst nur
# ungetaggte Layer an und kann ein getaggtes Image deshalb nie erwischen.
#
# Der Prune wirkt host-weit: dangling Images entstehen in allen Stacks auf
# planet (nicht nur im Mod-Stack). Das ist beabsichtigt (Issue #308).
#
# Umgebung (setzt die systemd-Unit; Defaults s.u.):
#   RB_HYGIENE_IMAGE_REPOS          Repo-Präfixe für den Vorher/Nachher-Nachweis
#   RB_HYGIENE_DRY_RUN              1 = nur anzeigen, nichts löschen
#   RB_HYGIENE_RBTOOLS_DIR          Basis der rbtools-Env-Dirs (Default /opt/rbmods/rbtools)
#   RB_HYGIENE_RBTOOLS_RETENTION    Anzahl gehaltener test-*-Verzeichnisse (Default 10)
# ============================================================
# `pipefail` sorgt dafür, dass ein fehlgeschlagenes `docker image ls` in der
# Zähl-Pipeline nicht stillschweigend als "0" durchgeht (Review-Blocker 2).
set -uo pipefail

REPOS="${RB_HYGIENE_IMAGE_REPOS:-rb-dedicated rb-headless-client}"
DRY_RUN="${RB_HYGIENE_DRY_RUN:-0}"

log(){ printf 'rbmods-host-hygiene: %s\n' "$*"; }

# --- rbtools test-* Retention (Issue #606) -----------------------------------
# Boot-Test-Leak: der Teardown (boot-test.yml) raeumt rbtools_dir
# (/opt/rbmods/rbtools/test-<run>) nicht ab -> ~90 Verzeichnisse und wachsend.
# Diese Retention behaelt die N neuesten (mtime) test-*-Verzeichnisse und
# entfernt aeltere. Unabhaengig von docker: laeuft VOR dem Image-Prune, damit
# ein Docker-Problem die rbtools-Hygiene nicht mitblockiert.
#
# Leitplanken:
#   * NUR `test-*`-Verzeichnisse (nie dev/prod/staging), Top-Level unter
#     $RBTOOLS_DIR.
#   * Kein `rm -rf` ohne bounded, pattern-geprueften Pfad: jeder Kandidat
#     stammt aus dem Glob `$RBTOOLS_DIR/test-*/` und wird nochmals per case
#     gegen `$RBTOOLS_DIR/test-*` geprueft, bevor er entfernt wird.
RBTOOLS_DIR="${RB_HYGIENE_RBTOOLS_DIR:-/opt/rbmods/rbtools}"
RBTOOLS_RETENTION="${RB_HYGIENE_RBTOOLS_RETENTION:-10}"

rbtools_retention_prune() {
  local base="${1%/}" keep="$2" dirs n i removed=0
  # Glob ohne Treffer bleibt literal -> `ls` gibt nichts aus (stderr
  # verworfen), dirs bleibt leer. `ls -1dt` sortiert nach mtime (neueste
  # zuerst) und ist portabel (GNU + BSD/macOS) — `find -printf` waere es nicht.
  # Bewusst `ls -1dt` statt `find -printf`: letzteres ist GNU-only und liefert
  # auf BSD/macOS eine leere Liste (siehe crash_collector.sh, `retention_prune`).
  # shellcheck disable=SC2012
  mapfile -t dirs < <(ls -1dt -- "${base}"/test-*/ 2>/dev/null | sed 's|/$||')
  n=${#dirs[@]}
  # dirs ist neueste-zu-aelteste; Indizes >= keep sind die aelteren -> weg.
  for ((i = keep; i < n; i++)); do
    case "${dirs[$i]}" in
      "${base}"/test-*) ;;   # ok: bounded, test-* unter base
      *) log "WARN: ueberspringe unerwarteten Retention-Kandidaten (nicht test-* unter ${base}): ${dirs[$i]}"; continue ;;
    esac
    if [ "$DRY_RUN" = "1" ]; then
      log "Retention [dry-run]: wuerde entfernen ${dirs[$i]}"
    else
      log "Retention: entferne ${dirs[$i]}"
      rm -rf -- "${dirs[$i]}"
      removed=$((removed + 1))
    fi
  done
  [ "$n" -gt 0 ] && log "Retention: ${n} test-* Verzeichnis(se), behalte ${keep}, entferne ${removed}."
}

if [ -z "${RBTOOLS_DIR}" ] || [ "${RBTOOLS_DIR}" = "/" ]; then
  log "WARN: RB_HYGIENE_RBTOOLS_DIR leer oder '/' — rbtools-Retention uebersprungen."
else
  case "${RBTOOLS_RETENTION}" in
    ''|*[!0-9]*) log "WARN: RB_HYGIENE_RBTOOLS_RETENTION muss eine nicht-negative Ganzzahl sein (ist '${RBTOOLS_RETENTION}') — rbtools-Retention uebersprungen." ;;
    *) rbtools_retention_prune "${RBTOOLS_DIR}" "${RBTOOLS_RETENTION}" ;;
  esac
fi

if ! command -v docker >/dev/null 2>&1; then
  log "docker nicht gefunden — nichts zu tun"; exit 0
fi
if ! docker info >/dev/null 2>&1; then
  log "Docker-Daemon nicht erreichbar — Abbruch, nichts angefasst"; exit 1
fi

# Getaggte Mod-Images (rb-*) zählen. Diese Menge darf sich durch den Lauf NICHT
# ändern (Nachweis aus dem Issue: getaggte Image-Menge vor/nach identisch).
tagged_count(){
  local repo total=0 n
  for repo in $REPOS; do
    n="$(docker image ls --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -c "^${repo}:" || true)"
    total=$((total + n))
  done
  printf '%s' "$total"
}

# Dangling-Images zählen. Mit `pipefail` schlägt die Pipeline fehl, wenn
# `docker image ls` fehlschlägt — sonst würde ein Daemon-Hänger als "0"
# durchgehen und ein stiller No-op als Erfolg gemeldet.
count_dangling(){ docker image ls -f dangling=true -q 2>/dev/null | wc -l | tr -d ' '; }

DANGLING_BEFORE="$(count_dangling)" || { log "FEHLER: 'docker image ls' (dangling) fehlgeschlagen — Zustand unklar, Abbruch"; exit 1; }
TAGGED_BEFORE="$(tagged_count)"
log "vorher: ${DANGLING_BEFORE} dangling, ${TAGGED_BEFORE} getaggte Mod-Images (${REPOS// /, })"

if [ "$DRY_RUN" = "1" ]; then
  log "DRY-RUN: wuerde 'docker image prune -f' ausfuehren (dangling only, kein -a)"
else
  log "docker image prune -f (dangling only, kein -a)"
  # Rückgabecode prüfen: ein fehlgeschlagener Prune darf NICHT als "ok"
  # durchgehen — sonst meldet der Timer Erfolg, obwohl nichts freigegeben wurde.
  PRUNE_RC=0
  docker image prune -f || PRUNE_RC=$?
  if [ "$PRUNE_RC" -ne 0 ]; then
    log "FEHLER: docker image prune fehlgeschlagen (rc=${PRUNE_RC}) — nichts aufgeraeumt"
    exit 1
  fi
fi

DANGLING_AFTER="$(count_dangling)" || { log "FEHLER: 'docker image ls' (dangling) nach dem Prune fehlgeschlagen — Abbruch"; exit 1; }
TAGGED_AFTER="$(tagged_count)"
log "nachher: ${DANGLING_AFTER} dangling, ${TAGGED_AFTER} getaggte Mod-Images"

# Leitplanke (Issue #308): ein `prune` ohne `-a` darf getaggte Images nie
# antasten. Weicht die Menge ab, ist etwas grundlegend falsch — laut scheitern.
if [ "$TAGGED_BEFORE" != "$TAGGED_AFTER" ]; then
  log "FEHLER: getaggte Mod-Images haben sich geaendert (${TAGGED_BEFORE} -> ${TAGGED_AFTER}) — erwartet wird Gleichstand"
  exit 1
fi

log "ok: dangling ${DANGLING_BEFORE} -> ${DANGLING_AFTER}, getaggte Mod-Images unveraendert (${TAGGED_AFTER})"
