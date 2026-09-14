#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_rbpack.py - Unit-Tests für rbpack (zip64-sicherer Game-Pack-Reader).

Deterministisch und offline: synthetische Zip-Packs im tmpdir (kein Zugriff auf
die echten Game-Daten, kein Container, kein Netz). Deckt list (Substring-Filter,
Ignore-Case, Mehr-Pack), binär-sicheres cat und grep (Treffer, Kontext,
--in-Filter, Hit-Cap, bytes-sicher) sowie die Exit-Codes ab.

Aufruf: python3 -m unittest test_rbpack -v   (aus diesem Verzeichnis)
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rbpack  # noqa: E402

RESOURCE_IRON = b'\tid\t"steel"\n\ticon\t"gui/hud/resource_icons/ironium"\n'
RESOURCE_CARBON = b'\tid\t"carbonium"\n'
LUA_CHEAT = b'cheat_add_resource("ironium", 10)\n'
RAW_BYTES = bytes(range(256))


def write_pack(path, members):
    """Kleines Test-Pack schreiben (zentralverzeichnis-kompatibel, zipfile)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as pack:
        for name, data in members.items():
            pack.writestr(name, data)


class _CaptureStdout(object):
    """stdout-Ersatz mit Text-Teil (print) und Binär-Teil (sys.stdout.buffer)."""

    def __init__(self):
        self.text = io.StringIO()
        self.buffer = io.BytesIO()

    def write(self, data):
        return self.text.write(data)

    def flush(self):
        return None


class RbpackTestCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="rbpack-")
        self.packs = os.path.join(self.root, "packs")
        os.makedirs(self.packs)
        write_pack(os.path.join(self.packs, "00_win_data.zip"), {
            "scripts/resources/iron.resource": RESOURCE_IRON,
            "scripts/resources/carbon.resource": RESOURCE_CARBON,
            "lua/commands/cheat.lua": LUA_CHEAT,
            "materials/raw.bin": RAW_BYTES,
        })

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_rbpack(self, args, pack_dir=None):
        """main() mit gefangenem stdout/stderr; gibt (code, text, bytes, err)."""
        cap = _CaptureStdout()
        err = io.StringIO()
        argv = [args[0], "--dir", pack_dir or self.packs] + list(args[1:])
        with contextlib.redirect_stdout(cap), contextlib.redirect_stderr(err):
            code = rbpack.main(argv)
        return code, cap.text.getvalue(), cap.buffer.getvalue(), err.getvalue()

    # --- list -------------------------------------------------------------

    def test_list_lists_all_members(self):
        code, out, _data, err = self.run_rbpack(["list"])
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 4)
        self.assertIn("scripts/resources/iron.resource", out)
        self.assertIn("4/4 Member", err)

    def test_list_substring_filter(self):
        code, out, _data, _err = self.run_rbpack(["list", "scripts/resources/"])
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 2)
        self.assertNotIn("lua/commands/cheat.lua", out)

    def test_list_ignore_case(self):
        code, out, _data, _err = self.run_rbpack(["list", "-i", "IRON.RESOURCE"])
        self.assertEqual(code, 0)
        self.assertIn("scripts/resources/iron.resource", out)

    def test_list_without_match_exits_one(self):
        code, out, _data, _err = self.run_rbpack(["list", "gibt-es-nicht"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")

    def test_list_single_pack_has_no_pack_prefix(self):
        code, out, _data, _err = self.run_rbpack(["list", "--pack", "00_win_data.zip"])
        self.assertEqual(code, 0)
        self.assertNotIn("00_win_data.zip", out)

    # --- cat --------------------------------------------------------------

    def test_cat_writes_exact_bytes(self):
        code, _out, data, err = self.run_rbpack(["cat", "materials/raw.bin"])
        self.assertEqual(code, 0)
        self.assertEqual(data, RAW_BYTES)
        self.assertIn("materials/raw.bin", err)

    def test_cat_missing_member_is_not_found(self):
        code, _out, data, err = self.run_rbpack(["cat", "nope.bin"])
        self.assertEqual(code, 1)
        self.assertEqual(data, b"")
        self.assertIn("keinem Pack gefunden", err)

    def test_cat_searches_all_packs(self):
        write_pack(os.path.join(self.packs, "03_win_data.zip"), {"only/in/second.txt": b"second"})
        code, _out, data, err = self.run_rbpack(["cat", "only/in/second.txt"])
        self.assertEqual(code, 0)
        self.assertEqual(data, b"second")
        self.assertIn("03_win_data.zip", err)

    # --- grep -------------------------------------------------------------

    def test_grep_hits_with_context(self):
        code, out, _data, err = self.run_rbpack(["grep", "ironium"])
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 2)
        self.assertIn("gui/hud/resource_icons/ironium", out)
        self.assertIn("cheat_add_resource", out)
        self.assertIn("2 Treffer", err)

    def test_grep_max_hits_caps_output(self):
        code, out, _data, err = self.run_rbpack(["grep", "ironium", "--max-hits", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("Hit-Cap", err)

    def test_grep_in_filter_limits_members(self):
        code, out, _data, _err = self.run_rbpack(["grep", "ironium", "--in", "scripts/resources/"])
        self.assertEqual(code, 0)
        self.assertNotIn("cheat_add_resource", out)

    def test_grep_without_match_exits_one(self):
        code, out, _data, err = self.run_rbpack(["grep", "definitiv-nicht-vorhanden"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("0 Treffer", err)

    def test_grep_is_bytes_safe_on_binary_member(self):
        # RAW_BYTES enthält genau ein NUL-Byte; der Renderer zeigt es als \x00.
        code, out, _data, _err = self.run_rbpack(["grep", "\x00"])
        self.assertEqual(code, 0)
        self.assertIn("materials/raw.bin", out)
        self.assertIn("\\x00", out)

    def test_grep_prefixes_pack_name_for_multiple_packs(self):
        write_pack(os.path.join(self.packs, "03_win_data.zip"), {"lua/other.lua": b'add("ironium")\n'})
        code, out, _data, _err = self.run_rbpack(["grep", "ironium"])
        self.assertEqual(code, 0)
        self.assertIn("00_win_data.zip:lua/commands/cheat.lua", out)
        self.assertIn("03_win_data.zip:lua/other.lua", out)

    # --- Fehlerpfade ------------------------------------------------------

    def test_grep_invalid_regex_is_usage_error(self):
        code, _out, _data, err = self.run_rbpack(["grep", "("])
        self.assertEqual(code, 2)
        self.assertIn("ungültige Regex", err)

    def test_missing_pack_dir_is_usage_error(self):
        code, _out, _data, err = self.run_rbpack(["list"], pack_dir=os.path.join(self.root, "nope"))
        self.assertEqual(code, 2)
        self.assertIn("keine", err)

    def test_unknown_pack_name_is_usage_error(self):
        code, _out, _data, err = self.run_rbpack(["list", "--pack", "99_missing_data.zip"])
        self.assertEqual(code, 2)
        self.assertIn("kein Pack für", err)


if __name__ == "__main__":
    unittest.main()
