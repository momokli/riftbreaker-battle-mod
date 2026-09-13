#!/usr/bin/env python3
"""Disassembler fuer die Riftbreaker-DLL (x64, Build 2.0.58485).

RE-Tooling (Issue #363/#365). Nutzt capstone + pefile aus ~/.venvs/rb-re.
Call-/RIP-rel-Targets sind in op_str inline sichtbar (call 0x... / [rip+0x...]).

Aufruf:
  ~/.venvs/rb-re/bin/python tools/re/disasm.py 0x2D3520 [60]
"""
import os
import sys
import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

# Default: Mac/CrossOver-Pfad. Per Env RBB_DLL uebersteuerbar (z.B. planet).
DLL = os.environ.get("RBB_DLL") or os.path.expanduser(
    "~/Library/Application Support/CrossOver/Bottles/gams/drive_c/"
    "Program Files (x86)/The Riftbreaker/bin/riftbreaker_dll_win_release.dll")

pe = pefile.PE(DLL, fast_load=True)
data = open(DLL, "rb").read()
ib = pe.OPTIONAL_HEADER.ImageBase


def rva_to_off(rva):
    for s in pe.sections:
        lo = s.VirtualAddress
        hi = s.VirtualAddress + max(s.Misc_VirtualSize, s.SizeOfRawData)
        if lo <= rva < hi:
            return s.PointerToRawData + (rva - lo)
    return None


def main():
    rva = int(sys.argv[1], 16)
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    off = rva_to_off(rva)
    if off is None:
        print("RVA 0x%x nicht mappable" % rva)
        return 1
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    print("== RVA 0x%x (VA 0x%x) ==" % (rva, ib + rva))
    for insn in md.disasm(data[off:off + count * 8], ib + rva):
        print("  0x%x: %-8s %s" % (insn.address, insn.mnemonic, insn.op_str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
