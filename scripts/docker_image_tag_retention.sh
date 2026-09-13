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
#   * Vor jedem `rmi` prueft ein Guard per `docker ps -aq --filter ancestor=<ref>`,
#     ob ein (auch gestoppter) Container dieses Image benutzt -> dann bleibt der
#     Tag erhalten; ein benutztes Image wird nie untagged/loeschbar. Der Filter
#     loest die Referenz zur Image-ID auf und greift daher AUCH, wenn ein
#     Container aus einer nackten Image-ID gestartet wurde (Config.Image ist
#     dann die kurze ID, kein `repo:tag`) — die reine Config.Image-Gleichheit
#     versagt dort.
#   * Ein letzter Guard prueft zusaetzlich vor jedem `rmi`, ob ein (auch
#     gestoppter) Container genau diesen Tag als Config.Image referenziert.
#   * Ohne Docker / ohne Tags = No-Op (Exit 0). Idempotent: ein zweiter Lauf
#     entfernt nichts mehr.
#
# Hinweis: RB_ROLLBACK_TAGS zaehlt TAGS, nicht distinkte Image-IDs. Traegt eine
# ID mehrere Tags, koennen nach dem Lauf weniger als N verschiedene Images als
# Rollback uebrig bleiben (dafuer ist jedes benutzte Image garantiert getaggt).
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

# Container-Referenzen (Config.Image) EINMAL sammeln (read-only), auch
# gestoppte (docker ps -a). Die Image-ID-Sicht (`{{.ImageID}}`) gibt es in
# Docker 27.3.1 nicht; der Benutzungs-Check laeuft per ancestor-Filter.
mapfile -t CONTAINER_IMAGE_REFS < <(docker ps -a --format '{{.Image}}' 2>/dev/null || true)

TOTAL_REMOVED=0

for repo in "${REPOS[@]}"; do
  [ -n "$repo" ] || continue

  # Tags dieses Repos: "Tag|ID", neueste zuerst (docker-Default-Reihenfolge).
  mapfile -t rows < <(docker image ls --format '{{.Tag}}|{{.ID}}' "$repo" 2>/dev/null || true)

  ALL_TAGS=()
  for row in "${rows[@]}"; do
    tag="${row%%|*}"
    [ -n "$tag" ] || continue
    [ "$tag" = "<none>" ] && continue
    ALL_TAGS+=("$tag")
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

  # (c) "pro benutzter Image-ID mindestens einen Tag retten" ist NICHT mehr
  #     ueber eine Container-Image-ID-Sicht umgesetzt (das Template-Feld
  #     `{{.ImageID}}` existiert in Docker 27.3.1 nicht und liefert nur einen
  #     verschluckten Fehler). Stattdessen prueft der Guard im Entfernen-Loop
  #     je Kandidat `docker ps -aq --filter ancestor=<ref>` (siehe unten) — das
  #     loest die Referenz zur Image-ID auf und deckt auch den Fall "Container
  #     aus nackter Image-ID gestartet" ab.

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
    # Guard 1 (Config.Image-Gleichheit) direkt vor dem Entfernen: referenziert
    # ein (auch gestoppter) Container genau diesen Tag? Dann nicht anfassen.
    if [ "${#CONTAINER_IMAGE_REFS[@]}" -gt 0 ] \
       && printf '%s\n' "${CONTAINER_IMAGE_REFS[@]}" | grep -Fxq "$ref"; then
      log "behalte (von Container referenziert): $ref"
      continue
    fi
    # Guard 2 (ancestor-Filter): benutzt ein (auch gestoppter) Container dieses Image?
    # `--filter ancestor=<ref>` loest die Referenz zur Image-ID auf und greift
    # damit auch, wenn ein Container aus einer nackten Image-ID gestartet wurde
    # (Config.Image = kurze ID) — so bleibt pro benutzter Image-ID mindestens
    # ein Tag erhalten (kein Untag).
    if [ -n "$(docker ps -aq --filter "ancestor=${ref}" 2>/dev/null || true)" ]; then
      log "behalte (Image in Benutzung): $ref"
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
