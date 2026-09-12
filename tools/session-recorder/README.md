# tools/session-recorder — Persistenter Session-Mitschnitt (Issue #280)

Tailt den Lua-Log des Dedicated-Servers (`exor_logs.txt` im Wine-Prefix) und
schreibt **jede** `[RBBATTLE] event=...`-Zeile als JSONL — **eine Datei pro
Spiel-Session**. Damit ist der Match-Verlauf nach einem Container-Restart nicht
verloren und vom Host aus query-bar.

## Warum ein Sidecar?

Die Mod emittiert strukturierte Events (`[RBBATTLE] event=...`), aber sie
landen nur **transient**: `docker logs` streamt sie (der Entrypoint macht
`tail -F` auf `exor_logs.txt`), doch `docker logs` ist flüchtig und pro
Container, und `exor_logs.txt` selbst wird bei jedem Serverlauf neu geschrieben.
Dieser Recorder hängt am **Wine-Volume** (`rb-wine`, read-only) und schreibt die
Events in ein **Host-Bind-Mount** — unabhängig vom Container-Lebenszyklus.

Kein Mod-Change, kein Version-Bump: die Session-ID vergibt der Recorder
(externe Quelle of truth), die Mod muss nichts wissen.

## Ausgabe (im Host-Verzeichnis, Default `/srv/rbmods-sessions`)

```
<sessions_dir>/
  index.jsonl                 # eine Zeile je abgeschlossener Session (Summary)
  .state.json                 # Cursor + offene Session (Restart-fest, intern)
  <session_id>.jsonl          # Session-Protokoll: session_start, events, session_end
  <session_id>.summary.json   # leichte Aggregation
```

Session-ID: `<UTC-Zeitstempel>-<4 hex>`, z. B. `20260912T112003Z-a1b2`.

Eine `*.jsonl`-Zeile sieht z. B. so aus:

```json
{"type": "event", "session_id": "20260912T112003Z-a1b2", "seq": 5,
 "ts": "2026-09-12T11:21:04.512Z", "event": "wave",
 "fields": {"level": "1", "status": "start", "spawned": "5", "anchor": "border"},
 "raw": "[server] [13:21:04.512] [info] LogService.cpp:71 - [LUA 'ConsoleService']: [RBBATTLE] event=wave level=1 status=start spawned=5 anchor=border"}
```

`summary.json` (Aggregation, optionaler Teil aus #280):

```json
{"session_id": "20260912T112003Z-a1b2", "duration_s": 812.4, "event_count": 137,
 "waves": 6, "commence": 1, "hq_hp_min": 0, "hq_dead": true,
 "end_reason": "hq_destroyed", "jsonl": "20260912T112003Z-a1b2.jsonl"}
```

## Session-Boundaries

* **Start:** erste Event-Zeile der Session (typisch `event=mod_load`/`event=setup`,
  spätestens `event=commence`).
* **Ende:** `event=match_end` schließt die Session (`session_end` + Summary +
  `index.jsonl`-Zeile). `event=hq_dead` wird als Metrik mitgeschrieben, ist aber
  **keine** Grenze (die Mod emittiert `hq_dead` und danach `match_end`).
* Eine Session ohne `match_end` (Prozess-Crash) bleibt **offen** und wird nach
  einem Restart fortgeführt — der Zustand liegt in `.state.json`.

## Restart-Verhalten

* **Append** (Log wächst weiter): der gespeicherte Cursor verhindert Duplikate.
* **Truncation/Rotation** (neuer Serverlauf, Log beginnt leer): erkannt anhand
  der Dateigröße → ab Offset 0 lesen; die offene Session läuft weiter.
* Bereits geschriebene `*.jsonl` werden nie überschrieben.

## Aufruf

```bash
# Sidecar (Compose, siehe deploy/roles/riftbreaker-server):
python3 -u /app/session_recorder.py --wine-prefix /data/.wine --out-dir /data/sessions

# Manuell/offline (einmalig verfügbare Zeilen verarbeiten, dann beenden):
python3 session_recorder.py --log /pfad/exor_logs.txt --out-dir /tmp/sessions --once
```

Flags: `--log PATH` (mehrfach; sonst aus `--wine-prefix`/`--wine-user`
abgeleitet), `--from-end` (nur neue Zeilen), `--no-player-events`,
`--poll-interval N`.

Player-JOIN (`OnNetPlayerCreateRequest`) wird als `event=player_join`
mitgeschnitten. **LEAVE** hat aktuell keine verlässliche Log-Signatur und wird
deshalb **nicht** geraten (offener Punkt, Issue #280).

## Host-Query

```bash
tail -f /srv/rbmods-sessions/index.jsonl
tail -n 50 "$(ls -t /srv/rbmods-sessions/*.jsonl | head -1)"
grep -h '"event": "wave"' /srv/rbmods-sessions/*.jsonl | jq -r '.fields.level'
docker exec riftbreaker-sessions tail -n 20 /data/sessions/index.jsonl
```

## Tests (ohne Docker/Spiel — Test-Split „OHNE Player")

```bash
cd tools/session-recorder
python3 -m unittest test_session_recorder -v
```

Deckt ab: komplette Event-Kette, `match_end` schließt + Summary, Restart
(Append **und** Truncation), Player-JOIN, CLI `--once`.

**Nur mit Player (offen, Momo/Matheo):** realer Match-Verlauf landet lückenlos
im JSONL (`commence` → Wellen → `hq_dead` → `match_end`).
