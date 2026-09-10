#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dispatch.py - Unit-Tests fuer den rbbridge-Pipe-Dispatch (Issue #60).

Testet PipeClient + Relay-Dispatch/ack-Pfad ohne Windows-Spiel:
  - Erfolg: FIFO als Named-Pipe-Ersatz (Linux) - die JSON-Zeile kommt an.
  - Pipe-fehlt: nicht existenter Pfad -> OSError -> kein ack, Retry-requeue.
  - Ack-Pfad: nach Erfolg wird dieselbe cmd_id nicht erneut dispatcht.

Nur Standardbibliothek (unittest, os, json, tempfile, time).

Aufruf: python3 -m unittest test_dispatch -v   (aus diesem Verzeichnis)
"""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import relay  # noqa: E402


class StubPipe:
    """In-Memory-Pipe-Fake fuer den ack/retry-Logiktest (deterministisch)."""

    def __init__(self):
        self.calls = []
        self.fail = False

    def send_exec(self, command, cmd_id):
        if self.fail:
            raise OSError('pipe gone (fake)')
        self.calls.append((command, cmd_id))
        return 123  # fake Byte-Zahl


class PipeClientTest(unittest.TestCase):
    def test_send_exec_writes_line_to_fifo(self):
        d = tempfile.mkdtemp(prefix='rb60-pipe-')
        fifo = os.path.join(d, 'fake_pipe')
        try:
            os.mkfifo(fifo)
            # Halte einen read+write-fd offen, damit die FIFO nicht leer-
            # laeuft/EOF liefert und os.open(O_RDWR) nicht blockiert.
            fd = os.open(fifo, os.O_RDWR)
            try:
                pc = relay.PipeClient(fifo, timeout_s=2.0)
                n = pc.send_exec('rb_wave 3', 7)
                data = os.read(fd, 4096)
                line = data.decode('utf-8').split('\n')[0]
                self.assertEqual(
                    json.loads(line),
                    {'cmd': 'exec', 'command': 'rb_wave 3', 'cmd_id': 7},
                )
                self.assertEqual(n, len(data))
                self.assertTrue(data.endswith(b'\n'))
            finally:
                os.close(fd)
        finally:
            os.unlink(fifo)
            os.rmdir(d)

    def test_send_exec_pipe_missing_raises(self):
        d = tempfile.mkdtemp(prefix='rb60-nopipe-')
        try:
            missing = os.path.join(d, 'no_such_pipe')
            pc = relay.PipeClient(missing, timeout_s=2.0)
            with self.assertRaises(OSError):
                pc.send_exec('rb_wave 3', 7)
        finally:
            os.rmdir(d)


class DispatchFlowTest(unittest.TestCase):
    def _cfg(self):
        return {
            'log_path': '/tmp/fake.log',
            'player_id': 'player_a',
            'match_id': 'm1',
            'server': 'http://127.0.0.1:8080',
            'poll_s': 1.0,
            'pipe_path': relay.DEFAULT_PIPE_PATH,
            'pipe_timeout_s': 5.0,
        }

    def test_success_marks_ack(self):
        pipe = StubPipe()
        r = relay.Relay(self._cfg(), pipe=pipe)
        r.handle_outgoing({'event': 'exec_command', 'command': 'rb_wave 3', 'cmd_id': 5})
        self.assertEqual(pipe.calls, [], 'noch kein Versuch vor dem Loop')

        due, _seq, item = r.dispatch_queue.get_nowait()
        ok = r._handle_dispatch_item(item)
        self.assertTrue(ok)
        self.assertEqual(pipe.calls, [('rb_wave 3', 5)])
        self.assertIn(5, r.acked)
        self.assertNotIn(5, r.pending_keys)

    def test_pipe_unavailable_no_ack_and_requeue(self):
        pipe = StubPipe()
        pipe.fail = True
        r = relay.Relay(self._cfg(), pipe=pipe)
        r.handle_outgoing({'event': 'exec_command', 'command': 'rb_wave 3', 'cmd_id': 5})

        due, _seq, item = r.dispatch_queue.get_nowait()
        ok = r._handle_dispatch_item(item)
        self.assertFalse(ok)
        self.assertNotIn(5, r.acked, 'Pipe-Fehler darf NICHT als ack verbrennen')
        self.assertIn(5, r.pending_keys, 'Kommando bleibt fuer Retry gemerkt')

        # Requeued mit Backoff (due in der Zukunft).
        ndue, _nseq, _nitem = r.dispatch_queue.get_nowait()
        self.assertGreater(ndue, due)
        self.assertGreaterEqual(ndue, time.time() + 1.0 - 0.05)  # ~1s Backoff

    def test_ack_dedup_after_success(self):
        pipe = StubPipe()
        r = relay.Relay(self._cfg(), pipe=pipe)
        r.handle_outgoing({'event': 'exec_command', 'command': 'rb_wave 3', 'cmd_id': 5})
        due, _seq, item = r.dispatch_queue.get_nowait()
        self.assertTrue(r._handle_dispatch_item(item))

        # Gleiche cmd_id erneut (z. B. Server-Replay): dedupe, kein neuer Call.
        r.handle_outgoing({'event': 'exec_command', 'command': 'rb_wave 3', 'cmd_id': 5})
        self.assertTrue(r.dispatch_queue.empty())
        self.assertEqual(len(pipe.calls), 1)


if __name__ == '__main__':
    unittest.main()
