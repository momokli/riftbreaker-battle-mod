#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-/Integrationstests fuer deploy/server-control/server_control.py (Issue #424).

Reine Tests ohne Docker, Netz oder Spiel: `docker` wird durch ein Fake-Binary
ersetzt (ein Python-Skript, ueber ``SERVER_CONTROL_DOCKER`` adressiert). Aufruf:

    python3 -m unittest test_server_control -v

Abgedeckt (Test-Split "OHNE Player"):
  * ``GET /server/status`` liefert state/restarting/health/uptime/started_at
    aus ``docker inspect``,
  * ``GET /server/logs?tail=N`` reicht ``--tail N`` an docker durch,
  * ``POST /server/restart|start|stop`` ruft genau den docker-Subcommand auf,
  * **Auth erzwungen**: 401 ohne/mit falschem Token, 200 mit Token,
  * kein Token konfiguriert => der Dienst startet nicht (fail-closed),
  * ``POST /server/config`` rendert ``config.cfg`` aus der echten Vorlage
    (``config.cfg.j2``) + restartet, weist Config-Injection ab.
"""

import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import server_control as sc

TOKEN = "test-token-123"
CONTAINER = "riftbreaker-dedicated"

FAKE_DOCKER = r'''#!/usr/bin/env python3
"""Fake `docker` fuer Tests: loggt Aufrufe, liefert canned Ausgaben."""
import json, os, sys

LOG = os.environ["FAKE_DOCKER_LOG"]
with open(LOG, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")

args = sys.argv[1:]
cmd = args[0] if args else ""

if cmd == "inspect":
    print(json.dumps([{
        "Name": "/" + args[-1],
        "State": {
            "Status": os.environ.get("FAKE_DOCKER_STATE", "running"),
            "Restarting": os.environ.get("FAKE_DOCKER_RESTARTING", "false") == "true",
            "StartedAt": os.environ.get("FAKE_DOCKER_STARTED",
                                       "2026-09-15T00:00:00.123456789Z"),
            "Health": {"Status": os.environ.get("FAKE_DOCKER_HEALTH", "healthy")},
        },
    }]))
elif cmd == "logs":
    sys.stdout.write("[server] line one\n[server] line two\n")
    sys.stderr.write("[server] line three\n")
elif cmd in ("restart", "start", "stop"):
    print(args[-1])
elif cmd == "boom":
    sys.stderr.write("cannot connect to the Docker daemon\n")
    sys.exit(1)
else:
    sys.stderr.write("unknown command\n")
    sys.exit(1)
'''


def write_fake_docker(directory):
    path = os.path.join(directory, "fake-docker")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(FAKE_DOCKER)
    os.chmod(path, 0o755)
    return path


class BaseFixture(unittest.TestCase):
    """Gemeinsames Setup: Fake-Docker, deployte Vorlage, Config-Pfade."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="server-control-")
        self.docker_bin = write_fake_docker(self.tmp)
        self.log_file = os.path.join(self.tmp, "docker-calls.log")
        os.environ["FAKE_DOCKER_LOG"] = self.log_file
        self.config_path = os.path.join(self.tmp, "config", "config.cfg")
        self.template_path = os.path.join(self.tmp, "config.cfg.j2")
        self.vars_path = os.path.join(self.tmp, "config-vars.json")

        # Echte deployte Vorlage verwenden — es gibt nur EINE Quelle fuer das
        # Rendering (deploy/server-control und die Ansible-Rolle lesen dieselbe).
        here = os.path.dirname(os.path.abspath(__file__))
        repo_root = os.path.abspath(os.path.join(here, "..", ".."))
        real_template = os.path.join(
            repo_root, "deploy", "roles", "riftbreaker-server", "templates", "config.cfg.j2"
        )
        self.assertTrue(os.path.exists(real_template), "config.cfg.j2 fehlt: %s" % real_template)
        with open(real_template, "r", encoding="utf-8") as handle:
            template = handle.read()
        with open(self.template_path, "w", encoding="utf-8") as handle:
            handle.write(template)

        with open(self.vars_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "riftbreaker_server_name": "RBBattle",
                    "riftbreaker_server_password": "s3cret",
                    "riftbreaker_server_rcon_password": "",
                    "riftbreaker_server_max_players": 4,
                    "riftbreaker_server_broadcast_enabled": 1,
                    "riftbreaker_server_pause_game_when_empty": 0,
                    "riftbreaker_server_campaign": "mp_survival/mp_survival",
                    "riftbreaker_server_mission": "survival/jungle",
                    "riftbreaker_server_difficulty": "coop_normal",
                },
                handle,
            )

        self.cfg = {
            "bind": "127.0.0.1",
            "port": 0,
            "token": TOKEN,
            "container": CONTAINER,
            "docker_bin": self.docker_bin,
            "timeout": 30,
            "config_path": self.config_path,
            "config_template": self.template_path,
            "config_vars": self.vars_path,
        }

    # -- Helfer ------------------------------------------------------------
    def docker_calls(self):
        if not os.path.exists(self.log_file):
            return []
        with open(self.log_file, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def read_config(self):
        with open(self.config_path, "r", encoding="utf-8") as handle:
            return handle.read()


class ServerControlTestCase(BaseFixture):
    """Fachlogik direkt (ohne HTTP)."""

    def test_status_running(self):
        status = sc.ServerControl(self.cfg).status()
        self.assertEqual(status["state"], "running")
        self.assertFalse(status["restarting"])
        self.assertEqual(status["health"], "healthy")
        self.assertEqual(status["started_at"], "2026-09-15T00:00:00.123456789Z")
        self.assertIsNotNone(status["uptime"])
        self.assertGreaterEqual(status["uptime_seconds"], 0)

    def test_status_restarting_and_health_none(self):
        os.environ["FAKE_DOCKER_STATE"] = "restarting"
        os.environ["FAKE_DOCKER_RESTARTING"] = "true"
        os.environ["FAKE_DOCKER_HEALTH"] = ""
        try:
            status = sc.ServerControl(self.cfg).status()
        finally:
            os.environ.pop("FAKE_DOCKER_STATE", None)
            os.environ.pop("FAKE_DOCKER_RESTARTING", None)
            os.environ.pop("FAKE_DOCKER_HEALTH", None)
        self.assertTrue(status["restarting"])
        self.assertEqual(status["health"], "none")
        self.assertIsNone(status["uptime_seconds"])

    def test_status_reports_deploy_identity(self):
        # Issue #483, US4: env/ref kommen aus der Config in /server/status.
        cfg = dict(self.cfg, env="prod", ref="abc123def456")
        status = sc.ServerControl(cfg).status()
        self.assertEqual(status["env"], "prod")
        self.assertEqual(status["ref"], "abc123def456")

    def test_status_defaults_identity_to_unknown(self):
        status = sc.ServerControl(self.cfg).status()
        self.assertEqual(status["env"], "unknown")
        self.assertEqual(status["ref"], "unknown")

    def test_logs_tail_passed_through(self):
        result = sc.ServerControl(self.cfg).logs(42)
        self.assertEqual(result["tail"], 42)
        self.assertIn("line one", "\n".join(result["lines"]))
        self.assertEqual(self.docker_calls()[-1], ["logs", "--tail", "42", CONTAINER])

    def test_lifecycle_uses_exact_subcommand(self):
        for action in ("restart", "start", "stop"):
            out = sc.ServerControl(self.cfg).lifecycle(action)
            self.assertTrue(out["ok"])
            self.assertEqual(self.docker_calls()[-1], [action, CONTAINER])

    def test_config_renders_template_and_restarts(self):
        out = sc.ServerControl(self.cfg).config(
            {"mode": "mp_survival/horde", "difficulty": "hard", "seed": "4711", "mission_save": "slot1"}
        )
        text = self.read_config()
        self.assertIn('set server_name "RBBattle"', text)
        self.assertIn('set server_password "s3cret"', text)
        self.assertIn('set campaign "mp_survival/horde"', text)
        self.assertIn('set difficulty "hard"', text)
        self.assertIn('set mission "survival/jungle"', text)
        self.assertIn('set seed "4711"', text)
        self.assertIn('set mission_save "slot1"', text)
        self.assertTrue(out["restarted"])
        self.assertEqual(self.docker_calls()[-1], ["restart", CONTAINER])
        self.assertEqual(stat.S_IMODE(os.stat(self.config_path).st_mode), 0o644)

    def test_config_rejects_injection(self):
        with self.assertRaises(sc.BadRequest):
            sc.ServerControl(self.cfg).config({"mission": 'x"\nset app_mode "evil'})
        self.assertFalse(os.path.exists(self.config_path))

    def test_config_unavailable_when_template_missing(self):
        cfg = dict(self.cfg, config_template=os.path.join(self.tmp, "nope.j2"))
        with self.assertRaises(sc.ConfigUnavailable):
            sc.ServerControl(cfg).config({"difficulty": "hard"})

    def test_config_unavailable_when_vars_missing(self):
        cfg = dict(self.cfg, config_vars=os.path.join(self.tmp, "nope.json"))
        with self.assertRaises(sc.ConfigUnavailable):
            sc.ServerControl(cfg).config({"difficulty": "hard"})

    def test_undefined_variable_maps_to_config_unavailable(self):
        try:
            import jinja2  # noqa: F401
        except ImportError:
            self.skipTest("jinja2 nicht installiert (Fallback wirft nicht)")
        template_path = os.path.join(self.tmp, "undefined.cfg.j2")
        with open(template_path, "w", encoding="utf-8") as handle:
            handle.write('set x "{{ never_defined }}"\n')
        cfg = dict(self.cfg, config_template=template_path)
        with self.assertRaises(sc.ConfigUnavailable):
            sc.ServerControl(cfg).config({"difficulty": "hard"})

    def test_config_no_restart_when_disabled(self):
        sc.ServerControl(self.cfg).config({"difficulty": "coop_normal"}, restart=False)
        self.assertEqual(self.docker_calls(), [])

    def test_docker_failure_maps_to_docker_error(self):
        cfg = dict(self.cfg, docker_bin=os.path.join(self.tmp, "missing-docker"))
        with self.assertRaises(sc.DockerError):
            sc.ServerControl(cfg).status()

    # -- Konfiguration -----------------------------------------------------
    def test_load_config_requires_token(self):
        with self.assertRaises(sc.ConfigError):
            sc.load_config({"SERVER_CONTROL_CONTAINER": CONTAINER})

    def test_load_config_requires_container(self):
        with self.assertRaises(sc.ConfigError):
            sc.load_config({"SERVER_CONTROL_TOKEN": TOKEN})

    def test_load_config_binds_localhost_by_default(self):
        cfg = sc.load_config({"SERVER_CONTROL_TOKEN": TOKEN, "SERVER_CONTROL_CONTAINER": CONTAINER})
        self.assertEqual(cfg["bind"], "127.0.0.1")

    def test_load_config_reads_deploy_identity(self):
        cfg = sc.load_config({
            "SERVER_CONTROL_TOKEN": TOKEN,
            "SERVER_CONTROL_CONTAINER": CONTAINER,
            "SERVER_CONTROL_ENV": " test ",
            "SERVER_CONTROL_REF": "sha-1",
        })
        self.assertEqual(cfg["env"], "test")
        self.assertEqual(cfg["ref"], "sha-1")

    def test_load_config_defaults_identity_to_unknown(self):
        cfg = sc.load_config({"SERVER_CONTROL_TOKEN": TOKEN, "SERVER_CONTROL_CONTAINER": CONTAINER})
        self.assertEqual(cfg["env"], "unknown")
        self.assertEqual(cfg["ref"], "unknown")

    def test_render_fallback_without_jinja2(self):
        text = sc.render_fallback('set x "{{ missing }}"\nset y "{{ other | default(\'z\') }}"\n', {"other": "q"})
        self.assertIn('set x ""', text)
        self.assertIn('set y "q"', text)

    def test_uptime_format(self):
        self.assertEqual(sc.format_uptime(0), "00:00:00")
        self.assertEqual(sc.format_uptime(3661), "01:01:01")
        self.assertEqual(sc.format_uptime(90061), "1d 01:01:01")
        self.assertIsNone(sc.format_uptime(None))


class HttpLayerTestCase(BaseFixture):
    """Echte HTTP-Requests gegen den gestarteten Agenten (Ephemeral-Port)."""

    def setUp(self):
        super().setUp()
        self.httpd = sc.build_server(self.cfg)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def request(self, path, method="GET", token=TOKEN, body=None):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if token is not None:
            req.add_header("Authorization", "Bearer " + token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_401_without_token(self):
        for path, method in (
            ("/server/status", "GET"),
            ("/server/logs", "GET"),
            ("/server/restart", "POST"),
            ("/server/start", "POST"),
            ("/server/stop", "POST"),
            ("/server/config", "POST"),
        ):
            status, _body = self.request(path, method, token=None)
            self.assertEqual(status, 401, "%s %s muss 401 liefern" % (method, path))

    def test_401_with_wrong_token(self):
        status, _body = self.request("/server/status", token="nope")
        self.assertEqual(status, 401)

    def test_401_with_non_ascii_bearer(self):
        # Non-ASCII-Bearer darf NIE eine Exception ausloesen (frueher:
        # TypeError in hmac.compare_digest -> RemoteDisconnected statt 401).
        status, body = self.request("/server/status", token="\u00fcn\u00efcode")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "unauthorized")

    def test_non_ascii_bearer_with_non_ascii_token(self):
        # Verschaeft: ein Token mit Non-ASCII-Zeichen muss weiter funktionieren.
        cfg = dict(self.cfg, token="t\u00f6ken-\u00fcber")
        httpd = sc.build_server(cfg)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request("http://127.0.0.1:%d/server/status" % port)
            req.add_header("Authorization", "Bearer t\u00f6ken-\u00fcber")
            with urllib.request.urlopen(req, timeout=10) as response:
                self.assertEqual(response.status, 200)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_status_ok_with_token(self):
        status, body = self.request("/server/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "running")
        self.assertIn("uptime", body)

    def test_status_identity_over_http(self):
        # Issue #483, US4: /server/status traegt env/ref auch ueber HTTP.
        cfg = dict(self.cfg, env="test", ref="sha-xyz")
        httpd = sc.build_server(cfg)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request("http://127.0.0.1:%d/server/status" % port)
            req.add_header("Authorization", "Bearer " + TOKEN)
            with urllib.request.urlopen(req, timeout=10) as response:
                body = json.loads(response.read().decode("utf-8"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
        self.assertEqual(body["env"], "test")
        self.assertEqual(body["ref"], "sha-xyz")

    def test_logs_ok_with_token(self):
        status, body = self.request("/server/logs?tail=5")
        self.assertEqual(status, 200)
        self.assertEqual(body["tail"], 5)
        self.assertTrue(body["lines"])
        self.assertEqual(self.docker_calls()[-1], ["logs", "--tail", "5", CONTAINER])

    def test_logs_tail_is_clamped(self):
        _status, body = self.request("/server/logs?tail=%d" % (sc.MAX_TAIL + 1))
        self.assertEqual(body["tail"], sc.MAX_TAIL)

    def test_restart_ok_with_token(self):
        status, body = self.request("/server/restart", method="POST")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(self.docker_calls()[-1], ["restart", CONTAINER])

    def test_method_not_allowed(self):
        status, body = self.request("/server/restart", method="GET")
        self.assertEqual(status, 405)
        self.assertEqual(body["error"], "method_not_allowed")

    def test_unknown_route_404(self):
        status, _body = self.request("/server/nope")
        self.assertEqual(status, 404)

    def test_bad_tail_400(self):
        status, body = self.request("/server/logs?tail=abc")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "bad_request")

    def test_config_missing_template_returns_503_not_500(self):
        cfg = dict(self.cfg, config_template=os.path.join(self.tmp, "nope.j2"))
        httpd = sc.build_server(cfg)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/server/config" % port,
                data=b"{}",
                method="POST",
            )
            req.add_header("Authorization", "Bearer " + TOKEN)
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    code, body = response.status, json.loads(response.read())
            except urllib.error.HTTPError as exc:
                code, body = exc.code, json.loads(exc.read())
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
        self.assertEqual(code, 503)
        self.assertEqual(body["error"], "config_unavailable")

    def test_config_endpoint_writes_and_restarts(self):
        status, body = self.request("/server/config", method="POST", body={"difficulty": "hard"})
        self.assertEqual(status, 200)
        self.assertTrue(body["restarted"])
        self.assertEqual(body["applied"]["difficulty"], "hard")
        self.assertIn('set difficulty "hard"', self.read_config())


if __name__ == "__main__":
    unittest.main(verbosity=2)
