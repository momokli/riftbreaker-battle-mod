#!/usr/bin/env bash
# ============================================================
# tests/shell/minidump_meta.test.sh
# ------------------------------------------------------------
# Planetfreier Red/Green-Test für deploy/crash-collector/minidump_meta.py (Issue #481).
#
# Baut im tmpdir synthetische MDMP-Fixtures (Python, nur struct) und prüft
# die Parser-Ausgabe — kein Docker, kein Wine, kein Netz, kein Game:
#   (1) gültig:  1 Modul, 1 Thread, Exception im Modul, Stack-Pointer im Modul
#   (2) truncated (halbe Datei)
#   (3) falsche Magic ("MZ …")
#   (4) fehlender Exception-Stream
#   (5) Exception-Adresse außerhalb aller Module
#   (6) nicht existierende Datei
# sowie der `_ok`-Vertrag: JEDER Fehlerfall -> rc=0, kein Traceback.
#
# Läuft in CI (lint.yml):  tests/shell/minidump_meta.test.sh
# ============================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PARSER="${REPO_ROOT}/deploy/crash-collector/minidump_meta.py"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- Fixtures bauen (synthetisches MDMP, nur stdlib) -------------------------
python3 - "$TMP" <<'PY'
import struct
import sys

out = sys.argv[1]

BASE = 0x140000000      # Modulbasis
SIZE = 0x10000          # Modulgroesse (Fault-Offset 0x1234 liegt darin)
NAME = "C:\\game\\riftbreaker.exe"
FAULT_THREAD = 0x1F4
EXC_CODE = 0xC0000005
STACK_START = 0x7FF000
STACK_LEN = 0x40
ADDR_INSIDE = BASE + 0x1234
ADDR_OUTSIDE = 0x7FFF0000DEAD

DIR_RVA = 32
N_STREAMS = 4


def u32(v):
    return struct.pack("<I", v)


def u64(v):
    return struct.pack("<Q", v)


def pad8(n):
    return (n + 7) // 8 * 8


def build(addr, include_exception=True):
    name_bytes = NAME.encode("utf-16-le")
    n_streams = N_STREAMS if include_exception else N_STREAMS - 1
    payload = DIR_RVA + n_streams * 12
    rva_name = payload
    payload += pad8(4 + len(name_bytes))
    rva_stack = payload
    payload += STACK_LEN
    rva_mem = payload
    payload += 4 + 16
    rva_thr = payload
    payload += 4 + 48
    rva_mod = payload
    payload += 4 + 108
    rva_exc = payload
    payload += 168
    total = payload

    buf = bytearray(total)
    buf[0:4] = b"MDMP"
    struct.pack_into("<I", buf, 4, 0xA793)
    struct.pack_into("<I", buf, 8, n_streams)
    struct.pack_into("<I", buf, 12, DIR_RVA)

    entries = [(4, rva_mod), (3, rva_thr), (5, rva_mem)]
    if include_exception:
        entries.append((6, rva_exc))
    for i, (stype, rva) in enumerate(entries):
        struct.pack_into("<I", buf, DIR_RVA + i * 12, stype)
        struct.pack_into("<I", buf, DIR_RVA + i * 12 + 8, rva)

    # Modulname (MINIDUMP_STRING: u32 Laenge in Bytes, dann UTF-16LE)
    struct.pack_into("<I", buf, rva_name, len(name_bytes))
    buf[rva_name + 4:rva_name + 4 + len(name_bytes)] = name_bytes

    # Stack: zwei Pointer ins Modul (einer doppelt), einer ausserhalb, Null.
    ptrs = [BASE + 0x500, BASE + 0xABC, BASE + 0x500, 0x7FF001, 0, 0, 0, 0]
    for i, p in enumerate(ptrs):
        struct.pack_into("<Q", buf, rva_stack + i * 8, p)

    # MemoryList: eine Range = der Stack.
    struct.pack_into("<I", buf, rva_mem, 1)
    struct.pack_into("<Q", buf, rva_mem + 4, STACK_START)
    struct.pack_into("<I", buf, rva_mem + 12, STACK_LEN)
    struct.pack_into("<I", buf, rva_mem + 16, rva_stack)

    # ThreadList: der Fault-Thread mit Stack-Descriptor.
    struct.pack_into("<I", buf, rva_thr, 1)
    struct.pack_into("<I", buf, rva_thr + 4, FAULT_THREAD)
    struct.pack_into("<Q", buf, rva_thr + 4 + 24, STACK_START)
    struct.pack_into("<I", buf, rva_thr + 4 + 32, STACK_LEN)
    struct.pack_into("<I", buf, rva_thr + 4 + 36, rva_stack)

    # ModuleList: ein Modul mit Name.
    struct.pack_into("<I", buf, rva_mod, 1)
    struct.pack_into("<Q", buf, rva_mod + 4, BASE)
    struct.pack_into("<I", buf, rva_mod + 12, SIZE)
    struct.pack_into("<I", buf, rva_mod + 24, rva_name)

    # ExceptionStream: Fault-Thread + ExceptionRecord(Code/Address).
    struct.pack_into("<I", buf, rva_exc, FAULT_THREAD)
    struct.pack_into("<I", buf, rva_exc + 8, EXC_CODE)
    struct.pack_into("<Q", buf, rva_exc + 8 + 16, addr)
    return bytes(buf)


with open(out + "/valid.dmp", "wb") as fh:
    fh.write(build(ADDR_INSIDE))
with open(out + "/outside.dmp", "wb") as fh:
    fh.write(build(ADDR_OUTSIDE))
with open(out + "/noexc.dmp", "wb") as fh:
    fh.write(build(ADDR_INSIDE, include_exception=False))

full = build(ADDR_INSIDE)
with open(out + "/truncated.dmp", "wb") as fh:
    fh.write(full[: len(full) // 2])
with open(out + "/badmagic.dmp", "wb") as fh:
    fh.write(b"MZ" + full[2:])
PY

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

# Führt den Parser aus; setzt RC und ERR (stderr). stdout wird verworfen.
run_parser() {
  local dmp="$1"; shift
  set +e
  python3 "$PARSER" "$@" "$dmp" >/dev/null 2>"${TMP}/err.txt"
  RC=$?
  set -e
  ERR="$(cat "${TMP}/err.txt")"
}

# Feld aus dem JSON (null -> "null", Liste -> JSON-String).
field() {
  python3 - "$1" "$2" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    data = json.load(fh)
value = data.get(sys.argv[2])
if isinstance(value, list):
    print("[" + ",".join(str(v) for v in value) + "]")
else:
    print(json.dumps(value))
PY
}

# --- (1) gültiger Dump -------------------------------------------------------
run_parser "${TMP}/valid.dmp"
assert_eq "valid: rc=0" "0" "$RC"
OUT_JSON="${TMP}/valid.json"
python3 "$PARSER" "${TMP}/valid.dmp" >"$OUT_JSON"
assert_eq "valid: _ok" "true" "$(field "$OUT_JSON" _ok)"
assert_eq "valid: exception_code" "3221225477" "$(field "$OUT_JSON" exception_code)"
assert_eq "valid: exception_address" '"140001234"' "$(field "$OUT_JSON" exception_address)"
assert_eq "valid: module" '"riftbreaker.exe"' "$(field "$OUT_JSON" module)"
assert_eq "valid: module_base" '"140000000"' "$(field "$OUT_JSON" module_base)"
assert_eq "valid: module_size" "65536" "$(field "$OUT_JSON" module_size)"
assert_eq "valid: fault_rva" '"1234"' "$(field "$OUT_JSON" fault_rva)"
assert_eq "valid: fault_thread" "500" "$(field "$OUT_JSON" fault_thread)"
assert_eq "valid: stack_rvas" "[500,abc]" "$(field "$OUT_JSON" stack_rvas)"
# fault_rva == exception_address - module_base
assert_eq "valid: fault_rva-Arithmetik" "True" "$(python3 - "$OUT_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    d = json.load(fh)
print(int(d["fault_rva"], 16) == int(d["exception_address"], 16) - int(d["module_base"], 16))
PY
)"
assert_eq "valid: --json akzeptiert" "0" "$(python3 "$PARSER" --json "${TMP}/valid.dmp" >/dev/null 2>&1; echo $?)"
assert_eq "valid: kein Traceback" "0" "$(printf '%s' "$ERR" | grep -c Traceback || true)"

# --- (2) truncated -----------------------------------------------------------
run_parser "${TMP}/truncated.dmp"
assert_eq "truncated: rc=0" "0" "$RC"
python3 "$PARSER" "${TMP}/truncated.dmp" >"${TMP}/truncated.json"
assert_eq "truncated: _ok=false" "false" "$(field "${TMP}/truncated.json" _ok)"
assert_true "truncated: reason vorhanden" grep -q '"reason"' "${TMP}/truncated.json"
assert_eq "truncated: kein Traceback" "0" "$(printf '%s' "$ERR" | grep -c Traceback || true)"

# --- (3) falsche Magic -------------------------------------------------------
run_parser "${TMP}/badmagic.dmp"
assert_eq "badmagic: rc=0" "0" "$RC"
python3 "$PARSER" "${TMP}/badmagic.dmp" >"${TMP}/badmagic.json"
assert_eq "badmagic: _ok=false" "false" "$(field "${TMP}/badmagic.json" _ok)"
assert_eq "badmagic: kein Traceback" "0" "$(printf '%s' "$ERR" | grep -c Traceback || true)"


# --- (4) fehlender Exception-Stream -----------------------------------------
run_parser "${TMP}/noexc.dmp"
assert_eq "noexc: rc=0" "0" "$RC"
python3 "$PARSER" "${TMP}/noexc.dmp" >"${TMP}/noexc.json"
assert_eq "noexc: _ok=false" "false" "$(field "${TMP}/noexc.json" _ok)"

# --- (5) Adresse ausserhalb aller Module ------------------------------------
run_parser "${TMP}/outside.dmp"
assert_eq "outside: rc=0" "0" "$RC"
python3 "$PARSER" "${TMP}/outside.dmp" >"${TMP}/outside.json"
assert_eq "outside: _ok=true" "true" "$(field "${TMP}/outside.json" _ok)"
assert_eq "outside: exception_address gesetzt" '"7fff0000dead"' "$(field "${TMP}/outside.json" exception_address)"
assert_eq "outside: module null" "null" "$(field "${TMP}/outside.json" module)"
assert_eq "outside: module_base null" "null" "$(field "${TMP}/outside.json" module_base)"
assert_eq "outside: fault_rva null" "null" "$(field "${TMP}/outside.json" fault_rva)"

# --- (6) nicht existierende Datei -------------------------------------------
run_parser "${TMP}/gibt-es-nicht.dmp"
assert_eq "missing: rc=0" "0" "$RC"
python3 "$PARSER" "${TMP}/gibt-es-nicht.dmp" >"${TMP}/missing.json"
assert_eq "missing: _ok=false" "false" "$(field "${TMP}/missing.json" _ok)"
assert_eq "missing: kein Traceback" "0" "$(printf '%s' "$ERR" | grep -c Traceback || true)"

if [ "$FAIL" -ne 0 ]; then
  printf '\nminidump_meta.test.sh: FAIL\n'
  exit 1
fi
printf '\nminidump_meta.test.sh: OK\n'