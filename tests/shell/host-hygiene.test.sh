#!/usr/bin/env bash
# ============================================================
# tests/shell/host-hygiene.test.sh
# ------------------------------------------------------------
# Planetfreier Red/Green-Test für deploy/host-hygiene/host_hygiene.sh (Issue #308):
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
HYGIENE="${REPO_ROOT}/deploy/host-hygiene/host_hygiene.sh"

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

# --- rbtools test-* Retention (Issue #606) -----------------------------------
# Planetfrei: die Retention laeuft VOR dem Docker-Abschnitt und benoetigt kein
# Docker. Wir pruefen sie ueber die Verzeichnis-Zustaende (nicht ueber $CALLS).
assert_dir_exists() {  # <path> <label>
  if [ -d "$1" ]; then printf 'PASS  %s\n' "$2"; else printf 'FAIL  %s fehlt\n' "$2"; FAIL=1; fi
}
assert_dir_absent() {  # <path> <label>
  if [ -e "$1" ] || [ -L "$1" ]; then printf 'FAIL  %s noch vorhanden (sollte entfernt sein)\n' "$2"; FAIL=1; else printf 'PASS  %s entfernt\n' "$2"; fi
}

# (e)/(5) Retention: behalte die N neuesten test-* (mtime), entferne aeltere;
# dev/prod/staging bleiben unangetastet.
C5="${TMP}/c5"; mkdir -p "$C5"
RB5="${C5}/rbtools"
mkdir -p "$RB5/test-a" "$RB5/test-b" "$RB5/test-c" "$RB5/test-d" "$RB5/dev" "$RB5/prod" "$RB5/staging"
touch -t 202401010000.00 "$RB5/test-a"
touch -t 202402010000.00 "$RB5/test-b"
touch -t 202403010000.00 "$RB5/test-c"
touch -t 202404010000.00 "$RB5/test-d"
touch -t 202301010000.00 "$RB5/dev" "$RB5/prod" "$RB5/staging"
exec_hygiene "$C5" RB_HYGIENE_RBTOOLS_DIR="$RB5" RB_HYGIENE_RBTOOLS_RETENTION=2
assert_dir_absent "$RB5/test-a" "test-a (aeltestes)"
assert_dir_absent "$RB5/test-b" "test-b (zweit-aeltestes)"
assert_dir_exists "$RB5/test-c" "test-c (neu) bleibt"
assert_dir_exists "$RB5/test-d" "test-d (neuestes) bleibt"
assert_dir_exists "$RB5/dev" "dev bleibt"
assert_dir_exists "$RB5/prod" "prod bleibt"
assert_dir_exists "$RB5/staging" "staging bleibt"

# (f)/(6) DRY-RUN: rbtools-Retention loescht nichts.
C6="${TMP}/c6"; mkdir -p "$C6"
RB6="${C6}/rbtools"
mkdir -p "$RB6/test-a" "$RB6/test-b"
touch -t 202401010000.00 "$RB6/test-a"
touch -t 202402010000.00 "$RB6/test-b"
exec_hygiene "$C6" RB_HYGIENE_RBTOOLS_DIR="$RB6" RB_HYGIENE_RBTOOLS_RETENTION=0 RB_HYGIENE_DRY_RUN=1
assert_dir_exists "$RB6/test-a" "dry-run: test-a bleibt"
assert_dir_exists "$RB6/test-b" "dry-run: test-b bleibt"

# (g)/(7) Retention=0 entfernt ALLE test-*; dev/prod/staging + Nicht-Verzeichnis bleiben.
C7="${TMP}/c7"; mkdir -p "$C7"
RB7="${C7}/rbtools"
mkdir -p "$RB7/test-a" "$RB7/test-b" "$RB7/dev" "$RB7/prod" "$RB7/staging"
printf 'x' > "$RB7/test-notdir"
touch -t 202401010000.00 "$RB7/test-a"
touch -t 202402010000.00 "$RB7/test-b"
exec_hygiene "$C7" RB_HYGIENE_RBTOOLS_DIR="$RB7" RB_HYGIENE_RBTOOLS_RETENTION=0
assert_dir_absent "$RB7/test-a" "retention=0: test-a"
assert_dir_absent "$RB7/test-b" "retention=0: test-b"
assert_dir_exists "$RB7/dev" "retention=0: dev bleibt"
assert_dir_exists "$RB7/prod" "retention=0: prod bleibt"
assert_dir_exists "$RB7/staging" "retention=0: staging bleibt"
if [ -f "$RB7/test-notdir" ]; then printf 'PASS  Datei test-notdir bleibt\n'; else printf 'FAIL  Datei test-notdir entfernt\n'; FAIL=1; fi

if [ "$FAIL" -ne 0 ]; then
  printf '\nhost-hygiene.test.sh: FAIL\n'
  exit 1
fi
printf '\nhost-hygiene.test.sh: OK\n'
