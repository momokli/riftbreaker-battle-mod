#!/usr/bin/env bash
# ============================================================
# tests/shell/image-retention.test.sh
# ------------------------------------------------------------
# Planetfreier Red/Green-Test fuer scripts/docker_image_tag_retention.sh
# (Issue #309). Beweist, dass die Retention den Rollback-Stand NICHT
# wegwirft:
#   * das laufende Image (Container-Referenz) bleibt unangetastet,
#   * der aktuelle Deploy-Tag bleibt,
#   * die letzten N Rollback-Tags bleiben,
#   * nur aeltere Tags gehen per `docker rmi` weg,
#   * ein Image, das ein Container benutzt, wird nie untagged — auch wenn der
#     Container aus einer nackten Image-ID gestartet wurde (Config.Image =
#     kurze ID), dann rettet der `ps -aq --filter ancestor=<ref>`-Guard den Tag,
#   * --dry-run veraendert nichts.
#
# Kein Docker/planet noetig: ein Fake-`docker` bildet die ECHTE CLI-Oberflaeche
# (Docker 27.3.1) nach — inklusive des ungueltigen Template-Felds `{{.ImageID}}`
# (Fehler, NICHT die ID) — und protokolliert `rmi` nur.
#
# Laeuft in CI (lint.yml, shellcheck-Job) und lokal:
#   tests/shell/image-retention.test.sh
# ============================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT="${REPO_ROOT}/scripts/docker_image_tag_retention.sh"
BASH_BIN="$(command -v bash)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

BIN="${TMP}/bin"
mkdir -p "$BIN"

# Fake-Docker — bildet die vom Skript genutzten Aufrufe wie echtes Docker ab.
#   FAKE_SCENARIO   REPO|TAG|ID     (Reihenfolge = neueste zuerst; ID = kurze ID)
#   FAKE_CONTAINERS REF|ID          REF = Config.Image des Containers (repo:tag
#                                   ODER nackte kurze ID), ID = Image-ID
#   FAKE_RMI_LOG    Zieldatei fuer entfernte Referenzen
cat > "${BIN}/docker" <<'FAKE'
#!/usr/bin/env bash
case "${1:-}" in
  image)
    # docker image ls --format '{{.Tag}}|{{.ID}}' <repo>
    repo=""
    for a in "$@"; do
      case "$a" in
        image|ls|--format|'{{.Tag}}|{{.ID}}') : ;;
        *) repo="$a" ;;
      esac
    done
    awk -F'|' -v r="$repo" '$1==r {print $2"|"$3}' "$FAKE_SCENARIO"
    ;;
  ps)
    fmt=""; filter=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --format) fmt="${2:-}"; shift ;;
        --filter) filter="${2:-}"; shift ;;
      esac
      shift
    done
    case "$fmt" in
      '{{.Image}}')
        # Config.Image: genau die beim Erstellen benutzte Referenz
        # (repo:tag ODER nackte kurze ID).
        awk -F'|' 'NF{print $1}' "$FAKE_CONTAINERS"
        ;;
      '{{.ImageID}}')
        # echtes Docker 27.3.1: kein gueltiges Feld -> Fehler. NICHT dieselbe
        # Ausgabe wie `image ls {{.ID}}` (das ist der Bug aus B1).
        printf '%s\n' "docker: Error response from daemon: template: :1:5: executing \"\" at <.ImageID>: can't evaluate field ImageID in type *formatter.ContainerContext" >&2
        exit 1
        ;;
      '')
        # docker ps -aq --filter ancestor=<ref>
        case "$filter" in
          ancestor=*)
            ref="${filter#ancestor=}"
            want="$ref"
            case "$ref" in
              *:*)
                # Tag-Referenz -> Image-ID aus dem Szenario aufloesen
                want="$(awk -F'|' -v k="$ref" '{ if ($1":"$2==k) { print $3; exit } }' "$FAKE_SCENARIO")"
                ;;
            esac
            [ -n "$want" ] || exit 0
            # nur Container, deren Image-ID zu <ref> passt (exakt oder Prefix)
            awk -F'|' -v w="$want" 'NF && index($2,w)==1 { print "c"NR }' "$FAKE_CONTAINERS"
            ;;
        esac
        ;;
    esac
    ;;
  rmi)
    printf '%s\n' "${2:-}" >> "$FAKE_RMI_LOG"
    ;;
esac
exit 0
FAKE
chmod +x "${BIN}/docker"

FAIL=0

# --- Szenario-Dateien --------------------------------------------------------
# 5 Tags, 5 verschiedene Image-IDs; Container laeuft auf sha5.
S_FLAT="${TMP}/scenario-flat"
cat > "$S_FLAT" <<'EOF'
rb-dedicated|sha5|111111111111
rb-dedicated|sha4|222222222222
rb-dedicated|sha3|333333333333
rb-dedicated|sha2|444444444444
rb-dedicated|sha1|555555555555
EOF
C_RUN5="${TMP}/containers-run5"
printf '%s\n' "rb-dedicated:sha5|111111111111" > "$C_RUN5"

# Rollback geht nach dem Cleanup noch: die letzten 2 (sha4, sha3) muessen
# danach noch als Tag existieren (nicht im rmi-Log) und inspizierbar bleiben.
expect_kept(){
  local log="$1" ref="$2" label="$3"
  if grep -Fxq "$ref" "$log" 2>/dev/null; then
    printf 'FAIL  %s — %s wurde entfernt, obwohl es Rollback-Stand ist\n' "$label" "$ref"
    FAIL=1
  else
    printf 'PASS  %s — %s bleibt (Rollback geht noch)\n' "$label" "$ref"
  fi
}

# Fuehrt das Skript mit Fake-Docker aus und vergleicht die entfernten Refs.
#   $1 Label  $2 Szenario  $3 Container  $4 protected  $5 keep
#   $6 erwartete entfernte Refs (space-Liste)  $7 Extra-Args (z. B. --dry-run)
run_case(){
  local label="$1" scenario="$2" containers="$3" protected="$4" keep="$5"
  local want="$6" extra="${7:-}" rmi log got rc=0 want_sorted
  rmi="${TMP}/rmi-$$-$RANDOM.log"
  : > "$rmi"
  # shellcheck disable=SC2086  # $extra ist absichtlich eine Wortliste (z. B. --dry-run)
  FAKE_SCENARIO="$scenario" FAKE_CONTAINERS="$containers" FAKE_RMI_LOG="$rmi" \
  RB_IMAGE_REPOS="rb-dedicated" RB_PROTECTED_TAGS="$protected" \
  RB_ROLLBACK_TAGS="$keep" \
    PATH="${BIN}:${PATH}" "$BASH_BIN" "$SCRIPT" $extra >/dev/null 2>&1 || rc=$?
  log="$rmi"
  got="$(sort -u "$log" 2>/dev/null | tr '\n' ' ' | sed -e 's/^ *//' -e 's/ *$//')"
  # shellcheck disable=SC2086  # $want ist absichtlich eine Wortliste
  want_sorted="$(printf '%s\n' $want | sort -u | tr '\n' ' ' | sed -e 's/^ *//' -e 's/ *$//')"
  if [ "$rc" -eq 0 ] && [ "$got" = "$want_sorted" ]; then
    printf 'PASS  %s (entfernt: %s)\n' "$label" "${got:-—}"
  else
    printf 'FAIL  %s — rc=%s erwartet="%s" bekommen="%s"\n' \
      "$label" "$rc" "$want_sorted" "$got"
    FAIL=1
  fi
}

# 1) Standardfall: laufendes Bild (sha5) + 2 Rollback-Tags (sha4, sha3) bleiben;
#    nur die beiden aeltesten gehen.
run_case "laufendes Image + 2 Rollback behalten" \
  "$S_FLAT" "$C_RUN5" "rb-dedicated:sha5" 2 "rb-dedicated:sha1 rb-dedicated:sha2"

# 2) Rollback-Nachweis: kein `rmi` trifft den laufenden Tag oder die Rollbacks.
RMI2="${TMP}/rmi2.log"; : > "$RMI2"
FAKE_SCENARIO="$S_FLAT" FAKE_CONTAINERS="$C_RUN5" FAKE_RMI_LOG="$RMI2" \
RB_IMAGE_REPOS="rb-dedicated" RB_PROTECTED_TAGS="rb-dedicated:sha5" RB_ROLLBACK_TAGS=2 \
  PATH="${BIN}:${PATH}" bash "$SCRIPT" >/dev/null 2>&1
expect_kept "$RMI2" "rb-dedicated:sha5" "laufendes Image unangetastet"
expect_kept "$RMI2" "rb-dedicated:sha4" "Rollback-Tag 1"
expect_kept "$RMI2" "rb-dedicated:sha3" "Rollback-Tag 2"

# 3) Mehrere Tags auf derselben Image-ID: aeltere Duplikate gehen, aber die ID
#    bleibt (ein benutztes Image wird nie untagged). sha5/sha4 = idB (laufend),
#    sha3/2/1 = idA.
S_DUP="${TMP}/scenario-dup"
cat > "$S_DUP" <<'EOF'
rb-dedicated|sha5|666666666666
rb-dedicated|sha4|666666666666
rb-dedicated|sha3|777777777777
rb-dedicated|sha2|777777777777
rb-dedicated|sha1|777777777777
EOF
run_case "Duplikat-IDs: aeltere Tags weg, ID bleibt" \
  "$S_DUP" "$C_RUN5" "rb-dedicated:sha5" 2 "rb-dedicated:sha1 rb-dedicated:sha2"

# 4) Ein (gestoppter) Container auf einem AELTEREN Tag schuetzt genau diesen
#    Tag — auch wenn er ausserhalb des Rollback-Fensters liegt.
C_TWO="${TMP}/containers-two"
printf '%s\n' "rb-dedicated:sha5|111111111111" "rb-dedicated:sha2|444444444444" > "$C_TWO"
run_case "aelterer, referenzierter Tag wird geschuetzt" \
  "$S_FLAT" "$C_TWO" "rb-dedicated:sha5" 2 "rb-dedicated:sha1"

# 5) B1-Regression: Container aus einer NACKTEN Image-ID gestartet — echtes
#    Docker liefert dann als Config.Image die kurze ID (hier 555555555555), kein
#    `repo:tag`. Die Config.Image-Gleichheit greift NICHT; nur der
#    `ps -aq --filter ancestor=<ref>`-Guard rettet den Tag des benutzten Images.
C_BYID="${TMP}/containers-byid"
printf '%s\n' "555555555555|555555555555" > "$C_BYID"
run_case "Container per nackter Image-ID: ancestor-Filter rettet den Tag" \
  "$S_FLAT" "$C_BYID" "rb-dedicated:sha5" 2 "rb-dedicated:sha2"

# 6) RB_ROLLBACK_TAGS=0: nur das laufende Image bleibt.
run_case "Rollback-Tags=0: nur laufendes Image" \
  "$S_FLAT" "$C_RUN5" "rb-dedicated:sha5" 0 \
  "rb-dedicated:sha1 rb-dedicated:sha2 rb-dedicated:sha3 rb-dedicated:sha4"

# 7) --dry-run aendert nichts (rmi-Log bleibt leer).
run_case "dry-run entfernt nichts" \
  "$S_FLAT" "$C_RUN5" "rb-dedicated:sha5" 2 "" "--dry-run"

# 8) Kleinere Tag-Menge als das Rollback-Fenster: No-Op (nichts zu entfernen).
S_MIN="${TMP}/scenario-min"
printf '%s\n' "rb-dedicated|sha5|111111111111" "rb-dedicated|sha4|222222222222" > "$S_MIN"
run_case "weniger Tags als Rollback-Fenster: No-Op" \
  "$S_MIN" "$C_RUN5" "rb-dedicated:sha5" 2 ""

# 9) Ohne Docker: No-Op mit Exit 0.
rc=0
PATH="/nonexistent" RB_IMAGE_REPOS="rb-dedicated" "$BASH_BIN" "$SCRIPT" >/dev/null 2>&1 || rc=$?
if [ "$rc" -eq 0 ]; then
  printf 'PASS  ohne Docker: No-Op, Exit 0\n'
else
  printf 'FAIL  ohne Docker — rc=%s (erwartet 0)\n' "$rc"
  FAIL=1
fi

# 10) Schnittstellen-Wache: `{{.ImageID}}` schlaegt fehl wie echtes Docker
#     (Docker 27.3.1) und liefert NICHT dieselbe Ausgabe wie `image ls {{.ID}}`.
if FAKE_SCENARIO="$S_FLAT" FAKE_CONTAINERS="$C_RUN5" FAKE_RMI_LOG="/dev/null" \
     "${BIN}/docker" ps -a --format '{{.ImageID}}' >/dev/null 2>&1; then
  printf 'FAIL  Fake: {{.ImageID}} muesste wie echtes Docker fehlschlagen\n'
  FAIL=1
else
  printf 'PASS  Fake: {{.ImageID}} schlaegt fehl (wie Docker 27.3.1)\n'
fi

exit "$FAIL"
