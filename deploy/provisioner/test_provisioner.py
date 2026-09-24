#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-/Integrationstests fuer deploy/provisioner/provisioner.py (Issue #908).

Hermetisch: ``docker`` wird durch ein Fake-Binary ersetzt (Python-Skript, ueber
``DockerCli(binary=...)`` / Config adressiert, loggt jede Argumentliste als
JSON-Lines und haelt Container-/Netz-/Volume-Zustand in einer State-Datei);
der Bridge-``/health`` wird durch einen lokalen ``http.server`` auf einem
Ephemeral-Port gestubbt. Kein echtes Docker, kein Netz, kein Spiel.

Aufruf:

    cd deploy/provisioner && python3 -m unittest test_provisioner -v
"""

import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import provisioner as prov

IMAGE = "test-image:latest"

FAKE_DOCKER = r'''#!/usr/bin/env python3
"""Fake `docker` fuer Tests: loggt Aufrufe, haelt simplen Zustand."""
import json, os, sys

LOG = os.environ["FAKE_DOCKER_LOG"]
STATE_DIR = os.environ["FAKE_DOCKER_STATE_DIR"]
FAIL = [x for x in os.environ.get("FAKE_DOCKER_FAIL", "").split(",") if x.strip()]
IMAGES = [x for x in os.environ.get("FAKE_DOCKER_IMAGES", "test-image:latest").split(",") if x.strip()]


def log(args):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(args) + "\n")


def load(name, default):
    path = os.path.join(STATE_DIR, name)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save(name, value):
    path = os.path.join(STATE_DIR, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh)
    os.replace(tmp, path)


def fail(key):
    return key in FAIL


args = sys.argv[1:]
log(args)
cmd = args[0] if args else ""
sub = args[1] if len(args) > 1 else ""

if cmd == "inspect":
    name = args[-1]
    containers = load("containers.json", {})
    if name not in containers:
        sys.stderr.write("Error: No such object: %s\n" % name)
        sys.exit(1)
    running = containers[name]["running"]
    print(json.dumps([{"Name": "/" + name,
                       "State": {"Status": "running" if running else "exited"}}]))
elif cmd == "ps":
    for name in load("containers.json", {}):
        print(name)
elif cmd == "port":
    name = args[-1]
    containers = load("containers.json", {})
    if name not in containers:
        sys.stderr.write("Error: No such container\n")
        sys.exit(1)
    print("6321/udp -> 127.0.0.1:32768")
    print("8080/tcp -> 127.0.0.1:%d" % containers[name].get("bridge_port", 0))
elif cmd == "image" and sub == "inspect":
    if args[-1] in IMAGES:
        print("[]")
    else:
        sys.stderr.write("Error: No such image: %s\n" % args[-1])
        sys.exit(1)
elif cmd == "network" and sub == "inspect":
    if args[-1] in load("networks.json", []):
        print("[]")
    else:
        sys.exit(1)
elif cmd == "network" and sub == "create":
    if fail("network create"):
        sys.stderr.write("Error: could not create network\n"); sys.exit(1)
    networks = load("networks.json", [])
    if args[-1] not in networks:
        networks.append(args[-1]); save("networks.json", networks)
    print(args[-1])
elif cmd == "network" and sub == "rm":
    networks = load("networks.json", [])
    if args[-1] in networks:
        networks.remove(args[-1]); save("networks.json", networks); print(args[-1])
    else:
        sys.stderr.write("Error: No such network\n"); sys.exit(1)
elif cmd == "volume" and sub == "inspect":
    if args[-1] in load("volumes.json", []):
        print("[]")
    else:
        sys.exit(1)
elif cmd == "volume" and sub == "create":
    if fail("volume create"):
        sys.stderr.write("Error: could not create volume\n"); sys.exit(1)
    volumes = load("volumes.json", [])
    if args[-1] not in volumes:
        volumes.append(args[-1]); save("volumes.json", volumes)
    print(args[-1])
elif cmd == "volume" and sub == "rm":
    volumes = load("volumes.json", [])
    if args[-1] in volumes:
        volumes.remove(args[-1]); save("volumes.json", volumes); print(args[-1])
    else:
        sys.stderr.write("Error: No such volume\n"); sys.exit(1)
elif cmd == "run":
    if fail("run"):
        sys.stderr.write("Error: cannot start container\n"); sys.exit(1)
    name = args[args.index("--name") + 1]
    bridge_port = 0
    for i, a in enumerate(args):
        if a == "-p":
            parts = args[i + 1].split(":")
            if len(parts) >= 3 and parts[1].isdigit():
                bridge_port = int(parts[1])
    containers = load("containers.json", {})
    containers[name] = {"running": True, "bridge_port": bridge_port}
    save("containers.json", containers)
    print(name)
elif cmd == "start":
    containers = load("containers.json", {})
    if args[-1] in containers:
        containers[args[-1]]["running"] = True; save("containers.json", containers); print(args[-1])
    else:
        sys.stderr.write("Error: No such container\n"); sys.exit(1)
elif cmd == "stop":
    containers = load("containers.json", {})
    if args[-1] in containers:
        containers[args[-1]]["running"] = False; save("containers.json", containers); print(args[-1])
    else:
        sys.stderr.write("Error: No such container\n"); sys.exit(1)
elif cmd == "rm":
    containers = load("containers.json", {})
    if args[-1] in containers:
        del containers[args[-1]]; save("containers.json", containers); print(args[-1])
    else:
        sys.stderr.write("Error: No such container\n"); sys.exit(1)
else:
    sys.stderr.write("unknown command: %s\n" % " ".join(args)); sys.exit(1)
'''


def write_fake_docker(directory):
    path = os.path.join(directory, "fake-docker")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(FAKE_DOCKER)
    os.chmod(path, 0o755)
    return path


def free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.server.hits += 1
        if self.server.ok and self.path == "/health":
            body = b'{"ok": true}'
            self.send_response(200)
        else:
            body = b'{"ok": false, "state": "starting"}'
            self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: A003
        pass


class HealthStub(object):
    """Lokaler HTTP-Health-Stub auf Ephemeral-Port (steuerbar ok/starting)."""

    def __init__(self, ok=True):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
        self.server.daemon_threads = True
        self.server.ok = ok
        self.server.hits = 0
        self.port = self.server.server_address[1]
        self.url = "http://127.0.0.1:%d/health" % self.port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class BaseFixture(unittest.TestCase):
    """Gemeinsames Setup: Fake-Docker, Health-Stub, run-scoped Config."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="provisioner-")
        self.docker_bin = write_fake_docker(self.tmp)
        self.log_file = os.path.join(self.tmp, "docker.log")
        self.state_dir = os.path.join(self.tmp, "state")
        os.makedirs(self.state_dir)
        self.base_dir = os.path.join(self.tmp, "srv")
        os.makedirs(self.base_dir)

        os.environ["FAKE_DOCKER_LOG"] = self.log_file
        os.environ["FAKE_DOCKER_STATE_DIR"] = self.state_dir
        os.environ["FAKE_DOCKER_IMAGES"] = IMAGE
        os.environ.pop("FAKE_DOCKER_FAIL", None)

        self.stub = HealthStub(ok=True)
        self.addCleanup(self.stub.close)

        bridge = free_port()
        while bridge == self.stub.port:
            bridge = free_port()

        self.cfg = prov.Config(
            env="test",
            image=IMAGE,
            base_dir=self.base_dir,
            docker=self.docker_bin,
            timeout=30,
            health_deadline=5.0,
            health_interval=0.0,
            min_free_gb=0.0,
            bridge_port_base=bridge,
            instance_id="0",
        )
        self.docker = prov.DockerCli(self.docker_bin, 30)

    def tearDown(self):
        for key in ("FAKE_DOCKER_LOG", "FAKE_DOCKER_STATE_DIR", "FAKE_DOCKER_IMAGES", "FAKE_DOCKER_FAIL"):
            os.environ.pop(key, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- Helfer ------------------------------------------------------------
    def docker_calls(self):
        if not os.path.exists(self.log_file):
            return []
        with open(self.log_file, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def run_calls(self):
        return [call for call in self.docker_calls() if call and call[0] == "run"]

    def provisioner(self, **overrides):
        cfg = prov.Config(**{**vars(self.cfg), **overrides})
        return prov.Provisioner(cfg, docker=self.docker, health_probe=lambda url: prov.http_health_ok(self.stub.url))

    def spec(self, instance_id="0"):
        return prov.InstanceSpec(self.cfg.env, instance_id, self.cfg)


class DockerCliTestCase(BaseFixture):
    def test_run_uses_argument_list(self):
        rc, _out, _err = self.docker.run(["ps", "-a"])
        self.assertEqual(rc, 0)
        calls = self.docker_calls()
        self.assertEqual(calls[-1], ["ps", "-a"])
        for call in calls:
            self.assertIsInstance(call, list)

    def test_run_or_fail_raises_on_bad_command(self):
        with self.assertRaises(prov.DockerError):
            self.docker.run_or_fail(["boom"])

    def test_image_exists(self):
        self.assertTrue(self.docker.image_exists(IMAGE))
        self.assertFalse(self.docker.image_exists("nope:latest"))

    def test_network_and_volume_lifecycle(self):
        self.assertFalse(self.docker.network_exists("n1"))
        self.docker.network_create("n1")
        self.assertTrue(self.docker.network_exists("n1"))
        self.docker.network_rm("n1")
        self.assertFalse(self.docker.network_exists("n1"))
        self.docker.volume_create("v1")
        self.assertTrue(self.docker.volume_exists("v1"))
        self.docker.volume_rm("v1")
        self.assertFalse(self.docker.volume_exists("v1"))

    def test_missing_binary_raises_docker_error(self):
        cli = prov.DockerCli(os.path.join(self.tmp, "nope-docker"), 5)
        with self.assertRaises(prov.DockerError):
            cli.image_exists("x")

    def test_inspect_optional_missing_returns_none(self):
        self.assertIsNone(self.docker.inspect_optional("ghost"))
        with self.assertRaises(prov.DockerError):
            self.docker.inspect("ghost")


class InstanceSpecTestCase(BaseFixture):
    def test_deterministic_names(self):
        spec = self.spec("12345")
        self.assertEqual(spec.container, "riftbreaker-dedicated-test-12345")
        self.assertEqual(spec.compose_project, "rb-test-12345")
        self.assertEqual(spec.network, "rb-test-12345_default")
        self.assertEqual(spec.wine_volume, "rb-test-wine-12345")
        self.assertEqual(spec.saves_volume, "rb-test-saves-12345")
        self.assertEqual(spec.game_dir, os.path.join(self.base_dir, "rift-test-12345", "game"))
        self.assertEqual(spec.backups_dir, os.path.join(self.base_dir, "rift-test-12345", "backups"))
        self.assertEqual(spec.sessions_dir, os.path.join(self.base_dir, "rift-test-12345", "sessions"))
        self.assertEqual(spec.bridge_port, self.cfg.bridge_port_base + (12345 % 20000))

    def test_deterministic_repeat(self):
        self.assertEqual(self.spec("777").to_dict(), self.spec("777").to_dict())

    def test_health_url(self):
        spec = self.spec("5")
        self.assertEqual(spec.health_url(), "http://127.0.0.1:%d/health" % spec.bridge_port)

    def test_invalid_instance_id(self):
        for bad in ("bad/id", "with space", "x" * 41, "a;b"):
            with self.assertRaises(prov.ProvisionError, msg=bad):
                prov.InstanceSpec("test", bad, self.cfg)

    def test_non_numeric_suffix_stable(self):
        a = prov.InstanceSpec("test", "local", self.cfg)
        b = prov.InstanceSpec("test", "local", self.cfg)
        self.assertEqual(a.bridge_port, b.bridge_port)
        self.assertGreaterEqual(a.bridge_port, self.cfg.bridge_port_base)


class ConfigTestCase(BaseFixture):
    def test_requires_image(self):
        with self.assertRaises(prov.ConfigError):
            prov.load_config({})

    def test_minimal_image_ok_and_defaults(self):
        cfg = prov.load_config({"PROVISIONER_IMAGE": IMAGE})
        self.assertEqual(cfg.env, "test")
        self.assertEqual(cfg.base_dir, "/srv")
        self.assertEqual(cfg.health_deadline, 180.0)
        self.assertEqual(cfg.min_free_gb, 10.0)
        self.assertEqual(cfg.bridge_port_base, 30000)

    def test_env_overrides(self):
        cfg = prov.load_config({
            "PROVISIONER_IMAGE": IMAGE,
            "PROVISIONER_ENV": " staging ",
            "PROVISIONER_MIN_FREE_GB": "42",
            "PROVISIONER_BRIDGE_PORT_BASE": "31000",
        })
        self.assertEqual(cfg.env, "staging")
        self.assertEqual(cfg.min_free_gb, 42.0)
        self.assertEqual(cfg.bridge_port_base, 31000)

    def test_bad_number_fails_closed(self):
        with self.assertRaises(prov.ConfigError):
            prov.load_config({"PROVISIONER_IMAGE": IMAGE, "PROVISIONER_TIMEOUT": "soon"})

    def test_invalid_instance_id_fails_closed(self):
        with self.assertRaises(prov.ConfigError):
            prov.load_config({"PROVISIONER_IMAGE": IMAGE, "PROVISIONER_INSTANCE_ID": "bad/id"})

    def test_json_file_and_env_precedence(self):
        path = os.path.join(self.tmp, "cfg.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"image": IMAGE, "min_free_gb": 3, "env": "fromfile"}, handle)
        cfg = prov.load_config({"PROVISIONER_CONFIG": path, "PROVISIONER_ENV": "fromenv"})
        self.assertEqual(cfg.image, IMAGE)
        self.assertEqual(cfg.min_free_gb, 3.0)
        self.assertEqual(cfg.env, "fromenv")

    def test_json_missing_file_fails_closed(self):
        with self.assertRaises(prov.ConfigError):
            prov.load_config({"PROVISIONER_CONFIG": os.path.join(self.tmp, "nope.json")})

    def test_http_health_ok_against_stub(self):
        self.assertTrue(prov.http_health_ok(self.stub.url))
        self.stub.server.ok = False
        self.assertFalse(prov.http_health_ok(self.stub.url))
        self.assertFalse(prov.http_health_ok("http://127.0.0.1:%d/health" % free_port()))


class StartTestCase(BaseFixture):
    def test_happy_path(self):
        status = self.provisioner().start("test", "solo", "0")
        spec = self.spec("0")
        self.assertTrue(status["running"])
        self.assertEqual(status["health"], "healthy")
        self.assertTrue(status["created"])
        self.assertEqual(status["container"], spec.container)
        self.assertEqual(status["instance"], "0")
        self.assertEqual(status["ports"]["bridge"], spec.bridge_port)
        # Genau EIN Container wurde erzeugt.
        self.assertEqual(len(self.run_calls()), 1)
        run = self.run_calls()[0]
        self.assertIn("--name", run)
        self.assertEqual(run[run.index("--name") + 1], spec.container)
        self.assertEqual(run[-1], IMAGE)
        # Run-scoped Verzeichnisse existieren.
        self.assertTrue(os.path.isdir(spec.game_dir))
        self.assertTrue(os.path.isdir(spec.sessions_dir))

    def test_idempotent_second_start(self):
        provisioner = self.provisioner()
        first = provisioner.start("test", "solo", "0")
        self.assertTrue(first["created"])
        runs_after_first = len(self.run_calls())
        second = provisioner.start("test", "solo", "0")
        self.assertFalse(second["created"])
        self.assertTrue(second["running"])
        self.assertEqual(second["health"], "healthy")
        # KEIN zweiter `docker run` — der zweite Start erzeugt keinen Container.
        self.assertEqual(len(self.run_calls()), runs_after_first)
        self.assertEqual(len(self.run_calls()), 1)

    def test_port_in_use_fails_loud(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", self.cfg.bridge_port_base))
        blocker.listen(1)
        try:
            with self.assertRaises(prov.ProvisionError):
                self.provisioner().start("test", "solo", "0")
        finally:
            blocker.close()
        self.assertEqual(self.run_calls(), [])

    def test_disk_full_fails_loud(self):
        with self.assertRaises(prov.ProvisionError):
            self.provisioner(min_free_gb=10 ** 9).start("test", "solo", "0")
        self.assertEqual(self.run_calls(), [])

    def test_image_missing_fails_loud(self):
        with self.assertRaises(prov.ProvisionError):
            self.provisioner(image="missing:latest").start("test", "solo", "0")
        self.assertEqual(self.run_calls(), [])

    def test_health_timeout_rolls_back(self):
        self.stub.server.ok = False
        spec = self.spec("0")
        with self.assertRaises(prov.ProvisionError):
            self.provisioner(health_deadline=0.0).start("test", "solo", "0")
        calls = self.docker_calls()
        self.assertIn(["rm", "-f", spec.container], calls)
        self.assertIn(["volume", "rm", spec.wine_volume], calls)
        self.assertIn(["volume", "rm", spec.saves_volume], calls)
        self.assertIn(["network", "rm", spec.network], calls)
        self.assertIsNone(self.docker.inspect_optional(spec.container))
        self.assertFalse(os.path.isdir(spec.run_root))

    def test_midway_failure_rolls_back_previous(self):
        os.environ["FAKE_DOCKER_FAIL"] = "volume create"
        spec = self.spec("0")
        with self.assertRaises(prov.DockerError):
            self.provisioner().start("test", "solo", "0")
        calls = self.docker_calls()
        # Netz war schon erzeugt -> muss zurueckgerollt sein.
        self.assertIn(["network", "rm", spec.network], calls)
        self.assertEqual(self.run_calls(), [])

    def test_start_forwards_mode_and_run_scope_labels(self):
        # Issue-Signatur ist start(env, mode): ``mode`` MUSS im Container ankommen
        # (RIFTBREAKER_MODE) und die Instanz run-scoped gelabelt sein.
        self.provisioner().start("test", "campaign", "0")
        run = self.run_calls()[0]
        self.assertEqual(run[run.index("-e") + 1], "RIFTBREAKER_MODE=campaign")
        self.assertIn("rb.provisioner.env=test", run)
        self.assertIn("rb.provisioner.instance=0", run)

    def test_idempotent_start_with_unhealthy_existing_fails_loud(self):
        # Laeuft der Container schon, aber /health nie ok -> laut, KEIN zweiter Container.
        spec = self.spec("0")
        self.docker.run_or_fail([
            "run", "-d", "--name", spec.container, "--network", spec.network,
            "-p", "127.0.0.1:%d:8080" % spec.bridge_port, IMAGE,
        ])
        self.stub.server.ok = False
        with self.assertRaises(prov.ProvisionError):
            self.provisioner(health_deadline=0.0).start("test", "solo", "0")
        self.assertEqual(len(self.run_calls()), 1)

    def test_existing_stopped_container_is_restarted_not_recreated(self):
        spec = self.spec("0")
        self.docker.run_or_fail([
            "run", "-d", "--name", spec.container, "--network", spec.network,
            "-p", "127.0.0.1:%d:8080" % spec.bridge_port, IMAGE,
        ])
        self.docker.stop(spec.container)
        status = self.provisioner().start("test", "solo", "0")
        self.assertFalse(status["created"])
        self.assertTrue(status["running"])
        self.assertEqual(len(self.run_calls()), 1)


class StopStatusTestCase(BaseFixture):
    def test_stop_removes_everything(self):
        provisioner = self.provisioner()
        provisioner.start("test", "solo", "0")
        spec = self.spec("0")
        result = provisioner.stop("0", "test")
        self.assertTrue(result["removed"]["container"])
        self.assertTrue(result["removed"]["network"])
        self.assertTrue(result["removed"]["volumes"])
        self.assertTrue(result["removed"]["dirs"])
        calls = self.docker_calls()
        self.assertIn(["rm", "-f", spec.container], calls)
        self.assertIn(["network", "rm", spec.network], calls)
        self.assertIn(["volume", "rm", spec.wine_volume], calls)
        self.assertIn(["volume", "rm", spec.saves_volume], calls)
        self.assertFalse(os.path.exists(spec.run_root))

    def test_stop_is_idempotent(self):
        provisioner = self.provisioner()
        provisioner.start("test", "solo", "0")
        provisioner.stop("0", "test")
        second = provisioner.stop("0", "test")
        self.assertEqual(
            second["removed"],
            {"container": False, "network": False, "volumes": False, "dirs": False},
        )

    def test_stop_on_never_started_instance(self):
        result = self.provisioner().stop("999", "test")
        self.assertEqual(
            result["removed"],
            {"container": False, "network": False, "volumes": False, "dirs": False},
        )

    def test_status_without_container(self):
        status = self.provisioner().status("123", "test")
        self.assertFalse(status["running"])
        self.assertEqual(status["health"], "unreachable")
        self.assertEqual(status["container"], "riftbreaker-dedicated-test-123")
        self.assertEqual(status["ports"]["bridge"], self.cfg.bridge_port_base + (123 % 20000))

    def test_status_with_running_container(self):
        provisioner = self.provisioner()
        provisioner.start("test", "solo", "0")
        status = provisioner.status("0", "test")
        self.assertTrue(status["running"])
        self.assertEqual(status["health"], "healthy")
        self.assertIn("8080/tcp", status["ports"]["docker"])

    def test_status_starting_when_health_not_ok(self):
        provisioner = self.provisioner()
        provisioner.start("test", "solo", "0")
        self.stub.server.ok = False
        status = provisioner.status("0", "test")
        self.assertTrue(status["running"])
        self.assertEqual(status["health"], "starting")


class CliTestCase(BaseFixture):
    def test_check_ok(self):
        os.environ["PROVISIONER_IMAGE"] = IMAGE
        try:
            self.assertEqual(prov.main(["--check"]), 0)
        finally:
            os.environ.pop("PROVISIONER_IMAGE", None)

    def test_check_fails_without_image(self):
        os.environ.pop("PROVISIONER_IMAGE", None)
        os.environ.pop("PROVISIONER_CONFIG", None)
        self.assertEqual(prov.main(["--check"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)