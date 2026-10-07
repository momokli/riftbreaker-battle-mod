#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermetische Tests fuer deploy/provisioner/provisioner_service.py (#1083).

Kein Docker, kein Netz, kein Spiel: die Fachlogik wird durch
:class:`FakeProvisioner` ersetzt und der Dienst auf ``127.0.0.1:0`` (Ephemeral-
Port) gebunden. Geprueft wird genau der HTTP-Vertrag, den die Queue
(``queue_flow.ProvisionerClient``) spricht.

Aufruf:

    cd deploy/provisioner && python3 -m unittest test_provisioner_service -v
    # oder aus dem Repo-Root:
    python3 -m unittest deploy/provisioner/test_provisioner_service.py -v
"""

import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import provisioner_service as svc  # noqa: E402


class FakeProvisioner(object):
    """Ersetzt die echte Fachlogik: recordet jede Signatur, liefert Dicts."""

    def __init__(self, start_error=None, stop_error=None):
        self.calls = []
        self.start_error = start_error
        self.stop_error = stop_error

    def start(self, env=None, mode="solo_self", instance_id=None, world=None):
        self.calls.append(("start", {"env": env, "mode": mode, "instance_id": instance_id, "world": world}))
        if self.start_error is not None:
            raise self.start_error
        return {
            "instance": instance_id,
            "container": "rbbattle-%s" % instance_id,
            "running": True,
            "created": True,
            "health": "healthy",
            "ports": {"gns": "127.0.0.1:40000", "bridge": 30000},
        }

    def stop(self, instance_id=None, env=None):
        self.calls.append(("stop", {"instance_id": instance_id, "env": env}))
        if self.stop_error is not None:
            raise self.stop_error
        return {"instance": instance_id, "removed": {"container": True, "sidecars": False}}

    def status(self, instance_id=None, env=None):
        self.calls.append(("status", {"instance_id": instance_id, "env": env}))
        return {
            "running": True,
            "health": "healthy",
            "ports": {"gns": "127.0.0.1:40000"},
            "container": "rbbattle-%s" % instance_id,
        }


class _ServerCase(unittest.TestCase):
    """Basis: Dienst mit FakeProvisioner auf 127.0.0.1:0 starten/abraeumen."""

    TOKEN = ""

    def setUp(self):
        self.fake = FakeProvisioner()
        self.config = svc.ProvisionerServiceConfig(env="dev", bind="127.0.0.1", port=0, token=self.TOKEN)
        self.httpd = svc.build_server(self.config, self.fake)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def _request(self, method, path, payload=None, token=None):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = "Bearer %s" % token
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            with exc:
                body = exc.read().decode("utf-8")
            return exc.code, (json.loads(body) if body.strip() else {})


class Routes(_ServerCase):
    def test_health(self):
        status, body = self._request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "env": "dev"})

    def test_start_passes_through_and_returns_ports(self):
        status, body = self._request(
            "POST",
            "/start",
            {"env": "dev", "mode": "solo_persona:aggro", "instance_id": "queue-1-a", "world": "A"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        # Der GNS-Endpoint, den die Queue defensiv liest, muss durchgereicht werden.
        self.assertEqual(body["ports"]["gns"], "127.0.0.1:40000")
        self.assertEqual(body["instance"], "queue-1-a")
        self.assertEqual(
            self.fake.calls[-1],
            ("start", {"env": "dev", "mode": "solo_persona:aggro", "instance_id": "queue-1-a", "world": "A"}),
        )

    def test_start_defaults_mode_and_missing_world(self):
        status, body = self._request("POST", "/start", {"instance_id": "queue-9"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        call = self.fake.calls[-1]
        self.assertEqual(call[0], "start")
        self.assertEqual(call[1]["mode"], "solo_self")  # Default ohne mode im Body
        self.assertIsNone(call[1]["world"])  # nicht gesetzt -> None

    def test_stop(self):
        status, body = self._request("POST", "/stop", {"env": "dev", "instance_id": "queue-1-a"})
        self.assertEqual(status, 200)
        self.assertEqual(body["instance"], "queue-1-a")
        self.assertEqual(body["removed"]["container"], True)
        self.assertEqual(self.fake.calls[-1], ("stop", {"instance_id": "queue-1-a", "env": "dev"}))

    def test_status_query_params(self):
        status, body = self._request("GET", "/status?instance_id=queue-1-a&env=dev")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["health"], "healthy")
        self.assertEqual(self.fake.calls[-1], ("status", {"instance_id": "queue-1-a", "env": "dev"}))

    def test_status_without_params(self):
        status, body = self._request("GET", "/status")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(self.fake.calls[-1], ("status", {"instance_id": None, "env": None}))


class Auth(_ServerCase):
    TOKEN = "s3cret"

    def test_health_without_bearer_401(self):
        status, body = self._request("GET", "/health")
        self.assertEqual(status, 401)
        self.assertEqual(body["reason"], "unauthorized")

    def test_start_without_bearer_401(self):
        status, body = self._request("POST", "/start", {"instance_id": "x"})
        self.assertEqual(status, 401)
        self.assertEqual(body["reason"], "unauthorized")
        self.assertEqual(self.fake.calls, [])  # nicht provisioniert

    def test_wrong_bearer_401(self):
        status, _ = self._request("POST", "/start", {"instance_id": "x"}, token="nope")
        self.assertEqual(status, 401)
        self.assertEqual(self.fake.calls, [])

    def test_correct_bearer_200(self):
        status, body = self._request("POST", "/start", {"instance_id": "x"}, token="s3cret")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])


class Errors(_ServerCase):
    def test_unknown_route_404(self):
        status, body = self._request("GET", "/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["reason"], "not_found")

    def test_wrong_method_405(self):
        status, body = self._request("GET", "/start")
        self.assertEqual(status, 405)
        self.assertEqual(body["reason"], "method_not_allowed")

    def test_bad_json_400(self):
        url = "http://127.0.0.1:%d/start" % self.port
        request = urllib.request.Request(
            url, data=b"{not json", headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            urllib.request.urlopen(request, timeout=5)
            self.fail("erwartet HTTPError 400")
        except urllib.error.HTTPError as exc:
            with exc:
                body = json.loads(exc.read().decode("utf-8"))
            self.assertEqual(exc.code, 400)
            self.assertEqual(body["reason"], "bad_request")

    def test_provision_error_500(self):
        self.fake.start_error = svc.ProvisionError("kein Image / Preflight fehlgeschlagen")
        status, body = self._request("POST", "/start", {"instance_id": "x"})
        self.assertEqual(status, 500)
        self.assertFalse(body["ok"])
        self.assertEqual(body["reason"], "provision_failed")

    def test_docker_error_503(self):
        self.fake.stop_error = svc.DockerError("docker daemon nicht erreichbar")
        status, body = self._request("POST", "/stop", {"instance_id": "x"})
        self.assertEqual(status, 503)
        self.assertFalse(body["ok"])
        self.assertEqual(body["reason"], "docker_error")


class Check(unittest.TestCase):
    """``--check`` validiert die Konfiguration und bricht fail-closed ab (rc 2)."""

    def test_check_ok_rc0(self):
        with mock.patch.dict(os.environ, {"PROVISIONER_IMAGE": "test:latest"}, clear=True):
            self.assertEqual(svc.main(["--check"]), 0)

    def test_check_missing_image_rc2(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(svc.main(["--check"]), 2)

    def test_check_bad_port_rc2(self):
        with mock.patch.dict(
            os.environ,
            {"PROVISIONER_IMAGE": "test:latest", "PROVISIONER_PORT": "nope"},
            clear=True,
        ):
            self.assertEqual(svc.main(["--check"]), 2)


if __name__ == "__main__":
    unittest.main()
