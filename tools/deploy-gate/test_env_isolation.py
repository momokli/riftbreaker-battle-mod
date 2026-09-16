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
    def __init__(self, schema=SCHEMA, host=HOST_VARS, prod=PROD_VARS, test=TEST_VARS, staging=""):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        base = os.path.join(self.root, "deploy")
        os.makedirs(os.path.join(base, "inventory", "host_vars", "planet"))
        self._write(os.path.join(base, "env-schema.yml"), schema)
        self._write(os.path.join(base, "inventory", "host_vars", "planet", "vars.yml"), host)
        self._write(os.path.join(base, "prod-vars.yml"), prod)
        self._write(os.path.join(base, "test-vars.yml"), test)
        self._write(os.path.join(base, "staging-vars.yml"), staging)

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
                handle.write("per_env:\n  x: [qa]\n")
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

    def test_unclassified_host_vars_only_variable_is_reported(self):
        # Negativ-Fall (Issue #483): ein Key, der NUR in der dev-Basis
        # (host_vars) steht, muss ebenso rot werden — sonst erbt ihn jede
        # andere Env still (genau die Bug-Klasse des Issues).
        host = HOST_VARS + "dev_only_thing: 1\n"
        repo = FixtureRepo(host=host)
        self.addCleanup(repo.cleanup)
        problems = cei.check(repo.root)
        self.assertTrue(any("dev_only_thing" in p for p in problems), problems)

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

    def test_real_schema_classifies_path_schema_vars(self):
        # Issue #483, Ziel B: die neuen `<env>`-Pfadvariablen sind klassifiziert
        # und der Compose-Projektname ist fuer ALLE Envs Pflicht (der Basename
        # ist im neuen Schema ueberall "riftbreaker" -> sonst Kollision).
        per_env, _shared, problems = cei.parse_schema(
            os.path.join(REPO_ROOT, "deploy", "env-schema.yml")
        )
        self.assertEqual(problems, [])
        for var in ("riftbreaker_game_dir", "riftbreaker_backup_dir",
                    "riftbreaker_deploy_dir", "riftbreaker_sessions_dir",
                    "rbtools_dir", "rbtools_staging_dir"):
            self.assertIn(var, per_env, var)
            self.assertIn("prod", per_env[var], var)
            self.assertIn("test", per_env[var], var)
        self.assertEqual(per_env.get("riftbreaker_compose_project"),
                         ["dev", "prod", "test", "staging"])

    def test_real_dev_basis_uses_env_schema(self):
        # dev ist kein Sonderfall mehr: die Pfade leiten sich aus `rift_env` ab.
        path = os.path.join(REPO_ROOT, "deploy", "inventory", "host_vars", "planet", "vars.yml")
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        for needle in ("/srv/rift-{{ rift_env }}/game",
                       "/srv/rift-{{ rift_env }}/backups",
                       "/srv/rift-{{ rift_env }}/sessions",
                       "/opt/rbmods/compose/rift-{{ rift_env }}/riftbreaker",
                       "/opt/rbmods/rbtools/{{ rift_env }}"):
            self.assertIn(needle, text, needle)


class DevPathsTest(unittest.TestCase):
    # Minimale Fixtures: NUR der Compose-Projektname, damit keine anderen
    # Variablen als "unclassified" durchschlagen.
    SCHEMA_CP = "per_env:\n  riftbreaker_compose_project: [dev, prod, test]\n"
    HOST_CP = "riftbreaker_compose_project: rift-dev\n"
    PROD_CP = "riftbreaker_compose_project: rift-prod\n"
    TEST_CP = "riftbreaker_compose_project: rb-test\n"

    def test_missing_dev_compose_project_is_reported(self):
        # per_env, das dev einschliesst: fehlt der Key in der dev-Basis
        # (host_vars), MUSS das Gate rot werden (sonst Kollision dev/prod).
        repo = FixtureRepo(schema=self.SCHEMA_CP, host="",
                           prod=self.PROD_CP, test=self.TEST_CP)
        self.addCleanup(repo.cleanup)
        problems = cei.check(repo.root)
        self.assertTrue(any("riftbreaker_compose_project" in p and "dev" in p
                            for p in problems), problems)

    def test_present_dev_compose_project_passes(self):
        repo = FixtureRepo(schema=self.SCHEMA_CP, host=self.HOST_CP,
                           prod=self.PROD_CP, test=self.TEST_CP)
        self.addCleanup(repo.cleanup)
        self.assertEqual(cei.check(repo.root), [])


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
        code, _out, err = self._run(repo.root, ["--env", "qa"])
        self.assertEqual(code, 1)
        self.assertIn("qa", err)


if __name__ == "__main__":
    unittest.main()
