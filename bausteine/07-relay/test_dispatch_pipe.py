#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dispatch_pipe.py - Baustein 07: dispatch_exec gegen einen Pipe-Fake (Issue #60)

Deckt die Akzeptanzkriterien von Issue #60 ab, ohne Windows/Spielprozess:

  - Erfolg: dispatch_exec schreibt {"cmd":"exec","command":...,"cmd_id":...}
    auf die (Fake-)Pipe; ein Reader auf der anderen Seite bekommt exakt diese
    Zeile.
  - Pipe fehlt: Kommando wird NICHT ack-markiert, sondern in die
    Retry-Queue gelegt (dispatch_pending) - Log zeigt
    "dispatch failed reason=pipe_unavailable".
  - Ack-Pfad: nach erfolgreichem Dispatch ist der cmd_id in `acked`
    vermerkt; ein zweites Poll-Event mit derselben cmd_id loest keinen
    weiteren Pipe-Write aus (Dedup).

Unter Linux steht als "Named-Pipe-Ersatz" eine echte FIFO (os.mkfifo) fuer
den Erfolgsfall; fuer "Pipe fehlt" zeigt der Pfad schlicht auf keine FIFO.
Nur Standardbibliothek (unittest), kein pip-Paket noetig - Aufruf z. B.:

    cd bausteine/07-relay
    python3 -m unittest test_dispatch_pipe -v
"""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest

import relay


class DispatchPipeTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='rbb-dispatch-')
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def make_relay(self, pipe_path):
        cfg = {
            'log_path': os.path.join(self.tmpdir, 'fake.log'),
            'player_id': 'player_a',
            'match_id': 'm1',
            'server': 'http://127.0.0.1:0',
            'poll_s': 1.0,
            'pipe_path': pipe_path,
        }
        return relay.Relay(cfg)

    def test_dispatch_exec_erfolg_ueber_fifo(self):
        """Erfolg: exec-Zeile kommt unveraendert beim Pipe-Reader an."""
        if os.name == 'nt':
            self.skipTest('FIFO-Fake ist Unix-only; Windows nutzt echte Named Pipes')
        fifo_path = os.path.join(self.tmpdir, 'rbbattle.fifo')
        os.mkfifo(fifo_path)

        received = {}

        def reader():
            with open(fifo_path, 'rb') as f:
                received['line'] = f.readline()

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        time.sleep(0.1)  # Reader muss vor dem nicht-blockierenden Write offen sein.

        r = self.make_relay(fifo_path)
        r.handle_outgoing({'event': 'exec_command', 'command': 'rb_wave 3', 'cmd_id': 42})
        t.join(timeout=2.0)

        self.assertIn('line', received, 'Reader hat keine Zeile erhalten')
        payload = json.loads(received['line'].decode('utf-8'))
        self.assertEqual(payload, {'cmd': 'exec', 'command': 'rb_wave 3', 'cmd_id': 42})
        self.assertIn(42, r.acked, 'erfolgreicher Dispatch muss ack-markiert werden')
        self.assertEqual(r.dispatch_pending, [], 'kein Retry-Eintrag bei Erfolg')

    def test_dispatch_exec_pipe_fehlt_kein_ack_sondern_retry(self):
        """Pipe fehlt: kein Ack, Kommando landet in der Retry-Queue."""
        missing_path = os.path.join(self.tmpdir, 'does-not-exist.fifo')
        r = self.make_relay(missing_path)

        r.handle_outgoing({'event': 'exec_command', 'command': 'rb_wave 1', 'cmd_id': 7})

        self.assertNotIn(7, r.acked, 'Pipe nicht erreichbar darf nicht ack-markiert werden')
        self.assertEqual(len(r.dispatch_pending), 1)
        pending = r.dispatch_pending[0]
        self.assertEqual(pending['command'], 'rb_wave 1')
        self.assertEqual(pending['cmd_id'], 7)
        self.assertEqual(pending['attempt'], 0)
        self.assertGreater(pending['next_try'], time.time())

    def test_dispatch_exec_retry_erfolgreich_nach_pipe_fehlt(self):
        """Nach 'Pipe fehlt' erreicht ein spaeterer Retry (dispatch_retry_loop) die Pipe."""
        if os.name == 'nt':
            self.skipTest('FIFO-Fake ist Unix-only; Windows nutzt echte Named Pipes')
        fifo_path = os.path.join(self.tmpdir, 'rbbattle.fifo')
        r = self.make_relay(fifo_path)

        # Erster Versuch: FIFO existiert noch nicht -> pipe_unavailable, Retry-Queue.
        r.handle_outgoing({'event': 'exec_command', 'command': 'rb_wave 2', 'cmd_id': 9})
        self.assertNotIn(9, r.acked)
        self.assertEqual(len(r.dispatch_pending), 1)

        # FIFO jetzt anlegen + Reader starten, danach faelligen Retry manuell anstossen
        # (statt auf den Hintergrund-Thread/Backoff zu warten).
        os.mkfifo(fifo_path)
        received = {}

        def reader():
            with open(fifo_path, 'rb') as f:
                received['line'] = f.readline()

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        time.sleep(0.1)

        item = r.dispatch_pending[0]
        r.dispatch_pending = []
        r.try_dispatch(item['command'], item['cmd_id'], item['key'], item['attempt'] + 1)
        t.join(timeout=2.0)

        self.assertIn('line', received)
        payload = json.loads(received['line'].decode('utf-8'))
        self.assertEqual(payload['command'], 'rb_wave 2')
        self.assertIn(9, r.acked)

    def test_ack_pfad_dedupliziert_ohne_erneuten_pipe_write(self):
        """Ack-Pfad: ein zweites Poll-Event mit derselben cmd_id schreibt nicht erneut."""
        if os.name == 'nt':
            self.skipTest('FIFO-Fake ist Unix-only; Windows nutzt echte Named Pipes')
        fifo_path = os.path.join(self.tmpdir, 'rbbattle.fifo')
        os.mkfifo(fifo_path)
        lines = []

        def reader():
            with open(fifo_path, 'rb') as f:
                for _ in range(1):
                    line = f.readline()
                    if not line:
                        break
                    lines.append(line)

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        time.sleep(0.1)

        r = self.make_relay(fifo_path)
        r.handle_outgoing({'event': 'exec_command', 'command': 'rb_wave 1', 'cmd_id': 1})
        t.join(timeout=2.0)
        self.assertEqual(len(lines), 1)

        # Zweites, identisches Poll-Event (Server koennte es erneut zustellen):
        # darf NICHT nochmal auf die Pipe schreiben (nur der eine Reader-Read oben).
        r.handle_outgoing({'event': 'exec_command', 'command': 'rb_wave 1', 'cmd_id': 1})
        self.assertEqual(len(lines), 1, 'Dedup darf keinen zweiten Pipe-Write ausloesen')


if __name__ == '__main__':
    unittest.main()
