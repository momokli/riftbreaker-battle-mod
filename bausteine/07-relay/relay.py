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
        -> event.exec_command  ->  dispatch_exec: {"cmd":"exec","command":"...",
           "cmd_id":"..."} auf die Named Pipe (Default \\.\pipe\rbbattle,
           Format wie von trainer/rbbridge/rbbridge.c erwartet, s. auch
           trainer/protocol.md). Pipe nicht erreichbar -> Kommando wird NICHT
           ack-markiert, sondern in einer Retry-Queue mit Backoff (1..30s)
           erneut versucht (dispatch_retry_loop).
           andere Event-Typen -> nur loggen

Nur Standardbibliothek (Python 3.7+), kein pip-Paket noetig.

Konfiguration (Umgebungsvariablen):
    RBB_LOG_PATH   Pfad zur exor_logs.txt
                   (Default: %%USERPROFILE%%\\Documents\\The Riftbreaker\\exor_logs.txt)
    RBB_PLAYER_ID  Spieler-/Instanz-ID, mit der sich das Relay registriert und pollt (Pflicht)
    RBB_MATCH_ID   Match-ID, unter der die Spiel-Events eingeliefert werden (Pflicht zum Posten)
    RBB_SERVER     Basis-URL des Tournament-Servers (Default: http://127.0.0.1:8080)

    RBB_POLL_S     Poll-Intervall in Sekunden (Default 1.0)
    RBB_PIPE_PATH  Pfad/Name der Named Pipe fuer exec-Dispatch
                   (Default: \\.\pipe\rbbattle, wie rbbridge.c PIPE_NAME_A;
                   unter Linux/Tests kann hier eine FIFO als Pipe-Fake stehen)

Verhalten:
    - Netzfehler (Server weg/Timeout/5xx): Retry mit exponentiellem Backoff
      (1s..30s), nichts geht verloren - Events warten in einer In-Memory-Queue
      (begrenzt auf 10000; ist sie voll, blockiert der Tail - natuerlicher
      Rueckstau statt Event-Verlust).
    - 4xx-Antworten des Servers (match nicht gefunden, invalid event type):
      Konfigurationsfehler -> wird geloggt und verworfen (Retry wuerde nichts
      aendern), der naechste Event wird verarbeitet.
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

DISPATCH_PIPE_PATH = r'\\.\pipe\rbbattle'   # Default wie rbbridge.c PIPE_NAME_A
DISPATCH_BACKOFF_BASE = 1.0   # erster Dispatch-Retry (s)
DISPATCH_BACKOFF_MAX = 30.0   # Dispatch-Retry-Obergrenze (s)
DISPATCH_RETRY_TICK_S = 0.5   # Poll-Intervall der Retry-Queue

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


def dispatch_backoff_delay(attempt):
    return min(DISPATCH_BACKOFF_MAX, DISPATCH_BACKOFF_BASE * (2 ** attempt))


def write_pipe_line(path, line):
    """Schreibt eine Protokollzeile (Zeilenende ergaenzt) auf die Named Pipe.

    Windows: Named Pipe wird wie ein Client-Handle geoeffnet (rbbridge.c
    laeuft als Pipe-Server, PIPE_TYPE_BYTE) - ein kurzlebiger Connect je
    Dispatch reicht, der Server nimmt danach die naechste Verbindung an.
    Linux/Tests: `path` zeigt auf eine FIFO (Pipe-Fake); nicht-blockierendes
    Oeffnen laesst "kein Reader verbunden" sofort als OSError (ENXIO)
    auffliegen - das Pendant zu "Windows-Pipe nicht erreichbar".

    Wirft OSError, wenn die Pipe/FIFO nicht erreichbar ist.
    """
    data = (line + '\n').encode('utf-8')
    if os.name == 'nt':
        with open(path, 'r+b', buffering=0) as f:
            f.write(data)
    else:
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)


class Relay:
    def __init__(self, cfg):
        self.cfg = cfg
        self.stop = threading.Event()
        self.queue = queue.Queue(maxsize=QUEUE_MAX)
        self.acked = {}  # cmd_id -> ts  (Ack-Markierung der dispatchen Kommandos)
        self.dispatch_lock = threading.Lock()
        self.dispatch_pending = []  # Liste von Dicts, s. try_dispatch (Retry-Queue)

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
        """Ein Event aus der Outbox: exec_command -> dispatch_exec (Pipe)."""
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
            self.try_dispatch(command, cid, key, attempt=0)
        else:
            log('poll: event={} (kein dispatch) {}'.format(kind, json.dumps(ev)))

    def mark_acked(self, key):
        self.acked[key] = time.time()
        if len(self.acked) > ACK_MAX:
            # aelteste Haelfte vergessen (nur Duplikat-Schutz, kein Verlust)
            for old in sorted(self.acked, key=self.acked.get)[:ACK_MAX // 2]:
                del self.acked[old]

    def dispatch_exec(self, command, cmd_id):
        """Schreibt {"cmd":"exec","command":...,"cmd_id":...} auf die Pipe.

        Format wie in trainer/rbbridge/rbbridge.c erwartet (handle_line:
        cmd=="exec" -> dispatch_exec(command); cmd_id ist ein unbekanntes,
        vorwaertskompatibles Zusatzfeld, s. trainer/protocol.md). Gibt die
        Laenge der geschriebenen Zeile zurueck; wirft OSError bei
        Pipe/FIFO nicht erreichbar.
        """
        line = json.dumps({'cmd': 'exec', 'command': command, 'cmd_id': cmd_id})
        write_pipe_line(self.cfg['pipe_path'], line)
        return len(line)

    def try_dispatch(self, command, cmd_id, key, attempt):
        """Ein Dispatch-Versuch. Erfolg -> ack; Fehler -> Retry-Queue (Backoff)."""
        try:
            length = self.dispatch_exec(command, cmd_id)
        except OSError as e:
            log('dispatch failed reason=pipe_unavailable cmd_id={} command={} err={}'
                .format(key, command, e))
            with self.dispatch_lock:
                self.dispatch_pending.append({
                    'command': command,
                    'cmd_id': cmd_id,
                    'key': key,
                    'attempt': attempt,
                    'next_try': time.time() + dispatch_backoff_delay(attempt),
                })
            return
        log('dispatch sent cmd_id={} len={}'.format(key, length))
        self.mark_acked(key)

    # -- Dispatch-Retry-Queue (Pipe war beim ersten Versuch nicht erreichbar) --
    def dispatch_retry_loop(self):
        while not self.stop.is_set():
            now = time.time()
            due = []
            with self.dispatch_lock:
                remaining = []
                for item in self.dispatch_pending:
                    if item['next_try'] <= now:
                        due.append(item)
                    else:
                        remaining.append(item)
                self.dispatch_pending = remaining
            for item in due:
                if item['key'] in self.acked:
                    continue
                self.try_dispatch(item['command'], item['cmd_id'], item['key'],
                                   item['attempt'] + 1)
            time.sleep(DISPATCH_RETRY_TICK_S)

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
            threading.Thread(target=self.dispatch_retry_loop, name='dispatch', daemon=True),
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
        description='07-relay: exor_logs.txt -> Tournament-Server -> dispatch_exec (Pipe)')
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
        'pipe_path': os.environ.get('RBB_PIPE_PATH', DISPATCH_PIPE_PATH),
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
