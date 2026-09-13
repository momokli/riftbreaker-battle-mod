#!/usr/bin/env bash
# ============================================================
# rbmods-image-retention.sh — Tag-Retention fuer Mod-Runtime-Images
# ------------------------------------------------------------
# Issue #309 (Parent #301). Der CD-Build taggt jedes Mal neu
# (rb-dedicated:<deploy-sha>), entfernt aber nie alte Tags. Live-Messung auf
# planet (2026-09-12): rb-dedicated 63 Tags / 12 Image-IDs,
# rb-headless-client 46 Tags / 6 IDs — je neue ID ~1,56 GB unique.
#
# Diese Retention loescht NICHT blind (KEIN `docker image prune -a`). Sie
# behaelt je Repo:
#   * das laufende Image — jeder Tag, den ein Container als Config.Image
#     traegt, wird nie angefasst,
#   * den aktuellen Deploy-Tag aus dem gerenderten Compose
#     (RB_PROTECTED_TAGS, z. B. "rb-dedicated:<deploy-sha>"),
#   * die letzten N Rollback-Tags (RB_ROLLBACK_TAGS, Default 2).
# Alles aeltere wird per `docker rmi <repo>:<tag>` entfernt.
#
# Sicherheitsleitplanken:
#   * Pro von einem Container benutzter Image-ID bleibt IMMER mindestens ein
#     Tag erhalten -> ein benutztes Image wird nie untagged/loeschbar.
#   * Ein letzter Guard prueft vor jedem `rmi` erneut, ob ein (auch gestoppter)
#     Container genau diesen Tag referenziert.
#   * Ohne Docker / ohne Tags = No-Op (Exit 0). Idempotent: ein zweiter Lauf
#     entfernt nichts mehr.
#
# Reihenfolge: `docker image ls <repo>` listet neueste zuerst (Docker-Default)
# — die "letzten N" Rollback-Tags sind damit die N obersten Kandidaten.
#
# Aufruf:
#   rbmods-image-retention.sh [--dry-run|--check] [--keep N] [--repo NAME]...
#
#   RB_IMAGE_REPOS="rb-dedicated rb-headless-client" \
#   RB_ROLLBACK_TAGS=2 \
#   RB_PROTECTED_TAGS="rb-dedicated:$(git rev-parse --short=12 HEAD)" \
#   RB_IMAGE_RETENTION_LOG=/var/log/rbmods-image-retention.log \
#     rbmods-image-retention.sh --dry-run
# ============================================================
set -uo pipefail

usage(){
  cat <<'EOF'
rbmods-image-retention.sh — Tag-Retention fuer Mod-Runtime-Images (Issue #309).

Aufruf:
  rbmods-image-retention.sh [--dry-run|--check] [--keep N] [--repo NAME]...

Optionen:
  --dry-run, --check   Nur zeigen, was entfernt wuerde (keine Aenderung).
  --keep N             Anzahl gehaltener Rollback-Tags je Repo (Default 2).
  --repo NAME          Nur dieses Image-Repo bearbeiten (mehrfach moeglich).
  -h, --help           Diese Hilfe.

Umgebung:
  RB_IMAGE_REPOS          Space-Liste der Repos
                          (Default: "rb-dedicated rb-headless-client").
  RB_ROLLBACK_TAGS        Rollback-Tags je Repo (Default 2).
  RB_PROTECTED_TAGS       Space-Liste "repo:tag", die NIE entfernt werden
                          (z. B. der aktuelle Deploy-Tag aus dem Compose).
  RB_IMAGE_RETENTION_LOG  Optionaler Logfile-Pfad (wird angehaengt).
EOF
}

DRY_RUN=0
KEEP=""
REPO_ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run|--check) DRY_RUN=1 ;;
    --keep) KEEP="${2:-}"; shift ;;
    --repo) REPO_ARGS+=("${2:-}"); shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "rbmods-image-retention: unbekannte Option '$1' (siehe --help)." >&2; exit 2 ;;
  esac
  shift
done

LOG="${RB_IMAGE_RETENTION_LOG:-}"

log(){
  local line
  line="$(date -u +%Y-%m-%dT%H:%M:%SZ) rbmods-image-retention: $*"
  printf '%s\n' "$line"
  if [ -n "$LOG" ]; then
    printf '%s\n' "$line" >> "$LOG" 2>/dev/null || true
  fi
}

if [ "${#REPO_ARGS[@]}" -gt 0 ]; then
  REPOS=("${REPO_ARGS[@]}")
else
  read -r -a REPOS <<< "${RB_IMAGE_REPOS:-rb-dedicated rb-headless-client}"
fi

if [ -z "$KEEP" ]; then
  KEEP="${RB_ROLLBACK_TAGS:-2}"
fi
case "$KEEP" in
  ''|*[!0-9]*)
    echo "rbmods-image-retention: --keep/RB_ROLLBACK_TAGS muss eine nicht-negative Ganzzahl sein (ist: '$KEEP')." >&2
    exit 2 ;;
esac

PROTECTED_RAW="${RB_PROTECTED_TAGS:-}"

if ! command -v docker >/dev/null 2>&1; then
  log "docker nicht gefunden — No-Op."
  exit 0
fi

# Container-Sichten EINMAL sammeln (read-only): Referenz (Config.Image) und
# Image-ID je Container, auch gestoppte (docker ps -a).
mapfile -t CONTAINER_IMAGE_REFS < <(docker ps -a --format '{{.Image}}' 2>/dev/null || true)
mapfile -t CONTAINER_IMAGE_IDS < <(docker ps -a --format '{{.ImageID}}' 2>/dev/null || true)

TOTAL_REMOVED=0

for repo in "${REPOS[@]}"; do
  [ -n "$repo" ] || continue

  # Tags dieses Repos: "Tag|ID", neueste zuerst (docker-Default-Reihenfolge).
  mapfile -t rows < <(docker image ls --format '{{.Tag}}|{{.ID}}' "$repo" 2>/dev/null || true)

  ALL_TAGS=()
  declare -A TAG_ID=()
  for row in "${rows[@]}"; do
    tag="${row%%|*}"
    id="${row#*|}"
    [ -n "$tag" ] || continue
    [ "$tag" = "<none>" ] && continue
    ALL_TAGS+=("$tag")
    TAG_ID["$tag"]="$id"
  done

  if [ "${#ALL_TAGS[@]}" -eq 0 ]; then
    log "$repo: keine Tags — uebersprungen."
    continue
  fi

  # --- Schutzmenge bestimmen -------------------------------------------------
  declare -A IS_PROTECTED=()
  # (a) explizit geschuetzte Tags (aktueller Deploy-Tag aus dem Compose).
  for p in $PROTECTED_RAW; do
    case "$p" in
      "${repo}":*) IS_PROTECTED["${p#"${repo}":}"]=1 ;;
    esac
  done
  # (b) Tags, die ein Container als Referenz traegt (Config.Image).
  for ref in "${CONTAINER_IMAGE_REFS[@]}"; do
    case "$ref" in
      "${repo}":*) IS_PROTECTED["${ref#"${repo}":}"]=1 ;;
    esac
  done

  # Image-IDs, die ein Container benutzt (auch gestoppt).
  declare -A IN_USE_ID=()
  for id in "${CONTAINER_IMAGE_IDS[@]}"; do
    [ -n "$id" ] && IN_USE_ID["$id"]=1
  done

  # (c) pro benutzter Image-ID mindestens EINEN Tag retten: das Image darf
  #     nicht untagged werden. Neuester Tag zuerst -> erster Treffer gewinnt.
  declare -A ID_HAS_PROTECTED=()
  for tag in "${!IS_PROTECTED[@]}"; do
    id="${TAG_ID[$tag]:-}"
    [ -n "$id" ] && ID_HAS_PROTECTED["$id"]=1
  done
  for tag in "${ALL_TAGS[@]}"; do
    id="${TAG_ID[$tag]}"
    if [ -n "${IN_USE_ID[$id]:-}" ] && [ -z "${ID_HAS_PROTECTED[$id]:-}" ]; then
      IS_PROTECTED["$tag"]=1
      ID_HAS_PROTECTED["$id"]=1
    fi
  done

  # --- Kandidaten (nicht geschuetztes), neueste zuerst ----------------------
  OTHERS=()
  for tag in "${ALL_TAGS[@]}"; do
    [ -n "${IS_PROTECTED[$tag]:-}" ] && continue
    OTHERS+=("$tag")
  done

  # Die letzten KEEP Kandidaten sind der Rollback-Stand; der Rest faellt.
  if [ "${#OTHERS[@]}" -gt "$KEEP" ]; then
    REMOVE=("${OTHERS[@]:$KEEP}")
  else
    REMOVE=()
  fi

  log "$repo: ${#ALL_TAGS[@]} Tags, geschuetzt ${#IS_PROTECTED[@]}, Rollback ${KEEP} — entferne ${#REMOVE[@]}."

  for tag in "${REMOVE[@]}"; do
    ref="${repo}:${tag}"
    # Letzter Guard direkt vor dem Entfernen: referenziert ein (auch
    # gestoppter) Container genau diesen Tag? Dann nicht anfassen.
    if [ "${#CONTAINER_IMAGE_REFS[@]}" -gt 0 ] \
       && printf '%s\n' "${CONTAINER_IMAGE_REFS[@]}" | grep -Fxq "$ref"; then
      log "behalte (von Container referenziert): $ref"
      continue
    fi
    if [ "$DRY_RUN" = 1 ]; then
      log "[dry-run] wuerde entfernen: $ref"
      continue
    fi
    if docker rmi "$ref" >/dev/null 2>&1; then
      log "entfernt: $ref"
      TOTAL_REMOVED=$((TOTAL_REMOVED + 1))
    else
      log "WARN konnte nicht entfernen (in Benutzung?): $ref"
    fi
  done
done

if [ "$DRY_RUN" = 1 ]; then
  log "dry-run beendet — keine Aenderung."
else
  log "fertig: $TOTAL_REMOVED Tag(s) entfernt."
fi
exit 0
