#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_check_mods_dir.py - Unit-Tests fuer den mods/-Guard (Issue #212).

Deterministisch und offline: synthetische mods/-Verzeichnisse pruefen, dass
mehr als ein manifest-tragender Ordner (bzw. ein Fremd-Ordner statt des
Ziel-Mods) als Fehler erkannt wird.

Aufruf: python3 -m unittest test_check_mods_dir -v   (aus diesem Verzeichnis)
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_mods_dir  # noqa: E402

MANIFEST = "{96745BE8-78FD-4C30-9718-D57AA40B9C09}.manifest"


def run(args):
    """main() mit unterdrueckter Ausgabe (haelt die Testausgabe sauber)."""
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        return check_mods_dir.main(args)


class ModsDirTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="modsguard-")
        self.mods = os.path.join(self.root, "mods")
        os.makedirs(self.mods)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def add_mod(self, name, manifest=True, nested=False):
        path = os.path.join(self.mods, name)
        os.makedirs(path)
        if manifest:
            if nested:
                sub = os.path.join(path, "lua")
                os.makedirs(sub)
                open(os.path.join(sub, MANIFEST), "w").close()
            else:
                open(os.path.join(path, MANIFEST), "w").close()
        else:
            open(os.path.join(path, "README.md"), "w").close()
        return path

    # --- Kernfall: genau der Ziel-Mod -> ok --------------------------------
    def test_only_expected_mod_is_ok(self):
        self.add_mod("rbbattle")
        self.assertEqual(run([self.mods]), check_mods_dir.EXIT_OK)

    def test_dirs_without_manifest_are_ignored(self):
        self.add_mod("rbbattle")
        self.add_mod("notes", manifest=False)
        self.add_mod("screenshots", manifest=False)
        self.assertEqual(run([self.mods]), check_mods_dir.EXIT_OK)

    def test_nested_manifest_counts(self):
        self.add_mod("rbbattle", nested=True)
        self.assertEqual(run([self.mods]), check_mods_dir.EXIT_OK)

    def test_empty_mods_dir_is_ok(self):
        self.assertEqual(run([self.mods]), check_mods_dir.EXIT_OK)

    # --- Der eigentliche Bug: Backup-Ordner mit gleicher Content-ID --------
    def test_backup_dir_with_manifest_is_violation(self):
        self.add_mod("rbbattle")
        self.add_mod("rbbattle.bak-20260910")
        self.assertEqual(run([self.mods]), check_mods_dir.EXIT_VIOLATION)

    def test_any_second_manifest_dir_is_violation(self):
        self.add_mod("rbbattle")
        self.add_mod("rbbattle-old")
        self.assertEqual(run([self.mods]), check_mods_dir.EXIT_VIOLATION)

    def test_only_foreign_mod_is_violation(self):
        # Ziel-Mod fehlt, stattdessen liegt ein fremder Manifest-Ordner da.
        self.add_mod("some-other-mod")
        self.assertEqual(run([self.mods]), check_mods_dir.EXIT_VIOLATION)

    def test_custom_expected_name(self):
        self.add_mod("my-mod")
        self.assertEqual(
            run([self.mods, "--expected", "my-mod"]), check_mods_dir.EXIT_OK
        )

    # --- Fehlerfaelle -------------------------------------------------------
    def test_missing_dir_is_usage_error(self):
        self.assertEqual(
            run([os.path.join(self.root, "nope")]), check_mods_dir.EXIT_USAGE
        )

    def test_quiet_suppresses_ok_text(self):
        self.add_mod("rbbattle")
        self.assertEqual(run([self.mods, "--quiet"]), check_mods_dir.EXIT_OK)

    # --- Hilfsfunktion ------------------------------------------------------
    def test_find_manifest_dirs_sorted(self):
        self.add_mod("rbbattle")
        self.add_mod("aaa-foreign")
        self.assertEqual(check_mods_dir.find_manifest_dirs(self.mods),
                         ["aaa-foreign", "rbbattle"])


if __name__ == "__main__":
    unittest.main()
