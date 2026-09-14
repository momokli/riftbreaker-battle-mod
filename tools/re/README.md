# tools/re — Helfer für die RE-Arbeit an den Spiel-Daten

Kleine, eigenständige Helfer fürs Reverse Engineering der Riftbreaker-Daten.
Betriebs-Hinweis zu den Game-Packs:
[`docs/research/resource-hash-map.md`](../../docs/research/resource-hash-map.md).

## rbpack.py — Game-Packs lesen (zip64-sicher)

**Für Packs > 4 GB `rbpack.py` benutzen, nicht `unzip`.** Info-ZIPs `unzip`
findet im Haupt-Pack `00_win_data.zip` (~7.7 GB, zip64) das Central Directory
nicht:

```console
$ unzip -l /home/momo/rb-game/extracted/base/packs/00_win_data.zip
error [.../00_win_data.zip]:  start of central directory not found;
  zipfile corrupt.
```

Pythons stdlib `zipfile` liest dasselbe Pack problemlos (67628 Member, Central
Directory in < 1 s) — `rbpack.py` benutzt genau die, nie `unzip`.

## Aufruf

```bash
# Member auflisten (alle *_data.zip aus --dir, oder --pack für eines)
python3 tools/re/rbpack.py list
python3 tools/re/rbpack.py list scripts/resources/
python3 tools/re/rbpack.py list -i IRON.RESOURCE --pack 00_win_data.zip

# Member binär nach stdout (wie `unzip -p`)
python3 tools/re/rbpack.py cat scripts/resources/iron.resource

# Regex bytes-sicher über Member-Inhalte (wie `unzip -p ... | grep`)
python3 tools/re/rbpack.py grep 'id\s+"steel"' --in scripts/resources/ --max-hits 5
```

`--dir` (Default: `$RB_PACKS` bzw. `/home/momo/rb-game/extracted/base/packs`)
nennt das Pack-Verzeichnis; `--pack` wählt Pfad, Name oder Glob darin (mehrfach
möglich, Default: alle `*_data.zip`). Ohne `--pack` durchsuchen `cat`/`grep` alle
Packs und melden den Fund mit Pack-Präfix.

Exit-Codes: `0` = ok, `1` = nichts gefunden, `2` = Aufruf-/IO-Fehler.

## Tests

```bash
cd tools/re
python3 -m unittest test_rbpack -v
```

Deterministisch und offline: synthetische Zips im tmpdir (kein Game-Daten-Zugriff).
Läuft in CI (`ci.yml`, Job `test`); `lint.yml` (`compileall` + `ruff`) deckt das
Verzeichnis mit ab.
