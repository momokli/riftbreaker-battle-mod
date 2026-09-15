#!/usr/bin/env bash
# ============================================================
# tests/shell/crash-collector.test.sh
# ------------------------------------------------------------
# Planetfreier Red/Green-Test für scripts/crash_collector.sh (Issue #462):
# nagelt die Collector-Logik fest, ohne Docker/Daemon/Spieler.
#
# Ein Fake-`docker` liegt im PATH und protokolliert jeden Aufruf:
#   * `docker logs -f --tail 0 <c>`  -> Zeilen aus $FAKE_LOGS (Stream-Ende = Dateiende)
#   * `docker exec <c> sh -c "ls -1t ... "` -> neuestes <uuid>.dmp (Datei $FAKE_NEWEST_DMP)
#   * `docker exec <c> sh -c "... rm -f ..."` -> protokolliert den Wine-Prune
#   * `docker cp <c>:<src> <dst>` -> kopiert aus $FAKE_ROOT<src>
#   * `docker inspect --format ...` -> Image-Tag / StartedAt
#
# Geprüft wird:
#   (a) CRASH-Marker -> Bundle <ts>-<uuid> mit dmp/log/trace + context.log + meta.json
#   (b) meta.json: image/git_sha/module_base/fault_address stimmen
#   (c) page fault-Marker löst ebenfalls ein Bundle aus
#   (d) kein Marker -> kein Bundle, rc=0 (graceful non-crash)
#   (e) Retention: nach dem Sammeln bleiben nur die neuesten N Bundles
#   (f) Idempotenz: derselbe Crash erzeugt kein zweites Bundle
#   (g) gueltiger MDMP im Bundle -> meta.json traegt exception_code/-address,
#       module, module_base, fault_rva, fault_thread, stack_rvas (#481)
#   (h) ungueltiger Dump ("MZ fake") -> rc=0, neue Felder null, module_base
#       weiterhin aus der module_range-Log-Zeile (#481)
#
# Läuft in CI (lint.yml) und lokal:  tests/shell/crash-collector.test.sh
# ============================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COLLECTOR="${REPO_ROOT}/scripts/crash_collector.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

BIN="${TMP}/bin"
mkdir -p "$BIN"

# Fixture-Wurzel: enthält crash_info/<uuid>.{dmp,log,trace} wie im Container.
FAKE_ROOT="${TMP}/root"
CRASHINFO="/data/.wine/drive_c/users/steamuser/Documents/The Riftbreaker/crash_info"
mkdir -p "${FAKE_ROOT}${CRASHINFO}"

UUID="cbb85f6a-699b-4b7d-8959-3c0698474f51"
printf 'MZ fake-minidump\n' >"${FAKE_ROOT}${CRASHINFO}/${UUID}.dmp"
printf '[08:37:25.292] [critical] CRASH\n' >"${FAKE_ROOT}${CRASHINFO}/${UUID}.log"
printf '(function-name not available)\n' >"${FAKE_ROOT}${CRASHINFO}/${UUID}.trace"

# Gueltiger MDMP (Issue #481): 1 Modul (riftbreaker.exe), Exception IM Modul,
# Stack-Pointer im Modulbereich -> der Collector muss die Dump-Felder uebernehmen.
VALID_UUID="7f3a1b2c-0000-4aaa-9bbb-0123456789ab"
VALID_DMP="${FAKE_ROOT}${CRASHINFO}/${VALID_UUID}.dmp"
python3 - "$VALID_DMP" <<'PY'
import struct
import sys

BASE = 0x140000000
SIZE = 0x10000
ADDR = BASE + 0x1234
THREAD = 0x1F4
STACK_START = 0x7FF000
STACK_LEN = 0x40
NAME = "C:\\game\\riftbreaker.exe".encode("utf-16-le")
DIR_RVA = 32
n_streams = 4
payload = DIR_RVA + n_streams * 12
rva_name = payload
payload += 4 + len(NAME)
rva_stack = payload
payload += STACK_LEN
rva_mem = payload
payload += 20
rva_thr = payload
payload += 52
rva_mod = payload
payload += 112
rva_exc = payload
payload += 168
buf = bytearray(payload)

buf[0:4] = b"MDMP"
struct.pack_into("<I", buf, 4, 0xA793)
struct.pack_into("<I", buf, 8, n_streams)
struct.pack_into("<I", buf, 12, DIR_RVA)
for i, (stype, rva) in enumerate([(4, rva_mod), (3, rva_thr), (5, rva_mem), (6, rva_exc)]):
    struct.pack_into("<I", buf, DIR_RVA + i * 12, stype)
    struct.pack_into("<I", buf, DIR_RVA + i * 12 + 8, rva)
struct.pack_into("<I", buf, rva_name, len(NAME))
buf[rva_name + 4:rva_name + 4 + len(NAME)] = NAME
for i, p in enumerate([BASE + 0x500, BASE + 0xABC, BASE + 0x500, 0x7FF001, 0, 0, 0, 0]):
    struct.pack_into("<Q", buf, rva_stack + i * 8, p)
struct.pack_into("<I", buf, rva_mem, 1)
struct.pack_into("<Q", buf, rva_mem + 4, STACK_START)
struct.pack_into("<I", buf, rva_mem + 12, STACK_LEN)
struct.pack_into("<I", buf, rva_mem + 16, rva_stack)
struct.pack_into("<I", buf, rva_thr, 1)
struct.pack_into("<I", buf, rva_thr + 4, THREAD)
struct.pack_into("<Q", buf, rva_thr + 4 + 24, STACK_START)
struct.pack_into("<I", buf, rva_thr + 4 + 32, STACK_LEN)
struct.pack_into("<I", buf, rva_thr + 4 + 36, rva_stack)
struct.pack_into("<I", buf, rva_mod, 1)
struct.pack_into("<Q", buf, rva_mod + 4, BASE)
struct.pack_into("<I", buf, rva_mod + 12, SIZE)
struct.pack_into("<I", buf, rva_mod + 24, rva_name)
struct.pack_into("<I", buf, rva_exc, THREAD)
struct.pack_into("<I", buf, rva_exc + 8, 0xC0000005)
struct.pack_into("<Q", buf, rva_exc + 8 + 16, ADDR)
with open(sys.argv[1], "wb") as fh:
    fh.write(bytes(buf))
PY
printf '[10:00:00.000] [critical] CRASH: in valid mdmp\n' >"${FAKE_ROOT}${CRASHINFO}/${VALID_UUID}.log"
printf '(function-name not available)\n' >"${FAKE_ROOT}${CRASHINFO}/${VALID_UUID}.trace"

cat >"${BIN}/docker" <<'FAKE'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"${FAKE_DOCKER_LOG}"
cmd="${1:-}"; shift || true
case "$cmd" in
  logs)
    case " $* " in
      *" -f "*) cat "${FAKE_LOGS}" 2>/dev/null ;;
      *) tail -n 20 "${FAKE_LOGS}" 2>/dev/null ;;
    esac ;;
  inspect)
    case "$*" in
      *".Config.Image"*) printf 'rb-dedicated:8131ee0bd0c8\n' ;;
      *".State.StartedAt"*) printf '2026-09-15T09:00:00.000000000Z\n' ;;
      *) printf '\n' ;;
    esac ;;
  exec)
    # Letztes Argument ist das sh-c-Skript.
    script="${*: -1}"
    case "$script" in
      *"rm -f"*) printf 'wine-prune\n' >>"${FAKE_DOCKER_LOG}" ;;
      *"ls -1t"*.dmp*) printf '%s\n' "${FAKE_NEWEST_DMP}" ;;
    esac
    exit 0 ;;
  cp)
    src="${1#*:}"
    cp "${FAKE_ROOT}${src}" "$2"
    exit $? ;;
esac
exit 0
FAKE
chmod +x "${BIN}/docker"

# Standard-Logstream: ein CRASH mit Modulbasis davor.
cat >"${TMP}/crash.log" <<EOF
[09:40:50.520] [rbbridge] module_range: via=getmodulehandle base=00006ffff6da0000 size=78778368 execfn=0000000000000000
[08:37:25.292] [critical] CrashHandlerWin32.cpp:103 -
 CRASH:
(filename not available):0 - (function-name not available)
EOF

# page-fault-Stream (Wine): enthaelt Zugriffs- UND Fault-Adresse; erwartet wird
# die Adresse hinter „at address".
cat >"${TMP}/pf.log" <<EOF
[09:00:00.000] [rbbridge] module_range: via=getmodulehandle base=00006ffff7a00000 size=1000 execfn=0000000000000000
wine: Unhandled page fault on read access to 0x0000000000000000 at address 0x0000000140001234
EOF

cat >"${TMP}/clean.log" <<'EOF'
[09:00:00.000] [server] [info] LogService.cpp:71 - [LUA 'lua/x.lua']: running
[09:00:03.000] [rbbridge] module_range: via=getmodulehandle base=00006ffff6da0000 size=78778368 execfn=0000000000000000
[09:00:06.000] [server] [info] LogService.cpp:71 - [LUA 'lua/y.lua']: ok
EOF

FAIL=0
assert_eq() {
  local label="$1" want="$2" got="$3"
  if [ "$want" = "$got" ]; then
    printf 'PASS  %s\n' "$label"
  else
    printf 'FAIL  %s — erwartet %s, bekommen %s\n' "$label" "$want" "$got"
    FAIL=1
  fi
}
assert_true() {
  local label="$1"; shift
  if "$@"; then printf 'PASS  %s\n' "$label"; else printf 'FAIL  %s\n' "$label"; FAIL=1; fi
}

# Führt den Collector einmalig aus. $1 = case-dir, $2 = log-fixture, $3 = newest dmp
run_case() {
  local dir="$1" logs="$2" newest="$3"
  mkdir -p "${dir}/crashes"
  : >"${dir}/docker.log"
  set +e
  env \
    PATH="${BIN}:${PATH}" \
    FAKE_DOCKER_LOG="${dir}/docker.log" \
    FAKE_LOGS="$logs" \
    FAKE_NEWEST_DMP="$newest" \
    FAKE_ROOT="${FAKE_ROOT}" \
    RB_CRASH_DIR="${dir}/crashes" \
    RB_CRASH_CRASHINFO="${CRASHINFO}" \
    RB_CRASH_CONTAINER="riftbreaker-dedicated" \
    RB_CRASH_WAIT_SECS=2 \
    RB_CRASH_RETRY_SLEEP=0 \
    bash "$COLLECTOR" --once >"${dir}/out.txt" 2>&1
  RC=$?
  set -e
}

# --- (a)+(b) CRASH-Marker -> Bundle + meta.json ------------------------------
C1="${TMP}/c1"
run_case "$C1" "${TMP}/crash.log" "${FAKE_ROOT}${CRASHINFO}/${UUID}.dmp"
assert_eq "CRASH -> rc=0" "0" "$RC"

mapfile -t BUNDLES < <(find "${C1}/crashes" -mindepth 1 -maxdepth 1 -type d | sed 's|.*/||')
assert_eq "genau ein Bundle" "1" "${#BUNDLES[@]}"
B="${BUNDLES[0]:-}"
assert_eq "Bundle-Name ist <ts>-<uuid>" "${B: -36}" "$UUID"
if [ -n "$B" ]; then
  BD="${C1}/crashes/${B}"
  for f in "${UUID}.dmp" "${UUID}.log" "${UUID}.trace" context.log meta.json; do
    assert_true "Bundle enthaelt ${f}" test -s "${BD}/${f}"
  done
  # context.log enthaelt die Stream-Zeilen (inkl. Marker).
  assert_true "context.log enthaelt den Marker" grep -q 'CRASH:' "${BD}/context.log"

  # meta.json mit python3 pruefen (kein jq auf allen Runnern garantiert).
  meta() { python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],''))" "${BD}/meta.json" "$1"; }
  assert_eq "meta.uuid" "$UUID" "$(meta uuid)"
  assert_eq "meta.image" "rb-dedicated:8131ee0bd0c8" "$(meta image)"
  assert_eq "meta.git_sha" "8131ee0bd0c8" "$(meta git_sha)"
  assert_eq "meta.module_base" "00006ffff6da0000" "$(meta module_base)"
  assert_eq "meta.module_size" "78778368" "$(meta module_size)"
  assert_eq "meta.container" "riftbreaker-dedicated" "$(meta container)"
  assert_eq "meta.crash_marker" "CRASH" "$(meta crash_marker)"
  # dmp/log/trace wurden per `docker cp` kopiert.
  assert_true "docker cp fuer .dmp aufgerufen" grep -q "cp riftbreaker-dedicated:${CRASHINFO}/${UUID}.dmp" "${C1}/docker.log"
  # Wine-Prune wurde (nach dem Kopieren) angestossen.
  assert_true "Wine-Retention-Prune aufgerufen" grep -q 'wine-prune' "${C1}/docker.log"
fi

# --- (c) page fault -> Bundle ------------------------------------------------
C2="${TMP}/c2"
run_case "$C2" "${TMP}/pf.log" "${FAKE_ROOT}${CRASHINFO}/${UUID}.dmp"
assert_eq "page fault -> rc=0" "0" "$RC"
mapfile -t B2 < <(find "${C2}/crashes" -mindepth 1 -maxdepth 1 -type d | sed 's|.*/||')
assert_eq "page fault -> genau ein Bundle" "1" "${#B2[@]}"
if [ "${#B2[@]}" -eq 1 ]; then
  row="$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(d['crash_marker'],d['fault_address'],d['module_base'])" "${C2}/crashes/${B2[0]}/meta.json")"
  assert_eq "page fault meta (marker/fault/base)" "page fault 0000000140001234 00006ffff7a00000" "$row"
fi

# --- (d) graceful non-crash -> kein Bundle, rc=0 -----------------------------
C3="${TMP}/c3"
run_case "$C3" "${TMP}/clean.log" "${FAKE_ROOT}${CRASHINFO}/${UUID}.dmp"
assert_eq "ohne Marker -> rc=0" "0" "$RC"
assert_eq "ohne Marker -> kein Bundle" "0" "$(find "${C3}/crashes" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"

# --- (e) Retention: nur die neuesten N Bundles bleiben -----------------------
C4="${TMP}/c4"
mkdir -p "${C4}/crashes"
for i in $(seq -w 1 25); do mkdir -p "${C4}/crashes/20260101T0000${i}Z-old"; done
run_case "$C4" "${TMP}/crash.log" "${FAKE_ROOT}${CRASHINFO}/${UUID}.dmp"
assert_eq "Retention -> rc=0" "0" "$RC"
N="$(find "${C4}/crashes" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
assert_eq "Retention haelt 20 Bundles" "20" "$N"
assert_true "aeltestes Bundle entfernt" test ! -d "${C4}/crashes/20260101T000001Z-old"
assert_true "neuestes Bundle (der Crash) noch da" grep -q . <(find "${C4}/crashes" -maxdepth 1 -type d -name "*${UUID}")

# --- (f) Idempotenz: derselbe Crash -> kein Doppel-Bundle --------------------
C5="${TMP}/c5"
run_case "$C5" "${TMP}/crash.log" "${FAKE_ROOT}${CRASHINFO}/${UUID}.dmp"
run_case "$C5" "${TMP}/crash.log" "${FAKE_ROOT}${CRASHINFO}/${UUID}.dmp"
assert_eq "zweiter Lauf -> rc=0" "0" "$RC"
assert_eq "Idempotenz: weiterhin ein Bundle" "1" "$(find "${C5}/crashes" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"

# --- (g) gueltiger Minidump -> Dump-Felder in meta.json (Issue #481) ---------
C6="${TMP}/c6"
run_case "$C6" "${TMP}/crash.log" "$VALID_DMP"
assert_eq "valid dmp -> rc=0" "0" "$RC"
mapfile -t B6 < <(find "${C6}/crashes" -mindepth 1 -maxdepth 1 -type d | sed 's|.*/||')
assert_eq "valid dmp -> genau ein Bundle" "1" "${#B6[@]}"
if [ "${#B6[@]}" -eq 1 ]; then
  M6="${C6}/crashes/${B6[0]}/meta.json"
  m6() { python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2]))" "$M6" "$1"; }
  assert_eq "meta.exception_code" "3221225477" "$(m6 exception_code)"
  assert_eq "meta.exception_address" "140001234" "$(m6 exception_address)"
  assert_eq "meta.module" "riftbreaker.exe" "$(m6 module)"
  assert_eq "meta.module_base (Dump)" "140000000" "$(m6 module_base)"
  assert_eq "meta.module_size" "65536" "$(m6 module_size)"
  assert_eq "meta.fault_rva" "1234" "$(m6 fault_rva)"
  assert_eq "meta.fault_thread" "500" "$(m6 fault_thread)"
  assert_eq "meta.stack_rvas" "500,abc" \
    "$(python3 -c "import json,sys;print(','.join(json.load(open(sys.argv[1]))['stack_rvas']))" "$M6")"
  # fault_rva == exception_address - module_base
  assert_eq "meta.fault_rva == addr - base" "True" \
    "$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(int(d['fault_rva'],16)==int(d['exception_address'],16)-int(d['module_base'],16))" "$M6")"
fi

# --- (h) ungueltiger Dump -> rc=0, neue Felder null, module_base aus Log ------
C7="${TMP}/c7"
run_case "$C7" "${TMP}/crash.log" "${FAKE_ROOT}${CRASHINFO}/${UUID}.dmp"
assert_eq "invalid dmp -> rc=0" "0" "$RC"
mapfile -t B7 < <(find "${C7}/crashes" -mindepth 1 -maxdepth 1 -type d | sed 's|.*/||')
assert_eq "invalid dmp -> genau ein Bundle" "1" "${#B7[@]}"
if [ "${#B7[@]}" -eq 1 ]; then
  M7="${C7}/crashes/${B7[0]}/meta.json"
  m7() { python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2]))" "$M7" "$1"; }
  assert_eq "invalid: exception_code null" "None" "$(m7 exception_code)"
  assert_eq "invalid: exception_address null" "None" "$(m7 exception_address)"
  assert_eq "invalid: module null" "None" "$(m7 module)"
  assert_eq "invalid: fault_rva null" "None" "$(m7 fault_rva)"
  assert_eq "invalid: fault_thread null" "None" "$(m7 fault_thread)"
  assert_eq "invalid: stack_rvas null" "None" "$(m7 stack_rvas)"
  assert_eq "invalid: module_base aus Log-Zeile" "00006ffff6da0000" "$(m7 module_base)"
fi

if [ "$FAIL" -ne 0 ]; then
  printf '\ncrash-collector.test.sh: FAIL\n'
  exit 1
fi
printf '\ncrash-collector.test.sh: OK\n'
