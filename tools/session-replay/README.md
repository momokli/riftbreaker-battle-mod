# session-replay — Wellen-Sequenz gegen ein laufendes Cockpit nachspielen

Entstanden aus Issue #784: Matheo hat auf Prod eine Runde gespielt (grob ab
~12 Uhr, eskaliert bis ~Wave 5) und wollte dieselben Sends automatisiert
erneut auslösen — als PR, damit das Skript auf jedem Gerät abrufbar ist statt
nur lokal bei einer Person zu liegen.

## Wichtiger Caveat — keine exakte Rekonstruktion

Dieses Skript spielt **keine** aus echten Server-Logs rekonstruierte Session
nach. Der Zugriff dafür fehlte der Session, die dieses Tool gebaut hat:

- Kein SSH auf `planet` (kein `ssh`-Binary im Environment, der Proxy erlaubt
  ohnehin nur HTTPS nach außen).
- `cockpit.rift.projectmellon.de` liegt komplett hinter Operator-Basic-Auth
  (`deploy/roles/website/templates/rift-caddy.Caddyfile.j2`, `handle /*`) —
  auch `/` liefert ohne Credentials ein nacktes 401.
- Die einzige Quelle für "was wurde wann gesendet" ist die
  Session-Recorder-JSONL auf dem Host (`/srv/rift-<env>/sessions/*.jsonl`,
  siehe `deploy/session-recorder/`) — nur per Host-Zugriff erreichbar, nicht
  über HTTP exponiert.

`--max-level` (Default 5) und `--interval` (Default 60s) sind daher **frei
gewählte Platzhalter** aus Matheos grober Erinnerung, keine gemessenen Werte
— dieselbe Konvention wie in `tools/wave-scheduler/wave_scheduler.py`
("FREI GEWAEHLTE Platzhalter"). Beide sind über CLI-Flags anpassbar.

## Warum `/activate_mission_flow` statt eines echten Kaufs

`POST /queue_send` (Attack-Cycle, respektiert Carbonium-Kosten) hat **keinen**
öffentlichen Endpunkt — nur der `send-tailer`-Sidecar im selben Compose-Netz
erreicht ihn (siehe `deploy/attack-cycle/README.md`). Von außen (dieses
Skript, von einem beliebigen Rechner aus) ist nur `POST /activate_mission_flow`
über die Bridge erreichbar — derselbe Kanal wie die Cockpit-SEND-MENU-Buttons.
Das feuert **sofort und kostenlos**, ohne die Attack-Cycle-Economy — kein
1:1-Nachbau eines echten Kaufs, sondern die direkteste von außen erreichbare
Annäherung.

## Aufruf

```bash
COCKPIT_URL=https://cockpit.rift.projectmellon.de \
COCKPIT_USER=operator COCKPIT_PASS=*** \
  tools/session-replay/replay_waves.sh --max-level 5 --interval 60
```

`--dry-run` gibt nur aus, was gepostet würde (kein echter Request, keine Env
nötig):

```bash
tools/session-replay/replay_waves.sh --dry-run --max-level 5
```

## Optionen

| Flag | Default | Bedeutung |
| --- | --- | --- |
| `--start-level N` | 1 | Erstes Level |
| `--max-level N` | 5 | Letztes Level (inklusive) |
| `--interval SECONDS` | 60 | Wartezeit zwischen den Sends |
| `--dry-run` | aus | Nur ausgeben, kein echter Call |

Level 1–9, Level 9 teilt sich die Logic-Datei mit Level 8 (kein eigener Pool,
#658) — gespiegelt aus `deploy/attack-cycle/attack_cycle.py::WAVE_LOGIC`.

## Test

```bash
tests/shell/replay-waves.test.sh
```

Hermetisch: Fake-`curl` im PATH, kein Netz, kein echtes Cockpit.

## Offener Punkt

Falls die echte Session-JSONL später doch verfügbar wird (Host-Zugriff auf
`planet`), kann daraus eine **exakte** Replay-Zeitleiste (echte
Zeitstempel/Level statt Platzhalter) gebaut werden — bisher nicht möglich,
siehe Caveat oben.
