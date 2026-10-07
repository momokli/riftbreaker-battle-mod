#!/usr/bin/env python3
"""Find the nearest PDB public symbol at or below an RVA.

Usage: findsym.py <publics.txt> <rva-hex> [<rva-hex> ...]
The publics.txt is `llvm-pdbutil dump -publics` output; the leading number is
the DECIMAL section-relative offset (.text base RVA 0x1000).
"""

import bisect
import re
import sys

SECTION_BASE = {1: 0x1000, 2: 0x2DA2000, 3: 0x3EE8000}

path = sys.argv[1]
syms = []
name = None
with open(path, errors="ignore") as fh:
    for line in fh:
        if "S_PUB32" in line and "`" in line:
            name = line.split("`", 1)[1].rstrip("`\n")
            continue
        m = re.search(r"addr = (\d+):(\d+)", line)
        if m and name is not None:
            sec, off = int(m.group(1)), int(m.group(2))
            base = SECTION_BASE.get(sec)
            if base is not None:
                syms.append((base + off, name))
            name = None
syms.sort()
offs = [s[0] for s in syms]

for arg in sys.argv[2:]:
    rva = int(arg, 16)
    i = bisect.bisect_right(offs, rva) - 1
    if i < 0:
        print("0x%x: no symbol" % rva)
        continue
    print("RVA 0x%x -> +0x%x  %s" % (rva, rva - syms[i][0], syms[i][1]))
