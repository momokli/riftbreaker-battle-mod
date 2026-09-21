#!/usr/bin/env python3
"""Hermetische Tests fuer pcap_flow.py (Spike #831).

Baut synthetische pcaps im tmpdir — kein Netz, kein Spiel, keine echten
Mitschnitte noetig.
"""

import os
import struct
import tempfile
import unittest

import pcap_flow

ETH_DST = b"\x00" * 6
ETH_SRC = b"\x11" * 6


def udp_packet(src: str, sport: int, dst: str, dport: int, payload: bytes) -> bytes:
    """IPv4/UDP-Paket (ohne Link-Layer) bauen."""
    udp = struct.pack(">HHHH", sport, dport, 8 + len(payload), 0) + payload
    src_b = bytes(int(p) for p in src.split("."))
    dst_b = bytes(int(p) for p in dst.split("."))
    total = 20 + len(udp)
    ip = struct.pack(
        ">BBHHHBBH", 0x45, 0, total, 0, 0, 64, 17, 0
    ) + src_b + dst_b
    return ip + udp


def ethernet(ip_packet: bytes) -> bytes:
    return ETH_DST + ETH_SRC + struct.pack(">H", 0x0800) + ip_packet


def vlan_tagged(ip_packet: bytes) -> bytes:
    return ETH_DST + ETH_SRC + struct.pack(">H", 0x8100) + struct.pack(">H", 7) + struct.pack(">H", 0x0800) + ip_packet


def write_pcap(packets, linktype=1) -> str:
    fd, path = tempfile.mkstemp(suffix=".pcap")
    with os.fdopen(fd, "wb") as fh:
        fh.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, linktype))
        for pkt in packets:
            fh.write(struct.pack("<IIII", 0, 0, len(pkt), len(pkt)))
            fh.write(pkt)
    return path


class ParsePacketTest(unittest.TestCase):
    def test_udp_ipv4(self):
        raw = ethernet(udp_packet("10.0.0.1", 1000, "10.0.0.2", 6321, b"hello"))
        ip = pcap_flow.strip_link(raw, pcap_flow.LINKTYPE_ETHERNET)
        src, sport, dst, dport, payload = pcap_flow.parse(ip)
        self.assertEqual((src, sport), ("10.0.0.1", 1000))
        self.assertEqual((dst, dport), ("10.0.0.2", 6321))
        self.assertEqual(payload, b"hello")

    def test_vlan_is_unwrapped(self):
        raw = vlan_tagged(udp_packet("10.0.0.1", 1, "10.0.0.2", 2, b"x"))
        ip = pcap_flow.strip_link(raw, pcap_flow.LINKTYPE_ETHERNET)
        self.assertIsNotNone(ip)
        src, _, dst, _, payload = pcap_flow.parse(ip)
        self.assertEqual((src, dst), ("10.0.0.1", "10.0.0.2"))
        self.assertEqual(payload, b"x")

    def test_non_udp_is_skipped(self):
        # IPv4, aber TCP (6) -> parse() muss None liefern.
        src_b = bytes([10, 0, 0, 1])
        dst_b = bytes([10, 0, 0, 2])
        ip = struct.pack(">BBHHHBBH", 0x45, 0, 40, 0, 0, 64, 6, 0) + src_b + dst_b
        self.assertIsNone(pcap_flow.parse(ip))

    def test_non_ipv4_is_skipped(self):
        self.assertIsNone(pcap_flow.parse(b"\x60" + b"\x00" * 39))

    def test_short_packet_is_skipped(self):
        self.assertIsNone(pcap_flow.strip_link(b"\x00" * 10, pcap_flow.LINKTYPE_ETHERNET))


class PcapIterationTest(unittest.TestCase):
    def test_packets_in_order(self):
        path = write_pcap(
            [
                ethernet(udp_packet("10.0.0.1", 1, "10.0.0.2", 6321, b"one")),
                ethernet(udp_packet("10.0.0.2", 6321, "10.0.0.1", 1, b"two")),
            ]
        )
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            linktype = struct.unpack_from("<I", data, 20)[0]
            self.assertEqual(linktype, 1)
            payloads = []
            for pkt in pcap_flow.iter_packets(data, linktype):
                ip = pcap_flow.strip_link(pkt, linktype)
                parsed = pcap_flow.parse(ip)
                if parsed:
                    payloads.append(parsed[4])
            self.assertEqual(payloads, [b"one", b"two"])
        finally:
            os.unlink(path)

    def test_truncated_trailer_ignored(self):
        path = write_pcap([ethernet(udp_packet("10.0.0.1", 1, "10.0.0.2", 2, b"z"))])
        try:
            with open(path, "rb") as fh:
                data = fh.read() + b"\x00\x01\x02"
            self.assertEqual(len(list(pcap_flow.iter_packets(data, 1))), 1)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
