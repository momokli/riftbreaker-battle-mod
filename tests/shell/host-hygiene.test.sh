#!/usr/bin/env bash
# ============================================================
# tests/shell/host-hygiene.test.sh
# ------------------------------------------------------------
# Planetfreier Red/Green-Test für scripts/host_hygiene.sh (Issue #308):
# nagelt die Kerninvarianten des Host-Hygiene-Prune fest, ohne Docker/Daemon.
#
# Ein Fake-`docker` liegt im PATH, protokolliert jeden Aufruf in
# $FAKE_DOCKER_LOG und steuert:
#   * `docker info`            -> FAKE_INFO_RC (Default 0)
#   * `docker image ls ...`    -> dangling-/tagged-Listen aus Dateien
#   * `docker image prune -f`  -> FAKE_PRUNE_RC; bei rc=0 wird die
#                                 dangling-Liste geleert (wie der echte prune);
#                                 FAKE_MUTATE_TAGGED=1 fügt zusätzlich ein
#                                 getaggtes Image hinzu (simuliert ein fremdes
#                                 Re-Tag zwischen den Messungen).
#
# Geprüft wird:
#   (a) prune wird IMMER ohne `-a` aufgerufen (Argument-Assertion)
#   (b) RB_HYGIENE_DRY_RUN=1 löscht nichts (gar kein prune-Aufruf)
#   (c) Änderung der getaggten Mod-Image-Menge -> Exit 1
#   (d) Fehlschlag von `docker image prune` -> Exit 1 (Review-Blocker 2)
#
# Läuft in CI (lint.yml, shellcheck-Job) und lokal:
#   tests/shell/host-hygiene.test.sh
# ============================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HYGIENE="${REPO_ROOT}/scripts/host_hygiene.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

BIN="${TMP}/bin"
mkdir -p "$BIN"

# Fake-`docker`: protokolliert "$*" und bedient die drei benutzten Aufrufe.
cat > "${BIN}/docker" <<'FAKE'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${FAKE_DOCKER_LOG}"
case "${1:-}" in
  info)
    exit "${FAKE_INFO_RC:-0}" ;;
  image)
    case "${2:-}" in
      ls)
        case " $* " in
          *" -f dangling=true "*) cat "${FAKE_DANGLING_FILE}" 2>/dev/null ;;
          *) cat "${FAKE_TAGGED_FILE}" 2>/dev/null ;;
        esac
        exit 0 ;;
      prune)
        rc="${FAKE_PRUNE_RC:-0}"
        if [ "$rc" = 0 ]; then
          [ "${FAKE_MUTATE_TAGGED:-0}" = 1 ] && \
            printf '%s\n' 'rb-dedicated:deadbeef' >> "${FAKE_TAGGED_FILE}"
          : > "${FAKE_DANGLING_FILE}"      # dangling weg, wie der echte prune
        fi
        exit "$rc" ;;
    esac ;;
esac
exit 0
FAKE
chmod +x "${BIN}/docker"

TAGGED_LINES=$'rb-dedicated:aaaa\nrb-headless-client:bbbb\nrb-dedicated:cccc'
DANGLING_LINES=$'dddd\neeee\nffff'
REPOS="rb-dedicated rb-headless-client"

FAIL=0

assert_eq(){
  local label="$1" want="$2" got="$3"
  if [ "$want" = "$got" ]; then
    printf 'PASS  %s\n' "$label"
  else
    printf 'FAIL  %s — erwartet %s, bekommen %s\n' "$label" "$want" "$got"
    FAIL=1
  fi
}

# Legt pro Fall frische Fake-Dateien an und führt das Skript aus.
# Aufruf: exec_hygiene <case-dir> <env...>
# Ergebnis: $OUT (stdout+stderr), $RC, $CALLS (Aufrufprotokoll).
exec_hygiene(){
  local case_dir="$1"; shift
  : > "${case_dir}/dangling"
  : > "${case_dir}/tagged"
  printf '%s\n' "$DANGLING_LINES" > "${case_dir}/dangling"
  printf '%s\n' "$TAGGED_LINES" > "${case_dir}/tagged"
  : > "${case_dir}/calls.log"
  OUT="${case_dir}/out.txt"
  set +e
  env "$@" \
    PATH="${BIN}:${PATH}" \
    RB_HYGIENE_IMAGE_REPOS="$REPOS" \
    FAKE_DOCKER_LOG="${case_dir}/calls.log" \
    FAKE_DANGLING_FILE="${case_dir}/dangling" \
    FAKE_TAGGED_FILE="${case_dir}/tagged" \
    bash "$HYGIENE" > "$OUT" 2>&1
  RC=$?
  set -e
  CALLS="${case_dir}/calls.log"
}

# (a)/(1) Normaler Lauf: dangling 3 -> 0, tagged unverändert, rc=0.
C1="${TMP}/c1"; mkdir -p "$C1"
exec_hygiene "$C1"
assert_eq "normaler Lauf -> rc=0" "0" "$RC"
if grep -qx 'image prune -f' "$CALLS"; then
  printf 'PASS  prune exakt als "image prune -f" aufgerufen\n'
else
  printf 'FAIL  prune-Aufruf fehlt/weicht ab; calls:\n%s\n' "$(cat "$CALLS")"
  FAIL=1
fi
# Blocker-Kern: KEIN -a, nirgends.
if grep -Eq '(^| )-a( |$)' "$CALLS"; then
  printf 'FAIL  prune mit -a aufgerufen (Rollback-Stand gefaehrdet); calls:\n%s\n' "$(cat "$CALLS")"
  FAIL=1
else
  printf 'PASS  kein -a in irgendeinem docker-Aufruf\n'
fi
assert_eq "dangling 3 -> 0 (Nachher-Zaehler)" "0" "$(wc -l < "${C1}/dangling" | tr -d ' ')"
if grep -q 'ok: dangling 3 -> 0' "$OUT"; then
  printf 'PASS  Journal meldet "ok: dangling 3 -> 0"\n'
else
  printf 'FAIL  Erfolgsmeldung fehlt; out:\n%s\n' "$(cat "$OUT")"
  FAIL=1
fi

# (b)/(2) DRY-RUN: kein prune-Aufruf, dangling unverändert, rc=0.
C2="${TMP}/c2"; mkdir -p "$C2"
exec_hygiene "$C2" RB_HYGIENE_DRY_RUN=1
assert_eq "DRY-RUN -> rc=0" "0" "$RC"
if grep -q 'prune' "$CALLS"; then
  printf 'FAIL  DRY-RUN hat prune aufgerufen; calls:\n%s\n' "$(cat "$CALLS")"
  FAIL=1
else
  printf 'PASS  DRY-RUN loescht nichts (kein prune-Aufruf)\n'
fi
assert_eq "DRY-RUN laesst dangling unveraendert (3)" "3" "$(wc -l < "${C2}/dangling" | tr -d ' ')"

# (c)/(3) getaggte Menge ändert sich -> lautes FEHLER + Exit 1.
C3="${TMP}/c3"; mkdir -p "$C3"
exec_hygiene "$C3" FAKE_MUTATE_TAGGED=1
assert_eq "getaggte Menge geaendert -> rc=1" "1" "$RC"
if grep -q 'FEHLER: getaggte Mod-Images haben sich geaendert' "$OUT"; then
  printf 'PASS  Leitplanke meldet geaenderte getaggte Menge\n'
else
  printf 'FAIL  Leitplanken-Fehlertext fehlt; out:\n%s\n' "$(cat "$OUT")"
  FAIL=1
fi

# (d)/(4) prune schlägt fehl -> rc=1 (Review-Blocker 2, vorher stiller rc=0).
C4="${TMP}/c4"; mkdir -p "$C4"
exec_hygiene "$C4" FAKE_PRUNE_RC=1
assert_eq "prune-Fehlschlag -> rc=1" "1" "$RC"
if grep -q 'FEHLER: docker image prune fehlgeschlagen' "$OUT"; then
  printf 'PASS  prune-Fehlschlag wird laut gemeldet\n'
else
  printf 'FAIL  prune-Fehlschlag nicht gemeldet; out:\n%s\n' "$(cat "$OUT")"
  FAIL=1
fi

if [ "$FAIL" -ne 0 ]; then
  printf '\nhost-hygiene.test.sh: FAIL\n'
  exit 1
fi
printf '\nhost-hygiene.test.sh: OK\n'
