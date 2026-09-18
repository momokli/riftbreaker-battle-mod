#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-Test fuer die RBBATTLE-Log-Zeilenerkennung (Issue #696).

Reine Logik, kein Docker/SSH/Netz. Aufruf:
`python3 -m unittest test_core_io_probe -v` aus tests/core-io/.
"""

import unittest

import core_io_probe as probe


class RbbattleLineTests(unittest.TestCase):
    def test_old_bare_tag_matches(self):
        self.assertTrue(probe.is_rbbattle_line("[server] [info] [RBBATTLE] event=mod_load status=ok"))

    def test_new_build_suffixed_tag_matches(self):
        # Seit 08bb0ca (#631) kann das Tag einen Build-Suffix tragen.
        line = ("[server] [20:12:25.161] [info] LogService.cpp:71 - "
                "[LUA 'lua/rbbattle_autoexec.lua']: [RBBATTLE:20260917-203600]: "
                "event=mod_load version=0.34.3 status=ok mode=server")
        self.assertTrue(probe.is_rbbattle_line(line))

    def test_unrelated_line_does_not_match(self):
        self.assertFalse(probe.is_rbbattle_line("[server] [info] GameplayState::ResumeGame"))


if __name__ == "__main__":
    unittest.main()
