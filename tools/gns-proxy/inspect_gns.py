#!/usr/bin/env python3
"""Inspect the shipped GameNetworkingSockets.dll (Spike #831).

The Riftbreaker dedicated server ships an *open-source* GNS build
("opensource Sep 13 2023", vcpkg commit 65d3dce2d9) but the clients present a
certificate bound to appid 780310.  GNS rejects incoming certs whose appid does
not match ``CSteamNetworkingUtils::GetAppID()`` -- which defaults to 0 in a
vanilla open-source build.  The game only imports five GNS symbols and none of
them sets an appid, so we need to find where the shipped build gets 780310 (or
at least where ``m_nAppID`` lives, so a proxy can set it).

Usage:
    python3 inspect_gns.py <path-to-GameNetworkingSockets.dll> [--disasm N]
"""

from __future__ import annotations

import argparse
import sys

import capstone
import pefile

EXPORTS_OF_INTEREST = (
    "SteamNetworkingUtils_LibV4",
    "SteamNetworkingSockets_LibV12",
    "GameNetworkingSockets_Init",
    "SteamAPI_ISteamNetworkingSockets_SetCertificate",
    "SteamAPI_ISteamNetworkingSockets_GetCertificateRequest",
)


def load(path: str) -> pefile.PE:
    return pefile.PE(path, fast_load=False)


def va_from_rva(pe: pefile.PE, rva: int) -> int:
    return pe.OPTIONAL_HEADER.ImageBase + rva


def rva_from_va(pe: pefile.PE, va: int) -> int:
    return va - pe.OPTIONAL_HEADER.ImageBase


def read_u64(pe: pefile.PE, va: int) -> int:
    return int.from_bytes(pe.get_data(rva_from_va(pe, va), 8), "little")


def exports(pe: pefile.PE) -> dict[str, int]:
    out: dict[str, int] = {}
    if not hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
        return out
    for sym in pe.DIRECTORY_ENTRY_EXPORT.symbols:
        if sym.name:
            out[sym.name.decode()] = sym.address
    return out


def disasm_at(pe: pefile.PE, rva: int, count: int) -> list[str]:
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    code = pe.get_data(rva, 64)
    lines = []
    for insn in md.disasm(code, va_from_rva(pe, rva)):
        lines.append(f"  0x{insn.address:x}: {insn.mnemonic} {insn.op_str}".rstrip())
        if insn.mnemonic == "ret":
            break
        if len(lines) >= count:
            break
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dll")
    ap.add_argument("--disasm", type=int, default=12)
    args = ap.parse_args()

    pe = load(args.dll)
    base = pe.OPTIONAL_HEADER.ImageBase
    exps = exports(pe)
    print(f"image base: 0x{base:x}   exports: {len(exps)}")

    print("\n== exports of interest ==")
    for name in EXPORTS_OF_INTEREST:
        rva = exps.get(name)
        print(f"  {name:60s} {'-- MISSING --' if rva is None else f'RVA 0x{rva:08X}'}")

    utils_rva = exps.get("SteamNetworkingUtils_LibV4")
    if utils_rva is None:
        print("cannot continue without SteamNetworkingUtils_LibV4")
        return 1

    print("\n== SteamNetworkingUtils_LibV4 (returns &s_utils) ==")
    for line in disasm_at(pe, utils_rva, args.disasm):
        print(line)

    # The accessor is `lea rax,[rip+off]; ret` -> find the lea target = static object.
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    obj_va = None
    for insn in md.disasm(pe.get_data(utils_rva, 128), va_from_rva(pe, utils_rva)):
        if insn.mnemonic == "lea" and "rip" in insn.op_str:
            disp = insn.disp
            obj_va = insn.address + insn.size + disp
            break
    if obj_va is None:
        print("could not locate static utils object; disassemble manually")
        return 1

    print(f"\nstatic utils object @ VA 0x{obj_va:x} (RVA 0x{rva_from_va(pe, obj_va):x})")
    vt_va = read_u64(pe, obj_va)
    print(f"  vtable           @ VA 0x{vt_va:x}")

    print("\n== vtable slots whose code is `mov eax,[rcx+disp]; ret` (GetAppID candidates) ==")
    for slot in range(0, 96):
        fn_va = read_u64(pe, vt_va + slot * 8)
        if not (base <= fn_va < base + pe.OPTIONAL_HEADER.SizeOfImage):
            continue
        fn_rva = rva_from_va(pe, fn_va)
        insns = list(md.disasm(pe.get_data(fn_rva, 16), fn_va))
        if len(insns) < 2:
            continue
        a, b = insns[0], insns[1]
        if a.mnemonic == "mov" and a.op_str.startswith("eax, dword ptr [rcx") and b.mnemonic == "ret":
            print(f"  slot {slot:3d}  fn 0x{fn_va:x}  ->  {a.mnemonic} {a.op_str}; ret")
    return 0


if __name__ == "__main__":
    sys.exit(main())
