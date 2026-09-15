#!/usr/bin/env python3
"""Generate cockpit_html.inc from cockpit.html (single source of truth).

Reads bausteine/08-control-ui/cockpit.html (Baustein 08, #474) and emits the C
string constant `COCKPIT_HTML` as an include file next to pipe_bridge.c, so the
bridge serves the operator cockpit UI without a duplicated, hand-maintained
copy. The UI is the single source of truth; the .inc is a build artifact for
pipe_bridge.c and stays in bausteine/rbbridge/pipe-bridge/.

Idempotent + deterministic (byte-for-byte stable for a given input). Run by
scripts/build_rbbridge_tools.sh before compiling pipe_bridge.c.
"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE_DIR = os.path.join(ROOT, "bausteine", "rbbridge", "pipe-bridge")
SRC = os.path.join(ROOT, "bausteine", "08-control-ui", "cockpit.html")
OUT = os.path.join(BRIDGE_DIR, "cockpit_html.inc")


def main():
    if not os.path.isfile(SRC):
        print("FEHLER: Quelle fehlt: %s" % SRC, file=sys.stderr)
        return 1

    html = io.open(SRC, encoding="utf-8").read()
    lines = html.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]

    body = "\n".join(
        '    "%s\\n"' % ln.replace("\\", "\\\\").replace('"', '\\"')
        for ln in lines
    )
    inc = "/* GENERIERT aus cockpit.html (scripts/gen_cockpit_html.py). */\n"
    inc += "static const char COCKPIT_HTML[] =\n" + body + ";\n"

    io.open(OUT, "w", encoding="utf-8", newline="\n").write(inc)
    print("OK: %s -> %s (%d Zeilen)" % (SRC, OUT, len(lines)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
