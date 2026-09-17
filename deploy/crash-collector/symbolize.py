#!/usr/bin/env python3
"""Minidump -> RVA -> Funktionsname (Issue #480, collector-seitige Symbolik).

Liest ein breakpad-/Windows-Minidump (``.dmp``), zieht die Kandidaten-Adressen
(Fault-Adresse aus dem Exception-Stream, der Register-Kontext rip/rsp/rbp des
faultenden Threads, ein geordneter x64-Stack-Unwind via ``.pdata``/``.xdata``
(Fallback RBP-Walk) und optional gescannte Return-Adressen aus dem Stack-
Speicher), filtert sie auf den Bereich
des Game-Moduls (``module_base <= addr < module_base + module_size``), rechnet
je Adresse ``RVA = addr - module_base`` und ruft fuer jede RVA

    llvm-symbolizer --obj=<dll> --relative-address <RVA>

auf (subprocess mit Argument-Liste, kein ``shell=True``). Die PDB findet
llvm-symbolizer ueber das CodeView-Debug-Directory der DLL im selben Ordner —
ein ``--pdb``-Flag existiert (llvm-18) nicht.

Nur stdlib (kein pip-Dependency). Ausgabe (``--out``, Default stdout) hat eine
``# key: value``-Kopfzeile und danach je Frame ``<RVA-hex>\\t<Funktionsname>``.

Exit-Codes:
  0  Erfolg (mindestens ein Frame symbolisiert)
  2  Dump nicht parsebar (kaputte Signatur / zu kurz / Pflicht-Streams fehlen)
  3  kein Kandidat im Modulbereich (nichts zu symbolisieren)
  4  llvm-symbolizer-Aufruf fehlgeschlagen (kein Teilergebnis geschrieben)
"""

import argparse
import os
import struct
import subprocess
import sys

SIG_MDMP = 0x504D444D
STREAM_THREAD_LIST = 3
STREAM_MODULE_LIST = 4
STREAM_MEMORY_LIST = 5
STREAM_EXCEPTION = 6

MODULE_SIZE = 108
THREAD_SIZE = 48
EXCEPTION_SIZE = 168
EXCEPTION_CONTEXT_OFF = 160  # MINIDUMP_EXCEPTION (152) + führende Header (8)
AMD64_RIP_OFF = 0xF8
AMD64_RSP_OFF = 0x98
AMD64_RBP_OFF = 0xA0
AMD64_CONTEXT_MIN = AMD64_RIP_OFF + 8

# x64-Stack-Unwind (Issue #676): Unwind-Opcodes + UNWIND_INFO-Flags der
# Windows-x64-ABI. Der Compiler legt diese Metadaten zwingend ab (fuer
# SEH/C++-Exceptions) — damit ist der Unwind auch bei Frame-Pointer-Omission
# (FPO) verlaesslich.
UWOP_PUSH_NONVOL = 0
UWOP_ALLOC_LARGE = 1
UWOP_ALLOC_SMALL = 2
UWOP_SET_FPREG = 3
UWOP_SAVE_NONVOL = 4
UWOP_SAVE_NONVOL_FAR = 5
UWOP_SAVE_XMM128 = 8
UWOP_SAVE_XMM128_FAR = 9
UWOP_PUSH_MACHFRAME = 10
UNW_FLAG_CHAININFO = 0x04
UNW_MAX_CHAIN = 4
PE32_MAGIC = 0x10B
PE32P_MAGIC = 0x20B
IMAGE_DIR_ENTRY_EXCEPTION = 3

EXIT_OK = 0
EXIT_DUMP_ERROR = 2
EXIT_NO_FRAME = 3
EXIT_TOOL_ERROR = 4


class DumpError(Exception):
    """Minidump ist nicht parsebar."""


class ToolError(Exception):
    """llvm-symbolizer-Aufruf fehlgeschlagen."""


def _u32(data, off):
    if off < 0 or off + 4 > len(data):
        raise DumpError("u32 bei 0x%x ausserhalb des Dumps" % off)
    return struct.unpack_from("<I", data, off)[0]


def _u64(data, off):
    if off < 0 or off + 8 > len(data):
        raise DumpError("u64 bei 0x%x ausserhalb des Dumps" % off)
    return struct.unpack_from("<Q", data, off)[0]


def _reg(data, ctx_rva, ctx_size, off):
    """Register aus dem AMD64-CONTEXT lesen; None wenn nicht vorhanden/lesbar."""
    if not ctx_rva or ctx_size < off + 8:
        return None
    try:
        return _u64(data, ctx_rva + off)
    except DumpError:
        return None


def _utf16(data, rva):
    """MINIDUMP_STRING: u32 Byte-Laenge + UTF-16LE (ohne Null)."""
    if rva == 0:
        return ""
    n = _u32(data, rva)
    raw = data[rva + 4 : rva + 4 + n]
    return raw.decode("utf-16-le", "replace")


def parse_streams(data):
    if len(data) < 32:
        raise DumpError("Dump kuerzer als der Minidump-Header (32 B)")
    sig = _u32(data, 0)
    if sig != SIG_MDMP:
        raise DumpError("keine Minidump-Signatur (MDMP) am Anfang")
    nstreams = _u32(data, 8)
    dir_rva = _u32(data, 12)
    streams = {}
    for i in range(nstreams):
        off = dir_rva + i * 12
        stype = _u32(data, off)
        dsize = _u32(data, off + 4)
        rva = _u32(data, off + 8)
        streams.setdefault(stype, {"rva": rva, "size": dsize})
    return streams


def parse_modules(data, streams):
    ent = streams.get(STREAM_MODULE_LIST)
    if ent is None:
        raise DumpError("ModuleList (Stream 4) fehlt")
    rva = ent["rva"]
    count = _u32(data, rva)
    modules = []
    for i in range(count):
        off = rva + 4 + i * MODULE_SIZE
        base = _u64(data, off)
        size = _u32(data, off + 8)
        name_rva = _u32(data, off + 20)
        name = _utf16(data, name_rva)
        modules.append(
            {
                "name": name,
                "basename": os.path.basename(name.replace("\\", "/")),
                "base": base,
                "size": size,
            }
        )
    return modules


def parse_threads(data, streams):
    ent = streams.get(STREAM_THREAD_LIST)
    if ent is None:
        return []
    rva = ent["rva"]
    count = _u32(data, rva)
    threads = []
    for i in range(count):
        off = rva + 4 + i * THREAD_SIZE
        stack_start = _u64(data, off + 24)
        stack_size = _u32(data, off + 32)
        stack_rva = _u32(data, off + 36)
        ctx_size = _u32(data, off + 40)
        ctx_rva = _u32(data, off + 44)
        threads.append(
            {
                "thread_id": _u32(data, off),
                "stack_start": stack_start,
                "stack_size": stack_size,
                "stack_rva": stack_rva,
                "context_rva": ctx_rva,
                "context_size": ctx_size,
            }
        )
    return threads


def parse_memory(data, streams):
    ent = streams.get(STREAM_MEMORY_LIST)
    if ent is None:
        return []
    rva = ent["rva"]
    count = _u32(data, rva)
    ranges = []
    for i in range(count):
        off = rva + 4 + i * 16
        ranges.append(
            {
                "start": _u64(data, off),
                "size": _u32(data, off + 8),
                "rva": _u32(data, off + 12),
            }
        )
    return ranges


def parse_exception(data, streams):
    ent = streams.get(STREAM_EXCEPTION)
    if ent is None:
        return {}
    rva = ent["rva"]
    if ent["size"] < EXCEPTION_SIZE:
        raise DumpError("Exception-Stream (6) zu klein")
    ctx_size = _u32(data, rva + EXCEPTION_CONTEXT_OFF)
    ctx_rva = _u32(data, rva + EXCEPTION_CONTEXT_OFF + 4)
    rip = None
    if ctx_size >= AMD64_CONTEXT_MIN:
        try:
            rip = _u64(data, ctx_rva + AMD64_RIP_OFF)
        except DumpError:
            rip = None
    return {
        "thread_id": _u32(data, rva),
        "code": _u32(data, rva + 8),
        "address": _u64(data, rva + 24),
        "rip": rip,
        "context_size": ctx_size,
        "context_rva": ctx_rva,
    }


def select_module(modules, want):
    """Game-Modul waehlen: Basename-Treffer, sonst Substring, sonst groesstes."""
    if not modules:
        raise DumpError("ModuleList ist leer")
    wl = want.lower()
    for m in modules:
        if os.path.basename(m["name"]).lower() == wl:
            return m
    for m in modules:
        if wl and wl in m["name"].lower():
            return m
    return max(modules, key=lambda m: m["size"])


def stack_ranges(threads, memory, fault_thread_id):
    ranges = []
    for t in threads:
        if fault_thread_id is not None and t["thread_id"] != fault_thread_id:
            continue
        if t["stack_size"]:
            ranges.append((t["stack_start"], t["stack_size"], t["stack_rva"]))
    if not ranges:
        ranges = [(r["start"], r["size"], r["rva"]) for r in memory if r["size"]]
    return ranges


def fault_regs(data, exc, threads):
    """Register-Kontext (rip/rsp/rbp) des faultenden Threads.

    Bevorzugt den CONTEXT aus der ThreadList (der faultende Thread); ist dort
    keiner vorhanden, faellt auf den Exception-Stream-CONTEXT zurueck.
    Fehlende/zu kurze Register werden als None ausgeliefert.
    """
    ctx_rva = exc.get("context_rva")
    ctx_size = exc.get("context_size")
    tid = exc.get("thread_id")
    for t in threads:
        if tid is None or t["thread_id"] == tid:
            if t["context_rva"] and t["context_size"]:
                ctx_rva = t["context_rva"]
                ctx_size = t["context_size"]
            break
    return {
        "rip": _reg(data, ctx_rva, ctx_size, AMD64_RIP_OFF),
        "rsp": _reg(data, ctx_rva, ctx_size, AMD64_RSP_OFF),
        "rbp": _reg(data, ctx_rva, ctx_size, AMD64_RBP_OFF),
    }


def _module_for(modules, addr):
    for m in modules:
        if m["base"] <= addr < m["base"] + m["size"]:
            return m
    return None


def _rva_to_off(sections, rva):
    """RVA -> Datei-Offset ueber die Section-Tabelle (None = nicht abbildbar)."""
    for vaddr, vsize, raw_ptr, raw_size in sections:
        if vaddr <= rva < vaddr + max(vsize, raw_size):
            return raw_ptr + (rva - vaddr)
    return None


def parse_pe_unwind(dll_path):
    """PE -> Unwind-Daten (.pdata RUNTIME_FUNCTION + .xdata UNWIND_INFO).

    Rueckgabe: dict mit ``data`` (Bytes), ``sections`` (Liste aus
    (vaddr, vsize, raw_ptr, raw_size)) und ``functions`` (Liste aus
    (begin, end, unwind_rva)). ``None`` wenn kein PE / keine Exception-Directory /
    unlesbar (dann greift der RBP-Fallback). Nur stdlib.
    """
    try:
        with open(dll_path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    try:
        if len(data) < 0x40:
            return None
        e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
        if data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
            return None
        coff = e_lfanew + 4
        nsections = struct.unpack_from("<H", data, coff + 2)[0]
        opt_off = coff + 20
        magic = struct.unpack_from("<H", data, opt_off)[0]
        if magic == PE32P_MAGIC:
            dd_off = opt_off + 112
        elif magic == PE32_MAGIC:
            dd_off = opt_off + 96
        else:
            return None
        exc_rva = struct.unpack_from("<I", data, dd_off + IMAGE_DIR_ENTRY_EXCEPTION * 8)[0]
        exc_size = struct.unpack_from("<I", data, dd_off + IMAGE_DIR_ENTRY_EXCEPTION * 8 + 4)[0]
        if not exc_rva or not exc_size:
            return None
        sec_off = opt_off + struct.unpack_from("<H", data, coff + 16)[0]
        sections = []
        for i in range(nsections):
            s = sec_off + i * 40
            if s + 40 > len(data):
                break
            vsize = struct.unpack_from("<I", data, s + 8)[0]
            vaddr = struct.unpack_from("<I", data, s + 12)[0]
            raw_size = struct.unpack_from("<I", data, s + 16)[0]
            raw_ptr = struct.unpack_from("<I", data, s + 20)[0]
            sections.append((vaddr, vsize, raw_ptr, raw_size))
        off = _rva_to_off(sections, exc_rva)
        if off is None:
            return None
        functions = []
        for i in range(exc_size // 12):
            base = off + i * 12
            if base + 12 > len(data):
                break
            begin = struct.unpack_from("<I", data, base)[0]
            end = struct.unpack_from("<I", data, base + 4)[0]
            unw = struct.unpack_from("<I", data, base + 8)[0]
            functions.append((begin, end, unw))
        return {"data": data, "sections": sections, "functions": functions}
    except (struct.error, IndexError):
        return None


def _find_runtime_function(functions, rva):
    for begin, end, unw in functions:
        if begin <= rva < end:
            return (begin, end, unw)
    return None


def _parse_unwind_info(data, sections, unwind_rva):
    """UNWIND_INFO an ``unwind_rva`` parsen -> dict oder None.

    ``codes`` = Liste (code_off, op, opinfo, operand); ``operand`` ist die
    aufgeloeste Groesse (ALLOC) bzw. der skalierte Offset (SAVE_*). Inline-
    Operanden (ALLOC_LARGE/SAVE_*_FAR) sind bereits eingerechnet.
    """
    off = _rva_to_off(sections, unwind_rva)
    if off is None or off + 4 > len(data):
        return None
    flags = data[off] >> 3
    prolog = data[off + 1]
    count = data[off + 2]
    b3 = data[off + 3]
    frame_reg = b3 & 0x0F
    frame_offset = b3 >> 4
    codes = []
    p = off + 4
    i = 0
    while i < count:
        if p + 2 > len(data):
            return None
        code_off = data[p]
        code_b = data[p + 1]
        op = code_b & 0x0F
        opinfo = code_b >> 4
        p += 2
        i += 1
        operand = None
        if op == UWOP_ALLOC_LARGE:
            if opinfo == 0:
                if p + 2 > len(data):
                    return None
                operand = struct.unpack_from("<H", data, p)[0] * 8
                p += 2
                i += 1
            else:
                if p + 4 > len(data):
                    return None
                operand = struct.unpack_from("<I", data, p)[0]
                p += 4
                i += 2
        elif op == UWOP_ALLOC_SMALL:
            operand = (opinfo + 1) * 8
        elif op == UWOP_SAVE_NONVOL:
            if p + 2 > len(data):
                return None
            operand = struct.unpack_from("<H", data, p)[0] * 8
            p += 2
            i += 1
        elif op == UWOP_SAVE_NONVOL_FAR:
            if p + 4 > len(data):
                return None
            operand = struct.unpack_from("<I", data, p)[0]
            p += 4
            i += 2
        elif op == UWOP_SAVE_XMM128:
            if p + 2 > len(data):
                return None
            operand = struct.unpack_from("<H", data, p)[0] * 16
            p += 2
            i += 1
        elif op == UWOP_SAVE_XMM128_FAR:
            if p + 4 > len(data):
                return None
            operand = struct.unpack_from("<I", data, p)[0]
            p += 4
            i += 2
        codes.append((code_off, op, opinfo, operand))
    if count % 2:
        p += 2  # Padding auf 4-Byte-Grenze
    chain = None
    if flags & UNW_FLAG_CHAININFO:
        if p + 12 <= len(data):
            chain = struct.unpack_from("<III", data, p)
    return {
        "flags": flags,
        "prolog": prolog,
        "codes": codes,
        "frame_reg": frame_reg,
        "frame_offset": frame_offset,
        "chain": chain,
    }


def _apply_unwind(info, rsp, regs, read_u64, prolog_off=None):
    """Unwind-Codes rueckwaerts anwenden -> neuer RSP (oder None bei Abbruch).

    ``regs`` (Register-Nr -> Wert) wird mutiert (Restore aus PUSH_NONVOL/
    SAVE_NONVOL). Nur der Return-Adressen-Pfad wird gebraucht; XMM-Saves werden
    ignoriert.
    """
    for code_off, op, opinfo, operand in reversed(info["codes"]):
        if prolog_off is not None and code_off > prolog_off:
            continue
        if op == UWOP_PUSH_NONVOL:
            v = read_u64(rsp)
            if v is not None:
                regs[opinfo] = v
            rsp += 8
        elif op in (UWOP_ALLOC_SMALL, UWOP_ALLOC_LARGE):
            rsp += operand
        elif op == UWOP_SET_FPREG:
            fr = regs.get(info["frame_reg"])
            if fr is None:
                return None
            rsp = fr - info["frame_offset"] * 16
        elif op in (UWOP_SAVE_NONVOL, UWOP_SAVE_NONVOL_FAR):
            v = read_u64(rsp + operand)
            if v is not None:
                regs[opinfo] = v
        elif op in (UWOP_SAVE_XMM128, UWOP_SAVE_XMM128_FAR):
            pass  # XMM wird fuer die Return-Adressen-Kette nicht gebraucht
        elif op == UWOP_PUSH_MACHFRAME:
            rsp += 8 + (8 if opinfo else 0)
        else:
            return None
    return rsp


def x64_unwind(modules, pe_map, rip, rsp, rbp, read_u64, max_frames):
    """x64-Stack-Unwind via .pdata/.xdata — geordnete Return-Adressen.

    Geht ueber Modulgrenzen (je Frame das PE des Moduls, das den RIP enthaelt)
    und deckt den jump-to-garbage-Fall ab (RIP in keinem Modul -> Ruecksprung-
    adresse direkt bei ``[rsp]``). Bricht ab bei fehlendem RUNTIME_FUNCTION,
    kaputter UNWIND_INFO, Stack-Read ausserhalb oder max_frames.
    """
    out = []
    cur_rip = rip
    cur_rsp = rsp
    regs = {5: rbp}  # 5 = RBP (einziger gestuetzter Frame-Register)
    for _ in range(max_frames):
        if cur_rip is None or cur_rsp is None:
            break
        mod = _module_for(modules, cur_rip)
        if mod is None:
            # jump-to-garbage: RIP in keinem Modul -> Ruecksprungadresse direkt bei [rsp].
            ret = read_u64(cur_rsp)
            if ret is None or ret == 0:
                break
            out.append(ret)
            cur_rip = ret
            cur_rsp = cur_rsp + 8
            continue
        pe = pe_map.get(mod["name"])
        if pe is None:
            break  # Modul ohne PE/Unwind-Daten -> Abbruch (RBP-Fallback)
        rva = cur_rip - mod["base"]
        rf = _find_runtime_function(pe["functions"], rva)
        if rf is None:
            break  # im Modul, aber in keiner Funktion: konservativ abbrechen
        info = _parse_unwind_info(pe["data"], pe["sections"], rf[2])
        depth = 0
        while info is not None and (info["flags"] & UNW_FLAG_CHAININFO) and info["chain"]:
            info = _parse_unwind_info(pe["data"], pe["sections"], info["chain"][2])
            depth += 1
            if depth > UNW_MAX_CHAIN:
                info = None
        if info is None:
            break
        prolog_off = None
        if info["prolog"] and 0 <= (rva - rf[0]) < info["prolog"]:
            prolog_off = rva - rf[0]
        new_rsp = _apply_unwind(info, cur_rsp, regs, read_u64, prolog_off)
        if new_rsp is None:
            break
        ret = read_u64(new_rsp)
        if ret is None or ret == 0:
            break
        out.append(ret)
        cur_rip = ret
        cur_rsp = new_rsp + 8
    return out


def read_text_range(dll_path):
    """PE-Section-Tabelle lesen -> (text_rva, text_size) der .text-Section.

    Nur Code-Adressen sind ein verlaesslicher Stack-Frame; Daten (vftable,
    Konstanten, __data_end__/__bss_end__) fallen in .rdata/.data und sollen
    aus dem Stack-Scan rausgefiltert werden. Rueckgabe None = nicht lesbar
    (dann wird nicht gefiltert, das alte Verhalten).
    """
    try:
        with open(dll_path, "rb") as fh:
            fh.seek(0x3C)
            e_lfanew = struct.unpack("<I", fh.read(4))[0]
            fh.seek(e_lfanew)
            if fh.read(4) != b"PE\0\0":
                return None
            coff = fh.read(20)
            nsections = struct.unpack_from("<H", coff, 2)[0]
            opt_size = struct.unpack_from("<H", coff, 16)[0]
            sec_off = e_lfanew + 4 + 20 + opt_size
            for i in range(nsections):
                fh.seek(sec_off + i * 40)
                sec = fh.read(40)
                name = sec[:8].rstrip(b"\0").decode("ascii", "replace")
                if name == ".text":
                    vsize = struct.unpack_from("<I", sec, 8)[0]
                    vaddr = struct.unpack_from("<I", sec, 12)[0]
                    return (vaddr, vsize)
    except (OSError, struct.error):
        return None
    return None


def collect_frames(
    data, modules, exc, threads, memory, max_frames, text_ranges=None, stack_scan=False, regs=None, pe_map=None
):
    """Frames aller gegebenen Module sammeln; jeder Frame kennt sein Modul.

    Reihenfolge im Ergebnis: fault (Exception-Address), context (RIP), dann der
    geordnete Unwind (x64 .pdata/.xdata, Fallback RBP-Walk; kind="unwind") und
    zuletzt der heuristische Stack-Scan (nur auf --stack-scan). Verlaesslich sind
    fault, context und unwind; der Stack-Scan fischt Daten-Pointer/vftable/
    Konstanten und fremde Code-Adressen und ist deshalb per Default AUS.

    Rueckgabe: (frames, unwind_method) mit unwind_method in {x64, rbp, none}.
    """
    frames, seen = [], set()
    text_ranges = text_ranges or {}
    if regs is None:
        regs = fault_regs(data, exc, threads)

    def add(addr, kind):
        if addr is None:
            return
        m = _module_for(modules, addr)
        if m is None:
            return
        rva = addr - m["base"]
        if kind in ("stack", "unwind"):
            tr = text_ranges.get(m["name"])
            if tr and not (tr[0] <= rva < tr[0] + tr[1]):
                return
        key = (m["name"], rva)
        if key in seen:
            return
        seen.add(key)
        frames.append({"addr": addr, "rva": rva, "kind": kind, "module": m})

    def read_stack(addr):
        for start, size, rva in stack_ranges(threads, memory, exc.get("thread_id")):
            if start <= addr and addr + 8 <= start + size:
                off = rva + (addr - start)
                if 0 <= off and off + 8 <= len(data):
                    return struct.unpack_from("<Q", data, off)[0]
        return None

    add(exc.get("address"), "fault")
    add(exc.get("rip"), "context")
    for tid in [exc.get("thread_id")]:
        for t in threads:
            if tid is None or t["thread_id"] == tid:
                if t["context_size"] >= AMD64_CONTEXT_MIN:
                    try:
                        add(_u64(data, t["context_rva"] + AMD64_RIP_OFF), "context")
                    except DumpError:
                        pass
                break

    # x64-Stack-Unwind (Issue #676): primaer. Greift nur, wenn ein PE mit
    # .pdata/.xdata vorliegt; sonst Fallback auf den RBP-Walk (#668).
    unwind_method = "none"
    if pe_map:
        rets = x64_unwind(modules, pe_map, regs.get("rip"), regs.get("rsp"), regs.get("rbp"), read_stack, max_frames)
        if rets:
            for ret in rets:
                add(ret, "unwind")
            unwind_method = "x64"

    if unwind_method == "none":
        # Fallback: geordneter RBP-Frame-Pointer-Walk ([rbp]=saved rbp, [rbp+8]=ret).
        rbp = regs.get("rbp")
        if rbp:
            for start, size, rva in stack_ranges(threads, memory, exc.get("thread_id")):
                if size < 16 or not (start <= rbp and rbp + 16 <= start + size):
                    continue
                cur = rbp
                for _ in range(max_frames):
                    if len(frames) >= max_frames:
                        break
                    if not (start <= cur and cur + 16 <= start + size):
                        break
                    off = rva + (cur - start)
                    if off < 0 or off + 16 > len(data):
                        break
                    saved = struct.unpack_from("<Q", data, off)[0]
                    ret = struct.unpack_from("<Q", data, off + 8)[0]
                    add(ret, "unwind")
                    unwind_method = "rbp"
                    if not saved or saved <= cur:
                        break
                    cur = saved
                break

    for start, count, rva in stack_ranges(threads, memory, exc.get("thread_id")):
        if not stack_scan or len(frames) >= max_frames:
            break
        for i in range(0, count - 7, 8):
            off = rva + i
            if off + 8 > len(data):
                break
            add(struct.unpack_from("<Q", data, off)[0], "stack")
            if len(frames) >= max_frames:
                break
    return frames[:max_frames], unwind_method


def symbolize_rva(symbolizer, dll, rva, timeout):
    """RVA -> Funktionsname. Wirft ToolError bei rc != 0 / leerer Ausgabe."""
    cmd = [symbolizer, "--obj=%s" % dll, "--relative-address", hex(rva)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ToolError("llvm-symbolizer Timeout (>%ss) bei RVA 0x%x" % (timeout, rva))
    except OSError as err:
        raise ToolError("llvm-symbolizer nicht ausfuehrbar: %s" % err)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise ToolError("llvm-symbolizer rc=%d bei RVA 0x%x: %s" % (proc.returncode, rva, tail[-1] if tail else ""))
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            return line
    raise ToolError("llvm-symbolizer lieferte keine Ausgabe fuer RVA 0x%x" % rva)


def render(uuid, modules, frames, names, fault_address, dlls, symbolizer, regs=None, unwind_method="none"):
    primary = modules[0]
    lines = [
        "# rbmods crash symbolizer (Issue #480)",
        "# uuid: %s" % uuid,
        "# module: %s" % primary["name"],
        "# module_base: 0x%x" % primary["base"],
        "# module_size: %d" % primary["size"],
        "# fault_address: %s" % (("0x%x" % fault_address) if fault_address else ""),
        "# dll: %s" % dlls.get(primary["name"], ""),
        "# tool: %s" % symbolizer,
    ]
    if regs:
        for reg in ("rsp", "rbp", "rip"):
            val = regs.get(reg)
            if val is not None:
                lines.append("# %s: 0x%x" % (reg, val))
    lines.append("# unwind: %s" % unwind_method)
    for m in modules[1:]:
        lines.append("# module2: %s" % m["name"])
        lines.append("# module2_base: 0x%x" % m["base"])
        lines.append("# module2_size: %d" % m["size"])
        lines.append("# dll2: %s" % dlls.get(m["name"], ""))
    lines.append("# frames: %d" % len(frames))
    lines.append("")
    for frame, name in zip(frames, names):
        mod = ""
        if frame["module"]["name"] != primary["name"]:
            mod = "[%s] " % frame["module"]["basename"]
        tag = "FAULT" if frame["kind"] == "fault" else frame["kind"]
        lines.append("0x%x\t%s%s %s" % (frame["rva"], mod, tag, name))
    return "\n".join(lines) + "\n"


def load_dump(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError as err:
        raise DumpError("Dump nicht lesbar: %s" % err)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Minidump collector-seitig symbolisieren (#480)")
    ap.add_argument("--dmp", required=True, help="Pfad zum Minidump (.dmp)")
    ap.add_argument("--dll", required=True, help="Game-Modul fuer llvm-symbolizer --obj")
    ap.add_argument("--symbolizer", default="llvm-symbolizer", help="llvm-symbolizer-Binary")
    ap.add_argument(
        "--module", default="riftbreaker_dll_win_release.dll", help="Basename/Substring des zu symbolisierenden Moduls"
    )
    ap.add_argument("--dll2", default="", help="Optional: zweites Modul (z.B. rbbridge.dll) fuer llvm-symbolizer --obj")
    ap.add_argument("--module2", default="", help="Optional: Basename/Substring des zweiten Moduls")
    ap.add_argument("--out", default="-", help="Ausgabedatei ('-' = stdout)")
    ap.add_argument("--uuid", default="", help="Crash-uuid fuer den Header")
    ap.add_argument("--max-frames", type=int, default=32, help="maximale Frame-Anzahl")
    ap.add_argument("--stack-scan", action="store_true", help="heuristischen Stack-Scan zuschalten (Default: aus)")
    ap.add_argument("--timeout", type=float, default=60.0, help="Timeout je Symbolizer-Aufruf")
    ap.add_argument("--list-modules", action="store_true", help="ModuleList ausgeben und beenden")
    args = ap.parse_args(argv)

    try:
        data = load_dump(args.dmp)
        streams = parse_streams(data)
        modules = parse_modules(data, streams)
    except DumpError as err:
        print("symbolize: Dump nicht parsebar: %s" % err, file=sys.stderr)
        return EXIT_DUMP_ERROR

    if args.list_modules:
        for m in modules:
            print("0x%016x  %10d  %s" % (m["base"], m["size"], m["name"]))
        return EXIT_OK

    uuid = args.uuid or os.path.basename(args.dmp)
    if uuid.endswith(".dmp"):
        uuid = uuid[:-4]

    try:
        selected = [select_module(modules, args.module)]
        if args.dll2 and args.module2:
            second = select_module(modules, args.module2)
            if second["name"] != selected[0]["name"]:
                selected.append(second)
        dlls = {selected[0]["name"]: args.dll}
        if len(selected) > 1:
            dlls[selected[1]["name"]] = args.dll2
        text_ranges = {name: read_text_range(path) for name, path in dlls.items()}
        pe_map = {name: parse_pe_unwind(path) for name, path in dlls.items()}
        exc = parse_exception(data, streams)
        threads = parse_threads(data, streams)
        memory = parse_memory(data, streams)
        regs = fault_regs(data, exc, threads)
        frames, unwind_method = collect_frames(
            data, selected, exc, threads, memory, args.max_frames, text_ranges, args.stack_scan, regs, pe_map
        )
    except DumpError as err:
        print("symbolize: Dump nicht parsebar: %s" % err, file=sys.stderr)
        return EXIT_DUMP_ERROR

    fault_address = exc.get("address")
    if not frames:
        print(
            "symbolize: kein Kandidat im Modulbereich (base=0x%x size=%d fault=%s)"
            % (selected[0]["base"], selected[0]["size"], ("0x%x" % fault_address) if fault_address else "?"),
            file=sys.stderr,
        )
        return EXIT_NO_FRAME

    dlls = {selected[0]["name"]: args.dll}
    if len(selected) > 1:
        dlls[selected[1]["name"]] = args.dll2

    names = []
    for frame in frames:
        dll = dlls.get(frame["module"]["name"], args.dll)
        try:
            names.append(symbolize_rva(args.symbolizer, dll, frame["rva"], args.timeout))
        except ToolError as err:
            print("symbolize: %s" % err, file=sys.stderr)
            return EXIT_TOOL_ERROR

    text = render(uuid, selected, frames, names, fault_address, dlls, args.symbolizer, regs, unwind_method)
    if args.out == "-":
        sys.stdout.write(text)
    else:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
