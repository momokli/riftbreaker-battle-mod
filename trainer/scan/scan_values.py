#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan_values.py — interaktiver Wert-Scan fuer die RE-Phase (Harness v0).

Ablauf (Cheat-Engine-Stil, rund um Ressourcen/Score):
    1. Prozess finden (riftbreaker*.exe) und oeffnen (pymem).
    2. Erst-Scan: aktuellen Wert eingeben (z.B. Kohlenstoff 320).
    3. Im Spiel den Wert veraendern (Ressource verbrauchen/verdienen).
    4. Rescan-Modus waehlen: neuerWert / increased / decreased / unchanged.
    5. Kandidatenliste schrumpft -> Werte live anzeigen und verifizieren,
       dann als Offset (relativ zur Modulbasis) in offsets.json speichern.

Nutzung (Windows, Python 3.10+):
    pip install pymem
    python scan_values.py                # Prozess automatisch finden
    python scan_values.py riftbreaker    # oder Name-Teilstring / PID

Datentypen: 4 = int32 (Standard, Ressourcen meist int32), 8 = int64,
f = float (nur wenn int nichts findet).

offsets.json (trainer/scan/): Eintraege sind HEX-Offsets relativ zur Basis
des Moduls, das die Adresse enthaelt (meist das Hauptmodul). Modulname,
Datentyp und Fund-Kontext schreibt das Skript in "notes".

Hinweise:
    - ReadProcessMemory/WriteProcessMemory nur im eigenen Spielprozess;
      das Spiel hat keinen Anti-Cheat.
    - Ergebnisse in offsets.json speichern und ins Repo committen
      (Live-Session-Ablauf: scan/README.md).
    - Geschwindigkeit: Der Erst-Scan liest den ganzen Prozess-Speicher
      (8-MB-Chunks, in Python trotzdem einige Sekunden bis Minuten).
      Vorher Karte laden und Spiel pausieren -> weniger Speicher-Churn.
"""

import ctypes
import json
import os
import struct
import sys
from ctypes import wintypes

try:
    import pymem
    import pymem.exception
    import pymem.process
except ImportError:
    sys.exit("Fehler: 'pymem' fehlt -> pip install pymem")

OFFSETS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "offsets.json")

# Wertebreiten (kleines Endian): name -> (bytes, struct-Format)
TYPE_FMT = {"4": (4, "<i"), "8": (8, "<q"), "f": (4, "<f")}

MAX_CANDIDATES = 2_000_000  # Treffer-Obergrenze (Speicher-/Speed-Schutz)

MEM_COMMIT = 0x1000
PROT_READABLE = 0x02 | 0x04 | 0x20 | 0x40 | 0x80  # R/RW/XC-R usw. (niedriges Byte)
PAGE_GUARD = 0x100
READ_CHUNK = 8 * 1024 * 1024


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


# ----------------------------------------------------------------------
# Prozess finden / oeffnen
# ----------------------------------------------------------------------

def find_riftbreaker_pid():
    import psutil  # pymem-Abhaengigkeit

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info["name"] or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name.endswith(".exe") and "riftbreak" in name:
            return proc.info["pid"]
    return None


def open_game(argv):
    pid = None
    for a in argv:
        if a.isdigit():
            pid = int(a)

    if pid is None:
        pid = find_riftbreaker_pid()
        if not pid:
            sys.exit("Kein riftbreaker*-Prozess gefunden. Spiel starten und "
                     "Karte laden; oder PID/Name als Argument angeben.")

    pm = pymem.Pymem()
    try:
        pm.open_process_from_id(pid)
    except pymem.exception.PymemError as exc:
        sys.exit(f"Prozess {pid} nicht oeffnbar: {exc}")
    print(f"[+] Prozess offen: pid={pid}, Hauptmodul-Basis=0x{pm.base_address:X}")
    return pm


# ----------------------------------------------------------------------
# Speicher-Zugriff (VirtualQueryEx + ReadProcessMemory via ctypes)
# ----------------------------------------------------------------------

def iter_readable_regions(pm):
    """Yield (start, size) aller committed, lesbaren, nicht-guard-Regionen."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    mbi = MEMORY_BASIC_INFORMATION()
    addr = ctypes.c_void_p(0)
    while True:
        ret = k32.VirtualQueryEx(pm.process_handle, addr,
                                 ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not ret:
            break
        if mbi.State == MEM_COMMIT and mbi.RegionSize > 0:
            prot_low = mbi.Protect & 0xFF
            if (prot_low & PROT_READABLE) and not (mbi.Protect & PAGE_GUARD):
                yield mbi.BaseAddress, mbi.RegionSize
        nxt = mbi.BaseAddress + mbi.RegionSize
        if nxt <= mbi.BaseAddress:  # Overflow-Schutz
            break
        addr = ctypes.c_void_p(nxt)


def read_bytes_at(pm, address, size):
    """Lesen via ReadProcessMemory; None bei Fehler oder Teillesen."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buf = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t(0)
    ok = k32.ReadProcessMemory(pm.process_handle, ctypes.c_void_p(address),
                               buf, size, ctypes.byref(read))
    if not ok or read.value != size:
        return None
    return buf.raw


def read_typed(pm, address, tname):
    """Wert an Adresse als gewaehlten Typ lesen (None bei Fehler)."""
    nbytes, fmt = TYPE_FMT[tname]
    data = read_bytes_at(pm, address, nbytes)
    if data is None:
        return None
    return struct.unpack(fmt, data)[0]


# ----------------------------------------------------------------------
# Scan-Kern
# ----------------------------------------------------------------------

def first_scan(pm, value, tname):
    """Vollscan: liefert Liste absoluter Adressen mit passendem Byte-Muster."""
    nbytes, fmt = TYPE_FMT[tname]
    try:
        pattern_bytes = struct.pack(fmt, value)
    except struct.error:
        print(f"  Wert {value!r} passt nicht zum Typ {tname}.")
        return []

    hits = []
    for start, size in iter_readable_regions(pm):
        pos = 0
        while pos < size:
            chunk = min(size - pos, READ_CHUNK)
            data = read_bytes_at(pm, start + pos, chunk)
            if data:
                off = 0
                while True:
                    i = data.find(pattern_bytes, off)
                    if i < 0:
                        break
                    hits.append(start + pos + i)
                    off = i + 1
                    if len(hits) >= MAX_CANDIDATES:
                        print(f"[!] Trefferlimit {MAX_CANDIDATES} erreicht - "
                              "Abbruch (Wert zu haeufig? Typ falsch?)")
                        return hits
            pos += chunk
    return hits


def rescan_list(pm, hits, prev, tname, mode, value):
    """Rescan der Kandidatenliste; liefert (neue_hits, neues_prev)."""
    new_hits = []
    new_prev = {}
    for addr in hits:
        cur = read_typed(pm, addr, tname)
        if cur is None:
            continue
        old = prev.get(addr)
        keep = False
        if mode == "v":
            keep = cur == value
        elif mode == "u":
            keep = old is not None and cur == old
        elif mode == "i":
            keep = old is not None and cur > old
        elif mode == "d":
            keep = old is not None and cur < old
        if keep:
            new_hits.append(addr)
        new_prev[addr] = cur
    return new_hits, new_prev


# ----------------------------------------------------------------------
# Anzeige / Speichern
# ----------------------------------------------------------------------

def module_of(pm, addr):
    """(Modulname, Basis) falls addr in einem gelisteten Modul liegt."""
    try:
        for m in pymem.process.list_modules(pm.process_handle):
            if m.baseaddress <= addr < m.baseaddress + m.size:
                return m.name, m.baseaddress
    except Exception:
        pass
    return None, None


def show_candidates(pm, hits, tname, limit=15):
    for addr in hits[:limit]:
        i32 = read_typed(pm, addr, "4")
        i64 = read_typed(pm, addr, "8")
        f32 = read_typed(pm, addr, "f")
        name, base = module_of(pm, addr)
        rel = f"rel. {name} 0x{addr - base:X}" if base else "ausserhalb Module"
        print(f"  0x{addr:X}  int32={i32!r:<12} int64={i64!r:<12} "
              f"float={f32!r:<12}  {rel}")
    if len(hits) > limit:
        print(f"  ... und {len(hits) - limit} weitere")


def load_offsets():
    if os.path.exists(OFFSETS_PATH):
        try:
            with open(OFFSETS_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[!] offsets.json nicht lesbar ({exc}) - starte neu.")
    return {"version": 1, "game": "riftbreaker", "notes": "", "offsets": {}}


def save_offsets(data):
    with open(OFFSETS_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"[+] offsets.json aktualisiert: {OFFSETS_PATH}")


def save_candidates(pm, hits, tname):
    print(f"\n=== Speichern ({len(hits)} Kandidaten) ===")
    show_candidates(pm, hits, tname, limit=10)
    raw = input("Kandidatenzeile 0..n speichern oder 'all' (Enter=0): ").strip()
    if raw == "all":
        chosen = list(hits)
    else:
        try:
            idx = int(raw) if raw else 0
            chosen = [hits[idx]] if 0 <= idx < len(hits) else []
        except ValueError:
            chosen = []
    if not chosen:
        print("  Nichts gespeichert.")
        return

    data = load_offsets()
    notes = data.get("notes", "")
    type_label = {"4": "int32", "8": "int64", "f": "float"}[tname]
    for addr in chosen:
        name, base = module_of(pm, addr)
        rel = addr - base if base else addr - pm.base_address
        label = input(f"Name fuer 0x{addr:X} (rel. {name} 0x{rel:X}) "
                      "[Enter=skip]: ").strip()
        if not label:
            continue
        data["offsets"][label] = f"0x{rel:X}"
        notes += (f"\n- {label}: 0x{rel:X} relativ zu {name or '?'} "
                  f"(absolut 0x{addr:X}), Typ {type_label}, "
                  f"gefunden am {__import__('datetime').date.today()}")
    data["notes"] = notes.strip()
    save_offsets(data)


# ----------------------------------------------------------------------
# Interaktiver Ablauf
# ----------------------------------------------------------------------

def ask(prompt, choices, default=None):
    while True:
        raw = input(prompt).strip().lower()
        if not raw and default is not None:
            return default
        if raw in choices:
            return raw
        print(f"  Moeglich: {', '.join(choices)}")


def interactive(pm):
    print("\n=== Wert-Scan (interaktiv) ===")
    print("Tipp: Karte laden + Spiel pausieren vor dem Erst-Scan.")
    tname = ask("Datentyp [4=int32, 8=int64, f=float] (4): ",
                ["4", "8", "f"], default="4")

    hits = []     # absolute Adressen der aktuellen Kandidaten
    prev = {}     # addr -> Wert der letzten Runde (fuer i/d/u)

    while True:
        print("\n---")
        mode = ask("Aktion [s=Erstwert, v=neuerWert, i=increased, "
                   "d=decreased, u=unchanged, l=listen, q=quit] (s): ",
                   ["s", "v", "i", "d", "u", "l", "q"], default="s")

        if mode == "q":
            break
        if mode == "l":
            if hits:
                show_candidates(pm, hits, tname)
            else:
                print("  Noch keine Kandidaten.")
            continue

        if mode == "s":
            raw = input("  Wert eingeben (z.B. 320): ").strip()
            try:
                value = float(raw) if tname == "f" else int(raw)
            except ValueError:
                print("  Keine Zahl - Abbruch dieses Scans.")
                continue
            hits = first_scan(pm, value, tname)
            prev = {}
            print(f"  Erst-Scan: {len(hits)} Treffer")
            if len(hits) <= 15:
                show_candidates(pm, hits, tname)
            continue

        if not hits:
            print("  Keine Kandidaten -> zuerst 's' (Erst-Scan).")
            continue
        if mode == "v":
            raw = input("  Neuen Wert eingeben (im Spiel veraendern!): ").strip()
            try:
                value = float(raw) if tname == "f" else int(raw)
            except ValueError:
                print("  Keine Zahl.")
                continue
        else:
            value = None

        hits, prev = rescan_list(pm, hits, prev, tname, mode, value)
        print(f"  Rescan: {len(hits)} Treffer")
        if len(hits) <= 15:
            show_candidates(pm, hits, tname)
        if len(hits) == 1:
            print("  -> EINDEUTIG! Speichern: Menuepunkt weiter unten, q zum "
                  "Beenden-Auswahl.")

    if hits:
        save_candidates(pm, hits, tname)
    else:
        print("\nKeine Kandidaten zum Speichern - Ende.")


def main():
    pm = open_game(sys.argv[1:])
    try:
        interactive(pm)
    except KeyboardInterrupt:
        print("\nAbbruch durch Benutzer.")
    except EOFError:
        print("\nEOF -> Ende.")


if __name__ == "__main__":
    main()
