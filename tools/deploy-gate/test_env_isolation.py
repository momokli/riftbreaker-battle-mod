#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-Tests fuer tools/deploy-gate/check_env_isolation.py (Issue #483, US2).

Rein datei-basiert (stdlib, kein PyYAML): baut Temp-Fixtures und prueft
Positiv- und Negativ-Faelle der reinen Entscheidungslogik + CLI-Exit-Codes.

Aufruf: python3 -m unittest test_env_isolation -v   (aus diesem Verzeichnis)
"""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_env_isolation as cei  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(HERE, "check_env_isolation.py")

# Kleines, vollstaendig klassifiziertes Fixture-Repo.
SCHEMA = """\
per_env:
  website_docroot: [prod, test]
  riftbreaker_game_dir: [prod, test]
shared:
  riftbreaker_content_mode: "ueberall sync — kein Env-Zustand."
"""

HOST_VARS = """\
riftbreaker_game_dir: /srv/rbgame
website_docroot: /srv/rbmods-site
riftbreaker_content_mode: sync
"""

PROD_VARS = """\
riftbreaker_game_dir: /srv/rbgame-prod
website_docroot: /srv/rbmods-site-prod
riftbreaker_content_mode: sync
"""

TEST_VARS = """\
riftbreaker_game_dir: /srv/rbgame-test
website_docroot: /srv/rbmods-site-test
riftbreaker_content_mode: sync
"""


class FixtureRepo:
    def __init__(self, schema=SCHEMA, host=HOST_VARS, prod=PROD_VARS, test=TEST_VARS):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        base = os.path.join(self.root, "deploy")
        os.makedirs(os.path.join(base, "inventory", "host_vars", "planet"))
        self._write(os.path.join(base, "env-schema.yml"), schema)
        self._write(os.path.join(base, "inventory", "host_vars", "planet", "vars.yml"), host)
        self._write(os.path.join(base, "prod-vars.yml"), prod)
        self._write(os.path.join(base, "test-vars.yml"), test)

    @staticmethod
    def _write(path, text):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def cleanup(self):
        self.tmp.cleanup()


class ParseSchemaTest(unittest.TestCase):
    def test_parses_per_env_and_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "env-schema.yml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(SCHEMA)
            per_env, shared, problems = cei.parse_schema(path)
        self.assertEqual(problems, [])
        self.assertEqual(per_env, {
            "website_docroot": ["prod", "test"],
            "riftbreaker_game_dir": ["prod", "test"],
        })
        self.assertIn("riftbreaker_content_mode", shared)

    def test_shared_without_reason_is_a_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "env-schema.yml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("shared:\n  lonely: \"\"\n")
            _per_env, _shared, problems = cei.parse_schema(path)
        self.assertTrue(any("ohne Begruendung" in p for p in problems), problems)

    def test_invalid_env_name_is_a_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "env-schema.yml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("per_env:\n  x: [staging]\n")
            _per_env, _shared, problems = cei.parse_schema(path)
        self.assertTrue(any("ungueltige Env" in p for p in problems), problems)


class CheckTest(unittest.TestCase):
    def test_positive_fixture_passes(self):
        repo = FixtureRepo()
        self.addCleanup(repo.cleanup)
        self.assertEqual(cei.check(repo.root), [])

    def test_per_env_key_missing_in_prod_is_reported(self):
        prod = PROD_VARS.replace("riftbreaker_game_dir: /srv/rbgame-prod\n", "")
        repo = FixtureRepo(prod=prod)
        self.addCleanup(repo.cleanup)
        problems = cei.check(repo.root)
        self.assertTrue(any("riftbreaker_game_dir" in p and "prod" in p for p in problems), problems)

    def test_per_env_key_missing_in_test_is_reported(self):
        test = TEST_VARS.replace("website_docroot: /srv/rbmods-site-test\n", "")
        repo = FixtureRepo(test=test)
        self.addCleanup(repo.cleanup)
        problems = cei.check(repo.root)
        self.assertTrue(any("website_docroot" in p and "test" in p for p in problems), problems)

    def test_unclassified_new_variable_is_reported(self):
        prod = PROD_VARS + "brand_new_thing: 1\n"
        repo = FixtureRepo(prod=prod)
        self.addCleanup(repo.cleanup)
        problems = cei.check(repo.root)
        self.assertTrue(any("brand_new_thing" in p for p in problems), problems)

    def test_shared_without_reason_reported_via_check(self):
        schema = 'shared:\n  some_var: ""\n'
        repo = FixtureRepo(schema=schema, prod="some_var: x\n", test="")
        self.addCleanup(repo.cleanup)
        problems = cei.check(repo.root)
        self.assertTrue(any("ohne Begruendung" in p for p in problems), problems)


class RealRepoTest(unittest.TestCase):
    def test_real_repo_passes(self):
        self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, "deploy", "env-schema.yml")))
        self.assertEqual(cei.check(REPO_ROOT), [])


class CliTest(unittest.TestCase):
    def _run(self, root, extra=None):
        args = [sys.executable, SCRIPT, "--repo-root", root]
        if extra:
            args += extra
        proc = subprocess.run(args, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def test_cli_ok_on_real_repo(self):
        code, out, _err = self._run(REPO_ROOT, ["--env", "dev"])
        self.assertEqual(code, 0)
        self.assertIn(cei.MARKER, out)

    def test_cli_fails_with_marker_on_violation(self):
        prod = PROD_VARS + "unclassified_thing: 1\n"
        repo = FixtureRepo(prod=prod)
        self.addCleanup(repo.cleanup)
        code, _out, err = self._run(repo.root, ["--env", "prod"])
        self.assertEqual(code, 1)
        self.assertIn(cei.MARKER, err)
        self.assertIn("unclassified_thing", err)

    def test_cli_rejects_invalid_env_name(self):
        repo = FixtureRepo()
        self.addCleanup(repo.cleanup)
        code, _out, err = self._run(repo.root, ["--env", "staging"])
        self.assertEqual(code, 1)
        self.assertIn("staging", err)


if __name__ == "__main__":
    unittest.main()
