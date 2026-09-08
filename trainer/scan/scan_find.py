#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan_find.py — Rift-Breaker-Prozess finden und Modul-/Basisadressen auflisten
(RE-Phase, Harness v0).

Zweck:
    - Laufenden Rift-Breaker-Prozess finden ("riftbreaker*.exe", z. B.
      "riftbreaker.exe" oder "RiftBreaker-Win64-Shipping.exe").
    - Prozess oeffnen (pymem) und Module inkl. Basisadresse/Groesse auflisten.
    - Basisadresse des Hauptmoduls ausgeben — Bezugspunkt fuer spaetere
      Offset-Berechnung (offsets sind relativ zur Modulbasis).

Nutzung (Windows, Python 3.10+):
    pip install pymem
    python scan_find.py                # ersten Treffer automatisch nehmen
    python scan_find.py riftbreaker    # Name-Teilstring, z.B. "riftbreaker"
    python scan_find.py 4821           # PID

Hinweise:
    - Das Spiel muss laufen und eine Karte geladen haben, damit die
      interessanten Module da sind.
    - Kein Admin noetig, wenn das Skript vom selben Windows-Benutzer
      laeuft wie das Spiel.
    - pymem oeffnet den Prozess mit PROCESS_ALL_ACCESS — funktioniert nur,
      weil das Spiel keinen Anti-Cheat hat.
"""

import sys

try:
    import pymem
    import pymem.process
except ImportError:
    sys.exit("Fehler: 'pymem' fehlt -> pip install pymem")

GAME_NAME_HINTS = ("riftbreaker", "riftbreak", "rift")


def find_pid(hint=None):
    """Erste PID, deren Prozessname einen Rift-Breaker-Hinweis enthaelt."""
    import psutil  # pymem bringt psutil als Abhaengigkeit mit

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info["name"] or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if not name.endswith(".exe"):
            continue
        if hint:
            if hint.lower() in name:
                return proc.info["pid"], name
        elif any(h in name for h in GAME_NAME_HINTS):
            return proc.info["pid"], name
    return None, None


def main():
    args = [a for a in sys.argv[1:]]
    hint = None
    pid = None
    if args:
        if args[0].isdigit():
            pid = int(args[0])
        else:
            hint = args[0]

    if pid is None:
        found_pid, found_name = find_pid(hint)
        if not found_pid:
            sys.exit(
                "Kein Rift-Breaker-Prozess gefunden.\n"
                "Tipp: Spiel starten, Karte laden, dann erneut ausfuehren;\n"
                "      oder PID direkt angeben: python scan_find.py <pid>"
            )
        pid = found_pid
        print(f"[+] Prozess gefunden: {found_name} (pid={pid})")

    try:
        pm = pymem.Pymem()
        pm.open_process_from_id(pid)
    except pymem.exception.PymemError as exc:
        sys.exit(f"[-] Prozess {pid} nicht oeffnbar: {exc}\n"
                 "    Gleicher Windows-Benutzer? Spiel laeuft?")

    proc_name = pymem.process.process_from_id(pid)
    try:
        proc_name = pymem.process.process_name(proc_name)
    except Exception:
        proc_name = "?"

    print(f"[+] Prozess offen: pid={pid} name={proc_name}")
    print(f"[+] Hauptmodul-Basis: 0x{pm.base_address:X}")

    try:
        exe_path = pymem.process.process_exe(pid)
        print(f"[+] Spiel-Pfad: {exe_path}")
    except Exception:
        pass

    print("\nModule (Name | Basis | Groesse):")
    try:
        # pymem.process.list_modules existiert in allen pymem-Versionen;
        # pm.list_modules() nur in neueren -> hier die sichere Variante.
        modules = pymem.process.list_modules(pm.process_handle)
    except Exception as exc:
        print(f"[-] Modul-Liste fehlgeschlagen: {exc}")
        modules = []

    main_base = pm.base_address
    interesting = []
    for m in modules:
        try:
            name = m.name
            base = m.baseaddress
            size = m.size
        except Exception:
            continue
        offset = base - main_base
        line = f"  {name:<45} 0x{base:012X}  {size:>10}  (rel. 0x{offset:X})"
        low = name.lower()
        if any(h in low for h in ("rift", "engine", "core", "mono", "lua",
                                  "game", "main")):
            interesting.append(line)
        print(line)

    print("\n[+] Kandidaten fuer Offset-Bezug (Hauptmodul/Engine/Lua):")
    for line in interesting:
        print(line)

    print("\nFazit: Basisadresse des Hauptmoduls merken (oberste Zeile).")
    print("Alle spaeteren Offsets in offsets.json sind relativ zu einer")
    print("Modulbasis -> Adresse - Modulbasis = Offset (siehe scan_values.py).")


if __name__ == "__main__":
    main()
