#!/usr/bin/env python3
"""Backt den Build-Ref in den Mod (Lua + Manifest).

Ersetzt den Platzhalter ``RBB_BUILD_REF`` in ``lua/rbbattle_autoexec.lua`` und
im ``*.manifest`` durch den echten Ref (SHA bzw. Tag). Fail-loud: fehlt eine
Datei, gibt es kein/mehrere Manifeste oder bleibt ein Platzhalter zurueck, wird
mit Exit != 0 abgebrochen (kein stilles Uebersehen).

Verwendung:
    python3 scripts/bake_mod_ref.py <mod_dir> <ref>
"""
import glob
import io
import os
import sys

PLACEHOLDER = "RBB_BUILD_REF"


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: bake_mod_ref.py <mod_dir> <ref>", file=sys.stderr)
        return 2

    mod_dir, ref = sys.argv[1], sys.argv[2]
    lua = os.path.join(mod_dir, "lua", "rbbattle_autoexec.lua")
    manifests = sorted(glob.glob(os.path.join(mod_dir, "*.manifest")))

    if len(manifests) != 1:
        print(
            "FEHLER: erwarte genau ein *.manifest unter %s, gefunden %d"
            % (mod_dir, len(manifests)),
            file=sys.stderr,
        )
        return 1

    for path in [lua] + manifests:
        if not os.path.isfile(path):
            print("FEHLER: Datei fehlt: %s" % path, file=sys.stderr)
            return 1
        with io.open(path, encoding="utf-8") as fh:
            content = fh.read()
        content = content.replace(PLACEHOLDER, ref)
        if PLACEHOLDER in content:
            print("FEHLER: %s blieb in %s zurueck" % (PLACEHOLDER, path), file=sys.stderr)
            return 1
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)

    print("[bake_mod_ref] OK: ref=%s in lua + manifest gebacken" % ref)
    return 0


if __name__ == "__main__":
    sys.exit(main())
