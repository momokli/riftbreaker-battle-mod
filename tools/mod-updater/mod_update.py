#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mod_update.py — lokale RBBattle-Mod-Installation installieren/aktualisieren (#120).

Dev-/Tester-Werkzeug: findet die lokale Riftbreaker-Mod-Installation, holt das
aktuelle Mod-Zip (deployter Stand oder `--zip`/`--url`), legt optional ein Backup
der alten Version an, ersetzt die Mod-Dateien und zeigt installierte vs.
aktuelle Version.

Nicht-Ziel (siehe #120): kein Auto-Update im Spiel, kein Mod-Manager-Framework.

Cross-platform (Windows/macOS/Linux) und nur Standardbibliothek (Python 3.8+).

Aufruf (Beispiele):
  python3 tools/mod-updater/mod_update.py status
  python3 tools/mod-updater/mod_update.py status --check   # Exit 2, wenn Update da
  python3 tools/mod-updater/mod_update.py update
  python3 tools/mod-updater/mod_update.py update --mods-dir /pfad/zu/mods
  python3 tools/mod-updater/mod_update.py update --zip /tmp/rbbattle.zip --dry-run

Exit-Codes:
  0 = ok (Status ermittelt bzw. Update installiert)
  1 = Fehler (keine Installation gefunden, Download/Installation fehlgeschlagen)
  2 = nur `status --check`: Update verfuegbar (installiert != aktuell)
"""

import argparse
import os
import platform
import re
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

MOD_NAME = "rbbattle"
GAME_DIR_NAMES = ("Riftbreaker", "The Riftbreaker")
# Default-Download: der deployte Stand (Caddy, Issue #209). GitHub-Releases
# sind reine Tags-Marker ohne Artefakte; `--url` bleibt der Override.
DEFAULT_RELEASE_URL = "https://rift.projectmellon.de/mods/rbbattle.zip"
MANIFEST_VERSION_RE = re.compile(r'^\s*version\s+"([^"]+)"', re.MULTILINE)
VDF_PATH_RE = re.compile(r'"path"\s*"([^"]+)"')
LOG_PREFIX = "[mod_update]"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UPDATE_AVAILABLE = 2


def log(msg):
    """Eine Statuszeile mit festem Praefix ausgeben."""
    print("%s %s" % (LOG_PREFIX, msg))


# ---------------------------------------------------------------------------
# Versionen & Manifest
# ---------------------------------------------------------------------------


def parse_manifest_version(text):
    """Version aus dem Inhalt eines Mod-Manifests lesen (Feld `version "x"`)."""
    match = MANIFEST_VERSION_RE.search(text or "")
    return match.group(1) if match else None


def find_manifest(mod_dir):
    """Erstes `*.manifest` in einem Ordner (sortiert) oder None."""
    mod_dir = Path(mod_dir)
    if not mod_dir.is_dir():
        return None
    manifests = sorted(mod_dir.glob("*.manifest"))
    return manifests[0] if manifests else None


def manifest_version_from_dir(mod_dir):
    """Installierte Version aus `<mod_dir>/*.manifest` lesen oder None."""
    manifest = find_manifest(mod_dir)
    if manifest is None:
        return None
    try:
        return parse_manifest_version(manifest.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def manifest_version_from_zip(zip_path):
    """Version aus dem Manifest an der Zip-Wurzel lesen oder None."""
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".manifest") and "/" not in n.rstrip("/")]
        for name in sorted(names):
            text = zf.read(name).decode("utf-8", errors="replace")
            version = parse_manifest_version(text)
            if version:
                return version
    return None


def version_key(version):
    """Numerische Bestandteile einer Version als Tupel (fuer den Vergleich)."""
    return tuple(int(part) for part in re.findall(r"\d+", version or ""))


def compare_versions(left, right):
    """-1/0/1 fuer left < / == / > right (nur numerische Teile)."""
    a = version_key(left)
    b = version_key(right)
    width = max(len(a), len(b))
    a = a + (0,) * (width - len(a))
    b = b + (0,) * (width - len(b))
    return (a > b) - (a < b)


def update_available(installed, latest):
    """True, wenn `latest` neu/anders ist oder noch nichts installiert ist."""
    if latest is None:
        return False
    if installed is None:
        return True
    return compare_versions(installed, latest) != 0


# ---------------------------------------------------------------------------
# Pfad-Erkennung (Steam-Bibliotheken, Windows/macOS/Linux)
# ---------------------------------------------------------------------------


def steam_roots(system=None, home=None, environ=None):
    """Kandidaten fuer Steam-Installationsordner des jeweiligen Systems."""
    system = system or platform.system()
    home = Path(home) if home else Path.home()
    environ = os.environ if environ is None else environ
    roots = []
    if system == "Windows":
        for var in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432"):
            base = environ.get(var)
            if base:
                roots.append(Path(base) / "Steam")
        roots.append(Path("C:/Program Files (x86)/Steam"))
        roots.append(Path("C:/Program Files/Steam"))
    elif system == "Darwin":
        roots.append(home / "Library" / "Application Support" / "Steam")
    else:
        roots.append(home / ".steam" / "steam")
        roots.append(home / ".steam" / "root")
        roots.append(home / ".local" / "share" / "Steam")
        roots.append(home / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam")
    unique = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


def library_paths_from_vdf(text):
    """Alle `"path" "…"`-Eintraege aus einer libraryfolders.vdf ziehen."""
    paths = []
    for raw in VDF_PATH_RE.findall(text or ""):
        # In VDF stehen Windows-Pfade doppelt escaped ("D:\\SteamLibrary").
        paths.append(Path(raw.replace("\\\\", "\\")))
    return paths


def candidate_library_roots(roots=None):
    """Steam-Wurzelordner plus die Bibliotheken aus libraryfolders.vdf."""
    roots = list(roots if roots is not None else steam_roots())
    extra = []
    for root in roots:
        vdf = Path(root) / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            try:
                extra.extend(library_paths_from_vdf(vdf.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    unique = []
    for lib in roots + extra:
        if lib not in unique:
            unique.append(lib)
    return unique


def candidate_mods_dirs(roots=None):
    """Moegliche `<game>/mods`-Ordner in Prioritaetsreihenfolge."""
    dirs = []
    for lib in candidate_library_roots(roots):
        for game in GAME_DIR_NAMES:
            mods = lib / "steamapps" / "common" / game / "mods"
            if mods not in dirs:
                dirs.append(mods)
    return dirs


def detect_mods_dir(explicit=None, env=None, roots=None):
    """Mods-Ordner bestimmen: explizit / RBM_MODS_DIR / Steam-Bibliothek.

    Liefert (mods_dir, detected) — bei *keinem* Treffer (None, False).
    """
    env = os.environ if env is None else env
    if explicit:
        return Path(explicit).expanduser(), True
    from_env = env.get("RBM_MODS_DIR")
    if from_env:
        return Path(from_env).expanduser(), True
    candidates = candidate_mods_dirs(roots)
    for mods in candidates:
        if mods.is_dir() or (mods / MOD_NAME).is_dir():
            return mods, True
    # Spiel gefunden, `mods/` fehlt noch -> bei der Installation anlegen.
    for mods in candidates:
        if mods.parent.is_dir():
            return mods, False
    return None, False


def find_installed_version(mods_dir):
    """Installierte Version in `<mods_dir>/rbbattle` oder None."""
    if not mods_dir:
        return None
    return manifest_version_from_dir(Path(mods_dir) / MOD_NAME)


# ---------------------------------------------------------------------------
# Download, Entpacken, Installation
# ---------------------------------------------------------------------------


def download_zip(url, dest, timeout=60):
    """Mod-Zip laden; liefert die finale URL (nach Redirects)."""
    request = urllib.request.Request(url, headers={"User-Agent": "rbbattle-mod-updater"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        data = response.read()
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return final_url


def extract_zip(zip_path, dest):
    """Zip nach `dest` entpacken und Pfad-Ausbrueche (Zip-Slip) ablehnen."""
    dest = Path(dest).resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            name = member.filename
            member_path = Path(name)
            target = (dest / member_path).resolve()
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError("unsicherer Pfad im Zip: %s" % name)
            if target != dest and dest not in target.parents:
                raise ValueError("unsicherer Pfad im Zip: %s" % name)
        zf.extractall(str(dest))


def install(zip_path, mods_dir, backup=True, backup_dir=None, force=False, dry_run=False):
    """Mod-Zip installieren/ersetzen; Ergebnis als dict (status: siehe unten).

    Status: "up-to-date" | "installed" | "updated" | "dry-run" | "error".
    """
    mods_dir = Path(mods_dir)
    target = mods_dir / MOD_NAME
    result = {
        "status": "error",
        "mod_dir": str(target),
        "installed": find_installed_version(mods_dir),
        "latest": None,
        "backup": None,
        "actions": [],
        "message": "",
    }
    try:
        latest = manifest_version_from_zip(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        result["message"] = "Zip nicht lesbar: %s" % exc
        return result
    result["latest"] = latest
    if not latest:
        result["message"] = "Zip enthaelt kein lesbares *.manifest mit version-Feld."
        return result

    target_exists = target.is_dir()
    if (target_exists and not force and result["installed"] is not None
            and compare_versions(result["installed"], latest) == 0):
        result["status"] = "up-to-date"
        return result

    if target_exists:
        if backup:
            result["actions"].append("backup")
        result["actions"].append("replace")
    else:
        result["actions"].append("install")
    if dry_run:
        result["status"] = "dry-run"
        return result

    staging = Path(tempfile.mkdtemp(prefix="rbbattle-mod-"))
    backup_path = None
    try:
        extract_zip(zip_path, staging)
        if not manifest_version_from_dir(staging):
            raise RuntimeError("extrahiertes Zip ohne lesbares *.manifest")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if backup:
                bdir = Path(backup_dir) if backup_dir else target.parent / (MOD_NAME + "-backups")
                bdir.mkdir(parents=True, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
                backup_path = bdir / ("%s-%s-%s" % (MOD_NAME, result["installed"] or "unbekannt", stamp))
                shutil.copytree(str(target), str(backup_path))
                result["backup"] = str(backup_path)
            shutil.rmtree(str(target))
        shutil.move(str(staging), str(target))
    except Exception as exc:  # noqa: BLE001 - Fehler an den Aufrufer melden
        if backup_path and not target.exists():
            try:
                shutil.copytree(str(backup_path), str(target))
                result["message"] = "Rollback aus Backup nach Fehler: %s" % exc
            except OSError as restore_exc:
                result["message"] = "Fehler: %s (Rollback fehlgeschlagen: %s)" % (exc, restore_exc)
        else:
            result["message"] = "Fehler: %s" % exc
        return result
    finally:
        shutil.rmtree(str(staging), ignore_errors=True)

    result["installed"] = find_installed_version(mods_dir)
    if result["installed"] and compare_versions(result["installed"], latest) == 0:
        result["status"] = "updated" if target_exists else "installed"
    else:
        result["status"] = "error"
        result["message"] = "Verifikation fehlgeschlagen: installiert=%s erwartet=%s" % (
            result["installed"],
            latest,
        )
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mod_update.py",
        description="RBBattle-Mod lokal installieren/aktualisieren (#120).",
    )
    parser.add_argument("command", nargs="?", choices=["status", "update"], default="status",
                        help="status (Default): Versionen zeigen — update: installieren/ersetzen")
    parser.add_argument("--mods-dir", help="Mods-Ordner (sonst Auto-Erkennung / RBM_MODS_DIR)")
    parser.add_argument("--url", default=DEFAULT_RELEASE_URL, help="Download-URL des Mod-Zips")
    parser.add_argument("--zip", dest="zip_path", help="lokales Mod-Zip statt Download")
    parser.add_argument("--backup-dir", help="Zielordner fuer das Backup der alten Version")
    parser.add_argument("--no-backup", action="store_true", help="kein Backup der alten Version")
    parser.add_argument("--force", action="store_true", help="auch bei gleicher Version neu installieren")
    parser.add_argument("--dry-run", action="store_true", help="nur zeigen, was passieren wuerde")
    parser.add_argument("--check", action="store_true", help="status: Exit 2, wenn Update verfuegbar")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    mods_dir, detected = detect_mods_dir(args.mods_dir)
    if mods_dir is None:
        log("FEHLER: keine Riftbreaker-Mod-Installation gefunden.")
        log("        Bitte --mods-dir <…/Riftbreaker/mods> angeben oder RBM_MODS_DIR setzen.")
        return EXIT_ERROR

    installed = find_installed_version(mods_dir)
    log("Mods-Ordner: %s%s" % (mods_dir, "" if detected else " (neu)"))

    tmp_dir = None
    zip_path = args.zip_path
    source = args.zip_path
    try:
        if zip_path is None:
            tmp_dir = tempfile.mkdtemp(prefix="rbbattle-dl-")
            zip_path = os.path.join(tmp_dir, "rbbattle.zip")
            log("Lade %s" % args.url)
            source = download_zip(args.url, zip_path)
        try:
            latest = manifest_version_from_zip(zip_path)
        except (OSError, zipfile.BadZipFile) as exc:
            log("FEHLER: Zip nicht lesbar: %s" % exc)
            return EXIT_ERROR
        if not latest:
            log("FEHLER: Zip enthaelt kein lesbares *.manifest mit version-Feld.")
            return EXIT_ERROR

        log("Installiert: v%s" % (installed if installed else "— (nichts gefunden)"))
        log("Aktuell:     v%s  (%s)" % (latest, source))
        needed = update_available(installed, latest)

        if args.command == "status":
            if needed:
                log("Status:      Update verfuegbar (%s -> %s)" % (installed or "keine", latest))
            else:
                log("Status:      aktuell")
            return EXIT_UPDATE_AVAILABLE if (args.check and needed) else EXIT_OK

        if not needed and not args.force:
            log("Status:      bereits aktuell — nichts zu tun (--force erzwingt).")
            return EXIT_OK

        result = install(
            zip_path,
            mods_dir,
            backup=not args.no_backup,
            backup_dir=args.backup_dir,
            force=args.force,
            dry_run=args.dry_run,
        )
        if result["status"] == "dry-run":
            log("Dry-Run:     Aktionen %s -> %s" % (",".join(result["actions"]), result["mod_dir"]))
            return EXIT_OK
        if result["status"] == "error":
            log("FEHLER: %s" % result["message"])
            return EXIT_ERROR
        if result["backup"]:
            log("Backup:      %s" % result["backup"])
        log("Status:      %s -> v%s" % (result["status"], result["installed"]))
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 - CLI-Rahmen: Fehler knapp melden
        log("FEHLER: %s" % exc)
        return EXIT_ERROR
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
