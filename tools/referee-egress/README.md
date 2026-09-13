# tools/referee-egress — Spiel-Log → Referee (Egress, Issue #358 / #268)

Tailt den Lua-Log des Dedicated-Servers (`exor_logs.txt` im Wine-Prefix) und
postet die für den Referee relevanten Events (`ready` / `wave_done` /
`hq_destroyed`) an `POST /referee/event` des Tournament-Servers. Damit wird der
Tournament-Server auch im **dedicated-server**-Setup die autoritative
State-Quelle („Solo online", #268).

**Bewusst nur Egress (Spiel → Referee).** Der Ingress (Referee → Spiel, `exec`)
läuft unverändert über die Wine-x64-Bridge `pipe_bridge.exe` (HTTP :9001, #265);
der Referee entscheidet aus den Events die Commands und legt sie in die Outbox
(`GET /referee/poll`) — siehe [`docs/INGRESS_IO.md`](../../docs/INGRESS_IO.md)
und [`docs/REFEREE.md`](../../docs/REFEREE.md).

## Warum ein Sidecar?

`bausteine/07-relay/relay.py` (Host-Prozess, `RBB_REFEREE=1`) macht denselben
Egress für den **Pipe-Loop** — dort überträgt er auch den Rückkanal
(`GET /referee/poll` → Pipe-Dispatch). Der Dedicated-Server hat diese
Host-Wiring nicht: der Log liegt im **Wine-Volume** des Game-Containers. Ein
schlanker Sidecar im selben Compose-Projekt liest den Log dort read-only und
schickt die Events über das Bridge-Netz an den Tournament-Server
(`host.docker.internal` via `host-gateway`). Kein Mod-Change, kein Image-Build
(reines stdlib-Skript auf `python:3.12-slim`).

## Contract (Executor → Referee)

Tail + Parse der `[RBBATTLE]`-Zeilen, Mapping deckungsgleich zu
`relay.py::map_referee_event`:

| Log-Event | Referee-Event (`POST /referee/event`) |
|---|---|
| `event=mod_load` \| `event=setup` | `{"world": W, "type": "ready"}` |
| `event=wave level=N status=done` | `{"world": W, "type": "wave_done", "level": N}` |
| `event=hq_dead` | `{"world": W, "type": "hq_destroyed"}` |
| sonst (u. a. `wave … status=start`) | – (ignoriert) |

Pfad `/referee/event`, `Content-Type: application/json`, `world` `A`/`B`.
Retry mit Backoff (`1s … 30s`) bei Netzfehlern und 5xx; **4xx wird verworfen**
(Konfigurationsfehler, kein Retry). Der Server ist idempotent (doppeltes
`ready`/`wave_done`/`hq_destroyed` erzeugt keinen Doppelbefehl).

## Betrieb (Compose-Sidecar)

Rolle `riftbreaker-server`, siehe
`deploy/roles/riftbreaker-server/templates/docker-compose.yml.j2`:

```bash
python3 -u /app/referee_egress.py \
  --wine-prefix /data/.wine \
  --server http://host.docker.internal:8081 \
  --world A
```

Je Lauf eindeutiger `container_name` (`deploy/test-vars.yml`,
`riftbreaker_referee_egress_container` = `riftbreaker-egress-test-<run_id>`) —
der Test-Deploy bindet die Rolle ein, ein globaler Name kollidiert bei
parallelen `boot-tests` (Klasse #318, `docs/ci-parallel-safety.md` §B).

## Aufruf / Flags

```bash
# Manuell/offline (verfügbare Zeilen verarbeiten, dann beenden):
python3 referee_egress.py --log /pfad/exor_logs.txt --server http://127.0.0.1:8081 --once

python3 -m unittest test_referee_egress -v   # Tools-Tests ohne Spiel/Netz
```

* `--log PATH` (mehrfach) — sonst aus `--wine-prefix`/`--wine-user` abgeleitet.
* `--server URL` (Default `$RBB_SERVER`, `http://127.0.0.1:8081`).
* `--world A|B` (Default `$RBB_WORLD`, `A`).
* `--from-end` — beim ersten Start nur **neue** Zeilen lesen. **Default: von
  Anfang an** (`_offset = 0`).
* `--once` — verfügbare Zeilen verarbeiten, dann beenden.

## Replay-Verhalten (bekannter, offener Punkt)

Ohne `--from-end` startet der Feeder bei `_offset = 0` und postet den **ganzen**
`exor_logs.txt` bei jedem (Re-)Start erneut. Während eines laufenden Matches ist
das dank Referee-Idempotenz unkritisch; im Zustand `restart_pending` (nach
`hq_destroyed`) kann der replayte `mod_load→ready` jedoch `running=true` setzen
und `rb_wave` erneut auslösen, bis der replayte `hq_dead` wieder `rb_reset`
auslöst. Das Compose setzt deshalb **kein** `--from-end` (kein Event-Verlust bei
einem isolierten Feeder-Recreate), die Rotation wird über den Cursor
(`size < offset` → von vorn) erkannt. Ein persistenter Cursor (wie
`session-recorder` via Out-Dir-State) bzw. `--from-end` ist die Alternative und
bleibt als offener Punkt in #358 vermerkt.

`relay.py` hat dasselbe `pos = 0`, läuft aber als selten neu gestarteter
Host-Prozess.

## Tests (ohne Docker/Spiel — Test-Split „OHNE Player")

```bash
cd tools/referee-egress
python3 -m unittest test_referee_egress -v
```

Deckt `parse_rbbattle` (Marker/`event=`/Felder), `map_referee_event` (alle
Zweige + Welt-Forwarding) und den POST-Kontrakt (Pfad/Body/Header) ab. In
`ci.yml` als eigener Schritt verdrahtet.

**Nur mit Player (offen, Momo/Matheo):** der reale Match-Verlauf
(`commence` → Wellen → `hq_dead`) landet live im Referee. Tail/Rotation/Retry
sind ungetestet (kein automatischer Tail-Test).
