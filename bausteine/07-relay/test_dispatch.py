#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dispatch.py - Unit-Tests fuer den rbbridge-Pipe-Dispatch (Issue #60/#73).

Testet PipeClient + Relay-Dispatch/ack-Pfad ohne Windows-Spiel:
  - Erfolg: FIFO als Named-Pipe-Ersatz (Linux) - die JSON-Zeile kommt an.
  - Pipe-fehlt: nicht existenter Pfad -> OSError -> kein ack, Retry-requeue.
  - Ack-Pfad: nach Erfolg wird dieselbe cmd_id nicht erneut dispatcht.
  - exec_result-Antwort (Issue #73): kommt sie an, wird sie geliefert;
    bleibt sie aus, liefert send_exec_and_wait (n, None) statt zu haengen.

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
        self.result = None  # optional: exec_result-Dict, das send_exec_and_wait liefert

    def send_exec_and_wait(self, command, cmd_id):
        if self.fail:
            raise OSError('pipe gone (fake)')
        self.calls.append((command, cmd_id))
        return 123, self.result  # fake Byte-Zahl + konfigurierbares Ergebnis


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


class ReadResultTest(unittest.TestCase):
    """PipeClient._read_result: exec_result-Zeilen parsen/matchen (Issue #73).

    Getestet ueber os.pipe() (echte, getrennte Read-/Write-Enden) statt einer
    einzelnen FIFO: Schreibt man auf einer FIFO und liest sofort auf demselben
    fd zurueck, bekommt man (Linux-Queue-Semantik) mit hoher Wahrscheinlichkeit
    die eigenen, gerade geschriebenen Bytes zurueck statt die der Gegenseite -
    ein reiner Artefakt der FIFO-als-Duplex-Pipe-Simulation, das bei einer
    echten Windows-Named-Pipe (getrennte Puffer je Richtung) nicht auftritt.
    os.pipe() hat dieses Problem nicht (Leseende kann nicht selbst schreiben).
    """

    def test_read_result_matches_exec_result(self):
        r_fd, w_fd = os.pipe()
        try:
            reply = json.dumps({'event': 'exec_result', 'command': 'rb_wave 3', 'ok': True}) + '\n'
            os.write(w_fd, reply.encode('utf-8'))
            pc = relay.PipeClient('/unused', timeout_s=2.0)
            msg = pc._read_result(r_fd, 'rb_wave 3', time.time() + 2.0)
            self.assertEqual(msg, {'event': 'exec_result', 'command': 'rb_wave 3', 'ok': True})
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_read_result_skips_unrelated_messages(self):
        r_fd, w_fd = os.pipe()
        try:
            other = json.dumps({'event': 'score_update', 'score': 10}) + '\n'
            wrong_cmd = json.dumps({'event': 'exec_result', 'command': 'rb_status', 'ok': True}) + '\n'
            match = json.dumps({'event': 'exec_result', 'command': 'rb_wave 3', 'ok': False,
                                 'reason': 'not_implemented'}) + '\n'
            os.write(w_fd, (other + wrong_cmd + match).encode('utf-8'))
            pc = relay.PipeClient('/unused', timeout_s=2.0)
            msg = pc._read_result(r_fd, 'rb_wave 3', time.time() + 2.0)
            self.assertEqual(msg, {'event': 'exec_result', 'command': 'rb_wave 3',
                                    'ok': False, 'reason': 'not_implemented'})
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_read_result_eof_returns_none(self):
        r_fd, w_fd = os.pipe()
        os.close(w_fd)  # sofortiges EOF, keine Gegenseite mehr
        try:
            pc = relay.PipeClient('/unused', timeout_s=2.0)
            msg = pc._read_result(r_fd, 'rb_wave 3', time.time() + 2.0)
            self.assertIsNone(msg)
        finally:
            os.close(r_fd)


class PipeClientResultTest(unittest.TestCase):
    """send_exec_and_wait: End-to-End inkl. Timeout-Verhalten (Issue #73)."""

    def test_send_exec_and_wait_no_reply_returns_none(self):
        d = tempfile.mkdtemp(prefix='rb73-pipe-noreply-')
        fifo = os.path.join(d, 'fake_pipe')
        try:
            os.mkfifo(fifo)
            harness_fd = os.open(fifo, os.O_RDWR)  # Leser da, aber keine Antwort
            try:
                pc = relay.PipeClient(fifo, timeout_s=0.5)
                n, msg = pc.send_exec_and_wait('rb_wave 3', 7)
                self.assertGreater(n, 0, 'Schreiben muss trotzdem erfolgreich sein')
                self.assertIsNone(msg, 'keine Antwort -> None statt Fehler/Haenger')
            finally:
                os.close(harness_fd)
        finally:
            os.unlink(fifo)
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

    def test_result_logging_ok_error_timeout(self):
        """_log_dispatch_result klassifiziert msg=None/ok/error korrekt (Issue #73)."""
        r = relay.Relay(self._cfg(), pipe=StubPipe())
        logged = []
        orig_log = relay.log
        relay.log = lambda msg: logged.append(msg)
        try:
            r._log_dispatch_result(1, 'rb_wave 1', None)
            r._log_dispatch_result(2, 'rb_wave 2', {'event': 'exec_result', 'command': 'rb_wave 2', 'ok': True})
            r._log_dispatch_result(3, 'rb_wave 3', {'event': 'exec_result', 'command': 'rb_wave 3',
                                                      'ok': False, 'reason': 'not_implemented'})
        finally:
            relay.log = orig_log
        self.assertIn('dispatch result cmd_id=1 status=timeout', logged[0])
        self.assertIn('dispatch result cmd_id=2 status=ok', logged[1])
        self.assertIn('dispatch result cmd_id=3 status=error', logged[2])
        self.assertIn('not_implemented', logged[2])


if __name__ == '__main__':
    unittest.main()
