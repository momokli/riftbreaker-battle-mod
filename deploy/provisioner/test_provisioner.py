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
import stat
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
    entry = containers[name]
    running = entry["running"]
    print(json.dumps([{"Name": "/" + name,
                       "Config": {"Labels": entry.get("labels", {}),
                                  "Env": entry.get("env", [])},
                       "State": {"Status": "running" if running else "exited",
                                 "StartedAt": "2026-01-01T00:00:00Z"}}]))
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
    print("9001/tcp -> 127.0.0.1:%d" % containers[name].get("bridge_port", 0))
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
    name = args[args.index("--name") + 1] if "--name" in args else ""
    if fail("run") or fail("run %s" % name):
        sys.stderr.write("Error: cannot start container\n"); sys.exit(1)
    bridge_port = 0
    bridge_port = 0
    for i, a in enumerate(args):
        if a == "-p":
            parts = args[i + 1].split(":")
            if len(parts) >= 3 and parts[1].isdigit():
                bridge_port = int(parts[1])
    containers = load("containers.json", {})
    labels = {}
    for i, a in enumerate(args):
        if a == "--label" and i + 1 < len(args) and "=" in args[i + 1]:
            key, value = args[i + 1].split("=", 1)
            labels[key] = value
    env = []
    for i, a in enumerate(args):
        if a == "-e" and i + 1 < len(args):
            env.append(args[i + 1])
    containers[name] = {"running": True, "bridge_port": bridge_port,
                        "labels": labels, "env": env}
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
    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self.server.hits += 1
        if self.path == "/health":
            if self.server.ok:
                self._send(200, {"ok": True})
            else:
                self._send(503, {"ok": False, "state": "starting"})
        elif self.path == "/game_config":
            self._send(200, self.server.game_config)
        elif self.path == "/personas":
            self._send(200, self.server.personas or {})
        else:
            self._send(404, {"ok": False})

    def do_POST(self):  # noqa: N802
        self.server.hits += 1
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        if self.server.fail_seed:
            self._send(500, {"ok": False, "reason": "boom"})
            return
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except ValueError:
            payload = None
        self.server.posts.append((self.path, payload))
        if self.path == "/game_config":
            self.server.game_config = payload if isinstance(payload, dict) else {}
            self._send(200, {"ok": True})
        elif self.path == "/personas":
            self.server.personas = payload if isinstance(payload, dict) else {}
            self._send(200, {"ok": True})
        elif self.path == "/persona_active":
            self.server.persona_active = payload if isinstance(payload, dict) else {}
            self._send(200, {"ok": True})
        else:
            self._send(404, {"ok": False})

    def log_message(self, fmt, *args):  # noqa: A003
        pass


class HealthStub(object):
    """Lokaler HTTP-Stub auf Ephemeral-Port (Health + Bridge-Seeding #993)."""

    def __init__(self, ok=True):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
        self.server.daemon_threads = True
        self.server.ok = ok
        self.server.hits = 0
        # #993: Bridge-Seeding-Endpunkte (GET/POST /game_config, /personas,
        # /persona_active) — steuerbar fuer Merge-/Fehler-Tests.
        self.server.fail_seed = False
        self.server.game_config = {"warmup_s": 120}
        self.server.personas = {}
        self.server.persona_active = {}
        self.server.posts = []
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

        # Reale Image-Quellen (US2/R3): Preflight prueft deren Existenz, also
        # muessen die Fixtures echte Pfade bereitstellen statt zu brechen.
        self.sources = os.path.join(self.tmp, "sources")
        os.makedirs(self.sources)
        self.game_source = os.path.join(self.sources, "game")
        os.makedirs(self.game_source)
        self.rbtools_dir = os.path.join(self.sources, "rbtools")
        os.makedirs(self.rbtools_dir)
        self.config_cfg = os.path.join(self.sources, "config.cfg")
        with open(self.config_cfg, "w", encoding="utf-8") as handle:
            # Analog config.cfg.j2: die Quelle braucht eine server_name-Zeile
            # (Preflight/Staging pruefen sie, #970).
            handle.write('set server_name "RBBattle"\nset server_password "secret"\n# test config\n')
        # Persona-Quelle (#993): hermetische Datei mit der Persona `aggro`.
        self.personas_file = os.path.join(self.sources, "personas.json")
        with open(self.personas_file, "w", encoding="utf-8") as handle:
            json.dump({"personas": {"aggro": [[0, 0, 1, 0, 1, 0, 0, 0, 0]]}}, handle)

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
            attack_cycle_port_base=bridge + 1000,
            game_source=self.game_source,
            config_cfg=self.config_cfg,
            personas_file=self.personas_file,
            rbtools_dir=self.rbtools_dir,
            sessions_image=IMAGE,
            send_tailer_image=IMAGE,
            match_loop_image=IMAGE,
            attack_cycle_image=IMAGE,
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
        return prov.Provisioner(
            cfg,
            docker=self.docker,
            health_probe=lambda url: prov.http_health_ok(self.stub.url),
            bridge_url=lambda spec: "http://127.0.0.1:%d" % self.stub.port,
        )

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

    def test_inspect_optional_daemon_error_raises(self):
        # #969 B2: nur „nicht gefunden" -> None; ein echter Daemon-Fehler laut.
        self.docker._run = lambda args: (1, "", "Cannot connect to the Docker daemon")  # type: ignore[assignment]
        with self.assertRaises(prov.DockerError):
            self.docker.inspect_optional("whatever")


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

    def test_sidecar_names_are_deterministic(self):
        spec = self.spec("12345")
        self.assertEqual(spec.send_tailer_container, "rb-test-12345-send-tailer")
        self.assertEqual(spec.attack_cycle_container, "rb-test-12345-attack-cycle")
        self.assertEqual(spec.match_loop_container, "rb-test-12345-match-loop")
        self.assertEqual(spec.session_recorder_container, "rb-test-12345-session-recorder")
        self.assertEqual(spec.sidecar_containers(), [
            "rb-test-12345-session-recorder",
            "rb-test-12345-send-tailer",
            "rb-test-12345-match-loop",
            "rb-test-12345-attack-cycle",
        ])

    def test_sidecar_urls_mirror_compose_network(self):
        spec = self.spec("12345")
        self.assertEqual(
            spec.send_tailer_queue_url(),
            "http://rb-test-12345-attack-cycle:%d/queue_send" % spec.attack_cycle_container_port,
        )
        self.assertEqual(
            spec.match_loop_bridge_url(),
            "http://riftbreaker-dedicated-test-12345:%d" % spec.bridge_container_port,
        )
        self.assertEqual(spec.attack_cycle_bridge_url(), spec.match_loop_bridge_url())
        self.assertEqual(spec.attack_cycle_url(), "http://127.0.0.1:%d" % spec.attack_cycle_port)
        self.assertEqual(spec.session_recorder_sessions_dir(), spec.sessions_dir)

    def test_sidecar_port_never_collides_with_bridge_port(self):
        # Eigener Port-Base (#967): fuer dieselbe instance_id immer distintos.
        for instance_id in ("0", "1", "12345", "local", "parked-7"):
            spec = self.spec(instance_id)
            self.assertNotEqual(spec.attack_cycle_port, spec.bridge_port, instance_id)
            self.assertGreaterEqual(spec.attack_cycle_port, self.cfg.attack_cycle_port_base)

    def test_sidecar_fields_in_to_dict(self):
        spec = self.spec("12345")
        payload = spec.to_dict()
        self.assertEqual(payload["attack_cycle_port"], spec.attack_cycle_port)
        self.assertEqual(payload["attack_cycle_container"], spec.attack_cycle_container)
        self.assertEqual(payload["send_tailer_queue_url"], spec.send_tailer_queue_url())
        self.assertEqual(payload["attack_cycle_url"], spec.attack_cycle_url())
        self.assertEqual(payload["match_loop_bridge_url"], spec.match_loop_bridge_url())
        self.assertEqual(payload["session_recorder_sessions_dir"], spec.sessions_dir)

    def test_deterministic_repeat(self):
        self.assertEqual(self.spec("777").to_dict(), self.spec("777").to_dict())

    def test_health_url(self):
        spec = self.spec("5")
        self.assertEqual(spec.health_url(), "http://127.0.0.1:%d/health" % spec.bridge_port)

    def test_invalid_instance_id(self):
        for bad in ("bad/id", "with space", "x" * 41, "a;b"):
            with self.assertRaises(prov.ProvisionError, msg=bad):
                prov.InstanceSpec("test", bad, self.cfg)

    def test_env_placeholder_substitution(self):
        cfg = prov.Config(
            env="dev",
            image=IMAGE,
            base_dir=self.base_dir,
            bridge_container_port=9001,
        )
        spec = prov.InstanceSpec("dev", "0", cfg)
        self.assertEqual(spec.game_source, "/srv/rift-dev/game")
        self.assertEqual(
            spec.config_cfg,
            "/opt/rbmods/compose/rift-dev/riftbreaker/config/config.cfg",
        )
        self.assertEqual(spec.rbtools_dir, "/opt/rbmods/rbtools/dev")
        self.assertEqual(spec.bridge_container_port, 9001)
        payload = spec.to_dict()
        self.assertEqual(payload["game_source"], "/srv/rift-dev/game")
        self.assertEqual(payload["config_cfg"], spec.config_cfg)
        self.assertEqual(payload["rbtools_dir"], spec.rbtools_dir)
        self.assertEqual(payload["bridge_container_port"], 9001)

    def test_non_numeric_suffix_stable(self):
        a = prov.InstanceSpec("test", "local", self.cfg)
        b = prov.InstanceSpec("test", "local", self.cfg)
        self.assertEqual(a.bridge_port, b.bridge_port)
        self.assertGreaterEqual(a.bridge_port, self.cfg.bridge_port_base)

    # -- #970: instanz-eigene config.cfg ----------------------------------
    def test_config_staged_paths_deterministic(self):
        spec = self.spec("12345")
        run_root = os.path.join(self.base_dir, "rift-test-12345")
        self.assertEqual(spec.config_dir, os.path.join(run_root, "config"))
        self.assertEqual(
            spec.config_cfg_staged, os.path.join(run_root, "config", "config.cfg")
        )
        self.assertEqual(self.spec("12345").config_cfg_staged, spec.config_cfg_staged)

    def test_server_name_suffix_default_and_override(self):
        self.assertEqual(self.spec("0").server_name_suffix, "-test-0")
        cfg = prov.Config(**{**vars(self.cfg), "server_name_suffix": "-x-{instance_id}"})
        self.assertEqual(prov.InstanceSpec("test", "0", cfg).server_name_suffix, "-x-0")
        self.assertEqual(prov.InstanceSpec("test", "0", cfg).suffixed_server_name("RBBattle"), "RBBattle-x-0")

    def test_server_name_suffix_empty_opts_out(self):
        cfg = prov.Config(**{**vars(self.cfg), "server_name_suffix": ""})
        spec = prov.InstanceSpec("test", "0", cfg)
        self.assertEqual(spec.server_name_suffix, "")
        self.assertEqual(spec.suffixed_server_name("RBBattle"), "RBBattle")

    def test_config_cfg_is_source_unchanged(self):
        # Regression: `config_cfg` bleibt die geteilte Env-QUelle (additiv).
        spec = self.spec("0")
        self.assertEqual(spec.config_cfg, self.config_cfg)
        self.assertEqual(spec.config_cfg_source(), self.config_cfg)

    def test_to_dict_has_staged_fields(self):
        spec = self.spec("0")
        payload = spec.to_dict()
        self.assertEqual(payload["config_dir"], spec.config_dir)
        self.assertEqual(payload["config_cfg_staged"], spec.config_cfg_staged)
        self.assertEqual(payload["server_name_suffix"], spec.server_name_suffix)
        self.assertEqual(payload["config_cfg"], spec.config_cfg)


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

    def test_new_field_defaults(self):
        cfg = prov.load_config({"PROVISIONER_IMAGE": IMAGE})
        self.assertEqual(cfg.bridge_container_port, 9001)
        self.assertEqual(
            cfg.config_cfg,
            "/opt/rbmods/compose/rift-{env}/riftbreaker/config/config.cfg",
        )
        self.assertEqual(cfg.rbtools_dir, "/opt/rbmods/rbtools/{env}")
        self.assertEqual(cfg.game_source, "/srv/rift-{env}/game")

    def test_new_env_overrides(self):
        cfg = prov.load_config({
            "PROVISIONER_IMAGE": IMAGE,
            "PROVISIONER_BRIDGE_CONTAINER_PORT": "9100",
            "PROVISIONER_CONFIG_CFG": "/tmp/c/config.cfg",
            "PROVISIONER_RBTOOLS_DIR": "/tmp/c/rbtools",
            "PROVISIONER_GAME_SOURCE": "/tmp/c/game",
        })
        self.assertEqual(cfg.bridge_container_port, 9100)
        self.assertEqual(cfg.config_cfg, "/tmp/c/config.cfg")
        self.assertEqual(cfg.rbtools_dir, "/tmp/c/rbtools")
        self.assertEqual(cfg.game_source, "/tmp/c/game")

    def test_new_json_and_env_precedence(self):
        path = os.path.join(self.tmp, "cfg-new.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({
                "image": IMAGE,
                "bridge_container_port": 9002,
                "config_cfg": "/from/file.cfg",
                "game_source": "/from/file/game",
            }, handle)
        cfg = prov.load_config({
            "PROVISIONER_CONFIG": path,
            "PROVISIONER_CONFIG_CFG": "/from/env.cfg",
        })
        self.assertEqual(cfg.bridge_container_port, 9002)
        self.assertEqual(cfg.config_cfg, "/from/env.cfg")  # Env > Datei
        self.assertEqual(cfg.game_source, "/from/file/game")
        self.assertEqual(cfg.rbtools_dir, "/opt/rbmods/rbtools/{env}")

    def test_invalid_bridge_container_port_fails_closed(self):
        for bad in ("0", "70000", "notaport", "-1"):
            with self.assertRaises(prov.ConfigError, msg=bad):
                prov.load_config({
                    "PROVISIONER_IMAGE": IMAGE,
                    "PROVISIONER_BRIDGE_CONTAINER_PORT": bad,
                })

    def test_invalid_attack_cycle_container_port_fails_closed(self):
        for bad in ("0", "70000", "notaport", "-1"):
            with self.assertRaises(prov.ConfigError, msg=bad):
                prov.load_config({
                    "PROVISIONER_IMAGE": IMAGE,
                    "PROVISIONER_ATTACK_CYCLE_CONTAINER_PORT": bad,
                })

    def test_sidecar_defaults(self):
        cfg = prov.load_config({"PROVISIONER_IMAGE": IMAGE})
        self.assertEqual(cfg.attack_cycle_port_base, 31000)
        self.assertEqual(cfg.attack_cycle_container_port, 9102)
        self.assertEqual(cfg.attack_cycle_interval, 420)
        self.assertEqual(cfg.attack_cycle_difficulty_interval, 200)
        self.assertEqual(cfg.match_loop_restart_delay, 10)
        self.assertEqual(cfg.sessions_image, "python:3.12-slim")
        self.assertTrue(cfg.attack_cycle_script.endswith("attack-cycle/attack_cycle.py"))

    def test_sidecar_env_overrides(self):
        cfg = prov.load_config({
            "PROVISIONER_IMAGE": IMAGE,
            "PROVISIONER_ATTACK_CYCLE_PORT_BASE": "41000",
            "PROVISIONER_ATTACK_CYCLE_INTERVAL": "100",
            "PROVISIONER_SEND_TAILER_IMAGE": "tailer:1",
            "PROVISIONER_MATCH_LOOP_SCRIPT": "/tmp/match_loop.py",
        })
        self.assertEqual(cfg.attack_cycle_port_base, 41000)
        self.assertEqual(cfg.attack_cycle_interval, 100)
        self.assertEqual(cfg.send_tailer_image, "tailer:1")
        self.assertEqual(cfg.match_loop_script, "/tmp/match_loop.py")

    def test_http_health_ok_against_stub(self):
        self.assertTrue(prov.http_health_ok(self.stub.url))
        self.stub.server.ok = False
        self.assertFalse(prov.http_health_ok(self.stub.url))
        self.assertFalse(prov.http_health_ok("http://127.0.0.1:%d/health" % free_port()))

    # -- #970: server_name_suffix (Config) -------------------------------
    def test_server_name_suffix_default_is_template(self):
        cfg = prov.load_config({"PROVISIONER_IMAGE": IMAGE})
        self.assertEqual(cfg.server_name_suffix, "-{env}-{instance_id}")

    def test_server_name_suffix_env_override_trimmed(self):
        cfg = prov.load_config({
            "PROVISIONER_IMAGE": IMAGE,
            "PROVISIONER_SERVER_NAME_SUFFIX": " -x-{instance_id} ",
        })
        self.assertEqual(cfg.server_name_suffix, "-x-{instance_id}")

    def test_server_name_suffix_empty_env_opts_out(self):
        cfg = prov.load_config({
            "PROVISIONER_IMAGE": IMAGE,
            "PROVISIONER_SERVER_NAME_SUFFIX": "",
        })
        self.assertEqual(cfg.server_name_suffix, "")

    def test_server_name_suffix_json_key_accepted(self):
        path = os.path.join(self.tmp, "cfg-suffix.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"image": IMAGE, "server_name_suffix": "-j-{instance_id}"}, handle)
        cfg = prov.load_config({"PROVISIONER_CONFIG": path})
        self.assertEqual(cfg.server_name_suffix, "-j-{instance_id}")

    def test_server_name_suffix_rejects_quote_or_newline(self):
        for bad in ('-"{instance_id}"', "a\nb", 'x"y', "a b", "semi;colon"):
            with self.assertRaises(prov.ConfigError, msg=bad):
                prov.load_config({
                    "PROVISIONER_IMAGE": IMAGE,
                    "PROVISIONER_SERVER_NAME_SUFFIX": bad,
                })


class StartTestCase(BaseFixture):
    def test_happy_path(self):
        status = self.provisioner().start("test", "solo_self", "0")
        spec = self.spec("0")
        self.assertTrue(status["running"])
        self.assertEqual(status["health"], "healthy")
        self.assertTrue(status["created"])
        self.assertEqual(status["container"], spec.container)
        self.assertEqual(status["instance"], "0")
        self.assertEqual(status["ports"]["bridge"], spec.bridge_port)
        # Genau EIN Dedi-Container + vier Sidecars wurden erzeugt (#966).
        self.assertEqual(len(self.run_calls()), 5)
        run = self.run_calls()[0]
        self.assertIn("--name", run)
        self.assertEqual(run[run.index("--name") + 1], spec.container)
        self.assertEqual(run[-1], IMAGE)
        # Run-scoped Verzeichnisse existieren.
        self.assertTrue(os.path.isdir(spec.game_dir))
        self.assertTrue(os.path.isdir(spec.sessions_dir))
        # Instanz-eigene config.cfg wurde gestaged (#970).
        self.assertTrue(os.path.isfile(spec.config_cfg_staged))

    def test_start_creates_four_sidecars_in_network(self):
        status = self.provisioner().start("test", "solo_self", "0")
        spec = self.spec("0")
        runs = self.run_calls()
        self.assertEqual(len(runs), 5)
        by_name = {r[r.index("--name") + 1]: r for r in runs}
        for name in spec.sidecar_containers():
            self.assertIn(name, by_name)
            run = by_name[name]
            self.assertIn("--network", run)
            self.assertEqual(run[run.index("--network") + 1], spec.network)
            self.assertIn("rb.provisioner.env=test", run)
            self.assertIn("rb.provisioner.instance=0", run)
        # Nur der Attack-Cycle publiziert einen Host-Port (127.0.0.1).
        cycle_run = by_name[spec.attack_cycle_container]
        self.assertIn(
            "127.0.0.1:%d:%d" % (spec.attack_cycle_port, spec.attack_cycle_container_port),
            cycle_run,
        )
        self.assertIn("--control-port", cycle_run)
        self.assertEqual(
            cycle_run[cycle_run.index("--control-port") + 1],
            str(spec.attack_cycle_container_port),
        )
        # send-tailer/match-loop/session-recorder ohne Host-Port.
        for name in (spec.send_tailer_container, spec.match_loop_container,
                     spec.session_recorder_container):
            self.assertNotIn("-p", by_name[name])
        # send-tailer zeigt auf den Cycle im Netz, match-loop auf die Bridge.
        st = by_name[spec.send_tailer_container]
        self.assertIn(spec.send_tailer_queue_url(), st)
        ml = by_name[spec.match_loop_container]
        self.assertIn(spec.match_loop_bridge_url(), ml)
        # Sidecar-URLs sind auch im Status sichtbar (US3).
        self.assertEqual(status["ports"]["attack_cycle"], spec.attack_cycle_port)
        self.assertEqual(status["ports"]["attack_cycle_url"], spec.attack_cycle_url())

    def test_idempotent_second_start(self):
        provisioner = self.provisioner()
        first = provisioner.start("test", "solo_self", "0")
        self.assertTrue(first["created"])
        runs_after_first = len(self.run_calls())
        second = provisioner.start("test", "solo_self", "0")
        self.assertFalse(second["created"])
        self.assertTrue(second["running"])
        self.assertEqual(second["health"], "healthy")
        # KEIN zweiter `docker run` — der zweite Start erzeugt keinen Container.
        self.assertEqual(len(self.run_calls()), runs_after_first)
        self.assertEqual(len(self.run_calls()), 5)

    def test_port_in_use_fails_loud(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", self.cfg.bridge_port_base))
        blocker.listen(1)
        try:
            with self.assertRaises(prov.ProvisionError):
                self.provisioner().start("test", "solo_self", "0")
        finally:
            blocker.close()
        self.assertEqual(self.run_calls(), [])

    def test_disk_full_fails_loud(self):
        with self.assertRaises(prov.ProvisionError):
            self.provisioner(min_free_gb=10 ** 9).start("test", "solo_self", "0")
        self.assertEqual(self.run_calls(), [])

    def test_image_missing_fails_loud(self):
        with self.assertRaises(prov.ProvisionError):
            self.provisioner(image="missing:latest").start("test", "solo_self", "0")
        self.assertEqual(self.run_calls(), [])

    def test_health_timeout_rolls_back(self):
        self.stub.server.ok = False
        spec = self.spec("0")
        with self.assertRaises(prov.ProvisionError):
            self.provisioner(health_deadline=0.0).start("test", "solo_self", "0")
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
            self.provisioner().start("test", "solo_self", "0")
        calls = self.docker_calls()
        # Netz war schon erzeugt -> muss zurueckgerollt sein.
        self.assertIn(["network", "rm", spec.network], calls)
        self.assertEqual(self.run_calls(), [])

    def test_run_args_real_image_layout(self):
        self.provisioner().start("test", "solo_self", "0")
        spec = self.spec("0")
        self.assertEqual(len(self.run_calls()), 5)
        run = self.run_calls()[0]
        # Host-Port = spec.bridge_port, Container-Port = 9001.
        self.assertIn("127.0.0.1:%d:9001" % spec.bridge_port, run)
        self.assertIn("127.0.0.1::6321/udp", run)
        # Genau die fuenf realen Mounts (inkl. der :ro-Quellen).
        self.assertIn("%s:/opt/riftbreaker" % spec.game_source, run)
        self.assertIn("%s:/data/.wine" % spec.wine_volume, run)
        self.assertIn("%s:/data/saves" % spec.saves_volume, run)
        self.assertIn("%s:/data/config/config.cfg:ro" % spec.config_cfg_staged, run)
        # Die geteilte Env-Quelle wird NICHT mehr direkt gemountet (#970).
        self.assertNotIn("%s:/data/config/config.cfg:ro" % spec.config_cfg, run)
        self.assertTrue(os.path.isfile(spec.config_cfg_staged))
        self.assertIn("%s:/opt/rbtools:ro" % spec.rbtools_dir, run)
        # Bridge-Bind/-Port im Container + Rig-Sync-Env (sonst Crash vor bind()).
        self.assertIn("RBB_BRIDGE_BIND=0.0.0.0", run)
        self.assertIn("RBB_BRIDGE_PORT=9001", run)
        self.assertIn("WINEESYNC=0", run)
        self.assertIn("WINEFSYNC=0", run)
        # Parked-Instanz-Haertung (#968): Compose-Paritaet am `docker run`.
        self.assertIn("--restart", run)
        self.assertEqual(run[run.index("--restart") + 1], "unless-stopped")
        self.assertIn("--log-opt", run)
        self.assertIn("max-size=10m", run)
        self.assertIn("max-file=3", run)
        self.assertIn("LC_ALL=C.UTF-8", run)
        self.assertIn("LANG=C.UTF-8", run)
        self.assertIn("RBB_ENV=test", run)
        self.assertIn("RBB_REF=unknown", run)
        # Image bleibt das letzte Argument.
        self.assertEqual(run[-1], IMAGE)

    def test_missing_sources_fail_loud(self):
        for override in (
            {"game_source": os.path.join(self.tmp, "nope-game")},
            {"config_cfg": os.path.join(self.tmp, "nope.cfg")},
            {"rbtools_dir": os.path.join(self.tmp, "nope-rbtools")},
            {"attack_cycle_script": os.path.join(self.tmp, "nope-cycle.py")},
            {"send_tailer_script": os.path.join(self.tmp, "nope-send.py")},
        ):
            with self.assertRaises(prov.ProvisionError, msg=override):
                self.provisioner(**override).start("test", "solo_self", "0")
        self.assertEqual(self.run_calls(), [])

    def test_missing_sidecar_image_fails_loud_before_container(self):
        # Fail-loud VOR dem ersten Container (#966): kein halber Stack.
        with self.assertRaises(prov.ProvisionError):
            self.provisioner(sessions_image="missing:latest").start("test", "solo_self", "0")
        self.assertEqual(self.run_calls(), [])

    def test_sidecar_failure_rolls_back_container(self):
        # Bricht ein Sidecar-Start ab, wird der ganze Stack zurueckgerollt.
        spec = self.spec("0")
        os.environ["FAKE_DOCKER_FAIL"] = "run %s" % spec.match_loop_container
        with self.assertRaises(prov.DockerError):
            self.provisioner().start("test", "solo_self", "0")
        calls = self.docker_calls()
        self.assertIn(["rm", "-f", spec.container], calls)
        self.assertIn(["rm", "-f", spec.send_tailer_container], calls)
        self.assertIsNone(self.docker.inspect_optional(spec.container))
        self.assertFalse(os.path.isdir(spec.run_root))

    def test_start_forwards_mode_and_run_scope_labels(self):
        # Issue-Signatur ist start(env, mode): ``mode`` MUSS im Container ankommen
        # (RIFTBREAKER_MODE) und die Instanz run-scoped gelabelt sein.
        self.provisioner().start("test", "solo_self", "0")
        run = self.run_calls()[0]
        self.assertEqual(run[run.index("-e") + 1], "RIFTBREAKER_MODE=solo_self")
        self.assertIn("rb.provisioner.env=test", run)
        self.assertIn("rb.provisioner.instance=0", run)

    def test_idempotent_start_with_unhealthy_existing_fails_loud(self):
        # Laeuft der Container schon, aber /health nie ok -> laut, KEIN zweiter Container.
        spec = self.spec("0")
        self.docker.run_or_fail([
            "run", "-d", "--name", spec.container, "--network", spec.network,
            "-p", "127.0.0.1:%d:9001" % spec.bridge_port, IMAGE,
        ])
        self.stub.server.ok = False
        with self.assertRaises(prov.ProvisionError):
            self.provisioner(health_deadline=0.0).start("test", "solo_self", "0")
        self.assertEqual(len(self.run_calls()), 1)

    def test_existing_stopped_container_is_restarted_not_recreated(self):
        spec = self.spec("0")
        self.docker.run_or_fail([
            "run", "-d", "--name", spec.container, "--network", spec.network,
            "-p", "127.0.0.1:%d:9001" % spec.bridge_port, IMAGE,
        ])
        self.docker.stop(spec.container)
        status = self.provisioner().start("test", "solo_self", "0")
        self.assertFalse(status["created"])
        self.assertTrue(status["running"])
        self.assertEqual(len(self.run_calls()), 1)


class HardeningTestCase(BaseFixture):
    """#968: `docker run` spiegelt das Compose-Haertungs-Layout."""

    def test_run_args_hardening_defaults(self):
        self.provisioner().start("test", "solo_self", "0")
        run = self.run_calls()[0]
        self.assertEqual(run[run.index("--restart") + 1], "unless-stopped")
        # Beide log-opts vorhanden (Reihenfolge egal).
        opts = [run[i + 1] for i, arg in enumerate(run) if arg == "--log-opt"]
        self.assertIn("max-size=10m", opts)
        self.assertIn("max-file=3", opts)
        self.assertIn("-e", run)
        self.assertIn("LC_ALL=C.UTF-8", run)
        self.assertIn("LANG=C.UTF-8", run)
        self.assertIn("RBB_ENV=test", run)
        self.assertIn("RBB_REF=unknown", run)
        self.assertEqual(run[-1], IMAGE)

    def test_rbb_ref_from_config(self):
        self.provisioner(deploy_ref="v1.0.11-abc1234").start("test", "solo_self", "0")
        run = self.run_calls()[0]
        self.assertIn("RBB_REF=v1.0.11-abc1234", run)

    def test_rbb_env_follows_env_segment(self):
        self.provisioner(env="staging").start("staging", "solo_self", "0")
        run = self.run_calls()[0]
        self.assertIn("RBB_ENV=staging", run)
        self.assertNotIn("RBB_ENV=test", run)


class HardeningConfigTestCase(BaseFixture):
    """#968: Config-Feld `deploy_ref` (Default/Env/JSON)."""

    def test_deploy_ref_default_unknown(self):
        cfg = prov.load_config({"PROVISIONER_IMAGE": IMAGE})
        self.assertEqual(cfg.deploy_ref, "unknown")

    def test_deploy_ref_env_override_trimmed(self):
        cfg = prov.load_config({
            "PROVISIONER_IMAGE": IMAGE,
            "PROVISIONER_DEPLOY_REF": " sha-9 ",
        })
        self.assertEqual(cfg.deploy_ref, "sha-9")

    def test_deploy_ref_json_and_env_precedence(self):
        path = os.path.join(self.tmp, "cfg-ref.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"image": IMAGE, "deploy_ref": "fromfile"}, handle)
        cfg = prov.load_config({"PROVISIONER_CONFIG": path})
        self.assertEqual(cfg.deploy_ref, "fromfile")
        cfg_env = prov.load_config({
            "PROVISIONER_CONFIG": path,
            "PROVISIONER_DEPLOY_REF": "fromenv",
        })
        self.assertEqual(cfg_env.deploy_ref, "fromenv")  # Env > Datei

    def test_deploy_ref_json_key_accepted(self):
        # Regression: `deploy_ref` ist ein bekannter Key (kein ConfigError).
        path = os.path.join(self.tmp, "cfg-ref-known.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"image": IMAGE, "deploy_ref": "jsonref"}, handle)
        cfg = prov.load_config({"PROVISIONER_CONFIG": path})
        self.assertEqual(cfg.deploy_ref, "jsonref")


class StopStatusTestCase(BaseFixture):
    def test_stop_removes_everything(self):
        provisioner = self.provisioner()
        provisioner.start("test", "solo_self", "0")
        spec = self.spec("0")
        result = provisioner.stop("0", "test")
        self.assertTrue(result["removed"]["container"])
        self.assertTrue(result["removed"]["network"])
        self.assertTrue(result["removed"]["volumes"])
        self.assertTrue(result["removed"]["dirs"])
        calls = self.docker_calls()
        self.assertIn(["rm", "-f", spec.container], calls)
        for name in spec.sidecar_containers():
            self.assertIn(["rm", "-f", name], calls)
        self.assertIn(["network", "rm", spec.network], calls)
        self.assertIn(["volume", "rm", spec.wine_volume], calls)
        self.assertIn(["volume", "rm", spec.saves_volume], calls)
        self.assertFalse(os.path.exists(spec.run_root))

    def test_stop_is_idempotent(self):
        provisioner = self.provisioner()
        provisioner.start("test", "solo_self", "0")
        provisioner.stop("0", "test")
        second = provisioner.stop("0", "test")
        self.assertEqual(
            second["removed"],
            {"container": False, "sidecars": False, "network": False,
             "volumes": False, "dirs": False},
        )

    def test_stop_on_never_started_instance(self):
        result = self.provisioner().stop("999", "test")
        self.assertEqual(
            result["removed"],
            {"container": False, "sidecars": False, "network": False,
             "volumes": False, "dirs": False},
        )

    def test_status_without_container(self):
        status = self.provisioner().status("123", "test")
        self.assertFalse(status["running"])
        self.assertEqual(status["health"], "unreachable")
        self.assertEqual(status["container"], "riftbreaker-dedicated-test-123")
        self.assertEqual(status["ports"]["bridge"], self.cfg.bridge_port_base + (123 % 20000))

    def test_status_with_running_container(self):
        provisioner = self.provisioner()
        provisioner.start("test", "solo_self", "0")
        status = provisioner.status("0", "test")
        self.assertTrue(status["running"])
        self.assertEqual(status["health"], "healthy")
        self.assertIn("9001/tcp", status["ports"]["docker"])

    def test_status_exposes_gns_udp_endpoint(self):
        # Issue #929: der GNS-UDP-Host-Port (`6321/udp`-Mapping) wird first-class
        # als `ports["gns"]` surface (Ziel fuer den Relay-/solo-Pin).
        provisioner = self.provisioner()
        provisioner.start("test", "solo_self", "0")
        status = provisioner.status("0", "test")
        self.assertEqual(status["ports"]["gns"], "127.0.0.1:32768")
        self.assertIn("6321/udp", status["ports"]["docker"])

    def test_gns_endpoint_none_without_udp_mapping(self):
        # Fehlt das 6321/udp-Mapping, bleibt `gns` None — kein Crash.
        self.assertIsNone(prov.Provisioner._gns_from_mapping({"9001/tcp": "127.0.0.1:30001"}))
        self.assertIsNone(prov.Provisioner._gns_from_mapping({}))
        self.assertEqual(
            prov.Provisioner._gns_from_mapping({"6321/udp": "127.0.0.1:32768"}),
            "127.0.0.1:32768",
        )

    def test_status_starting_when_health_not_ok(self):
        provisioner = self.provisioner()
        provisioner.start("test", "solo_self", "0")
        self.stub.server.ok = False
        status = provisioner.status("0", "test")
        self.assertTrue(status["running"])
        self.assertEqual(status["health"], "starting")


class _StagingFailProvisioner(prov.Provisioner):
    """Test-Helfer: simulierter Schreibfehler genau im Staging-Schritt (#970)."""

    def _render_config(self, source_path, base_name, new_name):
        raise OSError("simulated staging failure")


class InstanceConfigStagingTestCase(BaseFixture):
    """#970: instanz-eigene config.cfg ableiten, mounten, restfrei stoppen."""

    def test_staging_rewrites_only_server_name(self):
        self.provisioner().start("test", "solo_self", "0")
        spec = self.spec("0")
        with open(spec.config_cfg, "r", encoding="utf-8") as handle:
            source = handle.read().splitlines()
        with open(spec.config_cfg_staged, "r", encoding="utf-8") as handle:
            staged = handle.read().splitlines()
        self.assertEqual(len(staged), len(source))
        self.assertEqual(staged[0], 'set server_name "RBBattle-test-0"')
        # Alle uebrigen Zeilen (inkl. Passwort) bleiben byte-identisch.
        self.assertEqual(staged[1:], source[1:])

    def test_staging_mode_is_0644(self):
        self.provisioner().start("test", "solo_self", "0")
        spec = self.spec("0")
        mode = stat.S_IMODE(os.stat(spec.config_cfg_staged).st_mode)
        self.assertEqual(mode, 0o644)

    def test_two_instances_get_distinct_names(self):
        provisioner = self.provisioner()
        provisioner.start("test", "solo_self", "0")
        provisioner.start("test", "solo_self", "1")
        spec_a, spec_b = self.spec("0"), self.spec("1")
        self.assertNotEqual(spec_a.config_cfg_staged, spec_b.config_cfg_staged)
        self.assertTrue(os.path.isfile(spec_a.config_cfg_staged))
        self.assertTrue(os.path.isfile(spec_b.config_cfg_staged))
        name_a = provisioner._read_server_name(spec_a.config_cfg_staged)
        name_b = provisioner._read_server_name(spec_b.config_cfg_staged)
        self.assertEqual(name_a, "RBBattle-test-0")
        self.assertEqual(name_b, "RBBattle-test-1")
        self.assertNotEqual(name_a, name_b)

    def test_source_without_server_name_fails_loud_before_container(self):
        bad = os.path.join(self.sources, "config_no_name.cfg")
        with open(bad, "w", encoding="utf-8") as handle:
            handle.write('# keine server_name-Zeile\nset server_password "secret"\n')
        with self.assertRaises(prov.ProvisionError):
            self.provisioner(config_cfg=bad).start("test", "solo_self", "0")
        self.assertEqual(self.run_calls(), [])

    def test_missing_config_source_fails_loud(self):
        with self.assertRaises(prov.ProvisionError):
            self.provisioner(
                config_cfg=os.path.join(self.tmp, "nope.cfg")
            ).start("test", "solo_self", "0")
        self.assertEqual(self.run_calls(), [])

    def test_staging_write_failure_rolls_back(self):
        cfg = prov.Config(**vars(self.cfg))
        provisioner = _StagingFailProvisioner(cfg, docker=self.docker)
        spec = self.spec("0")
        with self.assertRaises(OSError):
            provisioner.start("test", "solo_self", "0")
        self.assertEqual(self.run_calls(), [])
        self.assertFalse(os.path.exists(spec.config_cfg_staged))
        self.assertFalse(os.path.isdir(spec.run_root))

    def test_password_never_logged(self):
        with self.assertLogs(prov.LOG, level="INFO") as cm:
            status = self.provisioner().start("test", "solo_self", "0")
        self.assertNotIn("secret", "\n".join(cm.output))
        with open(self.log_file, "r", encoding="utf-8") as handle:
            docker_log = handle.read()
        self.assertNotIn("secret", docker_log)
        self.assertNotIn("secret", json.dumps(status))

    def test_staged_content_not_in_status(self):
        status = self.provisioner().start("test", "solo_self", "0")
        spec = self.spec("0")
        blob = json.dumps(status) + json.dumps(spec.to_dict())
        self.assertNotIn("secret", blob)
        self.assertNotIn("set server_name", blob)

    def test_stop_removes_staged_config_and_dir(self):
        provisioner = self.provisioner()
        provisioner.start("test", "solo_self", "0")
        spec = self.spec("0")
        self.assertTrue(os.path.isdir(spec.config_dir))
        self.assertTrue(os.path.isfile(spec.config_cfg_staged))
        result = provisioner.stop("0", "test")
        self.assertTrue(result["removed"]["dirs"])
        self.assertFalse(os.path.isdir(spec.config_dir))
        self.assertFalse(os.path.exists(spec.config_cfg_staged))
        self.assertFalse(os.path.exists(spec.run_root))


class ListInstancesTestCase(BaseFixture):
    """#969 US1: ``list_instances`` liefert die Container der EIGENEN env."""

    def _make_foreign(self):
        self.docker.run_or_fail([
            "run", "-d", "--name", "riftbreaker-dedicated-other-x",
            "--label", "rb.provisioner.env=other",
            "--label", "rb.provisioner.instance=x",
            IMAGE,
        ])

    def test_lists_own_env_with_fields(self):
        self.provisioner().start("test", "solo_self", "0")
        rows = self.provisioner().list_instances("test")
        by_container = {row["container"]: row for row in rows}
        self.assertIn("riftbreaker-dedicated-test-0", by_container)
        row = by_container["riftbreaker-dedicated-test-0"]
        self.assertEqual(row["env"], "test")
        self.assertEqual(row["instance"], "0")
        self.assertEqual(row["status"], "running")
        self.assertTrue(row["running"])
        self.assertEqual(row["started_at"], "2026-01-01T00:00:00Z")

    def test_foreign_env_excluded(self):
        self.provisioner().start("test", "solo_self", "0")
        self._make_foreign()
        rows = self.provisioner().list_instances("test")
        self.assertEqual({row["env"] for row in rows}, {"test"})
        self.assertNotIn(
            "riftbreaker-dedicated-other-x", {row["container"] for row in rows}
        )

    def test_env_defaults_to_cfg_env(self):
        self.provisioner().start("test", "solo_self", "0")
        rows = self.provisioner().list_instances()
        self.assertTrue(any(row["instance"] == "0" for row in rows))

    def test_ps_uses_env_label_filter(self):
        self.provisioner().list_instances("test")
        ps_calls = [c for c in self.docker_calls() if c and c[0] == "ps"]
        self.assertEqual(ps_calls[-1], ["ps", "-a", "--format", "{{.Names}}",
                                        "--filter", "label=rb.provisioner.env=test"])

    def test_missing_inspect_is_skipped_without_crash(self):
        self.provisioner().start("test", "solo_self", "0")
        original = self.docker.ps_all

        def ps_all(filter_label=None):
            return list(original(filter_label)) + ["ghost-container"]

        self.docker.ps_all = ps_all
        rows = self.provisioner().list_instances("test")
        self.assertNotIn("ghost-container", {row["container"] for row in rows})
        self.assertTrue(any(row["container"] == "riftbreaker-dedicated-test-0" for row in rows))

    def test_stopped_container_reports_exited(self):
        spec_name = "riftbreaker-dedicated-test-9"
        self.docker.run_or_fail([
            "run", "-d", "--name", spec_name,
            "--label", "rb.provisioner.env=test",
            "--label", "rb.provisioner.instance=9",
            IMAGE,
        ])
        self.docker.stop(spec_name)
        rows = {row["container"]: row for row in self.provisioner().list_instances("test")}
        self.assertEqual(rows[spec_name]["status"], "exited")
        self.assertFalse(rows[spec_name]["running"])

    def test_instance_falls_back_to_container_name(self):
        # Label `rb.provisioner.instance` fehlt -> Suffix aus dem Dedi-Namen.
        self.docker.run_or_fail([
            "run", "-d", "--name", "riftbreaker-dedicated-test-7",
            "--label", "rb.provisioner.env=test",
            IMAGE,
        ])
        rows = {row["container"]: row for row in self.provisioner().list_instances("test")}
        self.assertEqual(rows["riftbreaker-dedicated-test-7"]["instance"], "7")

    def test_ps_failure_raises_docker_error(self):
        # #969 B2: `docker ps`-Ausfall wird LAUT gemeldet, nicht als leere Liste.
        self.docker._run = lambda args: (1, "", "Cannot connect to the Docker daemon")  # type: ignore[assignment]
        with self.assertRaises(prov.DockerError):
            self.docker.ps_all("rb.provisioner.env=test")

    def test_list_instances_propagates_ps_failure(self):
        # #969 B2: der Provisioner darf den Discovery-Ausfall nicht schlucken.
        def boom(*_args, **_kwargs):
            raise prov.DockerError("docker ps explo")

        self.docker.ps_all = boom  # type: ignore[assignment]
        with self.assertRaises(prov.DockerError):
            self.provisioner().list_instances("test")

    def test_list_instances_propagates_inspect_daemon_error(self):
        # #969 B2: ein ECHTER inspect-Daemon-Fehler wird laut propagiert (nicht
        # wie ein fehlender Container still uebersprungen).
        self.provisioner().start("test", "solo_self", "0")

        def boom(_name):
            raise prov.DockerError("docker inspect explo")

        self.docker.inspect_optional = boom  # type: ignore[assignment]
        with self.assertRaises(prov.DockerError):
            self.provisioner().list_instances("test")


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


class ModeTestCase(BaseFixture):
    """#993: Modus-Schema, fail-loud, Args, Staging/Rollback, Bridge-Seeding."""

    # -- US1: Parser ------------------------------------------------------
    def test_parse_solo_self(self):
        sel = prov.parse_mode("solo_self")
        self.assertEqual(sel.mode, "solo_self")
        self.assertEqual(sel.kind, "self")
        self.assertIsNone(sel.persona)
        self.assertTrue(sel.send_yourself)
        self.assertFalse(sel.persona_on)

    def test_parse_solo_persona(self):
        sel = prov.parse_mode("solo_persona:aggro")
        self.assertEqual(sel.mode, "solo_persona:aggro")
        self.assertEqual(sel.kind, "persona")
        self.assertEqual(sel.persona, "aggro")
        self.assertFalse(sel.send_yourself)
        self.assertTrue(sel.persona_on)

    def test_parse_invalid_fails_loud(self):
        for bad in ("", "solo", "campaign", "solo_persona:", "solo_persona:a:b",
                    "solo_persona:bad name", None, "SOLO_SELF", " solo_self",
                    "solo_persona:" + "x" * 64):
            with self.assertRaises(prov.ModeError, msg=repr(bad)):
                prov.parse_mode(bad)

    def test_mode_error_is_provision_error(self):
        self.assertTrue(issubclass(prov.ModeError, prov.ProvisionError))

    # -- US1: fail-loud start + CLI --------------------------------------
    def test_start_invalid_mode_before_docker(self):
        for bad in ("solo", "campaign", "nope", ""):
            with self.assertRaises(prov.ProvisionError, msg=bad):
                self.provisioner().start("test", bad, "0")
        self.assertEqual(self.run_calls(), [])

    def test_cli_invalid_mode_exit_one(self):
        os.environ["PROVISIONER_IMAGE"] = IMAGE
        try:
            self.assertEqual(prov.main(["start", "--mode", "nope"]), 1)
        finally:
            os.environ.pop("PROVISIONER_IMAGE", None)

    # -- US2: Persona-Validierung ----------------------------------------
    def test_unknown_persona_fails_loud_before_container(self):
        with self.assertRaises(prov.ProvisionError):
            self.provisioner().start("test", "solo_persona:ghost", "0")
        self.assertEqual(self.run_calls(), [])

    def test_missing_personas_file_fails_loud(self):
        with self.assertRaises(prov.ProvisionError):
            self.provisioner(
                personas_file=os.path.join(self.tmp, "nope.json")
            ).start("test", "solo_persona:aggro", "0")
        self.assertEqual(self.run_calls(), [])

    def test_broken_personas_file_fails_loud(self):
        bad = os.path.join(self.sources, "personas-bad.json")
        with open(bad, "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        with self.assertRaises(prov.ProvisionError):
            self.provisioner(personas_file=bad).start("test", "solo_persona:aggro", "0")
        self.assertEqual(self.run_calls(), [])

    def test_wrong_shape_personas_file_fails_loud(self):
        bad = os.path.join(self.sources, "personas-shape.json")
        with open(bad, "w", encoding="utf-8") as handle:
            json.dump({"nope": True}, handle)
        with self.assertRaises(prov.ProvisionError):
            self.provisioner(personas_file=bad).start("test", "solo_persona:aggro", "0")
        self.assertEqual(self.run_calls(), [])

    def test_solo_self_does_not_read_personas_file(self):
        status = self.provisioner(
            personas_file=os.path.join(self.tmp, "nope.json")
        ).start("test", "solo_self", "0")
        self.assertTrue(status["running"])
        self.assertEqual(len(self.run_calls()), 5)

    # -- US3: Container + Attack-Cycle-Sidecar ---------------------------
    def _cycle_run(self):
        name = self.spec("0").attack_cycle_container
        for call in self.run_calls():
            if name in call:
                return call
        return None

    def test_solo_self_container_and_cycle_args(self):
        self.provisioner().start("test", "solo_self", "0")
        run = self.run_calls()[0]
        self.assertIn("RIFTBREAKER_MODE=solo_self", run)
        cycle = self._cycle_run()
        self.assertIn("--send-yourself", cycle)
        self.assertEqual(cycle[cycle.index("--send-yourself") + 1], "on")
        self.assertNotIn("--persona", cycle)
        self.assertNotIn("RBB_PERSONA_FILE=/data/personas.json", cycle)

    def test_persona_container_and_cycle_args(self):
        self.provisioner().start("test", "solo_persona:aggro", "0")
        spec = self.spec("0")
        run = self.run_calls()[0]
        self.assertIn("RIFTBREAKER_MODE=solo_persona:aggro", run)
        cycle = self._cycle_run()
        self.assertEqual(cycle[cycle.index("--send-yourself") + 1], "off")
        self.assertEqual(cycle[cycle.index("--persona") + 1], "aggro")
        self.assertEqual(cycle[cycle.index("--persona-file") + 1], "/data/personas.json")
        self.assertIn("RBB_PERSONA_FILE=/data/personas.json", cycle)
        self.assertIn("%s:/data/personas.json:ro" % spec.personas_staged, cycle)

    # -- US4: Staging + Rollback -----------------------------------------
    def test_persona_file_staged_0644(self):
        self.provisioner().start("test", "solo_persona:aggro", "0")
        spec = self.spec("0")
        self.assertTrue(os.path.isfile(spec.personas_staged))
        self.assertEqual(stat.S_IMODE(os.stat(spec.personas_staged).st_mode), 0o644)
        with open(spec.personas_staged, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
        self.assertIn("aggro", doc["personas"])

    def test_persona_mode_sidecar_failure_rolls_back_file(self):
        spec = self.spec("0")
        os.environ["FAKE_DOCKER_FAIL"] = "run %s" % spec.match_loop_container
        with self.assertRaises(prov.DockerError):
            self.provisioner().start("test", "solo_persona:aggro", "0")
        self.assertFalse(os.path.exists(spec.personas_staged))
        self.assertFalse(os.path.isdir(spec.run_root))

    def test_stop_removes_staged_personas(self):
        provisioner = self.provisioner()
        provisioner.start("test", "solo_persona:aggro", "0")
        spec = self.spec("0")
        self.assertTrue(os.path.isfile(spec.personas_staged))
        provisioner.stop("0", "test")
        self.assertFalse(os.path.exists(spec.personas_staged))

    # -- US5: Bridge-Seeding ---------------------------------------------
    def test_solo_self_seeds_bridge(self):
        self.stub.server.game_config = {"warmup_s": 120}
        self.provisioner().start("test", "solo_self", "0")
        posted = {path: body for path, body in self.stub.server.posts}
        gc = posted["/game_config"]
        self.assertIs(gc["send_yourself"], True)
        self.assertIs(gc["persona"], False)
        self.assertEqual(gc["mode"], "solo_self")
        self.assertEqual(gc["warmup_s"], 120)  # Merge erhaelt Fremdfelder
        self.assertEqual(self.stub.server.persona_active, {"name": ""})
        self.assertNotIn("/personas", posted)

    def test_persona_seeds_bridge(self):
        self.provisioner().start("test", "solo_persona:aggro", "0")
        posted = {path: body for path, body in self.stub.server.posts}
        gc = posted["/game_config"]
        self.assertIs(gc["send_yourself"], False)
        self.assertIs(gc["persona"], True)
        self.assertEqual(gc["mode"], "solo_persona:aggro")
        self.assertIn("aggro", posted["/personas"]["personas"])
        self.assertEqual(self.stub.server.persona_active, {"name": "aggro"})

    def test_seed_failure_rolls_back(self):
        self.stub.server.fail_seed = True
        spec = self.spec("0")
        with self.assertRaises(prov.ProvisionError):
            self.provisioner().start("test", "solo_self", "0")
        self.assertIsNone(self.docker.inspect_optional(spec.container))
        self.assertFalse(os.path.isdir(spec.run_root))

    # -- US6: CLI `--personas-file` (Regression: stiller No-Op) ----------
    def test_cli_personas_file_override_reaches_provisioner(self):
        # Regression #993: der CLI-Override muss VOR `Provisioner(cfg)` greifen,
        # sonst ist `--personas-file` ein stiller No-Op. Die Persona `cliaggro`
        # existiert NUR in der uebergebenen Datei -> rc==0 belegt, dass die
        # Validierung wirklich die CLI-Datei nutzt (Default wuerde scheitern).
        tmp = os.path.join(self.sources, "personas-cli.json")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump({"personas": {"cliaggro": [[[0] * 10]]}}, handle)
        captured = {}

        class _Spy(object):
            def __init__(self, cfg, *args, **kwargs):
                captured["cfg"] = cfg
                self.cfg = cfg

            def start(self, env, mode, instance_id):
                sel = prov.parse_mode(mode)
                original(self.cfg)._validate_persona(sel)
                return {"ok": True, "mode": sel.mode}

        original = prov.Provisioner
        prov.Provisioner = _Spy
        try:
            os.environ["PROVISIONER_IMAGE"] = IMAGE
            rc = prov.main(
                ["start", "--mode", "solo_persona:cliaggro", "--personas-file", tmp]
            )
        finally:
            prov.Provisioner = original
            os.environ.pop("PROVISIONER_IMAGE", None)
        self.assertEqual(rc, 0)
        self.assertEqual(captured["cfg"].personas_file, tmp)

    def test_cli_without_personas_file_keeps_default(self):
        # Ohne Flag bleibt der Default aus der Config unveraendert (RIFTBREAKER_*
        # / JSON-Konfiguration), kein versehentlicher Override.
        captured = {}
        default_file = prov.load_config(env={"PROVISIONER_IMAGE": IMAGE}).personas_file

        class _Spy(object):
            def __init__(self, cfg, *args, **kwargs):
                captured["cfg"] = cfg
                self.cfg = cfg

            def start(self, env, mode, instance_id):
                return {"ok": True}

        original = prov.Provisioner
        prov.Provisioner = _Spy
        try:
            os.environ["PROVISIONER_IMAGE"] = IMAGE
            rc = prov.main(["start", "--mode", "solo_self"])
        finally:
            prov.Provisioner = original
            os.environ.pop("PROVISIONER_IMAGE", None)
        self.assertEqual(rc, 0)
        self.assertEqual(captured["cfg"].personas_file, default_file)

    # -- Grenzfall: vorhandener Container mit anderem Modus --------------
    def test_existing_container_mode_mismatch_fails_loud(self):
        provisioner = self.provisioner()
        provisioner.start("test", "solo_self", "0")
        runs_before = len(self.run_calls())
        posts_before = len(self.stub.server.posts)
        with self.assertRaises(prov.ProvisionError):
            provisioner.start("test", "solo_persona:aggro", "0")
        # Kein stilles Umschalten: kein Re-Create, kein Re-Seed.
        self.assertEqual(len(self.run_calls()), runs_before)
        self.assertEqual(len(self.stub.server.posts), posts_before)

    def test_existing_container_same_mode_is_idempotent(self):
        provisioner = self.provisioner()
        provisioner.start("test", "solo_self", "0")
        runs_before = len(self.run_calls())
        second = provisioner.start("test", "solo_self", "0")
        self.assertFalse(second["created"])
        self.assertEqual(len(self.run_calls()), runs_before)

    def test_existing_container_without_mode_env_skips_check(self):
        # Legacy/extern erzeugter Container ohne RIFTBREAKER_MODE: nichts zu
        # vergleichen -> Idempotenz bleibt (kein Fehlalarm).
        provisioner = self.provisioner()
        provisioner.start("test", "solo_self", "0")
        real = self.docker.inspect_optional

        def stripped(name):
            data = real(name)
            if data is not None:
                data = dict(data)
                cfg = dict(data.get("Config") or {})
                cfg.pop("Env", None)
                data["Config"] = cfg
            return data

        self.docker.inspect_optional = stripped
        status = provisioner.start("test", "solo_persona:aggro", "0")
        self.assertFalse(status["created"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
