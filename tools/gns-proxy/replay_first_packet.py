#!/usr/bin/env python3
"""replay_first_packet.py — Gegenprobe zu E1 (Issue #831).

Nimmt das ERSTE Client-Paket eines echten, erfolgreichen Handshakes aus einem
Mitschnitt und schickt es an einen Ziel-Server. Vergleicht die Antwort mit der
des echten Servers (aus demselben Mitschnitt) — damit laesst sich ohne Spieler
und ohne laufende Instanz pruefen, ob ein fremder GNS-Server gleich/anders
antwortet.

Der Mitschnitt muss GNS/UDP-Pakete enthalten (IP + UDP + Payload). Der "Client"
ist die Quelle der groessten Pakete VOR der ersten Server-Antwort.

Aufruf:
    python3 replay_first_packet.py <pcap> <ziel-ip:port> [--min-len 400]

Nur Standardbibliothek.
"""

import argparse
import socket
import struct
import sys
import time


def read_pcap(path):
    """Klassisches pcap (little-endian, Ethernet) -> Liste von (src, dst, sport, dport, payload)."""
    with open(path, "rb") as handle:
        blob = handle.read()
    if len(blob) < 24:
        raise ValueError("pcap zu klein")
    magic = struct.unpack_from("<I", blob, 0)[0]
    if magic not in (0xA1B2C3D4, 0xA1B2C3D4 & 0xFFFFFFFF):
        # auch little-endian-Variante (0xd4c3b2a1) abfangen
        if magic != 0xD4C3B2A1:
            raise ValueError("unerwarteter pcap-Magic 0x%08x" % magic)
    linktype = struct.unpack_from("<I", blob, 20)[0]
    if linktype != 1:
        raise ValueError("nur Ethernet (linktype 1) unterstuetzt, ist %d" % linktype)

    packets = []
    off = 24
    while off + 16 <= len(blob):
        _ts_s, _ts_u, incl, _orig = struct.unpack_from("<IIII", blob, off)
        off += 16
        frame = blob[off : off + incl]
        off += incl
        if len(frame) < 42:
            continue
        eth_type = struct.unpack_from(">H", frame, 12)[0]
        if eth_type != 0x0800:
            continue
        ip = frame[14:]
        ihl = (ip[0] & 0x0F) * 4
        if len(ip) < ihl + 8 or ip[9] != 17:  # UDP
            continue
        src = ".".join(str(b) for b in ip[12:16])
        dst = ".".join(str(b) for b in ip[16:20])
        sport, dport, length, _csum = struct.unpack_from(">HHHH", ip, ihl)
        payload = ip[ihl + 8 : ihl + length] if length >= 8 else b""
        packets.append((src, dst, sport, dport, payload))
    return packets


def pick_pair(packets, min_len):
    """Erstes grosses Client-Paket + die direkt folgende Server-Antwort."""
    for index, pkt in enumerate(packets):
        if len(pkt[4]) < min_len:
            continue
        sport, dport = pkt[2], pkt[3]
        for follow in packets[index + 1 :]:
            if follow[3] == sport and follow[2] == dport:
                return pkt, follow
            if follow[2] == sport and follow[3] == dport:
                continue  # Client wiederholt, weiter suchen
    return None, None


def hexdump(data, limit=64):
    out = []
    for off in range(0, min(len(data), limit), 16):
        chunk = data[off : off + 16]
        out.append("%04x: %s" % (off, " ".join("%02x" % b for b in chunk)))
    if len(data) > limit:
        out.append("... (%d Bytes gesamt)" % len(data))
    return "\n".join(out)


def session_replay(packets, target, steps, timeout, settle):
    """Replay der ersten `steps` Client-Pakete in Reihenfolge und Vergleich der
    Antworten mit denen des echten Servers — finden der Divergenzstelle."""
    host, _, port = target.rpartition(":")
    host, port = host, int(port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)

    # echtes Client-Paket ist das erste grosse; Richtung daraus ableiten
    first = None
    for index, pkt in enumerate(packets):
        if len(pkt[4]) >= 200:
            first = (index, pkt)
            break
    if first is None:
        print("kein Client-Paket gefunden")
        return 1
    _idx, pkt = first
    client_ip, server_ip = pkt[0], pkt[1]
    client_port, server_port = pkt[2], pkt[3]
    print("Flow: %s:%d <-> %s:%d" % (client_ip, client_port, server_ip, server_port))

    step = 0
    index = _idx
    while step < steps and index < len(packets):
        src, dst, sport, dport, payload = packets[index]
        index += 1
        if not (src == client_ip and sport == client_port and dst == server_ip and dport == server_port):
            continue
        if not payload:
            continue
        step += 1

        # echte Antwort(en) auf dieses Client-Paket aus dem Mitschnitt sammeln
        real = []
        look = index
        while look < len(packets):
            rs, rd, rsp, rdp, rp = packets[look]
            if rs == server_ip and rsp == server_port and rd == client_ip and rdp == client_port:
                real.append(rp)
            elif rs == client_ip and rsp == client_port:
                break
            look += 1

        sock.sendto(payload, (host, port))
        got = []
        deadline = time.time() + settle
        while time.time() < deadline:
            try:
                data, _peer = sock.recvfrom(4096)
            except socket.timeout:
                break
            got.append(data)
            if len(got) >= max(1, len(real)):
                break

        real_sizes = [len(r) for r in real]
        got_sizes = [len(g) for g in got]
        verdict = "OK" if real_sizes == got_sizes else "ABWEICHUNG"
        print("\n=== Schritt %d: Client-Paket %d B ===" % (step, len(payload)))
        print("  echte Antwortgroessen: %s" % (real_sizes or "-"))
        print("  unsere Antwortgroessen: %s   -> %s" % (got_sizes or "KEINE ANTWORT", verdict))
        if got:
            print(hexdump(got[0], limit=48))
        if not got:
            print("  (Probe hat nicht geantwortet — hier divergieren wir)")
            return 0
    print("\nReplay beendet nach %d Schritten" % step)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay des ersten Handshake-Pakets (Spike #831)")
    parser.add_argument("pcap")
    parser.add_argument("target", help="Ziel fuer das Replay, ip:port")
    parser.add_argument("--min-len", type=int, default=400, help="Mindestgroesse des Client-Pakets (Default 400)")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument(
        "--session",
        type=int,
        default=0,
        help="statt Einzel-Replay: die ersten N Client-Pakete in Reihenfolge replizieren",
    )
    parser.add_argument("--settle", type=float, default=0.4, help="Wartezeit auf Antworten je Schritt (Default 0.4s)")
    args = parser.parse_args(argv)

    packets = read_pcap(args.pcap)
    print("pcap: %d UDP-Pakete" % len(packets))
    if args.session:
        return session_replay(packets, args.target, args.session, args.timeout, args.settle)
    client_pkt, real_reply = pick_pair(packets, args.min_len)
    if client_pkt is None:
        print("kein Client-Paket >= %d Bytes gefunden" % args.min_len)
        return 1

    src, dst, sport, dport, payload = client_pkt
    print("echtes Client-Paket: %s:%d -> %s:%d  (%d Bytes)" % (src, sport, dst, dport, len(payload)))
    print(hexdump(payload))
    if real_reply is not None:
        print("echte Server-Antwort (%d Bytes):" % len(real_reply[4]))
        print(hexdump(real_reply[4]))
    else:
        print("keine echte Server-Antwort im Mitschnitt gefunden")

    host, _, port = args.target.rpartition(":")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(args.timeout)
    sock.sendto(payload, (host, int(port)))
    try:
        reply, _peer = sock.recvfrom(4096)
    except socket.timeout:
        print("REPLAY: keine Antwort innerhalb %.1fs" % args.timeout)
        return 2
    print("REPLAY-Antwort vom Ziel (%d Bytes):" % len(reply))
    print(hexdump(reply))
    if real_reply is not None:
        same = reply[: len(real_reply[4])] == real_reply[4]
        print("Antworten identisch: %s" % ("JA" if same else "NEIN"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
