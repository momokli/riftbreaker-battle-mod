#!/usr/bin/env python3
"""minidump_meta.py — minimaler MDMP-Parser (Issue #481).

Liest einen Windows-Minidump (``MDMP``) mit **nur stdlib** (``struct``/``json``/
``sys``/``os``) und liefert die für die Crash-Triage entscheidenden Felder als
JSON. Bewusst minimal: **kein** PDB, **keine** Symbole, kein Netz, keine
Game-Abhängigkeit — damit planetfrei testbar.

Genutzte Streams: ``Exception(6)``, ``ModuleList(4)``, ``ThreadList(3)``,
``MemoryList(5)``; ``Memory64List(9)`` nur best-effort (siehe Grenzen).

CLI::

    minidump_meta.py [--json] <dmp>

Ausgabe (stdout, JSON)::

    {
      "exception_code": 3221225477,
      "exception_address": "140001234",
      "module": "riftbreaker.exe",
      "module_base": "140000000",
      "module_size": 4096,
      "fault_rva": "1234",
      "fault_thread": 500,
      "stack_rvas": ["500", "abc"],
      "_ok": true
    }

Adressen/Offsets sind Hex-Strings **ohne** ``0x`` (wie ``meta.json``), Zähler
sind ``int``. Jeder Parsefehler (keine ``MDMP``-Magic, truncated, fehlender
Stream, RVA außerhalb der Datei) → ``{"_ok": false, "reason": ...}`` und
**rc=0** (kein Traceback): der Collector darf an einem kaputten Dump nicht
sterben. ``exception_code``/``fault_thread`` sind ``null``, wenn kein
Exception-Stream vorliegt.

Grenzen (bewusst, siehe docs/DEPLOYMENT.md):
  * Stack-Pointer fest 8 Byte (64-bit Wine) — 32-bit-Dumps liefern falsche
    Kandidaten, die Modulbasis-Breite wird nicht erkannt (nicht geraten).
  * ``stack_rvas`` wird nur gegen das Fault-Modul relativiert, dedupliziert,
    aufsteigend, Cap 32.
  * Liegt der Stack nicht in ``MemoryList``/``Memory64List``, bleibt
    ``stack_rvas`` leer (kein Fehler).
"""

import json
import os
import struct
import sys

MDMP_MAGIC = 0x504D444D
STREAM_THREAD_LIST = 3
STREAM_MODULE_LIST = 4
STREAM_MEMORY_LIST = 5
STREAM_EXCEPTION = 6
STREAM_MEMORY64_LIST = 9

MINIDUMP_THREAD_SIZE = 48
MINIDUMP_MODULE_SIZE = 108
MINIDUMP_MEMORY_DESCRIPTOR_SIZE = 16
DIRECTORY_ENTRY_SIZE = 12

STACK_POINTER_SIZE = 8
STACK_RVA_CAP = 32


class ParseError(Exception):
    """Jeder MDMP-Verstoß — wird zu ``_ok:false`` statt zu einem Traceback."""


class Dump:
    """Bounds-geprüfter Lesezugriff auf die Dump-Datei."""

    def __init__(self, path):
        self.fh = open(path, "rb")
        self.size = os.fstat(self.fh.fileno()).st_size

    def close(self):
        self.fh.close()

    def read(self, off, size):
        if off < 0 or size < 0 or off + size > self.size:
            raise ParseError(
                "read out of bounds (off=0x%x size=%d file=%d)" % (off, size, self.size)
            )
        self.fh.seek(off)
        data = self.fh.read(size)
        if len(data) != size:
            raise ParseError("short read at 0x%x" % off)
        return data

    def u32(self, off):
        return struct.unpack("<I", self.read(off, 4))[0]

    def u64(self, off):
        return struct.unpack("<Q", self.read(off, 8))[0]


def parse_streams(dump):
    """Stream-Typ -> RVA aus dem Verzeichnis (erster Eintrag gewinnt)."""
    if dump.u32(0) != MDMP_MAGIC:
        raise ParseError("not a minidump (bad magic)")
    number = dump.u32(8)
    directory_rva = dump.u32(12)
    streams = {}
    for i in range(number):
        base = directory_rva + i * DIRECTORY_ENTRY_SIZE
        stream_type = dump.u32(base)
        rva = dump.u32(base + 8)
        streams.setdefault(stream_type, rva)
    return streams


def parse_exception(dump, rva):
    """ExceptionStream -> (thread_id, exception_code, exception_address)."""
    thread_id = dump.u32(rva)
    # ExceptionRecord beginnt bei RVA+8; ExceptionAddress @ +16 im Record.
    code = dump.u32(rva + 8)
    address = dump.u64(rva + 8 + 16)
    return thread_id, code, address


def parse_module_name(dump, rva):
    if rva == 0:
        return ""
    length = dump.u32(rva)
    raw = dump.read(rva + 4, length)
    return raw.decode("utf-16-le", "replace").rstrip("\x00")


def parse_modules(dump, rva):
    count = dump.u32(rva)
    modules = []
    for i in range(count):
        base = rva + 4 + i * MINIDUMP_MODULE_SIZE
        image_base = dump.u64(base)
        size = dump.u32(base + 8)
        name_rva = dump.u32(base + 20)
        name = os.path.basename(parse_module_name(dump, name_rva).replace("\\", "/"))
        modules.append({"base": image_base, "size": size, "name": name})
    return modules


def parse_threads(dump, rva):
    count = dump.u32(rva)
    threads = []
    for i in range(count):
        base = rva + 4 + i * MINIDUMP_THREAD_SIZE
        threads.append(
            {
                "id": dump.u32(base),
                "stack_start": dump.u64(base + 24),
                "stack_size": dump.u32(base + 32),
                "stack_rva": dump.u32(base + 36),
            }
        )
    return threads


def parse_memory_list(dump, rva):
    """MemoryList -> {StartOfMemoryRange: (Rva, DataSize)}."""
    count = dump.u32(rva)
    ranges = {}
    for i in range(count):
        base = rva + 4 + i * MINIDUMP_MEMORY_DESCRIPTOR_SIZE
        start = dump.u64(base)
        size = dump.u32(base + 8)
        data_rva = dump.u32(base + 12)
        ranges[start] = (data_rva, size)
    return ranges


def parse_memory64_list(dump, rva):
    """Memory64List (best-effort) -> {Start: (berechnete Rva, DataSize)}."""
    count = dump.u64(rva)
    cursor = dump.u64(rva + 8)
    ranges = {}
    for i in range(count):
        base = rva + 16 + i * MINIDUMP_MEMORY_DESCRIPTOR_SIZE
        start = dump.u64(base)
        size = dump.u64(base + 8)
        ranges[start] = (cursor, size)
        cursor += size
    return ranges


def scan_stack(dump, location, module_base, module_size):
    """8-Byte-Pointer im Stack, die ins Fault-Modul zeigen (relativiert)."""
    data_rva, size = location
    if not data_rva or size < STACK_POINTER_SIZE:
        return []
    raw = dump.read(data_rva, size)
    limit = module_base + module_size
    found = set()
    for off in range(0, len(raw) - STACK_POINTER_SIZE + 1, STACK_POINTER_SIZE):
        value = struct.unpack_from("<Q", raw, off)[0]
        if module_base <= value < limit:
            found.add(value - module_base)
    return sorted(found)[:STACK_RVA_CAP]


def _hex(value):
    if value is None:
        return None
    return "%x" % value


def analyze(path):
    """Dump -> Ergebnis-Dict (auch für Fehlerfälle; wirft nicht nach außen)."""
    if not os.path.isfile(path):
        return {"_ok": False, "reason": "no such file: %s" % path}
    dump = Dump(path)
    try:
        streams = parse_streams(dump)
        if STREAM_EXCEPTION not in streams:
            raise ParseError("no exception stream")
        thread_id, code, address = parse_exception(dump, streams[STREAM_EXCEPTION])

        modules = []
        if STREAM_MODULE_LIST in streams:
            modules = parse_modules(dump, streams[STREAM_MODULE_LIST])

        module = None
        module_base = None
        module_size = None
        for entry in modules:
            if entry["base"] <= address < entry["base"] + entry["size"]:
                module = entry["name"] or None
                module_base = entry["base"]
                module_size = entry["size"]
                break

        fault_rva = None
        if module_base is not None:
            fault_rva = address - module_base

        stack_rvas = []
        if STREAM_THREAD_LIST in streams and module_base is not None:
            threads = parse_threads(dump, streams[STREAM_THREAD_LIST])
            ranges = {}
            if STREAM_MEMORY_LIST in streams:
                ranges.update(parse_memory_list(dump, streams[STREAM_MEMORY_LIST]))
            if STREAM_MEMORY64_LIST in streams:
                for key, value in parse_memory64_list(dump, streams[STREAM_MEMORY64_LIST]).items():
                    ranges.setdefault(key, value)
            thread = next((t for t in threads if t["id"] == thread_id), None)
            if thread is not None:
                location = ranges.get(thread["stack_start"])
                if location is None and thread["stack_rva"]:
                    location = (thread["stack_rva"], thread["stack_size"])
                if location is not None:
                    stack_rvas = scan_stack(dump, location, module_base, module_size)

        return {
            "_ok": True,
            "exception_code": code,
            "exception_address": _hex(address),
            "module": module,
            "module_base": _hex(module_base),
            "module_size": module_size,
            "fault_rva": _hex(fault_rva),
            "fault_thread": thread_id,
            "stack_rvas": [_hex(v) for v in stack_rvas],
        }
    finally:
        dump.close()


def main(argv):
    args = [a for a in argv[1:] if a != "--json"]
    if len(args) != 1:
        sys.stderr.write("usage: %s [--json] <dmp>\n" % os.path.basename(argv[0]))
        return 2
    try:
        result = analyze(args[0])
    except Exception as exc:  # bewusst: kein Traceback — Vertrag ist rc=0.
        result = {"_ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))