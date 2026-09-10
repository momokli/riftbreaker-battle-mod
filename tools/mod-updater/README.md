# tools/mod-updater — Mod lokal mit einem Befehl aktualisieren (Issue #120)

Dev-/Tester-Werkzeug: bringt die **lokale** Riftbreaker-Mod-Installation in
unter einer Minute von der alten auf die neueste `rbbattle`-Version — ohne
manuelles Entpacken und Suchen des Mods-Ordners.

Eigenständiges CLI (Cross-platform Windows/macOS/Linux, nur Python-Standard­bibliothek,
keine Installation noetig). Bewusst **kein** Auto-Update im Spiel und **kein**
Mod-Manager-Framework (Nicht-Ziel aus #120).

## Was es tut

1. **Findet** den Mods-Ordner: `--mods-dir` → `RBM_MODS_DIR` → automatische
   Steam-Erkennung (Windows/macOS/Linux, inkl. zusätzlicher Bibliotheken aus
   `steamapps/libraryfolders.vdf`).
2. **Holt** das aktuelle Mod-Zip: per Default
   `releases/latest/download/rbbattle.zip`, alternativ `--url` oder ein lokales
   `--zip` (z. B. schon heruntergeladene Datei).
3. **Vergleicht** installierte vs. aktuelle Version — beide werden aus dem
   Mod-Manifest (`<GUID>.manifest`, Feld `version`) gelesen, der einzigen Quelle
   der Wahrheit (siehe `scripts/mod_version.sh`, Issue #119).
4. **Installiert/ersetzt** die Mod-Dateien in `<mods>/rbbattle/` und legt
   vorher optional ein **Backup** unter `<mods>/rbbattle-backups/` an.

## Aufruf

```bash
# 1) Status: installierte vs. aktuelle Version (ändert nichts)
python3 tools/mod-updater/mod_update.py status

# 2) Update installieren (Backup der alten Version inklusive)
python3 tools/mod-updater/mod_update.py update

# Mods-Ordner explizit (sonst Auto-Erkennung / RBM_MODS_DIR)
python3 tools/mod-updater/mod_update.py update --mods-dir "D:/SteamLibrary/steamapps/common/Riftbreaker/mods"

# Aus lokalem Zip, ohne Download — erst zeigen, was passiert
python3 tools/mod-updater/mod_update.py update --zip ~/Downloads/rbbattle.zip --dry-run
```

Optionen: `--backup-dir <dir>`, `--no-backup`, `--force` (auch bei gleicher
Version neu installieren), `--dry-run`, `--check` (Status-Exit 2, wenn Update
verfügbar), `--url <url>`, `--zip <pfad>`.

Exit-Codes: `0` = ok / aktuell, `1` = Fehler, `2` = `status --check` mit Update.

Der Mods-Ordner ist der Ordner `mods` innerhalb der Spielinstallation
(`<SteamLibrary>/steamapps/common/Riftbreaker/mods`); die Mod selbst landet als
`rbbattle/` darin — genau die Struktur, die `mod/README.md` → „Installation“
beschreibt.

## Tests

```bash
cd tools/mod-updater
python3 -m unittest test_mod_update -v
```

Deterministisch und offline: Pfad-Erkennung gegen synthetische Verzeichnisse,
Installation gegen lokale Zips (frisch / Update + Backup / Skip / `--force` /
`--dry-run`), Zip-Slip-Schutz sowie die CLI-Exit-Codes. Läuft in CI (`ci.yml`,
Job `test`); `lint.yml` (`compileall` + `ruff`) deckt `tools/` mit ab.

## Download

Das Skript ist eine einzelne Datei und wird von der CI als Release-Asset
(`mod_update.py`) mitveröffentlicht — damit ist das Tool ohne Repo-Checkout
herunterladbar.
