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


def main():
    args = sys.argv[1:]
    watch = "--watch" in args
    args = [a for a in args if a != "--watch"]

    # Optional: exec-Kommando als Argumente, z. B. "exec rb_wave 3".
    exec_cmd = None
    if args and args[0] == "exec":
        exec_cmd = " ".join(args[1:]) if len(args) > 1 else ""

    print(f"[pipe_client] verbinde mit {PIPE_NAME} ...", flush=True)
    print("[pipe_client] (blockiert bis rbbridge.dll injiziert ist - ggf. jetzt injizieren)", flush=True)
    fd = os.open(PIPE_NAME, os.O_RDWR | os.O_BINARY)
    print("[pipe_client] verbunden.", flush=True)

    buf = bytearray()

    # 1) ping -> erwartet pong
    send(fd, {"cmd": "ping"})
    line = recv_and_print(fd, buf)

    # 2) optional exec -> v0-Harness antwortet ok:false (no-op, RE-Punkt)
    if exec_cmd is not None:
        send(fd, {"cmd": "exec", "command": exec_cmd})
        line = recv_and_print(fd, buf)

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
