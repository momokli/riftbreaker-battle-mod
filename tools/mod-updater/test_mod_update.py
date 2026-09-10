#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_mod_update.py — Unit-Tests fuer tool/mod-updater (Issue #120).

Deterministisch, ohne Netz und ohne echte Steam-Installation: Pfad-Erkennung
laeuft gegen synthetische Verzeichnisse, Downloads werden durch ein lokales
Zip (`--zip`) ersetzt. Abgedeckt:

  - Manifest-Version parsen (Text/Zip), Versionen vergleichen
  - libraryfolders.vdf parsen (inkl. Windows-Escaping) + Kandidaten-Reihenfolge
  - detect_mods_dir: explizit / RBM_MODS_DIR / gefundene Bibliothek
  - extract_zip: Zip-Slip wird abgelehnt
  - install: frisch, Update + Backup, Skip bei gleicher Version, --force, --dry-run
  - main: status/--check Exit-Codes, update mit lokalem Zip

Nur Standardbibliothek (unittest, tempfile, zipfile, contextlib).

Aufruf: python3 -m unittest test_mod_update -v   (aus diesem Verzeichnis)
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mod_update  # noqa: E402

MANIFEST_NAME = "{96745BE8-78FD-4C30-9718-D57AA40B9C09}.manifest"


def write_manifest(path, version):
    path.write_text('WorkspaceManifest\n{\n\tversion "%s"\n}\n' % version, encoding="utf-8")


def make_mod_zip(path, version):
    """Minimales Mod-Zip mit Manifest an der Wurzel (+ lua-Datei)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, 'WorkspaceManifest\n{\n\tversion "%s"\n}\n' % version)
        zf.writestr("lua/rbbattle_autoexec.lua", "-- rbbattle %s\n" % version)
    return path


class VersionTests(unittest.TestCase):
    def test_parse_manifest_version(self):
        text = 'WorkspaceManifest\n{\n\tgame_version "EXE: 1186"\n\tversion "0.29.0"\n}\n'
        self.assertEqual(mod_update.parse_manifest_version(text), "0.29.0")

    def test_parse_manifest_version_missing(self):
        self.assertIsNone(mod_update.parse_manifest_version('version_nicht "x"'))
        self.assertIsNone(mod_update.parse_manifest_version(""))

    def test_compare_versions(self):
        self.assertEqual(mod_update.compare_versions("0.28.0", "0.29.0"), -1)
        self.assertEqual(mod_update.compare_versions("0.29.0", "0.28.9"), 1)
        self.assertEqual(mod_update.compare_versions("1.2", "1.2.0"), 0)
        self.assertEqual(mod_update.compare_versions("v0.3.0", "0.3.0"), 0)

    def test_update_available(self):
        self.assertFalse(mod_update.update_available("0.29.0", "0.29.0"))
        self.assertTrue(mod_update.update_available("0.28.0", "0.29.0"))
        self.assertTrue(mod_update.update_available(None, "0.29.0"))
        self.assertFalse(mod_update.update_available("0.29.0", None))


class ManifestZipTests(unittest.TestCase):
    def test_manifest_version_from_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_mod_zip(Path(tmp) / "rbbattle.zip", "0.29.0")
            self.assertEqual(mod_update.manifest_version_from_zip(zip_path), "0.29.0")

    def test_manifest_version_from_zip_without_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "leer.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("lua/x.lua", "-- nichts")
            self.assertIsNone(mod_update.manifest_version_from_zip(zip_path))

    def test_manifest_version_from_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "rbbattle"
            mod_dir.mkdir()
            write_manifest(mod_dir / MANIFEST_NAME, "0.30.1")
            self.assertEqual(mod_update.manifest_version_from_dir(mod_dir), "0.30.1")
        self.assertIsNone(mod_update.manifest_version_from_dir(Path(tmp) / "gibtsnicht"))


class PathDetectionTests(unittest.TestCase):
    def test_library_paths_from_vdf_windows_escaping(self):
        vdf = (
            '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t"D:\\\\SteamLibrary"\n\t}\n'
            '\t"1"\n\t{\n\t\t"path"\t"/mnt/games/Steam"\n\t}\n}\n'
        )
        paths = mod_update.library_paths_from_vdf(vdf)
        self.assertEqual(paths, [Path("D:\\SteamLibrary"), Path("/mnt/games/Steam")])

    def test_candidate_mods_dirs_include_vdf_libraries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Steam"
            (root / "steamapps").mkdir(parents=True)
            (root / "steamapps" / "libraryfolders.vdf").write_text(
                '"libraryfolders"\n{\n\t"1"\n\t{\n\t\t"path"\t"%s"\n\t}\n}\n'
                % str(Path(tmp) / "ExtraLib").replace("\\", "\\\\"),
                encoding="utf-8",
            )
            dirs = mod_update.candidate_mods_dirs([root])
            self.assertIn(root / "steamapps" / "common" / "Riftbreaker" / "mods", dirs)
            self.assertIn(Path(tmp) / "ExtraLib" / "steamapps" / "common" / "Riftbreaker" / "mods", dirs)

    def test_detect_mods_dir_finds_installed_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Steam"
            mods = root / "steamapps" / "common" / "Riftbreaker" / "mods"
            (mods / "rbbattle").mkdir(parents=True)
            found, detected = mod_update.detect_mods_dir(env={}, roots=[root])
            self.assertEqual(found, mods)
            self.assertTrue(detected)

    def test_detect_mods_dir_env_override(self):
        target = Path(tempfile.gettempdir()) / "rbm-mods-env"
        found, detected = mod_update.detect_mods_dir(env={"RBM_MODS_DIR": str(target)})
        self.assertEqual(found, target)
        self.assertTrue(detected)

    def test_detect_mods_dir_explicit_wins(self):
        found, detected = mod_update.detect_mods_dir(explicit="/tmp/rbm-explicit", env={"RBM_MODS_DIR": "/x"})
        self.assertEqual(found, Path("/tmp/rbm-explicit"))
        self.assertTrue(detected)

    def test_detect_mods_dir_none_when_nothing_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            found, detected = mod_update.detect_mods_dir(env={}, roots=[Path(tmp) / "leer"])
            self.assertIsNone(found)
            self.assertFalse(detected)

    def test_steam_roots_per_os(self):
        home = Path("/home/tester")
        self.assertIn(home / "Library" / "Application Support" / "Steam",
                      mod_update.steam_roots(system="Darwin", home=home))
        self.assertIn(home / ".steam" / "steam",
                      mod_update.steam_roots(system="Linux", home=home))
        win = mod_update.steam_roots(
            system="Windows", home=Path("C:/Users/t"), environ={"ProgramFiles(x86)": "C:\\PF86"}
        )
        self.assertIn(Path("C:/Program Files (x86)/Steam"), win)


class ExtractTests(unittest.TestCase):
    def test_extract_zip_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_mod_zip(Path(tmp) / "m.zip", "0.29.0")
            dest = Path(tmp) / "out"
            mod_update.extract_zip(zip_path, dest)
            self.assertTrue((dest / MANIFEST_NAME).is_file())
            self.assertTrue((dest / "lua" / "rbbattle_autoexec.lua").is_file())

    def test_extract_zip_rejects_zip_slip(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "boese.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("../ausserhalb.txt", "x")
            with self.assertRaises(ValueError):
                mod_update.extract_zip(zip_path, Path(tmp) / "out")


class InstallTests(unittest.TestCase):
    def test_install_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp) / "mods"
            mods.mkdir()
            zip_path = make_mod_zip(Path(tmp) / "rbbattle.zip", "0.29.0")
            result = mod_update.install(zip_path, mods)
            self.assertEqual(result["status"], "installed")
            self.assertEqual(result["installed"], "0.29.0")
            self.assertEqual(mod_update.find_installed_version(mods), "0.29.0")
            self.assertIsNone(result["backup"])

    def test_install_update_creates_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp) / "mods"
            (mods / "rbbattle").mkdir(parents=True)
            write_manifest(mods / "rbbattle" / MANIFEST_NAME, "0.28.0")
            zip_path = make_mod_zip(Path(tmp) / "rbbattle.zip", "0.29.0")

            result = mod_update.install(zip_path, mods)
            self.assertEqual(result["status"], "updated")
            self.assertEqual(result["installed"], "0.29.0")
            self.assertIsNotNone(result["backup"])
            backup = Path(result["backup"])
            self.assertTrue(backup.is_dir())
            self.assertEqual(mod_update.manifest_version_from_dir(backup), "0.28.0")
            self.assertEqual(mod_update.find_installed_version(mods), "0.29.0")

    def test_install_skips_when_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp) / "mods"
            (mods / "rbbattle").mkdir(parents=True)
            write_manifest(mods / "rbbattle" / MANIFEST_NAME, "0.29.0")
            zip_path = make_mod_zip(Path(tmp) / "rbbattle.zip", "0.29.0")
            result = mod_update.install(zip_path, mods)
            self.assertEqual(result["status"], "up-to-date")
            self.assertIsNone(result["backup"])

    def test_install_force_reinstalls(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp) / "mods"
            (mods / "rbbattle").mkdir(parents=True)
            write_manifest(mods / "rbbattle" / MANIFEST_NAME, "0.29.0")
            zip_path = make_mod_zip(Path(tmp) / "rbbattle.zip", "0.29.0")
            result = mod_update.install(zip_path, mods, force=True)
            self.assertEqual(result["status"], "updated")
            self.assertIsNotNone(result["backup"])

    def test_install_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp) / "mods"
            (mods / "rbbattle").mkdir(parents=True)
            write_manifest(mods / "rbbattle" / MANIFEST_NAME, "0.28.0")
            zip_path = make_mod_zip(Path(tmp) / "rbbattle.zip", "0.29.0")
            result = mod_update.install(zip_path, mods, dry_run=True)
            self.assertEqual(result["status"], "dry-run")
            self.assertEqual(result["actions"], ["backup", "replace"])
            self.assertEqual(mod_update.find_installed_version(mods), "0.28.0")

    def test_install_rejects_zip_without_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp) / "mods"
            mods.mkdir()
            zip_path = Path(tmp) / "leer.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("lua/x.lua", "-- nichts")
            result = mod_update.install(zip_path, mods)
            self.assertEqual(result["status"], "error")
            self.assertIn("manifest", result["message"])


class CliTests(unittest.TestCase):
    def _run(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = mod_update.main(argv)
        return code, buf.getvalue()

    def test_status_check_outdated_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp) / "mods"
            (mods / "rbbattle").mkdir(parents=True)
            write_manifest(mods / "rbbattle" / MANIFEST_NAME, "0.28.0")
            zip_path = make_mod_zip(Path(tmp) / "rbbattle.zip", "0.29.0")
            code, out = self._run(["status", "--mods-dir", str(mods), "--zip", str(zip_path), "--check"])
            self.assertEqual(code, mod_update.EXIT_UPDATE_AVAILABLE)
            self.assertIn("Update verfuegbar", out)

    def test_status_check_current_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp) / "mods"
            (mods / "rbbattle").mkdir(parents=True)
            write_manifest(mods / "rbbattle" / MANIFEST_NAME, "0.29.0")
            zip_path = make_mod_zip(Path(tmp) / "rbbattle.zip", "0.29.0")
            code, out = self._run(["status", "--mods-dir", str(mods), "--zip", str(zip_path), "--check"])
            self.assertEqual(code, mod_update.EXIT_OK)
            self.assertIn("aktuell", out)

    def test_update_with_local_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp) / "mods"
            (mods / "rbbattle").mkdir(parents=True)
            write_manifest(mods / "rbbattle" / MANIFEST_NAME, "0.28.0")
            zip_path = make_mod_zip(Path(tmp) / "rbbattle.zip", "0.29.0")
            code, out = self._run(["update", "--mods-dir", str(mods), "--zip", str(zip_path)])
            self.assertEqual(code, mod_update.EXIT_OK)
            self.assertIn("Backup:", out)
            self.assertEqual(mod_update.find_installed_version(mods), "0.29.0")

    def test_update_explicit_dir_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_mod_zip(Path(tmp) / "rbbattle.zip", "0.29.0")
            mods = Path(tmp) / "neu" / "mods"
            code, out = self._run(["update", "--zip", str(zip_path), "--mods-dir", str(mods)])
            # explizit gesetzter Pfad wird als Ziel akzeptiert und angelegt
            self.assertEqual(code, mod_update.EXIT_OK)
            self.assertEqual(mod_update.find_installed_version(mods), "0.29.0")
            self.assertIn("installed", out)

    def test_update_detects_nothing_without_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_mod_zip(Path(tmp) / "rbbattle.zip", "0.29.0")
            saved = mod_update.detect_mods_dir
            mod_update.detect_mods_dir = lambda *a, **k: (None, False)
            try:
                code, out = self._run(["update", "--zip", str(zip_path)])
            finally:
                mod_update.detect_mods_dir = saved
            self.assertEqual(code, mod_update.EXIT_ERROR)
            self.assertIn("keine Riftbreaker-Mod-Installation", out)


if __name__ == "__main__":
    unittest.main()
