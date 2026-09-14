#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rbpack.py - Riftbreaker-Game-Packs lesen (zip64-sicher, nur Standardbibliothek).

Warum dieses Tool und nicht `unzip`?
Info-ZIPs `unzip` liest das Haupt-Pack `00_win_data.zip` (~7.7 GB) NICHT - es
findet das Central Directory nicht:

  $ unzip -l /home/momo/rb-game/extracted/base/packs/00_win_data.zip
  error [.../00_win_data.zip]:  start of central directory not found;
    zipfile corrupt.

Ursache ist zip64: das Central Directory des Packs hängt an einem
ZIP64-EOCD-Locator, den der Info-ZIP-Build auf dem Host nicht auflöst.
Pythons stdlib `zipfile` liest dasselbe Pack problemlos (67628 Member,
Central Directory in unter einer Sekunde). Dieses Tool ist damit der Ersatz
für `unzip -l` / `unzip -p` / `unzip -p ... | grep` auf den Game-Packs.

Datenquelle (Host lan/.149): /home/momo/rb-game/extracted/base/packs/*_data.zip

Aufruf:
  rbpack.py list                                   # alle *_data.zip aus --dir
  rbpack.py list scripts/resources/                # Substring-Filter
  rbpack.py list -i IRON.RESOURCE --pack 00_win_data.zip
  rbpack.py cat scripts/resources/iron.resource    # binär nach stdout
  rbpack.py grep 'id\\s+"steel"' --in scripts/resources/
  rbpack.py grep ironium --context 60 --max-hits 5

Exit-Codes:
  0 = ok
  1 = nichts gefunden (kein Member bzw. kein Regex-Treffer)
  2 = Aufruf-/IO-Fehler (Pack fehlt, kaputtes Zip, ungültige Regex)

Nur Standardbibliothek (Python 3.8+).
"""

import argparse
import glob
import os
import re
import sys
import zipfile

DEFAULT_PACKS_DIR = os.environ.get("RB_PACKS", "/home/momo/rb-game/extracted/base/packs")
DEFAULT_PACK_GLOB = "*_data.zip"
DEFAULT_MAX_HITS = 50
DEFAULT_CONTEXT = 40

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_USAGE = 2

# Kontext-Ausgabe: druckbare ASCII-Zeichen bleiben lesbar, alles andere escaped.
_ESCAPES = {0x09: "\\t", 0x0a: "\\n", 0x0d: "\\r"}


class RbpackError(Exception):
    """Aufruf-/IO-Fehler -> Exit 2 (statt Traceback)."""


def _open_pack(path):
    """ZipFile öffnen; Fehler als RbpackError melden."""
    try:
        return zipfile.ZipFile(path)
    except FileNotFoundError:
        raise RbpackError("Pack nicht gefunden: %s" % path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise RbpackError("Pack nicht lesbar (%s): %s" % (path, exc))


def _resolve_packs(patterns, base_dir):
    """--pack-Werte -> konkrete Pack-Pfade.

    Jeder Wert ist ein Pfad (existiert direkt), ein Name oder ein Glob relativ
    zu base_dir. Ohne --pack: alle *_data.zip aus base_dir, sortiert
    (00_win_data.zip zuerst).
    """
    if patterns:
        paths = []
        for pattern in patterns:
            if os.path.exists(pattern):
                paths.append(pattern)
                continue
            hits = sorted(glob.glob(os.path.join(base_dir, pattern)))
            if not hits:
                raise RbpackError("kein Pack für %r in %s" % (pattern, base_dir))
            paths.extend(hits)
    else:
        paths = sorted(glob.glob(os.path.join(base_dir, DEFAULT_PACK_GLOB)))
        if not paths:
            raise RbpackError("keine %s in %s" % (DEFAULT_PACK_GLOB, base_dir))

    unique = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def _render(raw):
    """Bytes einzeilig und verlustfrei darstellen (Text bleibt lesbar)."""
    out = []
    for byte in raw:
        if 32 <= byte < 127:
            out.append(chr(byte))
        elif byte in _ESCAPES:
            out.append(_ESCAPES[byte])
        else:
            out.append("\\x%02x" % byte)
    return "".join(out)


def _write_bytes(data):
    stream = getattr(sys.stdout, "buffer", None)
    if stream is None:
        raise RbpackError("stdout hat keinen Binär-Stream (buffer)")
    stream.write(data)
    stream.flush()


def cmd_list(args):
    packs = _resolve_packs(args.pack, args.dir)
    multi = len(packs) > 1
    needle = args.pattern
    if needle and args.ignore_case:
        needle = needle.lower()

    shown = 0
    total = 0
    for path in packs:
        label = os.path.basename(path)
        with _open_pack(path) as pack:
            infos = pack.infolist()
        for info in infos:
            total += 1
            name = info.filename
            if needle:
                haystack = name.lower() if args.ignore_case else name
                if needle not in haystack:
                    continue
            shown += 1
            if multi:
                print("%s\t%d\t%s" % (label, info.file_size, name))
            else:
                print("%d\t%s" % (info.file_size, name))

    print("rbpack: %d/%d Member in %d Pack(s)" % (shown, total, len(packs)), file=sys.stderr)
    return EXIT_OK if shown else EXIT_NOT_FOUND


def cmd_cat(args):
    packs = _resolve_packs(args.pack, args.dir)
    for path in packs:
        label = os.path.basename(path)
        with _open_pack(path) as pack:
            try:
                data = pack.read(args.member)
            except KeyError:
                continue
            except (zipfile.BadZipFile, OSError) as exc:
                raise RbpackError("%s:%s nicht lesbar: %s" % (label, args.member, exc))
        print("rbpack: %s aus %s (%d Bytes)" % (args.member, label, len(data)), file=sys.stderr)
        _write_bytes(data)
        return EXIT_OK

    print("rbpack: Member %r in keinem Pack gefunden" % args.member, file=sys.stderr)
    return EXIT_NOT_FOUND


def cmd_grep(args):
    if args.max_hits < 1:
        raise RbpackError("--max-hits muss >= 1 sein")
    if args.context < 0:
        raise RbpackError("--context darf nicht negativ sein")

    flags = re.IGNORECASE if args.ignore_case else 0
    try:
        rx = re.compile(args.regex.encode("utf-8"), flags)
    except re.error as exc:
        raise RbpackError("ungültige Regex %r: %s" % (args.regex, exc))

    packs = _resolve_packs(args.pack, args.dir)
    multi = len(packs) > 1
    hits = 0
    scanned = 0
    capped = False

    for path in packs:
        label = os.path.basename(path)
        with _open_pack(path) as pack:
            infos = sorted(pack.infolist(), key=lambda info: info.filename)
            for info in infos:
                if args.only and args.only not in info.filename:
                    continue
                try:
                    data = pack.read(info)
                except (zipfile.BadZipFile, OSError) as exc:
                    print("rbpack: %s übersprungen (%s)" % (info.filename, exc), file=sys.stderr)
                    continue
                scanned += 1
                for match in rx.finditer(data):
                    hits += 1
                    where = "%s:%s" % (label, info.filename) if multi else info.filename
                    window = data[max(0, match.start() - args.context):match.end() + args.context]
                    print("%s:%d: %s" % (where, match.start(), _render(window)))
                    if hits >= args.max_hits:
                        capped = True
                        break
                if capped:
                    break
        if capped:
            break

    if capped:
        print("rbpack: Hit-Cap %d erreicht - mit --max-hits erhöhen" % args.max_hits, file=sys.stderr)
    else:
        print("rbpack: %d Treffer in %d Member(n), %d Pack(s)" % (hits, scanned, len(packs)), file=sys.stderr)
    return EXIT_OK if hits else EXIT_NOT_FOUND


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--dir", default=DEFAULT_PACKS_DIR, metavar="DIR",
        help="Verzeichnis mit den *_data.zip Packs (Default: $RB_PACKS bzw. %s)" % DEFAULT_PACKS_DIR,
    )
    common.add_argument(
        "--pack", action="append", metavar="PACK",
        help="Pack als Pfad, Name oder Glob in --dir; mehrfach möglich (Default: alle %s in --dir)"
             % DEFAULT_PACK_GLOB,
    )

    parser = argparse.ArgumentParser(
        prog="rbpack.py",
        description="Riftbreaker-Game-Packs lesen (zip64-sicher, nur stdlib zipfile - NICHT unzip).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", parents=[common], help="Member auflisten (optional Substring-Filter)")
    p_list.add_argument("pattern", nargs="?", help="Substring, der im Member-Namen vorkommen muss")
    p_list.add_argument("-i", "--ignore-case", action="store_true", help="Filter case-insensitiv")
    p_list.set_defaults(func=cmd_list)

    p_cat = sub.add_parser("cat", parents=[common], help="Member binär nach stdout schreiben")
    p_cat.add_argument("member", help="Member-Name (exakt, wie von list ausgegeben)")
    p_cat.set_defaults(func=cmd_cat)

    p_grep = sub.add_parser("grep", parents=[common], help="Regex über Member-Inhalte (bytes-safe)")
    p_grep.add_argument("regex", help="Python-Regex (wird als UTF-8-Bytes auf die Inhalte angewandt)")
    p_grep.add_argument("--in", dest="only", metavar="SUBSTR", help="nur Member mit SUBSTR im Namen durchsuchen")
    p_grep.add_argument("--max-hits", type=int, default=DEFAULT_MAX_HITS, metavar="N",
                        help="max. Treffer (Default: %d)" % DEFAULT_MAX_HITS)
    p_grep.add_argument("--context", type=int, default=DEFAULT_CONTEXT, metavar="N",
                        help="Kontext-Bytes links und rechts (Default: %d)" % DEFAULT_CONTEXT)
    p_grep.add_argument("-i", "--ignore-case", action="store_true", help="Regex case-insensitiv")
    p_grep.set_defaults(func=cmd_grep)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RbpackError as exc:
        print("rbpack: %s" % exc, file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # Pager/head hat die Pipe frueh geschlossen (z. B. `rbpack.py list | head`).
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        sys.exit(EXIT_OK)
