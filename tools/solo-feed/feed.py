#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rbbattle-solo-feed — tail the Riftbreaker solo-dev dedicated server log and
post game events into the Telegram SOLO DEV topic (312).

Read-only regarding the game server, except for the HQ-destroyed restart path:
the feed normally only reads `docker logs`; on HQ destruction it posts the
GAME OVER announce and triggers exactly ONE `docker restart` per match-end
(cooldown guard, no flapping). Issue #157.

Events posted (HTML):
  🟢 Server verfügbar (join 65.21.27.234:6321)   [entrypoint / NetServerGNS]
  👤 <name> gejoint                               [OnNetPlayerCreateRequest / Player '...']
  ▶️ Spiel gestartet                              [GameplayState::ResumeGame]
  🌊 Welle <n>                                    [[RBBATTLE] event=wave ...]
  💀 GAME OVER — HQ destroyed                     [[RBBATTLE] event=hq_dead ...]
  🆕 New game started — join now                  [after a restart: next server-up]

Only log lines newer than the stored cursor are processed; the cursor file
holds the docker timestamp of the last processed line. On first start the
cursor is set to "now", so old lines are never replayed. Identical messages
are suppressed within a per-kind window (join/wave spam guard). The restart
guard suppresses duplicate restarts within RESTART_COOLDOWN_S (flapping guard).

stdlib only. systemd unit: rbbattle-solo-feed.service (User=momo).
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE, ".env")
CURSOR_FILE = os.path.join(BASE, "cursor")

CONTAINER = "riftbreaker-dedicated"
CHAT_ID = "-1004485043253"
THREAD_ID = "312"
JOIN_ADDR = "65.21.27.234:6321"

RESTART_CMD = ["docker", "restart", CONTAINER]
# Genau EIN Restart pro Match-Ende; ein hq_dead innerhalb dieser Frist nach dem
# letzten Restart wird nicht erneut neu gestartet (kein Flapping).
RESTART_COOLDOWN_S = 60.0

DEFAULT_TEST_MESSAGE = "🤖 Solo-Dev-Feed aktiv — server events landen ab jetzt hier"

# identical message text is not re-sent within this window (seconds, per kind)
DEDUPE_WINDOW = {"up": 300, "join": 120, "start": 120, "wave": 600,
                 "game_over": 60, "new_game": 300}
DEFAULT_DEDUPE_WINDOW = 120

RE_TS = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\s(.*)$",
    re.S,
)
RE_JOIN_CREATE = re.compile(r"OnNetPlayerCreateRequest\s+'[^']*':'([^']+)'")
RE_JOIN_PLAYER = re.compile(r"ServerGameplayState: Player '[^']*':'([^']+)'")
RE_WAVE = re.compile(r"\[RBBATTLE\] event=wave(?: level=(\d+))?")
# HQ-Tod-Signal (#157). Bewusst `event=hq_dead` (nicht `match_end`), damit die
# mod_load-Zeile (`event=mod_load ... hq_dead=false`) NICHT matcht.
RE_HQ_DEAD = re.compile(r"\[RBBATTLE\] event=hq_dead(?: |$)")

GAME_OVER_TEXT = "💀 GAME OVER — HQ destroyed"
NEW_GAME_TEXT = "🆕 New game started — join now"

# real log lines (from planet, riftbreaker-dedicated) used by --selftest
SAMPLE_LINES = [
    "[entrypoint] UDP port 6321 is open — server should accept connections",
    "[server] [info] NetServerGNS.cpp:173 - [NetServerGNS] listening for P2P connection as 'ip:::1'",
    "[server] [info] NetServerGNS.cpp:182 - [NetServerGNS] running as: ip:[::]:6321",
    "[server] [info] ServerGameplayState.cpp:867 - "
    "ServerGameplayState: OnNetPlayerCreateRequest '0':'crossover'!",
    "[server] [info] ServerGameplayState.cpp:966 - ServerGameplayState: Player '0':'crossover'!",
    "[server] [info] GameplayState.cpp:1667 - GameplayState::ResumeGame",
    "[server] [info] LogService.cpp:71 - [LUA 'ConsoleService']: "
    "[RBBATTLE] event=wave level=3 status=start",
    "[server] [info] LogService.cpp:71 - [LUA 'ConsoleService']: "
    "[RBBATTLE] event=wave level=1 status=done spawned=5 skipped=0",
    "[server] [info] LogService.cpp:71 - [LUA 'ConsoleService']: "
    "[RBBATTLE] event=hq_dead status=match_end hp=0",
]


def log(msg):
    sys.stderr.write(time.strftime("%Y-%m-%dT%H:%M:%S%z") + " " + msg + "\n")
    sys.stderr.flush()


def parse_ts(s):
    """RFC3339(Nano) -> aware datetime (nanoseconds truncated to microseconds)."""
    s = s.strip()
    s = re.sub(r"(\.\d{6})\d+(?=Z|[+-]\d{2}:\d{2}$)", r"\1", s)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def split_ts(line):
    m = RE_TS.match(line)
    if not m:
        return None, line
    return m.group(1), m.group(2)


def read_env():
    env = {}
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def classify(msg):
    """Map a log message to (kind, telegram_text) or None."""
    if (
        "UDP port 6321 is open" in msg
        or "[NetServerGNS] listening" in msg
        or "[NetServerGNS] running as: ip:[::]:6321" in msg
    ):
        return "up", "🟢 Server verfügbar (join %s)" % JOIN_ADDR
    m = RE_JOIN_CREATE.search(msg) or RE_JOIN_PLAYER.search(msg)
    if m:
        return "join", "👤 %s gejoint" % html.escape(m.group(1))
    if "GameplayState::ResumeGame" in msg:
        return "start", "▶️ Spiel gestartet"
    m = RE_WAVE.search(msg)
    if m:
        n = m.group(1)
        return "wave", ("🌊 Welle %s" % n) if n else "🌊 Neue Welle"
    if RE_HQ_DEAD.search(msg):
        return "game_over", GAME_OVER_TEXT
    return None


class Deduper:
    def __init__(self):
        self._last = {}

    def allow(self, key, window, now=None):
        now = now or time.time()
        prev = self._last.get(key)
        if prev is not None and (now - prev) < window:
            return False
        self._last[key] = now
        return True

    def release(self, key):
        self._last.pop(key, None)


class RestartGuard:
    """Genau EIN Restart pro Match-Ende, mit Cooldown gegen Flapping (#157).

    `restart` ist ein Callable -> (ok, detail); `now` liefert Epoch-Sekunden
    (injectable fuer Tests). `on_game_over` startet nur neu, wenn der letzte
    Restart laenger als `cooldown_s` zurueckliegt; `take_new_game` feuert
    genau einmal nach einem Restart (fuer die "New game started"-Announce).
    """

    def __init__(self, restart, now=None, cooldown_s=RESTART_COOLDOWN_S):
        self.restart = restart
        self.now = now if now is not None else time.time
        self.cooldown_s = cooldown_s
        self.last_restart = float("-inf")  # erster Restart immer erlaubt
        self.pending_new_game = False

    def on_game_over(self):
        """HQ-Tod behandeln. Liefert einen Status-String.

        - "cooldown"       : innerhalb der Cooldown-Frist -> KEIN Restart.
        - "restarted"      : Restart ausgeloest (genau einmal).
        - "restart_failed" : Restart-Kommando fehlgeschlagen.
        """
        now = self.now()
        if now - self.last_restart < self.cooldown_s:
            return "cooldown"
        ok, detail = self.restart()
        self.last_restart = now
        if ok:
            self.pending_new_game = True
            return "restarted"
        return "restart_failed: %s" % detail

    def take_new_game(self):
        """True genau einmal nach einem Restart (fuer die "New game"-Announce)."""
        if self.pending_new_game:
            self.pending_new_game = False
            return True
        return False


def restart_server():
    """`docker restart` des Solo-Dev-Servers; liefert (ok, detail)."""
    try:
        proc = subprocess.run(RESTART_CMD, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
        ok = proc.returncode == 0
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        return ok, detail
    except Exception as exc:  # noqa: BLE001 - operational tool, broad is fine
        return False, str(exc)


def send_telegram(token, text, attempts=3):
    """Send one HTML message into the topic. Returns (ok, result_dict)."""
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "message_thread_id": THREAD_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    for attempt in range(attempts):
        req = urllib.request.Request(
            "https://api.telegram.org/bot%s/sendMessage" % token, data=data)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode())
            return payload.get("ok") is True, (payload.get("result") or {})
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode()[:300]
            except Exception:
                pass
            log("telegram http error %s: %s" % (exc.code, detail))
            if exc.code == 429 and attempt + 1 < attempts:
                time.sleep(5)
                continue
            if exc.code < 500:
                return False, {}
        except Exception as exc:
            log("telegram error: %s" % exc)
        if attempt + 1 < attempts:
            time.sleep(2 ** attempt)
    return False, {}


def _send(token, text, dedupe, window):
    """Dedupe + send one message; returns True if actually sent."""
    if not dedupe.allow(text, window):
        return False
    ok, res = send_telegram(token, text)
    if ok:
        log("sent [%s] (message_id=%s)" % (text, res.get("message_id")))
    else:
        dedupe.release(text)
        log("send failed [%s]" % text)
    return ok


def handle_line(msg, token, dedupe, guard):
    """Classify + announce + restart-orchestration for one log line.

    Returns the kind that was sent, or None if the line was ignored.
    """
    hit = classify(msg)
    if not hit:
        return None
    kind, text = hit
    window = DEDUPE_WINDOW.get(kind, DEFAULT_DEDUPE_WINDOW)
    if not dedupe.allow(text, window):
        log("dedupe-skip [%s] %s" % (kind, text))
        return None
    ok, res = send_telegram(token, text)
    if ok:
        log("sent [%s] %s (message_id=%s)" % (kind, text, res.get("message_id")))
    else:
        dedupe.release(text)
        log("send failed [%s] %s" % (kind, text))

    if kind == "game_over":
        status = guard.on_game_over()
        log("restart-guard [%s] %s" % (kind, status))

    if kind in ("up", "start") and guard.take_new_game():
        _send(token, NEW_GAME_TEXT, dedupe, DEDUPE_WINDOW["new_game"])

    return kind


def read_cursor():
    try:
        with open(CURSOR_FILE) as f:
            s = f.read().strip()
        if s:
            parse_ts(s)
            return s
    except FileNotFoundError:
        return None
    except Exception as exc:
        log("unreadable cursor (%s) — starting from now" % exc)
    return None


def write_cursor(ts):
    tmp = CURSOR_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(ts + "\n")
    os.replace(tmp, CURSOR_FILE)


def follow(token, dedupe, guard, cursor):
    cursor_dt = parse_ts(cursor)
    cmd = ["docker", "logs", "-f", "-t", "--since", cursor, CONTAINER]
    log("starting: %s" % " ".join(cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1)
    caught_up = False
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            if not line:
                continue
            ts, msg = split_ts(line)
            line_dt = None
            if ts:
                line_dt = parse_ts(ts)
                if not caught_up:
                    if line_dt <= cursor_dt:
                        continue
                    caught_up = True
            else:
                log("no-ts line: %s" % msg[:200])
            try:
                handle_line(msg, token, dedupe, guard)
            except Exception as exc:
                log("handle error: %s" % exc)
            if line_dt:
                cursor_dt = line_dt
                write_cursor(ts)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        log("docker logs stream ended (rc=%s)" % proc.returncode)


def run_forever(token):
    dedupe = Deduper()
    guard = RestartGuard(restart_server)
    cursor = read_cursor()
    if cursor is None:
        cursor = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        write_cursor(cursor)
        log("no cursor (first start) — processing only new lines from %s" % cursor)
    else:
        log("resuming from cursor %s" % cursor)
    while True:
        try:
            follow(token, dedupe, guard, cursor)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log("stream error: %s" % exc)
        cursor = read_cursor() or cursor
        time.sleep(3)


def parse_test(n):
    cmd = ["docker", "logs", "--tail", str(n), "-t", CONTAINER]
    print("== parse-test: %s (dry-run, no messages sent) ==" % " ".join(cmd))
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120)
    except Exception as exc:
        print("!! docker logs failed: %s" % exc)
        return 1
    lines = (out.stdout + out.stderr).splitlines()
    if out.returncode != 0:
        print("!! docker logs rc=%s: %s" % (out.returncode, out.stderr.strip()[:200]))
    counts = {}
    for line in lines:
        _ts, msg = split_ts(line)
        hit = classify(msg)
        if hit:
            kind, text = hit
            counts[kind] = counts.get(kind, 0) + 1
            print("MATCH kind=%s -> %s" % (kind, text))
    summary = ", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "none"
    print("-- summary: lines=%d, matches: %s, sent: 0" % (len(lines), summary))
    return 0


def replay(path):
    print("== replay: %s (simulation only, no messages sent) ==" % path)
    dedupe = Deduper()
    fake_restarts = []

    def fake_restart():
        fake_restarts.append(time.time())
        return True, ""

    guard = RestartGuard(fake_restart)
    sends = skips = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            ts, msg = split_ts(line)
            hit = classify(msg)
            if not hit:
                continue
            kind, text = hit
            now = parse_ts(ts).timestamp() if ts else None
            window = DEDUPE_WINDOW.get(kind, DEFAULT_DEDUPE_WINDOW)
            if dedupe.allow(text, window, now=now):
                sends += 1
                print("REPLAY %s SEND [%s] %s" % (ts or "-", kind, text))
            else:
                skips += 1
                print("REPLAY %s dedupe-skip [%s] %s" % (ts or "-", kind, text))
            if kind == "game_over":
                print("REPLAY restart-guard [%s] %s" % (kind, guard.on_game_over()))
            if kind in ("up", "start") and guard.take_new_game():
                print("REPLAY SEND [new_game] %s" % NEW_GAME_TEXT)
                sends += 1
    print("-- summary: sends=%d, dedupe-skips=%d, restarts=%d" % (
        sends, skips, len(fake_restarts)))
    return 0


def selftest():
    print("== selftest: %d sample lines (no messages sent) ==" % len(SAMPLE_LINES))
    counts = {}
    for msg in SAMPLE_LINES:
        hit = classify(msg)
        kind, text = hit if hit else ("none", "-")
        counts[kind] = counts.get(kind, 0) + 1
        print("SELFTEST kind=%s -> %s" % (kind, text))
    summary = ", ".join("%s=%d" % kv for kv in sorted(counts.items()))
    print("-- summary: %s" % summary)

    # Restart-Guard-Logik ohne Docker/Netz (Determinismus via fake `now`).
    fake_now = [0.0]
    calls = []

    def fake_restart():
        calls.append(fake_now[0])
        return True, ""

    guard = RestartGuard(fake_restart, now=lambda: fake_now[0], cooldown_s=60.0)
    assert guard.on_game_over() == "restarted"
    assert len(calls) == 1
    assert guard.on_game_over() == "cooldown"  # flapping guard
    assert len(calls) == 1
    fake_now[0] += 61.0
    assert guard.on_game_over() == "restarted"  # new match after cooldown
    assert len(calls) == 2
    assert guard.take_new_game() is True
    assert guard.take_new_game() is False  # one-shot announce
    print("SELFTEST restart-guard: OK")
    return 0


def check():
    ok = True
    env = read_env()
    print("env_file=%s exists=%s" % (ENV_FILE, os.path.exists(ENV_FILE)))
    token_present = bool(env.get("TELEGRAM_BOT_TOKEN"))
    print("token_present=%s" % token_present)
    ok = ok and token_present
    try:
        probe = subprocess.run(
            ["docker", "logs", "--tail", "1", "-t", CONTAINER],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30)
        print("docker_logs_rc=%d" % probe.returncode)
        ok = ok and probe.returncode == 0
    except Exception as exc:
        print("docker_logs_error=%s" % exc)
        ok = False
    try:
        tmp = os.path.join(BASE, ".write_test")
        with open(tmp, "w") as f:
            f.write("x")
        os.remove(tmp)
        print("dir_writable=True")
    except Exception as exc:
        print("dir_writable=False (%s)" % exc)
        ok = False
    print("check=%s" % ("ok" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="validate env/docker/dir access and exit")
    ap.add_argument("--selftest", action="store_true",
                    help="match regexes against built-in sample lines and exit")
    ap.add_argument("--parse-test", type=int, metavar="N",
                    help="match regexes against the last N log lines (no sending) and exit")
    ap.add_argument("--replay", metavar="FILE",
                    help="simulate processing of a log file incl. dedupe/restart-guard (no sending) and exit")
    ap.add_argument("--test-message", nargs="?", const=DEFAULT_TEST_MESSAGE, metavar="TEXT",
                    help="send one Telegram message into the topic and exit")
    args = ap.parse_args()

    if args.check:
        return check()
    if args.selftest:
        return selftest()
    if args.parse_test is not None:
        return parse_test(args.parse_test)
    if args.replay:
        return replay(args.replay)
    if args.test_message:
        env = read_env()
        token = env.get("TELEGRAM_BOT_TOKEN")
        if not token:
            log("TELEGRAM_BOT_TOKEN missing in %s" % ENV_FILE)
            return 1
        ok, res = send_telegram(token, html.escape(args.test_message))
        print("test-message: ok=%s message_id=%s thread=%s text=%s" % (
            ok, res.get("message_id"),
            res.get("message_thread_id", THREAD_ID), args.test_message))
        return 0 if ok else 1

    env = read_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        log("TELEGRAM_BOT_TOKEN missing in %s" % ENV_FILE)
        return 1
    run_forever(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
