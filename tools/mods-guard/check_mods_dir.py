#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_mods_dir.py — Guard/Regression-Check: genau EIN manifest-tragender
Mod-Ordner in <server>/mods/ (Issue #212).

Der Riftbreaker-Dedicated-Server lädt JEDEN Unterordner von mods/ mit einer
*.manifest als eigene External-Content-Mod — unabhängig vom Ordnernamen. Ein
Backup-Ordner mit gleicher Content-ID (z. B. rbbattle.bak-<ts>/) wird daher
ZUSÄTZLICH geladen: die Versionskonstante wird überschrieben und beide Kopien
registrieren ihre Handler doppelt (handler_errors / event_unreadable).

Dieses Skript prüft einen mods/-Ordner darauf, dass außer dem erwarteten
Mod-Ordner kein weiterer Ordner mit *.manifest existiert. Einsatz: lokal/CI als
Regression-Check bzw. als Deploy-Pre-Gate. Dieselbe Regel setzt die
Ansible-Rolle `deploy/roles/riftbreaker-server` als Guard um.

Aufruf:
  python3 tools/mods-guard/check_mods_dir.py <mods-dir>
  python3 tools/mods-guard/check_mods_dir.py <mods-dir> --expected rbbattle
  python3 tools/mods-guard/check_mods_dir.py <mods-dir> --quiet

Exit-Codes:
  0 = ok (kein Fremd-Ordner mit *.manifest)
  1 = Verstoß (Fremd-Ordner mit *.manifest gefunden)
  2 = Aufruf-/IO-Fehler (kein Verzeichnis / nicht lesbar)

Nur Standardbibliothek (Python 3.8+).
"""

import argparse
import os
import sys

DEFAULT_EXPECTED = "rbbattle"
MANIFEST_SUFFIX = ".manifest"

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_USAGE = 2


def _contains_manifest(path):
    """True, wenn unter path (rekursiv) mindestens eine *.manifest liegt."""
    for _root, _dirs, files in os.walk(path):
        for name in files:
            if name.endswith(MANIFEST_SUFFIX):
                return True
    return False


def find_manifest_dirs(mods_dir):
    """Direkte Unterordner von mods_dir, die eine *.manifest enthalten.

    Rueckgabe: alphabetisch sortierte Liste von Ordnernamen.
    """
    found = []
    for name in sorted(os.listdir(mods_dir)):
        child = os.path.join(mods_dir, name)
        if os.path.isdir(child) and _contains_manifest(child):
            found.append(name)
    return found


def build_parser():
    parser = argparse.ArgumentParser(
        description="Prueft, dass in <server>/mods/ nur der Ziel-Mod-Ordner "
                    "eine *.manifest traegt (Fremd-/Backup-Ordner = Fehler)."
    )
    parser.add_argument("mods_dir", help="Pfad zu <server>/mods/")
    parser.add_argument(
        "--expected", default=DEFAULT_EXPECTED,
        help="Name des erlaubten Ziel-Mod-Ordners (Default: %s)" % DEFAULT_EXPECTED,
    )
    parser.add_argument("--quiet", action="store_true",
                        help="Nur Fehler ausgeben (kein OK-Text).")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if not os.path.isdir(args.mods_dir):
        print("FEHLER: %s ist kein Verzeichnis." % args.mods_dir, file=sys.stderr)
        return EXIT_USAGE

    try:
        manifest_dirs = find_manifest_dirs(args.mods_dir)
    except OSError as exc:
        print("FEHLER: %s nicht lesbar: %s" % (args.mods_dir, exc), file=sys.stderr)
        return EXIT_USAGE

    strays = [name for name in manifest_dirs if name != args.expected]
    if strays:
        print(
            "FEHLER: In %s liegen neben '%s' weitere Ordner mit *.manifest: %s"
            % (args.mods_dir, args.expected, ", ".join(strays)),
            file=sys.stderr,
        )
        print(
            "Hinweis: Backups NIE in mods/ ablegen — ausserhalb (z. B. "
            "<server>-backups/) oder als .tar.gz ausserhalb von mods/.",
            file=sys.stderr,
        )
        return EXIT_VIOLATION

    if not args.quiet:
        if args.expected in manifest_dirs:
            print("OK: nur '%s' traegt eine *.manifest in %s." % (args.expected, args.mods_dir))
        else:
            print(
                "OK: kein Fremd-Ordner mit *.manifest in %s (Ziel-Mod '%s' fehlt)."
                % (args.mods_dir, args.expected)
            )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
