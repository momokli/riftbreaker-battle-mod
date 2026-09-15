#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-Tests fuer tools/deploy-gate/attest_identity.py (Issue #504).

Rein hermetisch (stdlib, kein planet, kein Netz): prueft die puren Extraktoren,
die Attestations-Entscheidungslogik (konsistent -> PASS, abweichend -> FAIL,
fehlende/inerte Flaeche -> n/a) und die CLI-Exit-Codes ueber ``--sources-json``.

Aufruf: python3 -m unittest test_attest_identity -v   (aus tools/deploy-gate)
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attest_identity as att  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPT = os.path.join(HERE, "attest_identity.py")

ENV = "dev"
REF = "abc123"


def consistent_sources():
    """Alle Surfaces mit identischer Identitaet (dev · abc123)."""
    return {
        "landing": (
            "<html><head>"
            '<meta name="rb-env" content="dev">'
            '<meta name="rb-ref" content="abc123">'
            "</head><body>"
            '<span class="badge" id="rb-identity">dev · abc123</span>'
            "</body></html>"
        ),
        "cockpit": (
            '<header class="app-header"><span class="title-badge">◆</span> RIFT BATTLE — OPERATOR COCKPIT</header>'
        ),
        "tournament_health": {"ok": True, "phase": "lobby", "env": "dev", "ref": "abc123"},
        "server_status": {"container": "x", "state": "running", "env": "dev", "ref": "abc123"},
        "session_jsonl": {"type": "event", "session_id": "s1", "env": "dev", "ref": "abc123"},
        "egress_jsonl": {"world": "A", "type": "ready", "env": "dev", "ref": "abc123"},
        "container_labels": {"Labels": {"RBB_ENV": "dev", "RBB_REF": "abc123"}},
        # Mod-Log ist inert (#499): env/ref = unknown -> n/a, KEIN Soll-Vergleich.
        "mod_log": "event=mod_load version=0.34.3 status=ok env=unknown ref=unknown",
        "mod_zip": "rbbattle-dev-abc123.zip",
    }


class ExtractorsTest(unittest.TestCase):
    def test_landing_meta_parses_env_and_ref(self):
        html = '<meta name="rb-env" content="prod"><meta name="rb-ref" content="v1.2.3">'
        self.assertEqual(att.extract_landing(html), ("prod", "v1.2.3"))

    def test_landing_meta_attribute_order_is_robust(self):
        html = '<meta content="prod" name="rb-env">'
        self.assertEqual(att.extract_landing(html), ("prod", None))

    def test_cockpit_without_identity_is_none(self):
        self.assertIsNone(
            att.extract_cockpit(
                '<header class="app-header"><span class="title-badge">◆</span> OPERATOR COCKPIT</header>'
            )
        )

    def test_health_json_string_and_dict(self):
        self.assertEqual(
            att.extract_tournament_health('{"ok": true, "env": "dev", "ref": "abc123"}'), ("dev", "abc123")
        )
        self.assertEqual(att.extract_tournament_health({"ok": True, "env": "dev", "ref": "abc123"}), ("dev", "abc123"))

    def test_container_labels_from_bare_labels_full_inspect_and_list(self):
        self.assertEqual(att.extract_container_labels('{"RBB_ENV": "dev", "RBB_REF": "abc123"}'), ("dev", "abc123"))
        self.assertEqual(
            att.extract_container_labels({"Config": {"Labels": {"RBB_ENV": "prod", "RBB_REF": "v1.0"}}}),
            ("prod", "v1.0"),
        )
        self.assertEqual(
            att.extract_container_labels([{"Labels": {"RBB_ENV": "test", "RBB_REF": "deadbeef"}}]), ("test", "deadbeef")
        )

    def test_mod_log_inert_unknown_is_none(self):
        line = "event=mod_load version=0.34.3 status=ok env=unknown ref=unknown"
        self.assertIsNone(att.extract_mod_log(line))

    def test_mod_log_with_real_identity_is_parsed(self):
        line = "event=mod_load version=0.34.3 status=ok env=prod ref=v1.0"
        self.assertEqual(att.extract_mod_log(line), ("prod", "v1.0"))

    def test_mod_log_without_mod_load_is_none(self):
        self.assertIsNone(att.extract_mod_log("event=wave level=3 status=done"))

    def test_mod_zip_name_parses_env_and_ref(self):
        self.assertEqual(att.extract_mod_zip("rbbattle-dev-abc123.zip"), ("dev", "abc123"))
        self.assertEqual(att.extract_mod_zip("rbbattle-prod-v1.2.3+sha.zip"), ("prod", "v1.2.3+sha"))
        self.assertIsNone(att.extract_mod_zip("rbbattle.zip"))


class AttestTest(unittest.TestCase):
    def _by_surface(self, results):
        return {r["surface"]: r for r in results}

    def test_consistent_identity_all_pass_or_na(self):
        results = att.attest(ENV, REF, consistent_sources())
        by = self._by_surface(results)
        self.assertEqual(by["landing"]["status"], "PASS")
        self.assertEqual(by["cockpit"]["status"], "n/a")
        self.assertEqual(by["tournament_health"]["status"], "PASS")
        self.assertEqual(by["server_status"]["status"], "PASS")
        self.assertEqual(by["session_jsonl"]["status"], "PASS")
        self.assertEqual(by["egress_jsonl"]["status"], "PASS")
        self.assertEqual(by["container_labels"]["status"], "PASS")
        self.assertEqual(by["mod_log"]["status"], "n/a")
        self.assertEqual(by["mod_zip"]["status"], "PASS")
        self.assertNotIn("FAIL", [r["status"] for r in results])
        self.assertEqual(att.exit_code(results), 0)

    def test_divergent_ref_fails(self):
        sources = consistent_sources()
        sources["landing"] = '<meta name="rb-env" content="dev"><meta name="rb-ref" content="deadbeef">'
        results = att.attest(ENV, REF, sources)
        landing = self._by_surface(results)["landing"]
        self.assertEqual(landing["status"], "FAIL")
        self.assertEqual(landing["reported_ref"], "deadbeef")
        self.assertEqual(att.exit_code(results), 1)

    def test_divergent_env_fails(self):
        sources = consistent_sources()
        sources["server_status"] = {"state": "running", "env": "prod", "ref": "abc123"}
        results = att.attest(ENV, REF, sources)
        self.assertEqual(self._by_surface(results)["server_status"]["status"], "FAIL")
        self.assertEqual(att.exit_code(results), 1)

    def test_missing_surface_is_na(self):
        sources = consistent_sources()
        del sources["session_jsonl"]
        results = att.attest(ENV, REF, sources)
        session = self._by_surface(results)["session_jsonl"]
        self.assertEqual(session["status"], "n/a")
        self.assertEqual(att.exit_code(results), 0)  # Rest weiterhin PASS

    def test_live_error_is_fail_not_na(self):
        results = att.attest(
            ENV, REF, consistent_sources(), errors={"tournament_health": "Live-Quelle nicht erreichbar"}
        )
        self.assertEqual(self._by_surface(results)["tournament_health"]["status"], "FAIL")
        self.assertEqual(att.exit_code(results), 1)

    def test_all_na_yields_nonzero(self):
        # Nichts attestierbar -> kein SOC-Beweis -> rc != 0.
        results = att.attest(ENV, REF, {"mod_log": "event=mod_load env=unknown ref=unknown"})
        self.assertEqual(att.exit_code(results), 1)


class RealRepoContractTest(unittest.TestCase):
    """Inventar-Konsistenz: die Attestation kennt die Surfaces der Templates."""

    def _read(self, rel):
        with open(os.path.join(REPO_ROOT, rel), "r", encoding="utf-8") as handle:
            return handle.read()

    def test_landing_template_carries_identity_markers(self):
        text = self._read("deploy/roles/website/templates/index.html.j2")
        self.assertIn('name="rb-env"', text)
        self.assertIn('name="rb-ref"', text)

    def test_server_compose_carries_labels(self):
        text = self._read("deploy/roles/riftbreaker-server/templates/docker-compose.yml.j2")
        self.assertIn("RBB_ENV", text)
        self.assertIn("RBB_REF", text)

    def test_server_control_env_carries_identity(self):
        text = self._read("deploy/roles/server-control/templates/server-control.env.j2")
        self.assertIn("SERVER_CONTROL_ENV", text)
        self.assertIn("SERVER_CONTROL_REF", text)

    def test_mod_zip_defaults_carry_env_tagged_name(self):
        text = self._read("deploy/roles/mods-zip/defaults/main.yml")
        self.assertIn("mods_zip_env_name", text)

    def test_mod_log_carries_mod_load_identity(self):
        text = self._read("client-mod/lua/rbbattle_autoexec.lua")
        self.assertIn("event=mod_load", text)
        self.assertIn("DeployEnv", text)


class CliTest(unittest.TestCase):
    def _run(self, extra, env=None):
        args = [sys.executable, SCRIPT] + extra
        full_env = dict(os.environ)
        full_env.update(env or {})
        proc = subprocess.run(args, capture_output=True, text=True, env=full_env)
        return proc.returncode, proc.stdout, proc.stderr

    def _write_sources(self, sources):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        json.dump(sources, handle)
        handle.close()
        return handle.name

    def test_cli_ok_with_sources_json(self):
        path = self._write_sources(consistent_sources())
        code, out, _err = self._run(["--env", ENV, "--ref", REF, "--sources-json", path])
        self.assertEqual(code, 0)
        self.assertIn(att.MARKER, out)
        self.assertIn("PASS", out)

    def test_cli_fails_on_divergent_identity(self):
        sources = consistent_sources()
        sources["mod_zip"] = "rbbattle-dev-deadbeef.zip"
        path = self._write_sources(sources)
        code, _out, err = self._run(["--env", ENV, "--ref", REF, "--sources-json", path])
        self.assertEqual(code, 1)
        self.assertIn(att.MARKER, err)

    def test_cli_reads_env_from_environment(self):
        path = self._write_sources(consistent_sources())
        code, out, _err = self._run(
            ["--sources-json", path],
            env={"RBB_ATTEST_ENV": ENV, "RBB_ATTEST_REF": REF},
        )
        self.assertEqual(code, 0)
        self.assertIn("env=%s ref=%s" % (ENV, REF), out)

    def test_cli_rejects_invalid_env(self):
        code, _out, err = self._run(["--env", "staging", "--ref", REF])
        self.assertEqual(code, 2)
        self.assertIn("staging", err)

    def test_cli_requires_ref(self):
        code, _out, err = self._run(["--env", ENV])
        self.assertEqual(code, 2)
        self.assertIn("ref", err)

    def test_cli_json_format(self):
        path = self._write_sources(consistent_sources())
        code, out, _err = self._run(["--env", ENV, "--ref", REF, "--sources-json", path, "--format", "json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["env"], ENV)
        self.assertIsInstance(payload["results"], list)


if __name__ == "__main__":
    unittest.main()
