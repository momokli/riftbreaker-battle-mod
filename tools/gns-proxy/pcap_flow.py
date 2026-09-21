#!/usr/bin/env python3
"""Extract the ordered UDP payloads of one flow from a pcap (Spike #831).

Used to see the *game-level* message sequence of a real Riftbreaker join:
client handshake ("EXE: … DATA: …"), server reply, then the client's next
message (which is where the player name lives).  Handy when deciding whether a
GNS-terminating proxy can answer the handshake itself.

Usage:
    python3 pcap_flow.py FILE.pcap [--ip 80.131.52.54] [--port 6321] [--max 12]
"""

from __future__ import annotations

import argparse
import struct
import sys

LINKTYPE_ETHERNET = 1
LINKTYPE_RAW_IP = 101
LINKTYPE_LINUX_SLL = 113
LINKTYPE_LINUX_SLL2 = 276
ETHERTYPE_IP = 0x0800
ETHERTYPE_VLAN = 0x8100


def iter_packets(data: bytes, linktype: int):
    off = 24  # global header
    while off + 16 <= len(data):
        _ts, _tus, caplen, _origlen = struct.unpack_from("<IIII", data, off)
        off += 16
        yield data[off : off + caplen]
        off += caplen


def strip_link(pkt: bytes, linktype: int) -> bytes | None:
    if linktype == LINKTYPE_ETHERNET:
        if len(pkt) < 14:
            return None
        eth = struct.unpack_from(">H", pkt, 12)[0]
        off = 14
        while eth in (ETHERTYPE_VLAN, 0x88A8):
            eth = struct.unpack_from(">H", pkt, off + 2)[0]
            off += 4
        return pkt[off:] if eth == ETHERTYPE_IP else None
    if linktype == LINKTYPE_LINUX_SLL:
        return pkt[16:] if struct.unpack_from(">H", pkt, 14)[0] == ETHERTYPE_IP else None
    if linktype == LINKTYPE_LINUX_SLL2:
        return pkt[20:] if struct.unpack_from(">H", pkt, 0)[0] == ETHERTYPE_IP else None
    if linktype == LINKTYPE_RAW_IP:
        return pkt
    return None


def ipv4_to_str(raw: bytes) -> str:
    return ".".join(str(b) for b in raw)


def parse(pkt: bytes):
    """Return (src, sport, dst, dport, payload) for a UDP/IPv4 packet."""
    if len(pkt) < 20 or (pkt[0] >> 4) != 4:
        return None
    ihl = (pkt[0] & 0x0F) * 4
    if pkt[9] != 17:  # UDP
        return None
    total = struct.unpack_from(">H", pkt, 2)[0]
    src, dst = ipv4_to_str(pkt[12:16]), ipv4_to_str(pkt[16:20])
    udp = pkt[ihl:]
    if len(udp) < 8:
        return None
    sport, dport, ulen = struct.unpack_from(">HHH", udp, 0)
    payload = udp[8:ulen] if 8 <= ulen <= len(udp) else udp[8:total - ihl]
    return src, sport, dst, dport, payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pcap")
    ap.add_argument("--ip", help="only packets involving this IP")
    ap.add_argument("--port", type=int, help="only packets involving this port")
    ap.add_argument("--max", type=int, default=12)
    args = ap.parse_args()

    data = open(args.pcap, "rb").read()
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic not in (0xA1B2C3D4, 0xA1B2C3D4 & 0xFFFFFFFF, 0xD4C3B2A1):
        pass
    linktype = struct.unpack_from("<I", data, 20)[0]
    print(f"# linktype={linktype} size={len(data)}")

    n = 0
    for pkt in iter_packets(data, linktype):
        ip = strip_link(pkt, linktype)
        if ip is None:
            continue
        parsed = parse(ip)
        if parsed is None:
            continue
        src, sport, dst, dport, payload = parsed
        if args.ip and args.ip not in (src, dst):
            continue
        if args.port and args.port not in (sport, dport):
            continue
        n += 1
        ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in payload[:48])
        print(f"\n#{n} {src}:{sport} -> {dst}:{dport}  len={len(payload)}")
        print(f"   {payload[:64].hex(' ')}")
        print(f"   |{ascii_}|")
        if n >= args.max:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
