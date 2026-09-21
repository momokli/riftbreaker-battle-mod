#!/usr/bin/env python3
"""Unit-/Integrationstests fuer rbrouter.py (Spike #825) — hermetisch, kein Netz nach aussen.

Der Kerntest faehrt den Router auf 127.0.0.1 gegen ein Fake-Backend auf
127.0.0.1 und prueft beide Richtungen inkl. der entscheidenden Eigenschaft:
die Antwort kommt beim Client **vom Eingangsport** an (nicht vom Backend).

    python3 -m unittest test_rbrouter -v
"""

import os
import socket
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rbrouter import Router, parse_endpoint, parse_names, read_names_file, read_target_file, scan_names  # noqa: E402


class ParseEndpointTests(unittest.TestCase):
    def test_ipv4_mit_port(self):
        self.assertEqual(parse_endpoint("65.21.27.234:6322"), ("65.21.27.234", 6322))

    def test_whitespace_wird_getrimmt(self):
        self.assertEqual(parse_endpoint("  10.0.0.1:1  "), ("10.0.0.1", 1))

    def test_leer_oder_ohne_port_ist_fehler(self):
        for bad in ("", "   ", "65.21.27.234", None):
            with self.assertRaises(ValueError):
                parse_endpoint(bad)

    def test_port_muss_zahl_im_bereich_sein(self):
        for bad in ("1.2.3.4:abc", "1.2.3.4:", "1.2.3.4:0", "1.2.3.4:65536", "1.2.3.4:-1"):
            with self.assertRaises(ValueError):
                parse_endpoint(bad)


class TargetFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="rbrouter-test-")
        self.path = os.path.join(self.dir, "target")

    def _write(self, text):
        with open(self.path, "w") as handle:
            handle.write(text)

    def test_fehlende_datei_gibt_fallback_ohne_warnung(self):
        fallback = ("127.0.0.1", 6322)
        self.assertEqual(read_target_file(self.path, fallback), (fallback, None))

    def test_leere_datei_gibt_fallback(self):
        self._write("\n")
        fallback = ("127.0.0.1", 6322)
        self.assertEqual(read_target_file(self.path, fallback), (fallback, None))

    def test_gueltige_datei_gewinnt(self):
        self._write("65.21.27.234:6321\n")
        target, warning = read_target_file(self.path, ("127.0.0.1", 6322))
        self.assertEqual(target, ("65.21.27.234", 6321))
        self.assertIsNone(warning)

    def test_kaputte_datei_gibt_fallback_mit_warnung(self):
        self._write("quatsch\n")
        fallback = ("127.0.0.1", 6322)
        target, warning = read_target_file(self.path, fallback)
        self.assertEqual(target, fallback)
        self.assertIn("unbrauchbar", warning)


class RelayLoopbackTests(unittest.TestCase):
    """Echter Datagramm-Durchlauf: Client -> Router -> Backend -> Router -> Client."""

    def setUp(self):
        self.backend = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.backend.bind(("127.0.0.1", 0))
        self.backend.settimeout(3.0)
        self.router = Router(("127.0.0.1", 0), target=self.backend.getsockname(), report_interval=0, poll_timeout=0.02)
        self.router.start()
        self.thread = threading.Thread(target=self.router.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        self.entry = self.router.in_sock.getsockname()
        self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.client.bind(("127.0.0.1", 0))
        self.client.settimeout(3.0)

    def tearDown(self):
        self.router.stop()
        self.thread.join(2.0)
        self.client.close()
        self.backend.close()

    def test_beide_richtungen_und_antwort_kommt_vom_eingangsport(self):
        self.client.sendto(b"hello-riftbreaker", self.entry)
        data, backend_peer = self.backend.recvfrom(4096)
        self.assertEqual(data, b"hello-riftbreaker")
        # Der Relay hat ein eigenes Socket -> Backend sieht eine andere Quelladresse.
        self.assertNotEqual(backend_peer, self.entry)

        self.backend.sendto(b"welcome", backend_peer)
        answer, peer = self.client.recvfrom(4096)
        self.assertEqual(answer, b"welcome")
        # Kern-Eigenschaft: die Antwort kommt vom Eingang, nicht vom Backend.
        self.assertEqual(peer, self.entry)

    def test_zweiter_flow_bekommt_eigenes_backend_socket(self):
        other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        other.bind(("127.0.0.1", 0))
        other.settimeout(3.0)
        try:
            self.client.sendto(b"eins", self.entry)
            _, peer_one = self.backend.recvfrom(4096)
            other.sendto(b"zwei", self.entry)
            _, peer_two = self.backend.recvfrom(4096)
            self.assertNotEqual(peer_one, peer_two)
            self.assertEqual(len(self.router.flows), 2)
        finally:
            other.close()

    def test_zahlreiche_pakete_in_folge_bleiben_in_ordnung(self):
        total = 200
        for index in range(total):
            self.client.sendto(b"pkt-%04d" % index, self.entry)
        seen = []
        while len(seen) < total:
            data, _ = self.backend.recvfrom(4096)
            seen.append(data)
        self.assertEqual(seen, [b"pkt-%04d" % index for index in range(total)])

    def test_idle_flow_wird_aufgeraeumt(self):
        self.router.idle_timeout = 0.05
        self.client.sendto(b"kurz", self.entry)
        self.backend.recvfrom(4096)
        deadline = time.time() + 3.0
        while (not self.router.flows or self.router.stats_flows != 1) and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.router.flows, {})
        self.assertEqual(self.router.stats_flows, 1)
        self.assertEqual(self.router.stats_c2s_pkts, 1)


class TargetSwitchTests(unittest.TestCase):
    """Zielwechsel ohne Neustart: nur *neue* Flows folgen der Datei."""

    def test_neuer_flow_folgt_der_datei(self):
        first = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        first.bind(("127.0.0.1", 0))
        first.settimeout(3.0)
        second = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        second.bind(("127.0.0.1", 0))
        second.settimeout(3.0)
        handle, path = tempfile.mkstemp(prefix="rbrouter-target-")
        os.close(handle)
        try:
            with open(path, "w") as out:
                out.write("127.0.0.1:%d\n" % first.getsockname()[1])
            router = Router(("127.0.0.1", 0), target_file=path, report_interval=0, poll_timeout=0.02)
            router.start()
            thread = threading.Thread(target=router.serve_forever)
            thread.daemon = True
            thread.start()
            entry = router.in_sock.getsockname()
            client_a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client_a.bind(("127.0.0.1", 0))
            client_a.settimeout(3.0)
            client_b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client_b.bind(("127.0.0.1", 0))
            client_b.settimeout(3.0)
            try:
                client_a.sendto(b"a", entry)
                self.assertEqual(first.recvfrom(4096)[0], b"a")

                with open(path, "w") as out:
                    out.write("127.0.0.1:%d\n" % second.getsockname()[1])

                client_b.sendto(b"b", entry)
                self.assertEqual(second.recvfrom(4096)[0], b"b")
                # Part-1-Semantik: der Zielwechsel verwirft den laufenden Flow A — der
                # Client-Port bleibt gleich, also geht auch A danach ans neue Backend.
                client_a.sendto(b"a2", entry)
                self.assertEqual(second.recvfrom(4096)[0], b"a2")
                self.assertEqual(router.stats_flows, 1)
            finally:
                client_a.close()
                client_b.close()
                router.stop()
                thread.join(2.0)
        finally:
            os.unlink(path)
            first.close()
            second.close()

    def test_keep_flows_on_switch_laesst_laufenden_flow_in_ruhe(self):
        first = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        first.bind(("127.0.0.1", 0))
        first.settimeout(3.0)
        second = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        second.bind(("127.0.0.1", 0))
        second.settimeout(3.0)
        handle, path = tempfile.mkstemp(prefix="rbrouter-target-keep-")
        os.close(handle)
        try:
            with open(path, "w") as out:
                out.write("127.0.0.1:%d\n" % first.getsockname()[1])
            router = Router(
                ("127.0.0.1", 0),
                target_file=path,
                report_interval=0,
                poll_timeout=0.02,
                drop_flows_on_switch=False,
            )
            router.start()
            thread = threading.Thread(target=router.serve_forever)
            thread.daemon = True
            thread.start()
            entry = router.in_sock.getsockname()
            client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client.bind(("127.0.0.1", 0))
            client.settimeout(3.0)
            try:
                client.sendto(b"a", entry)
                self.assertEqual(first.recvfrom(4096)[0], b"a")
                with open(path, "w") as out:
                    out.write("127.0.0.1:%d\n" % second.getsockname()[1])
                client.sendto(b"a2", entry)
                self.assertEqual(first.recvfrom(4096)[0], b"a2")
                self.assertEqual(router.stats_flows, 0)
            finally:
                client.close()
                router.stop()
                thread.join(2.0)
        finally:
            os.unlink(path)
            first.close()
            second.close()


class NamesFileTests(unittest.TestCase):
    def test_parse_names_gueltig(self):
        names = parse_names(
            "# kommentar\nmomod=65.21.27.234:6321\n\nmomop = 65.21.27.234:6322  # prod\nmomos=65.21.27.234:6323\n"
        )
        self.assertEqual(
            names,
            {
                "momod": ("65.21.27.234", 6321),
                "momop": ("65.21.27.234", 6322),
                "momos": ("65.21.27.234", 6323),
            },
        )

    def test_parse_names_ignoriert_kaputte_zeilen_und_kurze_namen(self):
        names = parse_names("ohne-gleich\nab=1.2.3.4:0\nab=1.2.3.4:abc\nxy=1.2.3.4:6321\nokay=1.2.3.4:6321\n")
        self.assertEqual(names, {"okay": ("1.2.3.4", 6321)})

    def test_fehlende_namensdatei_gibt_none_statt_leerem_dict(self):
        names, warning = read_names_file("/definitiv/nicht/da/rbrouter-names")
        self.assertIsNone(names)
        self.assertIsNone(warning)

    def test_scan_names_laengster_treffer_gewinnt(self):
        names = {
            "momo": ("1.1.1.1", 6321),
            "momod": ("2.2.2.2", 6322),
            "momop": ("3.3.3.3", 6323),
        }
        # Nutzlast enthaelt die Namen wie im echten Paket: Laengenbyte + ASCII.
        self.assertEqual(scan_names(b"\x00\x04momo\x02x", {"momo": ("1.1.1.1", 6321)}), "momo")
        self.assertEqual(scan_names(b"...momod...", names), "momod")
        self.assertEqual(scan_names(b"...momop...", names), "momop")
        self.assertIsNone(scan_names(b"...nix...", names))


class NameRoutingTests(unittest.TestCase):
    """Namens-Routing end-to-end ueber Loopback (gehaltene Pakete, Fallback, Re-Route)."""

    def setUp(self):
        self.fallback = self._backend()
        self.mapped = self._backend()
        self.router = Router(
            ("127.0.0.1", 0),
            target=self.fallback.getsockname(),
            names=True,
            names_file="/definitiv/nicht/da/rbrouter-names",
            hold_ms=0.0,
            report_interval=0,
            poll_timeout=0.02,
        )
        self.router.names = {"momop": self.mapped.getsockname()}
        self.router.start()
        self.thread = threading.Thread(target=self.router.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        self.entry = self.router.in_sock.getsockname()
        self.client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.client.bind(("127.0.0.1", 0))
        self.client.settimeout(3.0)

    def tearDown(self):
        self.router.stop()
        self.thread.join(2.0)
        self.client.close()
        self.fallback.close()
        self.mapped.close()

    def _backend(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sock.settimeout(3.0)
        return sock

    def test_name_im_ersten_paket_routet_aufs_gemappte_backend(self):
        self.client.sendto(b"\x00\x05momop-payload", self.entry)
        data, _ = self.mapped.recvfrom(4096)
        self.assertEqual(data, b"\x00\x05momop-payload")
        self.assertEqual(self.router.flows[list(self.router.flows)[0]].name, "momop")
        self.fallback.settimeout(0.3)
        with self.assertRaises(socket.timeout):
            self.fallback.recvfrom(4096)

    def test_ohne_name_geht_sofort_auf_fallback(self):
        self.client.sendto(b"kein-name-hier", self.entry)
        data, _ = self.fallback.recvfrom(4096)
        self.assertEqual(data, b"kein-name-hier")

    def test_name_gelernt_und_naechster_flow_routet_sofort(self):
        self.client.sendto(b"erst-nix", self.entry)
        self.assertEqual(self.fallback.recvfrom(4096)[0], b"erst-nix")
        # Name kommt spaeter -> Flow wird verworfen, Ziel wird gemerkt (src-IP).
        self.client.sendto(b"jetzt-momop-drin", self.entry)
        deadline = time.time() + 3.0
        while not self.router.learned and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.router.learned["127.0.0.1"][0], self.mapped.getsockname())
        self.assertEqual(self.router.flows, {})
        # Naechster Flow (gleiche Quell-IP) landet direkt auf dem gemappten Backend.
        self.client.sendto(b"wieder-da", self.entry)
        self.assertEqual(self.mapped.recvfrom(4096)[0], b"wieder-da")
        self.fallback.settimeout(0.3)
        with self.assertRaises(socket.timeout):
            self.fallback.recvfrom(4096)


if __name__ == "__main__":
    unittest.main()
