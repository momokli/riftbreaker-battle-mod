#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit-Tests fuer tools/session-recorder/session_recorder.py (Issue #280).

Reine Logik- und Datei-Tests ohne Docker/Netz/Spiel. Aufruf:
`python3 -m unittest test_session_recorder -v` aus tools/session-recorder/.

Abgedeckt (Test-Split "OHNE Player"):
  * komplette Event-Kette landet im JSONL,
  * `match_end` schliesst die Session (Summary + index.jsonl),
  * Container-Restart (Truncation/Append) verliert nichts und respektiert die
    offene Session,
  * Player-JOIN wird optional mitgeschnitten.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import session_recorder as sr

FIXTURE_CHAIN = [
    ("mod_load", "[server] [13:14:05.001] [info] LogService.cpp:71 - [LUA 'ConsoleService']: "
                 "[RBBATTLE] event=mod_load version=0.34.3 status=ok mode=sp econ_source=resource_obtained "
                 "econ_pool=0 hq_hp=100 hq_dead=false"),
    ("setup", "[server] [13:14:06.000] [info] L.cpp:1 - [RBBATTLE] event=setup difficulty=coop_normal "
              "creatures_difficulty=3"),
    ("commence", "[server] [13:14:07.000] [info] L.cpp:1 - [RBBATTLE] event=commence status=pending "
                 "hint=place_hq"),
    ("commence", "[server] [13:15:00.000] [info] L.cpp:1 - [RBBATTLE] event=commence status=ok"),
    ("wave", "[server] [13:16:00.000] [info] L.cpp:1 - [RBBATTLE] event=wave level=1 status=start "
             "spawned=5 skipped=0 anchor=border"),
    ("wave", "[server] [13:16:30.000] [info] L.cpp:1 - [RBBATTLE] event=wave level=1 status=done"),
    ("hq_hp", "[server] [13:17:00.000] [info] L.cpp:1 - [RBBATTLE] event=leak damage=8 hp_before=100 hp=92"),
    ("hq_hp", "[server] [13:17:01.000] [info] L.cpp:1 - [RBBATTLE] event=hq_hp hp=92 dead=false"),
    ("hq_dead", "[server] [13:20:00.000] [info] L.cpp:1 - [RBBATTLE] event=hq_hp hp=0 dead=true"),
    ("hq_dead", "[server] [13:20:01.000] [info] L.cpp:1 - [RBBATTLE] event=hq_dead status=match_end hp=0"),
    ("match_end", "[server] [13:20:02.000] [info] L.cpp:1 - [RBBATTLE] event=match_end "
                  "reason=hq_destroyed"),
]


def clock_factory(step=1.0):
    base = datetime(2026, 9, 12, 11, 0, 0, tzinfo=timezone.utc)
    state = {"n": 0}

    def _clock():
        state["n"] += 1
        dt = base + timedelta(seconds=state["n"] * step)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    return _clock


def ids():
    state = {"n": 0}

    def _make():
        state["n"] += 1
        return "20260912T1100%02dZ-t%02d" % (state["n"], state["n"])

    return _make


def write_lines(path, lines):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def read_jsonl(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class ClassifyTests(unittest.TestCase):
    def test_event_with_fields(self):
        rec = sr.classify(FIXTURE_CHAIN[0][1])
        self.assertEqual(rec["event"], "mod_load")
        self.assertEqual(rec["fields"]["version"], "0.34.3")
        self.assertEqual(rec["fields"]["hq_dead"], "false")

    def test_non_rbbattle_line_ignored(self):
        self.assertIsNone(sr.classify("[server] [info] GameplayState::ResumeGame"))

    def test_player_join_optional(self):
        line = "[server] [info] ServerGameplayState: OnNetPlayerCreateRequest '0':'crossover':'x'!"
        self.assertEqual(sr.classify(line)["event"], "player_join")
        self.assertEqual(sr.classify(line)["fields"]["player"], "crossover")
        self.assertIsNone(sr.classify(line, player_events=False))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = self.tmp.name
        self.log = os.path.join(self.base, "exor_logs.txt")
        self.out = os.path.join(self.base, "sessions")
        os.makedirs(self.out)

    def _recorder(self, player_events=True):
        return sr.SessionRecorder(self.out, log_names=[self.log], player_events=player_events,
                                  clock=clock_factory(), id_factory=ids())

    def test_full_chain_single_session(self):
        write_lines(self.log, [line for _ev, line in FIXTURE_CHAIN])
        rec = self._recorder()
        tailer = sr.LogTailer(rec, [self.log])
        tailer.poll_once()
        rec.close()
        jsonls = [f for f in os.listdir(self.out) if f.endswith(".jsonl") and f != "index.jsonl"]
        self.assertEqual(len(jsonls), 1)
        sid = jsonls[0][:-len(".jsonl")]
        records = read_jsonl(os.path.join(self.out, jsonls[0]))
        self.assertEqual(records[0]["type"], "session_start")
        self.assertEqual(records[0]["session_id"], sid)
        events = [r for r in records if r["type"] == "event"]
        self.assertEqual([r["event"] for r in events],
                         ["mod_load", "setup", "commence", "commence", "wave", "wave",
                          "leak", "hq_hp", "hq_hp", "hq_dead", "match_end"])
        self.assertEqual([r["seq"] for r in events], list(range(1, len(events) + 1)))
        self.assertTrue(all(r["session_id"] == sid for r in events))

    def test_match_end_closes_and_summary(self):
        write_lines(self.log, [line for _ev, line in FIXTURE_CHAIN])
        rec = self._recorder()
        sr.LogTailer(rec, [self.log]).poll_once()
        # Nach match_end ist die Session geschlossen — kein aktiver Zustand mehr.
        self.assertIsNone(rec.active_session_id)
        rec.close()
        summaries = [f for f in os.listdir(self.out) if f.endswith(".summary.json")]
        self.assertEqual(len(summaries), 1)
        with open(os.path.join(self.out, summaries[0]), encoding="utf-8") as fh:
            summary = json.load(fh)
        self.assertEqual(summary["end_reason"], "hq_destroyed")
        self.assertEqual(summary["waves"], 1)
        self.assertEqual(summary["hq_hp_min"], 0)
        self.assertTrue(summary["hq_dead"])
        self.assertEqual(summary["event_count"], 11)
        self.assertIsNotNone(summary["duration_s"])
        index = read_jsonl(os.path.join(self.out, "index.jsonl"))
        self.assertEqual(index[0]["session_id"], summary["session_id"])

    def test_match_end_then_new_commence_opens_new_session(self):
        lines = [line for _ev, line in FIXTURE_CHAIN[:-1]]  # bis hq_dead
        lines.append("[server] [x] [RBBATTLE] event=match_end reason=hq_destroyed")
        # zweite Runde (Commence-Announce einer neuen Karte)
        lines.append("[server] [x] [RBBATTLE] event=commence status=pending hint=place_hq")
        lines.append("[server] [x] [RBBATTLE] event=commence status=ok")
        write_lines(self.log, lines)
        rec = self._recorder()
        sr.LogTailer(rec, [self.log]).poll_once()
        second_id = rec.active_session_id
        rec.close()
        sessions = sorted(f for f in os.listdir(self.out)
                          if f.endswith(".jsonl") and f != "index.jsonl")
        self.assertEqual(len(sessions), 2)  # 1. Runde geschlossen, 2. Runde offen
        self.assertIn(second_id + ".jsonl", sessions)
        summaries = sorted(f for f in os.listdir(self.out) if f.endswith(".summary.json"))
        self.assertEqual(len(summaries), 1)  # zweite Session ist noch offen

    def test_restart_append_resumes_open_session_without_duplicates(self):
        write_lines(self.log, [line for _ev, line in FIXTURE_CHAIN[:6]])
        rec1 = self._recorder()
        tailer1 = sr.LogTailer(rec1, [self.log])
        tailer1.poll_once()
        sid = rec1.active_session_id
        rec1.close()

        # "Container-Restart" Nr. 1: Prozess neu, Log waechst (Append).
        rec2 = sr.SessionRecorder(self.out, log_names=[self.log], clock=clock_factory(),
                                  id_factory=ids())
        # Cursor aus .state.json: bereits gelesene Zeilen werden nicht wiederholt.
        self.assertEqual(rec2.get_cursor(self.log)["offset"], os.path.getsize(self.log))
        with open(self.log, "a", encoding="utf-8") as fh:
            fh.write(FIXTURE_CHAIN[6][1] + "\n")
            fh.write(FIXTURE_CHAIN[8][1] + "\n")
            fh.write(FIXTURE_CHAIN[10][1] + "\n")
        sr.LogTailer(rec2, [self.log]).poll_once()
        self.assertNotEqual(rec2.active_session_id, sid)  # match_end hat geschlossen
        self.assertIsNone(rec2.active_session_id)
        rec2.close()
        records = read_jsonl(os.path.join(self.out, sid + ".jsonl"))
        events = [r for r in records if r["type"] == "event"]
        self.assertEqual(len(events), 6 + 1 + 1 + 1)  # keine Duplikate
        self.assertEqual(events[-1]["event"], "match_end")

    def test_restart_truncation_reads_from_start(self):
        write_lines(self.log, [line for _ev, line in FIXTURE_CHAIN[:6]])
        rec1 = self._recorder()
        sr.LogTailer(rec1, [self.log]).poll_once()
        sid = rec1.active_session_id
        rec1.close()

        # "Container-Restart" Nr. 2: Log wird neu/leer geschrieben (Truncation).
        write_lines(self.log, ["[server] [0] [RBBATTLE] event=mod_load version=0.34.3 status=ok"])
        rec2 = sr.SessionRecorder(self.out, log_names=[self.log], clock=clock_factory(),
                                  id_factory=ids())
        sr.LogTailer(rec2, [self.log]).poll_once()
        # Session war nicht geschlossen → sie wird fortgefuehrt.
        self.assertEqual(rec2.active_session_id, sid)
        rec2.close()
        records = read_jsonl(os.path.join(self.out, sid + ".jsonl"))
        self.assertEqual([r["event"] for r in records if r["type"] == "event"][-1], "mod_load")
        self.assertEqual(rec2.get_cursor(self.log)["offset"], os.path.getsize(self.log))

    def test_player_join_recorded_in_session(self):
        write_lines(self.log, [FIXTURE_CHAIN[0][1],
                               "[server] [x] ServerGameplayState: OnNetPlayerCreateRequest "
                               "'0':'matheo':'abc'!"])
        rec = self._recorder()
        sr.LogTailer(rec, [self.log]).poll_once()
        sid = rec.active_session_id
        rec.close()
        events = [r["event"] for r in read_jsonl(os.path.join(self.out, sid + ".jsonl"))
                  if r["type"] == "event"]
        self.assertEqual(events, ["mod_load", "player_join"])

    def test_lone_match_end_is_ignored(self):
        write_lines(self.log, ["[server] [x] [RBBATTLE] event=match_end reason=hq_destroyed"])
        rec = self._recorder()
        sr.LogTailer(rec, [self.log]).poll_once()
        self.assertIsNone(rec.active_session_id)
        rec.close()
        self.assertEqual([f for f in os.listdir(self.out) if f.endswith(".jsonl")], [])


class CliTests(unittest.TestCase):
    def test_once_mode_writes_session(self):
        with tempfile.TemporaryDirectory() as base:
            log = os.path.join(base, "exor_logs.txt")
            out = os.path.join(base, "out")
            write_lines(log, [line for _ev, line in FIXTURE_CHAIN])
            rc = sr.main(["--log", log, "--out-dir", out, "--once"])
            self.assertEqual(rc, 0)
            names = os.listdir(out)
            self.assertTrue(any(n.endswith(".summary.json") for n in names))
            self.assertIn("index.jsonl", names)


if __name__ == "__main__":
    unittest.main()
