#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
relay.py - Baustein 07: Spiel-Log-Tail -> Tournament-Server -> Dispatch-Bridge

Prototyp-Strecke Spiel -> Trainer -> Relay -> Server -> Web-UI:

    exor_logs.txt ([RBBATTLE]-Zeilen, vom Lua-Mod geschrieben)
        -> Relay tailt die Datei, parst key=value-Tokens
        -> POST {server}/event  {match_id, player_id, event:{type, ...payload}}
        -> Server legt Antworten/Kommandos in die Outbox des Spielers
        -> GET /poll/:player_id  (Poll-Loop im Relay)
        -> event.exec_command  ->  dispatch_exec: {"cmd":"exec","command":...,
           "cmd_id":...} auf die rbbridge-Named-Pipe (\\\\.\\pipe\\rbbattle);
           Pipe nicht erreichbar -> Retry mit Backoff (Kommando wird erst
           NACH erfolgreichem Schreiben als ack markiert, kein Verlust).
           Danach wird auf derselben Verbindung die exec_result-Antwort
           gelesen (Issue #73) und geloggt (status=ok|error|timeout) -
           eine ausbleibende Antwort ist kein Fehler, nur ein Log-Hinweis.
           andere Event-Typen -> nur loggen

Nur Standardbibliothek (Python 3.7+), kein pip-Paket noetig.

Konfiguration (Umgebungsvariablen):
    RBB_LOG_PATH   Pfad zur exor_logs.txt
                   (Default: %%USERPROFILE%%\\Documents\\The Riftbreaker\\exor_logs.txt)
    RBB_PLAYER_ID  Spieler-/Instanz-ID, mit der sich das Relay registriert und pollt (Pflicht)
    RBB_MATCH_ID   Match-ID, unter der die Spiel-Events eingeliefert werden (Pflicht zum Posten)
    RBB_SERVER     Basis-URL des Tournament-Servers (Default: http://127.0.0.1:8080)

    RBB_POLL_S     Poll-Intervall in Sekunden (Default 1.0)
    RBB_PIPE_PATH  rbbridge-Named-Pipe (Default \\\\.\\pipe\\rbbattle, wie
                   rbbridge.c PIPE_NAME_A)
    RBB_PIPE_TIMEOUT_S  Timeout fuer Pipe-Connect/Write in Sekunden (Default 5.0)

Verhalten:
    - Netzfehler (Server weg/Timeout/5xx): Retry mit exponentiellem Backoff
      (1s..30s), nichts geht verloren - Events warten in einer In-Memory-Queue
      (begrenzt auf 10000; ist sie voll, blockiert der Tail - natuerlicher
      Rueckstau statt Event-Verlust).
    - 4xx-Antworten des Servers (match nicht gefunden, invalid event type):
      Konfigurationsfehler -> wird geloggt und verworfen (Retry wuerde nichts
      aendern), der naechste Event wird verarbeitet.
    - exec_command wird als {"cmd":"exec",...} auf die rbbridge-Pipe geschrieben;
      ist die Pipe nicht erreichbar (Spiel laeuft nicht), wird das Kommando
      NICHT verworfen, sondern mit Backoff erneut versucht (Log
      "dispatch failed reason=pipe_unavailable"). Nach erfolgreichem Schreiben
      wird auf derselben Verbindung bis zu RBB_PIPE_TIMEOUT_S auf die
      exec_result-Antwort gewartet (Log "dispatch result cmd_id=... status=
      ok|error|timeout"); ein Timeout hier blockiert keine weiteren Dispatches.
    - Log-Rotation (6 Dateien): erkannt (Datei schrumpft), Tail startet vorn.
    - Beenden: Strg+C (graceful), Encoding: UTF-8 mit errors=replace.

Beispiel ohne Spiel:
    RBB_LOG_PATH=fake.log RBB_PLAYER_ID=player_a RBB_MATCH_ID=m1-x \
    RBB_SERVER=http://127.0.0.1:8080 python3 relay.py
    # danach in einer zweiten Shell:  bash fake-log.sh fake.log
"""

import argparse
import json
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

TAIL_S = 0.5          # Tail-Poll-Intervall (s)
HTTP_TIMEOUT = 5.0    # Timeout je Request (s)
BACKOFF_BASE = 1.0    # erster Retry (s)
BACKOFF_MAX = 30.0    # Retry-Obergrenze (s)
QUEUE_MAX = 10000     # In-Memory-Queue-Grenze (Backpressure, kein Verlust)
ACK_MAX = 512         # gemerkte acks (cmd_id), aeltere werden verworfen
PIPE_TIMEOUT = 5.0                # Timeout fuer Pipe-Connect/Write (s)
DEFAULT_PIPE_PATH = r'\\.\pipe\rbbattle'  # wie rbbridge.c (PIPE_NAME_A)

# Nur diese event= Typen werden an den Server geschickt (protocol.md,
# game -> server). Alles andere (z. B. bridge_test, wave) wird geloggt+skip.
SERVER_EVENT_TYPES = frozenset([
    'score_update',
    'wave_sent',
    'wave_received',
    'round_start',
    'round_end',
    'match_end',
])

# Tokens, die der Server als Zahl erwartet -> in der Log-Zeile ohne Anfuehrungs-
# zeichen -> hier zu int/float konvertieren.
NUMERIC_KEYS = frozenset([
    'score', 'wave', 'level', 'round', 'duration_s', 'delay_s',
    'cost', 'kills', 'points',
])

log_lock = threading.Lock()


def log(msg):
    with log_lock:
        sys.stdout.write('[relay] ' + msg + '\n')
        sys.stdout.flush()


def default_log_path():
    base = os.environ.get('USERPROFILE') or os.path.expanduser('~')
    return os.path.join(base, 'Documents', 'The Riftbreaker', 'exor_logs.txt')


def parse_line(raw):
    """Parst eine [RBBATTLE]-Zeile.

    Rueckgabe:
      ("post", etype, payload)  - Event, das zum Server gehoert
      ("skip", etype, {})       - [RBBATTLE] mit event=, Typ aber nicht server-faehig
      None                       - keine [RBBATTLE]-Zeile / kein event= (raw-Meldung)

    "[RBBATTLE] event=wave_sent level=2 cost=50" -> ("post", "wave_sent", {"level": 2, "cost": 50})
    "[RBBATTLE] event=bridge_test status=done"    -> ("skip", "bridge_test", {})
    "[RBBATTLE] skeleton ok"                      -> None
    """
    try:
        text = raw.decode('utf-8', errors='replace').strip()
    except AttributeError:
        text = raw.strip()
    idx = text.find('[RBBATTLE]')
    if idx == -1:
        return None
    rest = text[idx + len('[RBBATTLE]'):].strip()
    if not rest:
        return None

    tokens = {}
    has_kv = False
    for tok in rest.split():
        if '=' in tok:
            key, _, value = tok.partition('=')
            if not key:
                continue
            if key in NUMERIC_KEYS:
                try:
                    value = int(value)
                except ValueError:
                    try:
                        value = float(value)
                    except ValueError:
                        pass  # bleibt String
            tokens[key] = value
            has_kv = True
        # Bare Tokens ohne '=' werden ignoriert (kein key=value -> kein Event).
    if not has_kv:
        return None
    etype = tokens.pop('event', None)
    if not etype:
        return None
    if etype not in SERVER_EVENT_TYPES:
        return ('skip', etype, {})
    return ('post', etype, tokens)


def http_json(method, url, body=None):
    """HTTP-Helfer: liefert (status, parsed_json) oder wirft URLError/HTTPError."""
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        raw = resp.read().decode('utf-8', errors='replace')
        return resp.status, (json.loads(raw) if raw else None)


def backoff_sleep(attempt, stop):
    """Exponentieller Backoff (1s..30s), in 0.2s-Schritten unterbrechbar."""
    delay = min(BACKOFF_MAX, BACKOFF_BASE * (2 ** attempt))
    waited = 0.0
    while waited < delay and not stop.is_set():
        time.sleep(min(0.2, delay - waited))
        waited += 0.2


class PipeClient:
    """Minimaler Client fuer die rbbridge-Named-Pipe (exec-Kanal).

    Schreibt eine JSON-Zeile {"cmd":"exec","command":...,"cmd_id":...} auf
    die Pipe. Zwei Varianten:
      - send_exec: schreibt und schliesst wieder (fire-and-forget, liest
        keine Antwort) - fuer einfache Faelle/Tests.
      - send_exec_and_wait: schreibt und liest auf DERSELBEN Verbindung die
        exec_result-Antwort der rbbridge (PIPE_ACCESS_DUPLEX, Issue #73),
        bevor sie schliesst. Vom Relay-Dispatch-Pfad genutzt.
    Connect+Write sind in beiden Faellen per Timeout begrenzt, damit eine
    nicht erreichbare Pipe (Spiel laeuft nicht / rbbridge nicht injiziert /
    Pipe belegt) die Dispatch-Schleife nicht blockiert.

    - Windows: Pfad \\\.\\pipe\\rbbattle -> os.open (Named Pipe; blockiert
      bis ein Server/Instanz-Slot frei ist - Timeout greift hier).
    - Linux (Test): ein FIFO-Pfad (Named-Pipe-Ersatz) oder ein nicht
      existenter Pfad (-> FileNotFoundError -> "pipe_unavailable").
    """

    def __init__(self, path, timeout_s):
        self.path = path
        self.timeout_s = timeout_s

    def _open_flags(self):
        # Beide Richtungen ueber denselben Handle (rbbridge: PIPE_ACCESS_DUPLEX,
        # exec_result kommt auf derselben Verbindung zurueck, Issue #73).
        # Windows: Named Pipe wie pipe_client.py (O_RDWR, blockiert bis ein
        # Server/Instanz-Slot frei ist -> Timeout greift). Linux (Test): FIFO
        # mit O_RDWR ist eine von striktem POSIX abweichende, unter Linux aber
        # garantierte Erweiterung (open(7)) - blockiert nicht wie reines
        # O_WRONLY/O_RDONLY und erlaubt echtes Request/Response als
        # Named-Pipe-Ersatz.
        if os.name == 'nt':
            return os.O_RDWR | getattr(os, 'O_BINARY', 0)
        return os.O_RDWR

    def _write(self, data):
        fd = os.open(self.path, self._open_flags())
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        return len(data)

    def send_exec(self, command, cmd_id):
        """Schreibt die exec-Zeile; liefert die Byte-Zahl oder wirft
        OSError/TimeoutError, wenn die Pipe nicht erreichbar ist.

        Fire-and-forget (kein Read der Antwort) - fuer die Antwort siehe
        send_exec_and_wait."""
        payload = {'cmd': 'exec', 'command': command, 'cmd_id': cmd_id}
        # Kompaktes JSON ohne Leerzeichen (Vertrag trainer/protocol.md).
        data = (json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
        if self.timeout_s is None or self.timeout_s <= 0:
            return self._write(data)

        result = {}

        def _run():
            try:
                result['n'] = self._write(data)
            except OSError as e:
                result['error'] = e

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(self.timeout_s)
        if t.is_alive():
            # Windows: os.open blockiert, wenn die Pipe belegt ist (kein
            # freier Instanz-Slot). Daemon-Thread bleibt haengen, blockiert
            # aber weder Prozessende noch den naechsten Versuch.
            raise TimeoutError('pipe connect/write timeout nach {}s'.format(self.timeout_s))
        if 'error' in result:
            raise result['error']
        return result['n']

    def _read_result(self, fd, command, deadline):
        """Liest Zeilen vom fd, bis ein passendes exec_result kommt oder
        die deadline (Unix-Timestamp, None = unbegrenzt) erreicht ist.

        Andere Nachrichten der DLL (pong/score_update/error/...) werden
        uebersprungen - nur ein exec_result mit demselben command-Feld
        zaehlt als Treffer (rbbridge kennt kein cmd_id, s. protocol.md).
        os.read() selbst hat kein natives Timeout; der Deadline-Check
        greift nur zwischen zwei Reads - der eigentliche Gesamt-Timeout
        wird vom Aufrufer (send_exec_and_wait) per Thread-Join durchgesetzt,
        das hier ist nur ein "genug erfolglos gelesen -> aufgeben"-Schutz
        gegen eine gespraechige Gegenseite.
        """
        buf = b''
        lines_seen = 0
        while deadline is None or time.time() < deadline:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                return None
            if not chunk:
                return None  # Gegenseite hat geschlossen - keine Antwort mehr
            buf += chunk
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                lines_seen += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode('utf-8', errors='replace'))
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get('event') == 'exec_result' \
                        and msg.get('command') == command:
                    return msg
                if lines_seen > 200:
                    return None  # Schutz gegen endlose Fremd-Zeilen
        return None

    def send_exec_and_wait(self, command, cmd_id):
        """Schreibt die exec-Zeile und liest auf derselben Verbindung die
        exec_result-Antwort (rbbridge ist PIPE_ACCESS_DUPLEX - ein
        Client-Handle fuer beide Richtungen, s. trainer/protocol.md).

        Liefert (bytes_geschrieben, result): result ist das geparste
        exec_result-Dict, oder None, wenn innerhalb von timeout_s keine
        passende Antwort ankam. Ein Antwort-Timeout ist KEIN Fehler -
        das Kommando wurde erfolgreich geschrieben. Wirft OSError/
        TimeoutError nur, wenn schon Connect/Write fehlschlagen (wie
        send_exec).
        """
        payload = {'cmd': 'exec', 'command': command, 'cmd_id': cmd_id}
        data = (json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
        timeout_s = self.timeout_s if (self.timeout_s and self.timeout_s > 0) else None

        result = {}
        written = threading.Event()
        finished = threading.Event()

        def _run():
            try:
                fd = os.open(self.path, self._open_flags())
            except OSError as e:
                result['error'] = e
                finished.set()
                return
            try:
                try:
                    os.write(fd, data)
                except OSError as e:
                    result['error'] = e
                    return
                result['n'] = len(data)
                written.set()
                deadline = None if timeout_s is None else time.time() + timeout_s
                result['msg'] = self._read_result(fd, command, deadline)
            finally:
                os.close(fd)
                finished.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        if timeout_s is None:
            finished.wait()
        else:
            # Phase 1: auf Connect+Write warten (wie send_exec) - Timeout
            # hier ist ein echter Fehler (Pipe nicht erreichbar).
            if not written.wait(timeout_s):
                if finished.is_set() and 'error' in result:
                    raise result['error']
                raise TimeoutError('pipe connect/write timeout nach {}s'.format(timeout_s))
            # Phase 2: Budget fuer die exec_result-Antwort - Timeout hier ist
            # KEIN Fehler (Kommando wurde schon geschrieben), nur "keine
            # Antwort"; der Hintergrund-Thread bleibt in dem Fall als Daemon
            # haengen (analog send_exec), blockiert aber nichts weiter.
            finished.wait(timeout_s)

        if 'error' in result:
            raise result['error']
        return result.get('n', len(data)), result.get('msg')


class Relay:
    def __init__(self, cfg, pipe=None):
        self.cfg = cfg
        self.stop = threading.Event()
        self.queue = queue.Queue(maxsize=QUEUE_MAX)
        self.dispatch_queue = queue.PriorityQueue()  # (due_ts, seq, item)
        self.acked = {}        # cmd_id -> ts  (erfolgreich dispatcht)
        self.pending_keys = set()  # keys in der Dispatch-Queue (Retry-Schutz)
        self.dispatch_seq = 0
        self.pipe = pipe if pipe is not None else PipeClient(
            cfg.get('pipe_path', DEFAULT_PIPE_PATH),
            cfg.get('pipe_timeout_s', PIPE_TIMEOUT),
        )

    # -- Konfiguration ------------------------------------------------------
    @staticmethod
    def base_url(cfg):
        return cfg['server'].rstrip('/')

    def url_event(self):
        return self.base_url(self.cfg) + '/event'

    def url_poll(self):
        return self.base_url(self.cfg) + '/poll/' + urllib.parse.quote(self.cfg['player_id'], safe='')

    def url_register(self):
        return self.base_url(self.cfg) + '/register'

    # -- Tail (Produzent) ---------------------------------------------------
    def tail_loop(self):
        path = self.cfg['log_path']
        pos = 0
        carry = b''
        log('tail: beobachte {} (Poll {}s)'.format(path, TAIL_S))
        while not self.stop.is_set():
            try:
                size = os.path.getsize(path)
            except OSError:
                # Log existiert noch nicht (Spiel laeuft nicht / nie geloggt).
                time.sleep(TAIL_S)
                continue

            if size < pos:
                # Log-Rotation: Datei wurde ersetzt -> von vorn lesen.
                pos = 0
                carry = b''
                log('tail: log-rotation erkannt, lese von vorn')

            if size > pos:
                try:
                    with open(path, 'rb') as f:
                        f.seek(pos)
                        chunk = f.read()
                except OSError as e:
                    log('tail: lesefehler: {}'.format(e))
                    time.sleep(TAIL_S)
                    continue
                eof = pos + len(chunk)
                work = carry + chunk
                lines = work.split(b'\n')
                if work.endswith(b'\n'):
                    carry = b''
                    complete = lines[:-1]
                else:
                    # Letztes Fragment ohne \n ist evtl. halb geschrieben:
                    # zurueckhalten, bis die Zeile komplett ist.
                    carry = lines.pop()
                    complete = lines
                pos = eof - len(carry)
                for raw in complete:
                    if not raw.strip():
                        continue
                    parsed = parse_line(raw)
                    if parsed is None:
                        continue
                    action, etype, payload = parsed
                    raw_text = raw.decode('utf-8', errors='replace').strip()
                    if action == 'skip':
                        log('tail: event={} nicht server-faehig, uebersprungen: {}'.format(etype, raw_text))
                        continue
                    item = {'event': {'type': etype}, 'payload': payload, 'raw': raw_text}
                    try:
                        self.queue.put(item, timeout=0.5)
                    except queue.Full:
                        log('queue voll - tail blockiert (Backpressure)')
                        # Nicht verlieren: warten bis Platz ist.
                        while not self.stop.is_set():
                            try:
                                self.queue.put(item, timeout=0.5)
                                break
                            except queue.Full:
                                pass
                    log('tail: {}'.format(item['raw']))
            time.sleep(TAIL_S)

    # -- POST /event (Konsument) --------------------------------------------
    def post_loop(self):
        attempt = 0
        while not self.stop.is_set():
            try:
                item = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if not self.cfg.get('match_id'):
                log('post: kein RBB_MATCH_ID gesetzt - Event nicht einlieferbar, '
                    'verworfen: {}'.format(item['raw']))
                continue

            body = {
                'match_id': self.cfg['match_id'],
                'player_id': self.cfg['player_id'],
                'event': dict(item['event'], **item['payload']),
            }
            try:
                status, _ = http_json('POST', self.url_event(), body)
                if 200 <= status < 300:
                    log('post: ok type={} ({})'.format(item['event']['type'], item['raw']))
                    attempt = 0
                else:
                    # 3xx/4xx/5xx ausserhalb der http_json-Fehlerpfade:
                    log('post: HTTP {} - verworfen: {}'.format(status, item['raw']))
                    attempt = 0
            except urllib.error.HTTPError as e:
                if 500 <= e.code:
                    # Server-Probleme: Retry mit Backoff, Event bleibt in der Warteschleife.
                    log('post: HTTP {} (server) - retry in ~{}s: {}'
                        .format(e.code, min(BACKOFF_MAX, BACKOFF_BASE * (2 ** attempt)),
                                item['raw']))
                    self.queue.put(item)  # ans Ende; nichts verlieren
                    backoff_sleep(attempt, self.stop)
                    attempt += 1
                else:
                    # 400/404/409 = Konfigurationsfehler, Retry aendert nichts.
                    log('post: HTTP {} abgelehnt - verworfen: {}'.format(e.code, item['raw']))
                    attempt = 0
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                log('post: netzfehler ({}) - retry in ~{}s, Event wartet: {}'
                    .format(e, min(BACKOFF_MAX, BACKOFF_BASE * (2 ** attempt)), item['raw']))
                self.queue.put(item)
                backoff_sleep(attempt, self.stop)
                attempt += 1

    # -- GET /poll/:player_id (Dispatch-Loop) --------------------------------
    def poll_loop(self):
        interval = self.cfg.get('poll_s', 1.0)
        log('poll: GET {} (Intervall {}s)'.format(self.url_poll(), interval))
        attempt = 0
        while not self.stop.is_set():
            try:
                status, data = http_json('GET', self.url_poll())
                attempt = 0
                if status != 200 or not isinstance(data, dict):
                    log('poll: HTTP {} - unerwartete Antwort'.format(status))
                    time.sleep(interval)
                    continue
                events = data.get('events') or []
                for ev in events:
                    self.handle_outgoing(ev)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    # Spieler nicht (mehr) registriert (Server-Neustart): erneut registrieren.
                    log('poll: 404 player not found - registriere neu')
                    try:
                        self.register_once()
                    except (urllib.error.URLError, urllib.error.HTTPError,
                            TimeoutError, OSError) as re:
                        log('poll: re-register fehlgeschlagen ({}) - retry in ~{}s'.format(
                            re, min(BACKOFF_MAX, BACKOFF_BASE * (2 ** attempt))))
                        backoff_sleep(attempt, self.stop)
                        attempt += 1
                    else:
                        attempt = 0
                elif 500 <= e.code:
                    log('poll: HTTP {} - retry in ~{}s'.format(
                        e.code, min(BACKOFF_MAX, BACKOFF_BASE * (2 ** attempt))))
                    backoff_sleep(attempt, self.stop)
                    attempt += 1
                else:
                    log('poll: HTTP {} - uebersprungen'.format(e.code))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                log('poll: netzfehler ({}) - retry in ~{}s'.format(
                    e, min(BACKOFF_MAX, BACKOFF_BASE * (2 ** attempt))))
                backoff_sleep(attempt, self.stop)
                attempt += 1
            time.sleep(interval)

    def handle_outgoing(self, ev):
        """Ein Event aus der Outbox: exec_command -> Dispatch-Queue."""
        if not isinstance(ev, dict):
            return
        kind = ev.get('event') or ev.get('type')
        if kind == 'exec_command':
            command = ev.get('command', '')
            cid = ev.get('cmd_id')
            key = cid if cid is not None else command
            if key in self.acked:
                log('poll: exec_command bereits ack (cmd_id={}) - uebersprungen'.format(key))
                return
            self.enqueue_dispatch(command, cid)
        else:
            log('poll: event={} (kein dispatch) {}'.format(kind, json.dumps(ev)))

    # -- Dispatch (exec_command -> rbbridge-Pipe) ---------------------------
    def enqueue_dispatch(self, command, cid):
        """Legt ein exec_command einmalig in die Dispatch-Queue."""
        key = cid if cid is not None else command
        if key in self.acked or key in self.pending_keys:
            return
        self.pending_keys.add(key)
        self.dispatch_seq += 1
        item = {'key': key, 'command': command, 'cmd_id': cid, 'attempt': 0}
        self.dispatch_queue.put((time.time(), self.dispatch_seq, item))

    def dispatch_exec(self, command, cmd_id):
        """Schreibt die exec-Zeile auf die rbbridge-Pipe und liest die
        exec_result-Antwort auf derselben Verbindung (Issue #73).

        Liefert (bytes_geschrieben, result) - result ist das exec_result-Dict
        oder None bei Antwort-Timeout (kein Fehler). Wirft OSError/
        TimeoutError, wenn schon Connect/Write fehlschlagen (Pipe nicht
        erreichbar).
        """
        return self.pipe.send_exec_and_wait(command, cmd_id)

    def _requeue_dispatch(self, item, due):
        self.dispatch_seq += 1
        self.dispatch_queue.put((due, self.dispatch_seq, item))

    def _prune_acked(self):
        if len(self.acked) > ACK_MAX:
            # aelteste Haelfte vergessen (nur Duplikat-Schutz, kein Verlust)
            for old in sorted(self.acked, key=self.acked.get)[:ACK_MAX // 2]:
                del self.acked[old]

    def _handle_dispatch_item(self, item):
        """Ein Queue-Item versuchen zu dispatchen; True = Erfolg (ack gesetzt).

        Bei Pipe-Fehler wird NICHT ack-markiert, sondern mit Backoff requeued.
        """
        key = item['key']
        command = item['command']
        cid = item['cmd_id']
        try:
            n, msg = self.dispatch_exec(command, cid)
        except (OSError, TimeoutError, ConnectionError) as e:
            delay = min(BACKOFF_MAX, BACKOFF_BASE * (2 ** item['attempt']))
            item['attempt'] += 1
            log('dispatch failed reason=pipe_unavailable cmd_id={} command={!r} '
                '({}) - retry in ~{}s'.format(key, command, e, delay))
            self._requeue_dispatch(item, time.time() + delay)
            return False
        self.acked[key] = time.time()
        self.pending_keys.discard(key)
        self._prune_acked()
        log('dispatch sent cmd_id={} len={}'.format(key, n))
        self._log_dispatch_result(key, command, msg)
        return True

    def _log_dispatch_result(self, key, command, msg):
        """Loggt die exec_result-Antwort (oder deren Ausbleiben, Issue #73)."""
        if msg is None:
            log('dispatch result cmd_id={} status=timeout command={!r}'.format(key, command))
            return
        if msg.get('ok'):
            log('dispatch result cmd_id={} status=ok command={!r}'.format(key, command))
        else:
            log('dispatch result cmd_id={} status=error command={!r} reason={!r}'.format(
                key, command, msg.get('reason', '')))

    def dispatch_loop(self):
        log('dispatch: pipe={} timeout={}s'.format(
            self.cfg.get('pipe_path', DEFAULT_PIPE_PATH),
            self.cfg.get('pipe_timeout_s', PIPE_TIMEOUT)))
        while not self.stop.is_set():
            try:
                due, _seq, item = self.dispatch_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            now = time.time()
            if now < due:
                self._requeue_dispatch(item, due)
                time.sleep(min(0.5, due - now))
                continue
            self._handle_dispatch_item(item)

    # -- Registration --------------------------------------------------------
    def register_once(self):
        body = {'player_id': self.cfg['player_id']}
        status, _ = http_json('POST', self.url_register(), body)
        if 200 <= status < 300:
            log('register: ok player_id={}'.format(self.cfg['player_id']))
        return 200 <= status < 300

    def register_with_retry(self):
        attempt = 0
        while not self.stop.is_set():
            try:
                if self.register_once():
                    return
                log('register: HTTP-Fehler, retry in ~{}s'.format(
                    min(BACKOFF_MAX, BACKOFF_BASE * (2 ** attempt))))
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
                log('register: server nicht erreichbar ({}) - retry in ~{}s'.format(
                    e, min(BACKOFF_MAX, BACKOFF_BASE * (2 ** attempt))))
            backoff_sleep(attempt, self.stop)
            attempt += 1

    # -- Main ----------------------------------------------------------------
    def run(self):
        threads = [
            threading.Thread(target=self.tail_loop, name='tail', daemon=True),
            threading.Thread(target=self.post_loop, name='post', daemon=True),
            threading.Thread(target=self.poll_loop, name='poll', daemon=True),
            threading.Thread(target=self.dispatch_loop, name='dispatch', daemon=True),
        ]
        for t in threads:
            t.start()
        log('laufend (Strg+C zum Beenden)')
        try:
            while not self.stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            log('Strg+C - beende ...')
            self.stop.set()


def main():
    ap = argparse.ArgumentParser(
        description='07-relay: exor_logs.txt -> Tournament-Server -> dispatch (rbbridge-Pipe)')
    ap.add_argument('--log', default=os.environ.get('RBB_LOG_PATH') or default_log_path(),
                    help='Pfad zur exor_logs.txt (Default: RBB_LOG_PATH bzw. '
                         '%%USERPROFILE%%\\Documents\\The Riftbreaker\\exor_logs.txt)')
    args = ap.parse_args()

    player_id = os.environ.get('RBB_PLAYER_ID', '')
    if not player_id:
        print('[relay] Fehler: RBB_PLAYER_ID ist nicht gesetzt '
              '(Spieler-/Instanz-ID fuer /register und /poll).', file=sys.stderr)
        sys.exit(2)

    cfg = {
        'log_path': args.log,
        'player_id': player_id,
        'match_id': os.environ.get('RBB_MATCH_ID', ''),
        'server': os.environ.get('RBB_SERVER', 'http://127.0.0.1:8080'),
        'poll_s': float(os.environ.get('RBB_POLL_S', '1.0')),
        'pipe_path': os.environ.get('RBB_PIPE_PATH', DEFAULT_PIPE_PATH),
        'pipe_timeout_s': float(os.environ.get('RBB_PIPE_TIMEOUT_S', str(PIPE_TIMEOUT))),
    }

    # UTF-8-Ausgabe auch auf Windows-Konsolen (cp1252) erzwingen.
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

    relay = Relay(cfg)
    print('[relay] start: player={} match={} server={} log={}'.format(
        player_id, cfg['match_id'] or '(keiner - Events werden nicht gepostet)',
        cfg['server'], cfg['log_path']), flush=True)
    relay.register_with_retry()
    relay.run()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n[relay] beendet.')
