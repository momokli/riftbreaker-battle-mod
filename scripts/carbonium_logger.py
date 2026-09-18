#!/usr/bin/env python3
"""Pollt POST /get_state der Server-I/O-Bridge in festem Intervall und haengt
jede Antwort als Zeile an eine CSV an — fuer eine echte Ressourcen-Zufuhr-
Zeitreihe waehrend einer gespielten Runde (Issue #205/#199 ECO-6-Baseline).

Rein lesend (POST /get_state ist ein Read, kein Write) — kein Risiko fuer den
laufenden Server. Laeuft bis Strg+C; jede Zeile wird sofort geflusht, damit
bei einem Abbruch nichts verloren geht.

Aufruf:
    python3 scripts/carbonium_logger.py
    python3 scripts/carbonium_logger.py --interval 5 --out runde1.csv
"""

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request

FIELDS = [
    "t_iso", "t_rel_s", "ok", "reason", "carbonium", "carbonium_max",
    "ironium", "ironium_max", "players", "hq_hp", "hq_hp_max", "hq_dead",
    "mission_flow", "mission_flow_active", "creatures_base_difficulty",
]


def fetch_state(url, timeout):
    req = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9001)
    ap.add_argument("--interval", type=float, default=5.0,
                     help="Sekunden zwischen den Abfragen (Default 5)")
    ap.add_argument("--out", default=None,
                     help="Ziel-CSV (Default: carbonium-log-<timestamp>.csv)")
    ap.add_argument("--timeout", type=float, default=3.0,
                     help="HTTP-Timeout pro Abfrage in Sekunden")
    args = ap.parse_args()

    out_path = args.out or time.strftime("carbonium-log-%Y%m%dT%H%M%S.csv")
    url = f"http://{args.host}:{args.port}/get_state"
    t_start = time.monotonic()

    print(f"[carbonium_logger] Ziel: {url}  Intervall: {args.interval}s  "
          f"Ausgabe: {out_path}  (Strg+C zum Beenden)", file=sys.stderr)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        f.flush()

        try:
            while True:
                row = {k: "" for k in FIELDS}
                row["t_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                row["t_rel_s"] = round(time.monotonic() - t_start, 1)
                try:
                    state = fetch_state(url, args.timeout)
                    for key in FIELDS:
                        if key in state:
                            row[key] = state[key]
                    row["ok"] = state.get("ok", "")
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    row["ok"] = False
                    row["reason"] = f"request_failed: {exc}"
                except json.JSONDecodeError as exc:
                    row["ok"] = False
                    row["reason"] = f"bad_json: {exc}"

                writer.writerow(row)
                f.flush()
                print(f"[carbonium_logger] t={row['t_rel_s']:>7}s "
                      f"carbonium={row.get('carbonium', '?')} "
                      f"ironium={row.get('ironium', '?')} "
                      f"ok={row['ok']}", file=sys.stderr)

                time.sleep(args.interval)
        except KeyboardInterrupt:
            print(f"\n[carbonium_logger] Beendet — Daten in {out_path}",
                  file=sys.stderr)


if __name__ == "__main__":
    main()
