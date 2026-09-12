#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipe_client.py - Test-Client fuer die rbbridge-Named-Pipe (Baustein 04).

Verbindet sich mit der Named Pipe \\.\pipe\rbbattle, die die injizierte
rbbridge.dll als Server bereitstellt, sendet ping/exec-Kommandos und druckt
die JSON-Antworten. Nur Standardbibliothek (Windows-Python, x64); die Pipe
wird mit plain os.open() geoeffnet (blockierend, byte mode).

Testablauf OHNE Spiel (siehe README.md):
  0) Standalone (keine Injection): rbbridge_standalone.exe starten
     (Build: rbbridge.c mit -DRBBRIDGE_STANDALONE) - Pipe-Server-Logik
     identisch zur DLL, kein notepad/injector noetig.
  1) (Test 1) rbbridge.dll in notepad.exe injizieren:
       injector.exe <pid> C:\\pfad\\rbbridge.dll
  2) diesen Client starten:
       python pipe_client.py                -> ping, erwartet pong
       python pipe_client.py exec rb_wave 3 -> zusaetzlich exec (v0: ok:false)
       python pipe_client.py --watch        -> danach weiterlesen (Heartbeats)
       python pipe_client.py --selftest     -> argv->Command-Verbindung pruefen

Argument-Handling (Issue #18): die Kommando-Tokens ab argv[2] werden zu
EINEM String verbunden (build_exec_command), damit unquotierte Argumente
erhalten bleiben - `exec rb_wave 3` -> {"cmd":"exec","command":"rb_wave 3"}.

Hinweis: Laeuft kein Pipe-Server (EXE nicht gestartet bzw. DLL nicht
injiziert), blockiert os.open() bis der Server bereit ist (Windows-Semantik
fuer Named Pipes) - also Server zuerst starten oder das Skript einfach
laufen lassen und dann starten/injizieren.

Protokoll v0: eine JSON-Nachricht pro Zeile, UTF-8, '\n' abgeschlossen
(siehe trainer/protocol.md bzw. README.md in diesem Ordner).
"""

import json
import os
import sys

PIPE_NAME = r"\\.\pipe\rbbattle"
CHUNK = 4096


def read_line(fd, buf):
    """Liest eine Zeile (bis '\\n') von der Pipe; blockiert bis Daten da sind."""
    while True:
        nl = buf.find(b"\n")
        if nl != -1:
            line = bytes(buf[:nl])
            del buf[: nl + 1]
            return line.rstrip(b"\r").decode("utf-8", "replace")
        chunk = os.read(fd, CHUNK)
        if not chunk:
            raise ConnectionError("Pipe geschlossen (Server beendet?)")
        buf.extend(chunk)


def send(fd, obj):
    payload = json.dumps(obj, ensure_ascii=False) + "\n"
    os.write(fd, payload.encode("utf-8"))
    print("->", payload.strip(), flush=True)


def recv_and_print(fd, buf):
    line = read_line(fd, buf)
    print("<-", line, flush=True)
    try:
        parsed = json.loads(line)
        print("   (json:", json.dumps(parsed, ensure_ascii=False), ")", flush=True)
    except ValueError:
        pass
    return line


def build_exec_command(args):
    """Baut den exec-Command-String aus den CLI-Argumenten.

    Konvention: `pipe_client.py exec <command...>` — `args` ist sys.argv[1:],
    args[0] == "exec" ist der Subcommand-Marker. Die eigentlichen
    Kommando-Tokens stehen ab args[1] (= argv ab Index 2) und werden zu
    EINEM String verbunden, damit unquotierte Argumente erhalten bleiben
    (Issue #18: `exec_cmd_client rb_wave 3` verlor "3" -> level 1).

    Liefert den Command-String oder None, wenn kein exec-Kommando angegeben ist.
    """
    if args and args[0] == "exec":
        return " ".join(args[1:]) if len(args) > 1 else ""
    return None


def _selftest():
    """Prueft die argv->Command-Verbindung ohne Pipe-Zugriff (Issue #18)."""
    cases = [
        (["exec", "rb_wave", "3"], "rb_wave 3"),   # unquotiert: Argument bleibt erhalten
        (["exec", "rb_wave"], "rb_wave"),           # nur Kommando, kein Argument
        (["exec", "rb_wave 3"], "rb_wave 3"),       # bereits gequotet (EIN Argument)
        (["exec", "go"], "go"),
        (["exec", "round_start", "2"], "round_start 2"),
        (["exec"], ""),                             # exec ohne Kommando
        ([], None),                                 # kein exec
        (["ping"], None),                           # kein exec-Subcommand
    ]
    failed = 0
    for args, expected in cases:
        got = build_exec_command(args)
        ok = got == expected
        status = "OK  " if ok else "FAIL"
        print(f"[selftest] {status} build_exec_command({args!r}) -> {got!r} (erwartet {expected!r})", flush=True)
        if not ok:
            failed += 1
    print(f"[selftest] {'OK' if failed == 0 else str(failed) + ' FEHLER'}", flush=True)
    return failed


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        sys.exit(1 if _selftest() else 0)

    watch = "--watch" in args
    args = [a for a in args if a != "--watch"]

    # Optional: exec-Kommando als Argumente, z. B. "exec rb_wave 3".
    exec_cmd = build_exec_command(args)

    print(f"[pipe_client] verbinde mit {PIPE_NAME} ...", flush=True)
    print("[pipe_client] (blockiert bis rbbridge.dll injiziert ist - ggf. jetzt injizieren)", flush=True)
    fd = os.open(PIPE_NAME, os.O_RDWR | os.O_BINARY)
    print("[pipe_client] verbunden.", flush=True)

    buf = bytearray()

    # 1) ping -> erwartet pong
    send(fd, {"cmd": "ping"})
    recv_and_print(fd, buf)

    # 2) optional exec -> dispatch_exec loest ConsoleService per AOB/RTTI auf:
    #    ohne Spielmodul ok:false (console_service_not_found), im Spielprozess ok:true
    if exec_cmd is not None:
        send(fd, {"cmd": "exec", "command": exec_cmd})
        recv_and_print(fd, buf)

    # 3) optional weiterlesen (Heartbeat-Events alle ~5 s)
    if watch:
        print("[pipe_client] --watch: lese weiter (Strg+C zum Beenden)...", flush=True)
        try:
            while True:
                recv_and_print(fd, buf)
        except KeyboardInterrupt:
            print("\n[pipe_client] beendet.")

    os.close(fd)
    print("[pipe_client] fertig.")


if __name__ == "__main__":
    try:
        main()
    except ConnectionError as e:
        print(f"[pipe_client] FEHLER: {e}", flush=True)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[pipe_client] abgebrochen.")
