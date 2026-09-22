#!/usr/bin/env python3
"""rbrouter.py — userspace-UDP-Router für Riftbreaker (:6321). Spike zu Issue #825.

Zwei Stufen, beide in dieser Datei:

  * **Part 1 (statisch):** ein Eingang (`0.0.0.0:6321`), pro Client-Flow ein eigenes
    Socket zum Ziel; das Ziel kommt bei jedem *neuen* Flow aus einer Datei.
  * **Part 2 (Namens-Routing):** die ersten Datagramme eines neuen Flows werden
    **kurz zurückgehalten** (Budget `--hold-ms`), im Payload nach einem
    registrierten Spielernamen gescannt (`<name>=<ip:port>` in der Namensdatei) und
    der Flow dann auf das passende Backend gelegt. Kommt der Name erst nach der
    ersten Weiterleitung, wird der Flow **umgehängt** (Late-Re-Route).

Der Payload ist bei diesem Build unverschlüsselt (GameNetworkingSockets +
BINSER-Serialisierung + Zstd-Raw-Blocks); der Spielername steht als ASCII im
Client->Server-Paket (Evidenz: Issue #825).

Was er *nicht* tut: Zstd-Blöcke dekomprimieren, Auth, Persistenz, mehrere Ziele
pro Spieler.

Nur Standardbibliothek, Python 3.6+ (Zielhost `sync` ist Ubuntu 18.04).
"""

import argparse
import selectors
import signal
import socket
import sys
import threading
import time

BUF_SIZE = 65535
DEFAULT_BIND = "0.0.0.0:6321"
DEFAULT_TARGET = "127.0.0.1:6322"
DEFAULT_TARGET_FILE = "/tmp/rbrouter_target"
DEFAULT_NAMES_FILE = "/tmp/rbrouter_names"
FLOW_IDLE_TIMEOUT_S = 120.0
REPORT_INTERVAL_S = 5.0
SELECT_TIMEOUT_S = 0.5
RCVBUF_BYTES = 8 * 1024 * 1024

# Der Spielername steht erst NACH dem Handshake im Client->Server-Strom (Issue #825),
# darum ist Halten per Default AUS: es verzoegert nur. Das Routing passiert ueber
# den Merker (src-IP -> Ziel) und einen erwarteten Reconnect.
HOLD_BUDGET_MS = 0.0
HOLD_MAX_PKTS = 20
HOLD_MAX_BYTES = 65536
WATCH_PKTS = 60
WATCH_WINDOW_S = 15.0
MIN_NAME_LEN = 3


def parse_endpoint(spec):
    """'ip:port' -> (ip, port). ValueError bei Unfug."""
    if spec is None:
        raise ValueError("leerer Endpunkt")
    text = spec.strip()
    if not text or ":" not in text:
        raise ValueError("Endpunkt braucht die Form ip:port, bekam %r" % (spec,))
    host, _, port_text = text.rpartition(":")
    try:
        port = int(port_text)
    except ValueError:
        raise ValueError("Port ist keine Zahl: %r" % (port_text,))
    if not host or not 0 < port < 65536:
        raise ValueError("ungueltiger Endpunkt: %r" % (spec,))
    return host, port


def read_target_file(path, fallback):
    """Ziel aus Datei lesen. Rueckgabe (ziel, warnung) — Datei fehlt/leer => fallback."""
    try:
        with open(path, "r") as handle:
            spec = handle.read().strip()
    except OSError:
        return fallback, None
    if not spec:
        return fallback, None
    try:
        return parse_endpoint(spec), None
    except ValueError as exc:
        return fallback, "Zieldatei %s unbrauchbar (%s) — bleibe bei %s:%d" % (
            path,
            exc,
            fallback[0],
            fallback[1],
        )


def parse_names(text):
    """Namensdatei parsen: Zeilen `<name>=<ip:port>`, `#`-Kommentare, Leerzeilen ok."""
    names = {}
    for line in (text or "").splitlines():
        entry = line.split("#", 1)[0].strip()
        if not entry or "=" not in entry:
            continue
        name, _, spec = entry.partition("=")
        name = name.strip()
        if len(name) < MIN_NAME_LEN:
            continue
        try:
            names[name] = parse_endpoint(spec)
        except ValueError:
            continue
    return names


def read_names_file(path):
    """Namensdatei lesen. Rueckgabe (namen, warnung) — fehlende Datei => (None, None)
    (dann bleiben die aktuell geladenen Regeln stehen, statt sie zu loeschen)."""
    try:
        with open(path, "r") as handle:
            text = handle.read()
    except OSError:
        return None, None
    return parse_names(text), None


def scan_names(payload, names):
    """Laengsten registrierten Namen finden, der als ASCII im Payload steht.

    Laengster Treffer gewinnt: `momo` ist Substring von `momod`/`momop`/`momos`,
    geroutet werden soll der tatsaechlich gesendete Name.
    """
    best = None
    for name in names:
        if len(name) < MIN_NAME_LEN:
            continue
        if name.encode("utf-8") in payload:
            if best is None or len(name) > len(best):
                best = name
    return best


def udp_snmp():
    """Aggregierte UDP-Zaehler aus /proc/net/snmp (Linux). Leeres dict bei Fehler."""
    try:
        with open("/proc/net/snmp", "r") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return {}
    header = None
    for line in lines:
        if not line.startswith("Udp: "):
            continue
        parts = line.split()
        if header is None:
            header = parts[1:]
            continue
        try:
            return dict(zip(header, [int(value) for value in parts[1:]]))
        except ValueError:
            return {}
    return {}


def _set_rcvbuf(sock):
    """Grosser Empfangspuffer — Bursts (1174-Byte-Pakete) sollen nicht im Kernel sterben."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF_BYTES)
    except OSError:
        return None
    return sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)


class Flow(object):
    """Ein Client-Flow: Quell-Adresse, Backend, eigenes Socket, Zaehler."""

    def __init__(self, client_addr, now):
        self.client_addr = client_addr
        self.backend = None
        self.sock = None
        self.decided = False
        self.name = None
        self.pending = []
        self.pending_bytes = 0
        self.held_since = now
        self.watch_pkts = WATCH_PKTS
        self.reroutes = 0
        self.c2s_pkts = 0
        self.c2s_bytes = 0
        self.s2c_pkts = 0
        self.s2c_bytes = 0
        self.send_errors = 0
        self.created = now
        self.last = now

    def summary(self):
        return "c2s=%dpkt/%dB s2c=%dpkt/%dB err=%d reroutes=%d name=%s dauer=%.0fs" % (
            self.c2s_pkts,
            self.c2s_bytes,
            self.s2c_pkts,
            self.s2c_bytes,
            self.send_errors,
            self.reroutes,
            self.name or "-",
            self.last - self.created,
        )


class Router(object):
    """UDP-Router: ein Eingang, pro Client-Flow ein Backend-Socket (+ Namens-Routing)."""

    def __init__(
        self,
        bind,
        target=None,
        target_file=None,
        names_file=None,
        names=False,
        hold_ms=HOLD_BUDGET_MS,
        out=None,
        report_interval=REPORT_INTERVAL_S,
        poll_timeout=SELECT_TIMEOUT_S,
        idle_timeout=FLOW_IDLE_TIMEOUT_S,
        drop_flows_on_switch=True,
    ):
        if isinstance(bind, str):
            bind = parse_endpoint(bind)
        self.bind = bind
        self.fixed_target = parse_endpoint(target) if isinstance(target, str) else target
        self.target_file = target_file
        self.names_file = names_file
        self.name_routing = bool(names)
        self.hold_ms = hold_ms
        self.out = out if out is not None else sys.stdout
        self.report_interval = report_interval
        self.poll_timeout = poll_timeout
        self.idle_timeout = idle_timeout
        self.drop_flows_on_switch = drop_flows_on_switch
        self.names = {}
        self.learned = {}
        self.flows = {}
        self.selector = selectors.DefaultSelector()
        self.in_sock = None
        self.target = self.fixed_target or parse_endpoint(DEFAULT_TARGET)
        self.target_source = "fix" if self.fixed_target else "default"
        self.started = 0.0
        self.last_report = 0.0
        self.snmp_last = {}
        self._stats_flows = 0
        self._stats_c2s_pkts = 0
        self._stats_s2c_pkts = 0
        self._stop = threading.Event()

    # -- Aufbau / Abbau ---------------------------------------------------

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rcvbuf = _set_rcvbuf(sock)
        sock.bind(self.bind)
        self.in_sock = sock
        self.selector.register(sock, selectors.EVENT_READ, None)
        self.started = time.time()
        self.last_report = self.started
        self.snmp_last = udp_snmp()
        self.reload_names()
        self.log(
            "eingang %s:%d (rcvbuf=%s) -> fallback %s:%d [%s]"
            % (self.bind[0], self.bind[1], rcvbuf, self.target[0], self.target[1], self.target_source)
        )
        if self.target_file:
            self.log("ziel-datei: %s (wird bei jedem neuen flow gelesen)" % self.target_file)
        if self.name_routing:
            self.log(
                "namens-routing: an (%d namen aus %s, hold=%.0f ms)" % (len(self.names), self.names_file, self.hold_ms)
            )
            for name in sorted(self.names):
                self.log("  name %s -> %s:%d" % (name, self.names[name][0], self.names[name][1]))
        else:
            self.log("namens-routing: aus")
        return self

    def stop(self):
        if self._stop.is_set() and self.in_sock is None:
            return
        self._stop.set()
        for addr in list(self.flows):
            self.close_flow(addr, "shutdown")
        if self.in_sock is not None:
            try:
                self.selector.unregister(self.in_sock)
            except (KeyError, ValueError):
                pass
            self.in_sock.close()
            self.in_sock = None
        self.selector.close()
        self.log(
            "gestoppt nach %.0fs: flows=%d c2s=%dpkt s2c=%dpkt"
            % (time.time() - self.started, self.stats_flows, self.stats_c2s_pkts, self.stats_s2c_pkts)
        )

    @property
    def stats_flows(self):
        return self._stats_flows

    @property
    def stats_c2s_pkts(self):
        return self._stats_c2s_pkts

    @property
    def stats_s2c_pkts(self):
        return self._stats_s2c_pkts

    # -- Logging ----------------------------------------------------------

    def log(self, message):
        stamp = time.strftime("%H:%M:%S")
        self.out.write("[%s] rbrouter: %s\n" % (stamp, message))
        self.out.flush()

    # -- Ziel / Namen -----------------------------------------------------

    def resolve_target(self):
        """Bei jedem neuen Flow die Zieldatei lesen (Umschalten ohne Neustart)."""
        if self.fixed_target:
            return self.fixed_target
        target, warning = read_target_file(self.target_file, self.target)
        if warning:
            self.log(warning)
        if target != self.target:
            self.log("ziel gewechselt: %s:%d -> %s:%d" % (self.target[0], self.target[1], target[0], target[1]))
            self.target = target
            # Part-1-Semantik (EIN globales Ziel): laufende Flows verwerfen — sonst bleibt
            # der Client mit seinem wiederverwendeten Quellport am alten Backend hängen.
            if self.drop_flows_on_switch:
                for addr in list(self.flows):
                    self.close_flow(addr, "zielwechsel")
        return target

    def reload_names(self):
        """Namensdatei lesen; Aenderungen loggen (einmal pro neuem Flow)."""
        if not self.name_routing:
            return
        names, warning = read_names_file(self.names_file)
        if warning:
            self.log(warning)
        if names is None:
            return
        if names != self.names:
            added = [name for name in names if name not in self.names]
            gone = [name for name in self.names if name not in names]
            self.names = names
            if added or gone:
                self.log(
                    "namen aktualisiert: +%s -%s" % (",".join(sorted(added)) or "-", ",".join(sorted(gone)) or "-")
                )

    # -- Flows ------------------------------------------------------------

    def open_flow(self, client_addr, now):
        # Ziel (und ggf. Flow-Verwerfen bei Zielwechsel) VOR dem Anlegen des neuen
        # Flows: sonst raeumt resolve_target() den gerade erzeugten Flow mit weg.
        target = self.resolve_target()
        flow = Flow(client_addr, now)
        self.flows[client_addr] = flow
        if not self.name_routing:
            self.decide(flow, target, "namens-routing aus")
            return flow
        self.reload_names()
        known = self.learned.get(client_addr[0])
        if known is None:
            if self.hold_ms > 0:
                self.log(
                    "flow neu %s:%d (warte auf name, max %.0f ms)" % (client_addr[0], client_addr[1], self.hold_ms)
                )
            else:
                self.log("flow neu %s:%d (name wird nach dem handshake gelernt)" % (client_addr[0], client_addr[1]))
            return flow
        learned_target, _when = known
        self.log(
            "flow neu %s:%d (gemerkter name %s -> %s:%d, kein hold)"
            % (client_addr[0], client_addr[1], flow.name or "?", learned_target[0], learned_target[1])
        )
        self.decide(flow, learned_target, "aus gemerktem routing")
        return flow

    def decide(self, flow, backend, reason):
        """Flow auf ein Backend festlegen und gepufferte Pakete rausschicken."""
        flow.backend = backend
        flow.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _set_rcvbuf(flow.sock)
        flow.sock.setblocking(False)
        self.selector.register(flow.sock, selectors.EVENT_READ, flow.client_addr)
        flow.decided = True
        held = (time.time() - flow.held_since) * 1000.0
        self.log(
            "entschieden %s:%d -> %s:%d (%s%s, %d pakete gepuffert, %.0f ms gehalten)"
            % (
                flow.client_addr[0],
                flow.client_addr[1],
                backend[0],
                backend[1],
                reason,
                ", name=%s" % flow.name if flow.name else "",
                len(flow.pending),
                held,
            )
        )
        buffered, flow.pending, flow.pending_bytes = flow.pending, [], 0
        for data in buffered:
            self.forward(flow, data)

    def hold_and_decide(self, flow, data, now):
        """Neuen Flow: Name im Paket suchen, sonst bis zum Budget sammeln."""
        if self.name_routing and flow.name is None:
            name = scan_names(data, self.names)
            if name:
                flow.name = name
                flow.pending.append(data)
                self.decide(flow, self.names[name], "name erkannt")
                return
        flow.pending.append(data)
        flow.pending_bytes += len(data)
        held_ms = (now - flow.held_since) * 1000.0
        if (
            not self.name_routing
            or held_ms >= self.hold_ms
            or len(flow.pending) >= HOLD_MAX_PKTS
            or flow.pending_bytes >= HOLD_MAX_BYTES
        ):
            self.decide(flow, self.resolve_target(), "kein name im budget")

    def watch_for_name(self, flow, data, now):
        """Nach der Entscheidung noch eine Weile nach dem Namen schauen.

        Der Name kommt erst NACH dem Handshake (evidenz: Issue #825), also laesst
        er sich nicht vor dem ersten Weiterleiten lesen. Ein laufender Flow darf
        aber nicht umgehaengt werden — der Client hat seine GNS-Session schon mit
        dem alten Backend aufgebaut (sonst "connection lost", live verifiziert).
        Darum: Namen merken (src-IP -> Ziel) und den Flow verwerfen -> der Client
        reconnectet, und der naechste Flow wird aus dem Merker sofort richtig
        geroutet.

        Rueckgabe: True, wenn der Flow weiterleben darf (Paket weiterleiten), False
        wenn er gerade verworfen wurde.
        """
        if not self.name_routing or flow.name is not None or flow.watch_pkts <= 0:
            return True
        if now - flow.created > WATCH_WINDOW_S:
            flow.watch_pkts = 0
            return True
        flow.watch_pkts -= 1
        name = scan_names(data, self.names)
        if not name:
            return True
        flow.name = name
        backend = self.names[name]
        self.learned[flow.client_addr[0]] = (backend, now)
        if backend == flow.backend:
            self.log(
                "name bestaetigt: %s -> %s:%d (gemerkt fuer %s)" % (name, backend[0], backend[1], flow.client_addr[0])
            )
            return True
        self.log(
            "name gelernt: %s -> %s:%d — flow %s:%d wird verworfen (reconnect erwartet)"
            % (name, backend[0], backend[1], flow.client_addr[0], flow.client_addr[1])
        )
        self.close_flow(flow.client_addr, "name gelernt, reconnect erwartet")
        return False

    def forward(self, flow, data):
        if flow.sock is None:
            return
        try:
            flow.sock.sendto(data, flow.backend)
        except OSError as exc:
            flow.send_errors += 1
            if flow.send_errors <= 3:
                self.log("sendefehler an %s:%d: %s" % (flow.backend[0], flow.backend[1], exc))

    def close_flow(self, client_addr, reason):
        flow = self.flows.get(client_addr)
        if flow is None:
            return
        # Zaehler VOR dem Entfernen aktualisieren: sonst kann ein Leser "flows leer"
        # sehen, bevor die Summen da sind (Race in Tests/Statuszeile).
        self._stats_flows = self.stats_flows + 1
        self._stats_c2s_pkts = self.stats_c2s_pkts + flow.c2s_pkts
        self._stats_s2c_pkts = self.stats_s2c_pkts + flow.s2c_pkts
        del self.flows[client_addr]
        if flow.sock is not None:
            try:
                self.selector.unregister(flow.sock)
            except (KeyError, ValueError):
                pass
            flow.sock.close()
        self.log(
            "flow weg %s:%d -> %s:%d (%s) %s"
            % (
                client_addr[0],
                client_addr[1],
                flow.backend[0] if flow.backend else 0,
                flow.backend[1] if flow.backend else 0,
                reason,
                flow.summary(),
            )
        )

    # -- Zyklus -----------------------------------------------------------

    def on_client(self, now):
        try:
            data, client_addr = self.in_sock.recvfrom(BUF_SIZE)
        except OSError as exc:
            self.log("eingang-lesefehler: %s" % exc)
            return
        flow = self.flows.get(client_addr)
        if flow is None:
            flow = self.open_flow(client_addr, now)
        flow.c2s_pkts += 1
        flow.c2s_bytes += len(data)
        flow.last = now
        if not flow.decided:
            self.hold_and_decide(flow, data, now)
            return
        if not self.watch_for_name(flow, data, now):
            return
        self.forward(flow, data)

    def on_backend(self, client_addr, now):
        flow = self.flows.get(client_addr)
        if flow is None:
            return
        try:
            data, _ = flow.sock.recvfrom(BUF_SIZE)
        except OSError as exc:
            self.log("backend-lesefehler (%s:%d): %s" % (flow.backend[0], flow.backend[1], exc))
            return
        flow.s2c_pkts += 1
        flow.s2c_bytes += len(data)
        flow.last = now
        try:
            self.in_sock.sendto(data, client_addr)
        except OSError as exc:
            flow.send_errors += 1
            if flow.send_errors <= 3:
                self.log("sendefehler an client %s:%d: %s" % (client_addr[0], client_addr[1], exc))

    def flush_held(self, now):
        """Ueberfaellige Holds entscheiden (zeitgetriggert, nicht paketgetriggert).

        Ohne das bliebe ein Flow, dessen Client nach dem ersten Paket schweigt,
        fuer immer im Puffer liegen.
        """
        for _addr, flow in list(self.flows.items()):
            if flow.decided:
                continue
            if (now - flow.held_since) * 1000.0 >= self.hold_ms:
                self.decide(flow, self.resolve_target(), "kein name im budget")

    def reap(self, now):
        for addr, flow in list(self.flows.items()):
            if now - flow.last > self.idle_timeout:
                self.close_flow(addr, "idle %.0fs" % (now - flow.last))

    def report(self, now):
        snmp = udp_snmp()
        drops = snmp.get("RcvbufErrors", 0) - self.snmp_last.get("RcvbufErrors", 0)
        inerr = snmp.get("InErrors", 0) - self.snmp_last.get("InErrors", 0)
        self.snmp_last = snmp
        if not self.flows and drops == 0 and inerr == 0:
            return
        pkts = sum(flow.c2s_pkts + flow.s2c_pkts for flow in self.flows.values())
        names = ",".join(sorted(flow.name for flow in self.flows.values() if flow.name)) or "-"
        self.log(
            "status flows=%d pakete=%d fallback=%s:%d kernel-drops(5s)=%d inerr(5s)=%d namen=%s"
            % (len(self.flows), pkts, self.target[0], self.target[1], drops, inerr, names)
        )

    def poll_once(self, timeout=None):
        if timeout is None:
            timeout = self.poll_timeout
            # Solange ein Flow auf seinen Namen wartet: feiner pollen, damit die
            # Haltezeit nicht um bis zu poll_timeout ueberschritten wird.
            if self.hold_ms and any(not flow.decided for flow in self.flows.values()):
                timeout = min(timeout, 0.05)
        try:
            events = self.selector.select(timeout)
        except OSError:
            # stop() schliesst Selector/Sockets — sauberer Ausstieg statt Traceback im Thread.
            return time.time()
        now = time.time()
        for key, _ in events:
            if key.data is None:
                self.on_client(now)
            else:
                self.on_backend(key.data, now)
        self.flush_held(now)
        self.reap(now)
        return now

    def serve_forever(self):
        while not self._stop.is_set():
            now = self.poll_once()
            if self.report_interval and now - self.last_report >= self.report_interval:
                self.last_report = now
                self.report(now)


def main(argv=None):
    parser = argparse.ArgumentParser(description="userspace-UDP-Router fuer Riftbreaker :6321 (Spike #825)")
    parser.add_argument("--bind", default=DEFAULT_BIND, help="Eingang ip:port (Default %s)" % DEFAULT_BIND)
    parser.add_argument("--target", default=None, help="festes Fallback-Ziel ip:port (sonst Zieldatei)")
    parser.add_argument(
        "--target-file",
        default=DEFAULT_TARGET_FILE,
        help="Datei mit Fallback-Ziel ip:port, bei jedem neuen Flow gelesen (Default %s)" % DEFAULT_TARGET_FILE,
    )
    parser.add_argument(
        "--names-file",
        default=DEFAULT_NAMES_FILE,
        help="Datei mit `<name>=<ip:port>`-Regeln (Default %s)" % DEFAULT_NAMES_FILE,
    )
    parser.add_argument(
        "--no-names",
        dest="names",
        action="store_false",
        help="Namens-Routing AUS (reines Part-1-Verhalten)",
    )
    parser.add_argument(
        "--hold-ms",
        type=float,
        default=HOLD_BUDGET_MS,
        help="Budget, in dem neue Flows auf einen Namen warten (Default %.0f ms)" % HOLD_BUDGET_MS,
    )
    parser.add_argument(
        "--report-interval", type=float, default=REPORT_INTERVAL_S, help="Statuszeile alle N Sekunden (0 = aus)"
    )
    parser.add_argument(
        "--keep-flows-on-switch",
        action="store_true",
        help="laufende Flows beim Zielwechsel NICHT verwerfen (Default: verwerfen)",
    )
    parser.add_argument("--self-test", action="store_true", help="nur Parser/Zieldatei testen, kein Netz")
    args = parser.parse_args(argv)

    if args.self_test:
        names, warning = read_names_file(args.names_file)
        print("target-file -> %s" % (read_target_file(args.target_file, parse_endpoint(DEFAULT_TARGET)),))
        print("parse ip:port -> %s" % (parse_endpoint("65.21.27.234:6322"),))
        print("namen (%d) -> %s warnung=%s" % (len(names), names, warning))
        print("scan -> %s" % (scan_names(b"...momod...", names),))
        return 0

    router = Router(
        args.bind,
        target=args.target,
        target_file=args.target_file,
        names_file=args.names_file,
        names=args.names,
        hold_ms=args.hold_ms,
        report_interval=args.report_interval,
        drop_flows_on_switch=not args.keep_flows_on_switch,
    ).start()

    def _bye(_signum, _frame):
        router.log("signal — beende")
        router.stop()

    signal.signal(signal.SIGINT, _bye)
    signal.signal(signal.SIGTERM, _bye)
    try:
        router.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if router.in_sock is not None:
            router.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
