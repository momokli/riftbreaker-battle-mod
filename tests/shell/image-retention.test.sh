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
#   * ein Image, das ein Container benutzt, wird nie untagged (pro benutzter
#     Image-ID ueberlebt mindestens ein Tag — auch bei mehreren Tags je ID),
#   * --dry-run veraendert nichts.
#
# Kein Docker/planet noetig: ein Fake-`docker` liefert die Sichten aus
# Szenario-Dateien; `rmi` wird nur protokolliert.
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

# Fake-Docker — nur die vom Skript genutzten Aufrufe.
#   FAKE_SCENARIO   REPO|TAG|ID     (Reihenfolge = neueste zuerst)
#   FAKE_CONTAINERS REF|IMAGEID     (Config.Image des Containers)
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
    fmt=""
    for a in "$@"; do
      case "$a" in
        '{{.Image}}') fmt=image ;;
        '{{.ImageID}}') fmt=imageid ;;
      esac
    done
    case "$fmt" in
      image) awk -F'|' 'NF{print $1}' "$FAKE_CONTAINERS" ;;
      imageid) awk -F'|' 'NF{print $2}' "$FAKE_CONTAINERS" ;;
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
rb-dedicated|sha5|id5
rb-dedicated|sha4|id4
rb-dedicated|sha3|id3
rb-dedicated|sha2|id2
rb-dedicated|sha1|id1
EOF
C_RUN5="${TMP}/containers-run5"
printf '%s\n' "rb-dedicated:sha5|id5" > "$C_RUN5"

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
#    bleibt (ein benutztes Image wird nie untagged). sha4/sha5 = idB (laufend),
#    sha1/2/3 = idA.
S_DUP="${TMP}/scenario-dup"
cat > "$S_DUP" <<'EOF'
rb-dedicated|sha5|idB
rb-dedicated|sha4|idB
rb-dedicated|sha3|idA
rb-dedicated|sha2|idA
rb-dedicated|sha1|idA
EOF
run_case "Duplikat-IDs: aeltere Tags weg, ID bleibt" \
  "$S_DUP" "$C_RUN5" "rb-dedicated:sha5" 2 "rb-dedicated:sha1 rb-dedicated:sha2"

# 4) Ein (gestoppter) Container auf einem AELTEREN Tag schuetzt genau diesen
#    Tag — auch wenn er ausserhalb des Rollback-Fensters liegt.
C_TWO="${TMP}/containers-two"
printf '%s\n' "rb-dedicated:sha5|id5" "rb-dedicated:sha2|id2" > "$C_TWO"
run_case "aelterer, referenzierter Tag wird geschuetzt" \
  "$S_FLAT" "$C_TWO" "rb-dedicated:sha5" 2 "rb-dedicated:sha1"

# 5) Nur die Image-ID des Containers ist bekannt (Tag-Referenz fehlt): die
#    neueste Tag der benutzten ID wird gerettet (kein Untag).
C_BYID="${TMP}/containers-byid"
printf '%s\n' "id5|id5" > "$C_BYID"
run_case "benutzte Image-ID (Referenz unbekannt) bleibt getaggt" \
  "$S_FLAT" "$C_BYID" "" 2 "rb-dedicated:sha1 rb-dedicated:sha2"

# 6) RB_ROLLBACK_TAGS=0: nur das laufende Image bleibt.
run_case "Rollback-Tags=0: nur laufendes Image" \
  "$S_FLAT" "$C_RUN5" "rb-dedicated:sha5" 0 \
  "rb-dedicated:sha1 rb-dedicated:sha2 rb-dedicated:sha3 rb-dedicated:sha4"

# 7) --dry-run aendert nichts (rmi-Log bleibt leer).
run_case "dry-run entfernt nichts" \
  "$S_FLAT" "$C_RUN5" "rb-dedicated:sha5" 2 "" "--dry-run"

# 8) Kleinere Tag-Menge als das Rollback-Fenster: No-Op (nichts zu entfernen).
S_MIN="${TMP}/scenario-min"
printf '%s\n' "rb-dedicated|sha5|id5" "rb-dedicated|sha4|id4" > "$S_MIN"
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

exit "$FAIL"
