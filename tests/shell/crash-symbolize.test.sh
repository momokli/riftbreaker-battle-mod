#!/usr/bin/env bash
# ============================================================
# tests/shell/crash-symbolize.test.sh
# ------------------------------------------------------------
# Planetfreier Red/Green-Test für die collector-seitige Symbolik (Issue #480):
# nagelt `deploy/crash-collector/symbolize.py` + `deploy/crash-collector/crash_symbolize.sh` fest, ohne
# planet, Docker, Spieler oder echtes llvm-18.
#
# Bausteine:
#   * synthetischer Minidump (im Test aus Bytes gebaut — kein 252-MB-Fixture
#     im Repo): ModuleList(4) + Exception(6, AMD64-Context mit RIP) +
#     ThreadList(3)/MemoryList(5) mit Stack-Speicher.
#   * Fake-`llvm-symbolizer` im PATH: protokolliert jeden Aufruf und liefert
#     deterministische Namen (`frame_<dezimal-rva>`).
#
# Geprüft wird:
#   (a) Default: Fault-Frame + Stack-Frames (Stack-Scan default an, #639);
#       Adressen AUSSERHALB des Modulbereichs werden gefiltert.
#   (a2) RB_CRASH_STACK_SCAN=0 -> nur der Fault-Frame (opt-out).
#   (b) Aufruf-Vertrag: `--obj=<dll> --relative-address <rva>` je Frame,
#       Ergebnis-Header + `RVA<TAB>[modul ]KIND Name` in symbolized.txt.
#   (c) Skip-Regeln: RB_CRASH_SYMBOLIZE=0 / PDB fehlt / DLL fehlt / Tool
#       fehlt / kein Dump -> rc=0, kein symbolized.txt, Grund im Log.
#   (d) Idempotenz: zweiter Lauf schreibt nichts neu.
#   (e) Fehlerpfad: Symbolizer rc!=0 -> rc=0, kein halb geschriebenes
#       symbolized.txt, keine Temp-Reste.
#   (f) Collector-Kopplung: crash_collector.sh --once erzeugt Bundle inkl.
#       symbolized.txt + meta.json.symbolized.status="ok".
#
# Läuft in CI (lint.yml) und lokal:  tests/shell/crash-symbolize.test.sh
# ============================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SYMBOLIZE_SH="${REPO_ROOT}/deploy/crash-collector/crash_symbolize.sh"
SYMBOLIZE_PY="${REPO_ROOT}/deploy/crash-collector/symbolize.py"
COLLECTOR="${REPO_ROOT}/deploy/crash-collector/crash_collector.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

BIN="${TMP}/bin"
mkdir -p "$BIN"

# --- Fake-llvm-symbolizer ----------------------------------------------------
cat >"${BIN}/llvm-symbolizer" <<'FAKE'
#!/usr/bin/env bash
# Protokolliert die Argumente und liefert deterministische Namen.
printf '%s\n' "$*" >>"${FAKE_SYM_LOG}"
rva=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--relative-address" ]; then rva="$a"; fi
  prev="$a"
done
if [ -n "${FAKE_SYM_EXIT:-}" ]; then
  printf 'garbage\n'
  exit "${FAKE_SYM_EXIT}"
fi
printf 'frame_%d\n' "$((rva))"
printf '/src/riftbreaker.cpp:1:1\n'
FAKE
chmod +x "${BIN}/llvm-symbolizer"

# --- synthetischer Minidump (Streams 3/4/5/6) --------------------------------
cat >"${TMP}/mkdump.py" <<'PY'
import struct
import sys


def p32(v):
    return struct.pack('<I', v)


def p64(v):
    return struct.pack('<Q', v)


def build(base, size, fault, returns, name, tid=1):
    ctx = bytearray(0x4d0)                       # CONTEXT_AMD64
    struct.pack_into('<I', ctx, 0x30, 0x0010001F)  # ContextFlags
    struct.pack_into('<Q', ctx, 0xF8, fault)       # Rip
    ctx = bytes(ctx)

    stack = b''.join(p64(a) for a in returns) or p64(0)
    stack_start = 0x0000007ff0000000

    raw_name = name.encode('utf-16-le')
    name_blob = p32(len(raw_name)) + raw_name + b'\x00\x00'

    base_rva = 32 + 12 * 4
    rva_name = base_rva
    rva_ctx = rva_name + len(name_blob)
    rva_stack = rva_ctx + len(ctx)
    rva_exc = rva_stack + len(stack)
    rva_threads = rva_exc + 168
    rva_modules = rva_threads + 52
    rva_memory = rva_modules + 112

    exc = (p32(tid) + p32(0) + p32(0xC0000005) + p32(0) + p64(0) + p64(fault)
           + p32(0) + p32(0) + b'\x00' * 120 + p32(len(ctx)) + p32(rva_ctx))
    threads = (p32(1) + p32(tid) + p32(0) + p32(2) + p32(0) + p64(0)
               + p64(stack_start) + p32(len(stack)) + p32(rva_stack)
               + p32(len(ctx)) + p32(rva_ctx))
    modules = (p32(1) + p64(base) + p32(size) + p32(0) + p32(0) + p32(rva_name)
               + b'\x00' * 52 + b'\x00' * 8 + b'\x00' * 8 + p64(0) + p64(0))
    memory = p32(1) + p64(stack_start) + p32(len(stack)) + p32(rva_stack)

    directory = b''.join([
        p32(3) + p32(len(threads)) + p32(rva_threads),
        p32(4) + p32(len(modules)) + p32(rva_modules),
        p32(5) + p32(len(memory)) + p32(rva_memory),
        p32(6) + p32(168) + p32(rva_exc),
    ])
    header = p32(0x504D444D) + p32(0xA793) + p32(4) + p32(32) + p32(0) + p32(0) + p64(0)
    return header + directory + name_blob + ctx + stack + exc + threads + modules + memory


out = sys.argv[1]
base = int(sys.argv[2], 0)
size = int(sys.argv[3], 0)
fault = int(sys.argv[4], 0)
returns = [int(a, 0) for a in sys.argv[5:]]
name = "C:\\game\\bin\\riftbreaker_dll_win_release.dll"
with open(out, 'wb') as fh:
    fh.write(build(base, size, fault, returns, name))
PY

# --- synthetischer Minidump mit ZWEI Modulen (Game + rbbridge, #559) ---
cat >"${TMP}/mkdump2.py" <<'PY'
import struct
import sys


def p32(v):
    return struct.pack('<I', v)


def p64(v):
    return struct.pack('<Q', v)


def module_entry(base, size, name_rva):
    return (p64(base) + p32(size) + p32(0) + p32(0) + p32(name_rva)
            + b'\x00' * 52 + b'\x00' * 8 + b'\x00' * 8 + p64(0) + p64(0))


def build(mods, fault, returns):
    ctx = bytearray(0x4d0)
    struct.pack_into('<I', ctx, 0x30, 0x0010001F)
    struct.pack_into('<Q', ctx, 0xF8, fault)
    ctx = bytes(ctx)

    stack = b''.join(p64(a) for a in returns) or p64(0)
    stack_start = 0x0000007ff0000000

    names = []
    for _b, _s, name in mods:
        raw = name.encode('utf-16-le')
        names.append(p32(len(raw)) + raw + b'\x00\x00')

    base_rva = 32 + 12 * 4
    rva = base_rva
    name_rvas = []
    for blob in names:
        name_rvas.append(rva)
        rva += len(blob)
    rva_ctx = rva
    rva_stack = rva_ctx + len(ctx)
    rva_exc = rva_stack + len(stack)
    rva_threads = rva_exc + 168
    rva_modules = rva_threads + 52
    rva_memory = rva_modules + 4 + 108 * len(mods)

    exc = (p32(1) + p32(0) + p32(0xC0000005) + p32(0) + p64(0) + p64(fault)
           + p32(0) + p32(0) + b'\x00' * 120 + p32(len(ctx)) + p32(rva_ctx))
    threads = (p32(1) + p32(1) + p32(0) + p32(2) + p32(0) + p64(0)
               + p64(stack_start) + p32(len(stack)) + p32(rva_stack)
               + p32(len(ctx)) + p32(rva_ctx))
    modules = p32(len(mods)) + b''.join(
        module_entry(b, s, name_rvas[i]) for i, (b, s, _n) in enumerate(mods))
    memory = p32(1) + p64(stack_start) + p32(len(stack)) + p32(rva_stack)

    directory = b''.join([
        p32(3) + p32(len(threads)) + p32(rva_threads),
        p32(4) + p32(len(modules)) + p32(rva_modules),
        p32(5) + p32(len(memory)) + p32(rva_memory),
        p32(6) + p32(168) + p32(rva_exc),
    ])
    header = p32(0x504D444D) + p32(0xA793) + p32(4) + p32(32) + p32(0) + p32(0) + p64(0)
    return header + directory + b''.join(names) + ctx + stack + exc + threads + modules + memory


out = sys.argv[1]
base = int(sys.argv[2], 0)
size = int(sys.argv[3], 0)
base2 = int(sys.argv[4], 0)
size2 = int(sys.argv[5], 0)
fault = int(sys.argv[6], 0)
returns = [int(a, 0) for a in sys.argv[7:]]
mods = [
    (base, size, "C:\\game\\bin\\riftbreaker_dll_win_release.dll"),
    (base2, size2, "C:\\rbmods\\rbtools\\rbbridge.dll"),
]
with open(out, 'wb') as fh:
    fh.write(build(mods, fault, returns))
PY

MODULE_BASE=$((0x00006ffff6da0000))
MODULE_SIZE=78778368
UUID="cbb85f6a-699b-4b7d-8959-3c0698474f51"

# --- Fixtures: DLL/PDB (nur Existenz wird geprüft) ---------------------------
DLL="${TMP}/riftbreaker_dll_win_release.dll"
PDB="${TMP}/riftbreaker_dll_win_release.pdb"
printf 'MZ fake-dll\n' >"$DLL"
printf 'fake-pdb\n' >"$PDB"

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
assert_false() {
  local label="$1"; shift
  if "$@"; then printf 'FAIL  %s\n' "$label"; FAIL=1; else printf 'PASS  %s\n' "$label"; fi
}

# Baut ein Bundle mit synthetischem Dump. $1 = bundle, $2 = fault, $3.. =
# Return-Adressen im Stack.
make_bundle() {
  local bundle="$1" fault="$2"; shift 2
  mkdir -p "$bundle"
  python3 "${TMP}/mkdump.py" "${bundle}/${UUID}.dmp" "$MODULE_BASE" "$MODULE_SIZE" "$fault" "$@"
}

# Führt die CLI aus. Erwartet FAKE_SYM_* bereits exportiert.
run_sym() {
  local dir="$1" bundle="$2"
  mkdir -p "$dir"
  : >"${dir}/sym.log"
  set +e
  env \
    PATH="${BIN}:${PATH}" \
    FAKE_SYM_LOG="${dir}/sym.log" \
    RB_CRASH_SYMBOLIZE_TOOL="$SYMBOLIZE_PY" \
    RB_CRASH_LLVM_SYMBOLIZER="${BIN}/llvm-symbolizer" \
    RB_CRASH_DLL="$DLL" \
    RB_CRASH_PDB="$PDB" \
    RB_CRASH_PYTHON="python3" \
    bash "$SYMBOLIZE_SH" "$bundle" >"${dir}/out.txt" 2>&1
  RC=$?
  set -e
}

# --- (a)+(b) Default: Fault + Stack-Frames (Stack-Scan an, #639) --------------
FAULT=$((MODULE_BASE + 0x1000))
OUTSIDE=$((0x1234))          # ausserhalb jedes Moduls -> muss gefiltert werden
A1="${TMP}/a1"
make_bundle "${A1}/bundle" "$FAULT" "$((MODULE_BASE + 0x2000))" "$OUTSIDE" "$((MODULE_BASE + 0x3000))"
run_sym "$A1" "${A1}/bundle"
assert_eq "symbolize ok -> rc=0" "0" "$RC"
SYM="${A1}/bundle/symbolized.txt"
assert_true "(a) symbolized.txt geschrieben" test -s "$SYM"
assert_eq "(a) Default: Fault + Stack-Frames" "0x1000 0x2000 0x3000" \
  "$(awk -F'\t' '/^0x/{print $1}' "$SYM" | tr '\n' ' ' | sed 's/ $//')"
assert_true "(b) Header enthaelt module_base" grep -q '^# module_base: 0x6ffff6da0000$' "$SYM"
assert_true "(b) Header enthaelt dll" grep -q "^# dll: ${DLL}$" "$SYM"
assert_true "(b) Header enthaelt tool" grep -q "^# tool: ${BIN}/llvm-symbolizer$" "$SYM"
assert_true "(b) Fault-Frame 0x1000 als FAULT markiert" grep -qP '^0x1000\tFAULT frame_4096$' "$SYM"
assert_true "(b) Stack-Frames 0x2000/0x3000 als stack markiert" grep -qP '^0x(2000|3000)\tstack frame_' "$SYM"
assert_false "(b) Adresse ausserhalb des Moduls gefiltert" grep -q '0x1234' "$SYM"
assert_true "(b) Aufruf mit --obj" grep -q -- "--obj=${DLL}" "${A1}/sym.log"
assert_true "(b) Aufruf mit --relative-address 0x1000" grep -q -- '--relative-address 0x1000' "${A1}/sym.log"
assert_eq "(b) genau 3 Symbolizer-Aufrufe" "3" "$(wc -l < "${A1}/sym.log" | tr -d ' ')"

# --- (a2) RB_CRASH_STACK_SCAN=0 -> nur Fault-Frame (opt-out, #639) -----------
A2="${TMP}/a2"
make_bundle "${A2}/bundle" "$FAULT" "$((MODULE_BASE + 0x2000))" "$OUTSIDE" "$((MODULE_BASE + 0x3000))"
mkdir -p "$A2"
: >"${A2}/sym.log"
set +e
env PATH="${BIN}:${PATH}" FAKE_SYM_LOG="${A2}/sym.log" \
  RB_CRASH_SYMBOLIZE_TOOL="$SYMBOLIZE_PY" RB_CRASH_LLVM_SYMBOLIZER="${BIN}/llvm-symbolizer" \
  RB_CRASH_DLL="$DLL" RB_CRASH_PDB="$PDB" RB_CRASH_PYTHON="python3" \
  RB_CRASH_STACK_SCAN=0 \
  bash "$SYMBOLIZE_SH" "${A2}/bundle" >"${A2}/out.txt" 2>&1
RC=$?
set -e
assert_eq "(a2) STACK_SCAN=0 -> rc=0" "0" "$RC"
SYM2="${A2}/bundle/symbolized.txt"
assert_eq "(a2) STACK_SCAN=0: genau der Fault-Frame" "0x1000" \
  "$(awk -F'\t' '/^0x/{print $1}' "$SYM2" | tr '\n' ' ' | sed 's/ $//')"
assert_false "(a2) STACK_SCAN=0: keine Stack-Frames" grep -qP '^0x(2000|3000)\t' "$SYM2"
assert_eq "(a2) STACK_SCAN=0: genau 1 Symbolizer-Aufruf" "1" "$(wc -l < "${A2}/sym.log" | tr -d ' ')"

# --- (c) Skip-Regeln ---------------------------------------------------------
C="${TMP}/c"
make_bundle "${C}/bundle" "$FAULT"

set +e
env RB_CRASH_SYMBOLIZE=0 RB_CRASH_SYMBOLIZE_TOOL="$SYMBOLIZE_PY" \
  RB_CRASH_LLVM_SYMBOLIZER="${BIN}/llvm-symbolizer" RB_CRASH_DLL="$DLL" RB_CRASH_PDB="$PDB" \
  FAKE_SYM_LOG="${C}/dummy.log" PATH="${BIN}:${PATH}" \
  bash "$SYMBOLIZE_SH" "${C}/bundle" >"${C}/off.txt" 2>&1
RC=$?
set -e
assert_eq "(c) Master-Switch aus -> rc=0" "0" "$RC"
assert_true "(c) Master-Switch: Grund im Log" grep -q 'symbolize: skip RB_CRASH_SYMBOLIZE=0' "${C}/off.txt"

set +e
env RB_CRASH_SYMBOLIZE_TOOL="$SYMBOLIZE_PY" RB_CRASH_LLVM_SYMBOLIZER="${BIN}/llvm-symbolizer" \
  RB_CRASH_DLL="$DLL" RB_CRASH_PDB="${TMP}/gibt-es-nicht.pdb" \
  FAKE_SYM_LOG="${C}/dummy.log" PATH="${BIN}:${PATH}" \
  bash "$SYMBOLIZE_SH" "${C}/bundle" >"${C}/nopdb.txt" 2>&1
RC=$?
set -e
assert_eq "(c) PDB fehlt -> rc=0" "0" "$RC"
assert_true "(c) PDB fehlt: Grund im Log" grep -q 'symbolize: skip PDB fehlt' "${C}/nopdb.txt"

set +e
env RB_CRASH_SYMBOLIZE_TOOL="$SYMBOLIZE_PY" RB_CRASH_LLVM_SYMBOLIZER="${BIN}/llvm-symbolizer" \
  RB_CRASH_DLL="${TMP}/gibt-es-nicht.dll" RB_CRASH_PDB="$PDB" \
  FAKE_SYM_LOG="${C}/dummy.log" PATH="${BIN}:${PATH}" \
  bash "$SYMBOLIZE_SH" "${C}/bundle" >"${C}/nodll.txt" 2>&1
RC=$?
set -e
assert_eq "(c) DLL fehlt -> rc=0" "0" "$RC"
assert_true "(c) DLL fehlt: Grund im Log" grep -q 'symbolize: skip DLL fehlt' "${C}/nodll.txt"

set +e
env RB_CRASH_SYMBOLIZE_TOOL="$SYMBOLIZE_PY" RB_CRASH_LLVM_SYMBOLIZER="${TMP}/kein-symbolizer" \
  RB_CRASH_DLL="$DLL" RB_CRASH_PDB="$PDB" \
  FAKE_SYM_LOG="${C}/dummy.log" PATH="${BIN}:${PATH}" \
  bash "$SYMBOLIZE_SH" "${C}/bundle" >"${C}/notool.txt" 2>&1
RC=$?
set -e
assert_eq "(c) Tool fehlt -> rc=0" "0" "$RC"
assert_true "(c) Tool fehlt: Grund im Log" grep -q 'symbolize: skip Tool fehlt' "${C}/notool.txt"

mkdir -p "${C}/emptydir"
run_sym "${C}/nodump" "${C}/emptydir"
assert_eq "(c) kein Dump -> rc=0" "0" "$RC"
assert_true "(c) kein Dump: Grund im Log" grep -q 'symbolize: skip kein Minidump' "${C}/nodump/out.txt"
assert_true "(c) kein symbolized.txt entstanden" test ! -e "${C}/bundle/symbolized.txt"

# --- (d) Idempotenz ----------------------------------------------------------
D="${TMP}/d"
make_bundle "${D}/bundle" "$FAULT" "$((MODULE_BASE + 0x2000))"
run_sym "$D" "${D}/bundle"
assert_eq "(d) erster Lauf rc=0" "0" "$RC"
BEFORE="$(md5sum "${D}/bundle/symbolized.txt" | cut -d' ' -f1)"
sleep 1
run_sym "$D" "${D}/bundle"
assert_eq "(d) zweiter Lauf rc=0" "0" "$RC"
assert_true "(d) Idempotenz: Grund im Log" grep -q 'symbolize: skip bereits symbolisiert' "${D}/out.txt"
assert_eq "(d) Datei unveraendert" "$BEFORE" "$(md5sum "${D}/bundle/symbolized.txt" | cut -d' ' -f1)"
assert_eq "(d) kein zweiter Symbolizer-Aufruf" "0" "$(wc -l < "${D}/sym.log" | tr -d ' ')"

# --- (e) Fehlerpfad: Symbolizer rc != 0 --------------------------------------
E="${TMP}/e"
make_bundle "${E}/bundle" "$FAULT" "$((MODULE_BASE + 0x2000))"
export FAKE_SYM_EXIT=1
run_sym "$E" "${E}/bundle"
unset FAKE_SYM_EXIT
assert_eq "(e) Symbolizer-Fehler -> rc=0" "0" "$RC"
assert_true "(e) kein symbolized.txt" test ! -e "${E}/bundle/symbolized.txt"
assert_eq "(e) keine Temp-Reste" "0" "$(find "${E}/bundle" -name 'symbolized.txt*' | wc -l | tr -d ' ')"
assert_true "(e) Fehler im Log" grep -q 'symbolize: error' "${E}/out.txt"

# --- (f) Collector-Kopplung --------------------------------------------------
F="${TMP}/f"
CRASHINFO="/data/.wine/drive_c/users/steamuser/Documents/The Riftbreaker/crash_info"
FAKE_ROOT="${F}/root"
mkdir -p "${FAKE_ROOT}${CRASHINFO}"
python3 "${TMP}/mkdump.py" "${FAKE_ROOT}${CRASHINFO}/${UUID}.dmp" \
  "$MODULE_BASE" "$MODULE_SIZE" "$FAULT" "$((MODULE_BASE + 0x2000))"
printf '[08:37:25.292] [critical] CRASH\n' >"${FAKE_ROOT}${CRASHINFO}/${UUID}.log"

cat >"${F}/docker" <<'FAKE'
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
chmod +x "${F}/docker"
mkdir -p "${F}/bin"
cp "${SYMBOLIZE_SH}" "${F}/bin/rbmods-crash-symbolize.sh"
chmod +x "${F}/bin/rbmods-crash-symbolize.sh"

cat >"${F}/crash.log" <<EOF
[09:40:50.520] [rbbridge] module_range: via=getmodulehandle base=00006ffff6da0000 size=${MODULE_SIZE} execfn=0000000000000000
[08:37:25.292] [critical] CrashHandlerWin32.cpp:103 -
 CRASH:
(filename not available):0 - (function-name not available)
EOF

mkdir -p "${F}/crashes"
: >"${F}/docker.log"
: >"${F}/sym.log"
set +e
env \
  PATH="${F}:${F}/bin:${BIN}:${PATH}" \
  FAKE_DOCKER_LOG="${F}/docker.log" \
  FAKE_LOGS="${F}/crash.log" \
  FAKE_NEWEST_DMP="${FAKE_ROOT}${CRASHINFO}/${UUID}.dmp" \
  FAKE_ROOT="${FAKE_ROOT}" \
  FAKE_SYM_LOG="${F}/sym.log" \
  RB_CRASH_DIR="${F}/crashes" \
  RB_CRASH_CRASHINFO="${CRASHINFO}" \
  RB_CRASH_CONTAINER="riftbreaker-dedicated" \
  RB_CRASH_WAIT_SECS=2 \
  RB_CRASH_RETRY_SLEEP=0 \
  RB_CRASH_SYMBOLIZE_BIN="${F}/bin/rbmods-crash-symbolize.sh" \
  RB_CRASH_SYMBOLIZE_TOOL="${SYMBOLIZE_PY}" \
  RB_CRASH_LLVM_SYMBOLIZER="${BIN}/llvm-symbolizer" \
  RB_CRASH_DLL="$DLL" \
  RB_CRASH_PDB="$PDB" \
  bash "$COLLECTOR" --once >"${F}/out.txt" 2>&1
RC=$?
set -e
assert_eq "(f) Collector -> rc=0" "0" "$RC"
mapfile -t FB < <(find "${F}/crashes" -mindepth 3 -maxdepth 3 -type d)
assert_eq "(f) genau ein Bundle" "1" "${#FB[@]}"
if [ "${#FB[@]}" -eq 1 ]; then
  BD="${FB[0]}"
  assert_true "(f) Bundle enthaelt symbolized.txt" test -s "${BD}/symbolized.txt"
  meta_sym() { python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['symbolized'].get(sys.argv[2],''))" "${BD}/meta.json" "$1"; }
  assert_eq "(f) meta.symbolized.status" "ok" "$(meta_sym status)"
  assert_eq "(f) meta.symbolized.frames" "2" "$(meta_sym frames)"
  assert_true "(f) symbolized.txt in meta.files" python3 -c "
import json, sys
files = json.load(open(sys.argv[1]))['files']
sys.exit(0 if 'symbolized.txt' in files else 1)
" "${BD}/meta.json"
fi

# --- (g) Zweites Modul (rbbridge.dll) ---------------------------------------
G="${TMP}/g"
RBBRIDGE_BASE=$((0x00006ffff3670000))
RBBRIDGE_SIZE=430080
RBBRIDGE_DLL="${TMP}/rbbridge.dll"
printf 'MZ fake-rbbridge\n' >"$RBBRIDGE_DLL"

mkdir -p "${G}/bundle"
python3 "${TMP}/mkdump2.py" "${G}/bundle/${UUID}.dmp" \
  "$MODULE_BASE" "$MODULE_SIZE" "$RBBRIDGE_BASE" "$RBBRIDGE_SIZE" \
  "$((RBBRIDGE_BASE + 0x50))" "$((MODULE_BASE + 0x100))"

mkdir -p "$G"
: >"${G}/sym.log"
set +e
env \
  PATH="${BIN}:${PATH}" \
  FAKE_SYM_LOG="${G}/sym.log" \
  RB_CRASH_SYMBOLIZE_TOOL="$SYMBOLIZE_PY" \
  RB_CRASH_LLVM_SYMBOLIZER="${BIN}/llvm-symbolizer" \
  RB_CRASH_DLL="$DLL" \
  RB_CRASH_PDB="$PDB" \
  RB_CRASH_RBBRIDGE_DLL="$RBBRIDGE_DLL" \
  RB_CRASH_PYTHON="python3" \
  bash "$SYMBOLIZE_SH" "${G}/bundle" >"${G}/out.txt" 2>&1
RC=$?
set -e
assert_eq "(g) zweites Modul -> rc=0" "0" "$RC"
SYMG="${G}/bundle/symbolized.txt"
assert_true "(g) rbbridge-Fault-Frame annotiert" grep -qP '^0x50\t\[rbbridge.dll\] FAULT frame_80$' "$SYMG"
assert_true "(g) game-Stack-Frame 0x100 als stack" grep -qP '^0x100\tstack frame_256$' "$SYMG"
assert_true "(g) header dll2" grep -q "^# dll2: ${RBBRIDGE_DLL}$" "$SYMG"
assert_true "(g) Aufruf --obj rbbridge.dll" grep -q -- "--obj=${RBBRIDGE_DLL}" "${G}/sym.log"
assert_true "(g) Aufruf --obj game-dll (game-Frame)" grep -q -- "--obj=${DLL}" "${G}/sym.log"

# --- (h) --stack-scan (explizit, jetzt Default): Stack-Frames inkl. Outside-Filter ---
H="${TMP}/h"
make_bundle "${H}/bundle" "$FAULT" "$((MODULE_BASE + 0x2000))" "$OUTSIDE" "$((MODULE_BASE + 0x3000))"
mkdir -p "$H"
: >"${H}/sym.log"
set +e
env PATH="${BIN}:${PATH}" FAKE_SYM_LOG="${H}/sym.log" \
  python3 "$SYMBOLIZE_PY" \
    --dmp "${H}/bundle/${UUID}.dmp" --dll "$DLL" --symbolizer "${BIN}/llvm-symbolizer" \
    --uuid "$UUID" --out "${H}/bundle/symbolized.txt" --stack-scan \
    >"${H}/out.txt" 2>&1
RC=$?
set -e
assert_eq "(h) --stack-scan rc=0" "0" "$RC"
SYMH="${H}/bundle/symbolized.txt"
assert_eq "(h) RVA-Reihenfolge/Anzahl" "0x1000 0x2000 0x3000" \
  "$(awk -F'\t' '/^0x/{print $1}' "$SYMH" | tr '\n' ' ' | sed 's/ $//')"
assert_false "(h) Adresse ausserhalb gefiltert" grep -q '0x1234' "$SYMH"
assert_eq "(h) genau 3 Symbolizer-Aufrufe" "3" "$(wc -l < "${H}/sym.log" | tr -d ' ')"

# --- Aufräumen der Log-Fixture-Prüfung ---------------------------------------
if [ "$FAIL" -ne 0 ]; then
  printf '\ncrash-symbolize.test.sh: FAIL\n'
  exit 1
fi
printf '\ncrash-symbolize.test.sh: OK\n'
