# Player-Lobby — API-Liste

> **Zweck:** Definitive Liste der Endpunkte der **Player-Lobby** — *welche wo exponiert sind*.
> Grundlage für das UI-Konzept. Stand: **2026-09-29**, read-only aus Live-`curl` + Code @ `main`.
>
> **Kernaussage:** Die eigentliche Lobby-API ist der **GNS-Relay** (`tools/gns-proxy/gns_probe.cpp`,
> eingebettete Single-File-UI). Sie hat **selbst keinen Auth** und lauscht nur auf
> `127.0.0.1:9200`; öffentlich wird sie allein durch den Host-Caddy unter
> `https://proxy.rift.projectmellon.de` mit **Basic-Auth** exponiert. Capsule/Parked/Bridge/Cycle
> sind **interne** Backends, die der Relay über loopback anspricht.

---

## 1 · Exposition

| Dienst | Bind | Öffentlich? | Auth | Erreicht von |
|---|---|---|---|---|
| **Lobby-UI + Relay-API** (`gns_probe`) | `127.0.0.1:9200` | **Ja** via `https://proxy.rift.projectmellon.de` | Caddy **Basic-Auth** (Relay selbst: keine) | Spieler (Browser), Operator |
| GNS-Einstieg (UDP) | `0.0.0.0:6321/udp` | **Ja** (client-hardgewired) | Spielname-Suffix-Routing | Riftbreaker-Client |
| GNS-Backends | `:6322` prod · `:6323` staging · `:6324` dev | nein | – | Relay |
| Parked-Pool | `127.0.0.1:9201` | nein | **Bearer** (`PARKED_TOKEN`) | Relay (`/solo`) |
| Capsule-Flow | `127.0.0.1:9211` | nein | **Bearer** (`CAPSULE_TOKEN`) | Relay (`/solo`,`/ready`) |
| Queue-Dienst | `127.0.0.1:9221` | nein | **Bearer** (`QUEUE_TOKEN`) | Relay (`/queue`) |
| Attack-Cycle-Control | `9102` dev · `9103` prod · `9104` staging | nein | keine | Capsule, Sidecar |
| IO-Bridge | `9001` dev · `9002` prod · `9003` staging | nein | keine | Capsule, Cockpit, Sidecars |

**Belege:** `deploy/roles/website/tasks/main.yml:234-245` (Caddy-Block: `basic_auth` + `reverse_proxy 127.0.0.1:9200`) · `gns_probe.cpp:1037,277` (bewusst auth-frei, nur 127.0.0.1) · `deploy/roles/gns-relay/defaults/main.yml:96-97,112` (parked 9201, capsule 9211).

Live-Check:
```
curl -u operator:<pw> https://proxy.rift.projectmellon.de/sessions      → []
curl -u operator:<pw> https://proxy.rift.projectmellon.de/targets       → [{"name":"PROD","endpoint":"127.0.0.1:6322"},…]
curl https://proxy.rift.projectmellon.de/sessions                        → 401 (ohne Auth)
curl http://127.0.0.1:9201/health                                        → {"ok":false,"reason":"unauthorized"}
curl http://127.0.0.1:9001/health                                        → {"ok":true,"pipe":true}
```

---

## 2 · Lobby-API (Relay `gns_probe`) — was die UI anspricht

Dispatcher `gns_probe.cpp:2226-2321`; UI `kUiHtml` `:1340-1347`.

| Methode | Pfad | Auth | Zweck | Request | Response (Schlüssel) | UI-Nutzung |
|---|---|---|---|---|---|---|
| GET | `/` | – (Caddy) | Lobby-HTML | – | `text/html` | Seite laden |
| GET | `/sessions` | – | **Session-Liste + Status** | – | JSON-Array (§3) | alle **1500 ms** gepollt: Karten, Header-Zähler, Badge |
| GET | `/targets` | – | Routing-Ziele | – | `[{name,endpoint}]` | Route-Buttons je Karte |
| POST | `/route` | – | Identität auf Endpoint pinnen | `{identitaet,target}` | `{ok}` | (Legacy/Diagnose) |
| POST | `/solo` | – | **Claim/Provision · Join · Modus-Trigger** | s. u. | `{ok,identitaet,target|instance,mode?,self_send}` / `{ok:false,reason}` | Main-Screen-Kacheln (Spieler hinschicken), solo/join |
| POST | `/ready` | – | Capsule resume + Warmup-Start | `{}` | `{ok}` o. Capsule-Body | **READY**-Button |
| POST | `/queue` | – | **Queue-Join (vs)** — Proxy an Queue-Dienst | `{identitaet,mode?:"vs"}` | `{ok,status:"queued",position}` / `{ok,status:"matched",match:{…,assignments:[{identitaet,world,instance,target}]}}` / `{ok:false,reason}` | **`[ Queue (vs) ]`**-Button |
| POST | `/queue/leave` | – | Queue-Join zurueckziehen | `{identitaet}` | `{ok,identitaet}` / `{ok:false,reason}` | (Leave) |
| GET | `/queue/status` | – | Queue + Matches (Proxy) | – | Queue-Snapshot | **Queue-Zähler + Phase** (alle 1500 ms, Fallback `/sessions`) |
| POST | `/backends` / DELETE `/backends?name=` | – | Backend registrieren/abmelden | `{name,endpoint}` | `{ok}` | Operator/Deploy (nicht Spieler) |

### `POST /solo` — drei Bedeutungen (`:1966-2131`)
1. **Claim + Provision:** `{identitaet, mode?, env?, self_send?}` → Relay ruft Capsule `POST /capsule/open` (Vorrang) bzw. Parked `POST /claim`, erhält `gns_endpoint` + `instance`, pinnt die Identität.
2. **Join bestehender Instanz (#936):** `{identitaet, instance, self_send?}` → kein neuer Claim; Fehler `409 unknown_instance` / `409 instance_full`.
3. **Main-Screen-Trigger:** `{identitaet, mode, self_send}` mit `mode ∈ {solo_self, solo_persona:<name>}`.

`mode` wird ungeprüft durchgereicht; validiert in Parked/Capsule (`400 bad_mode`).
Fehler-`reason`s: `none_parked, backend_starting, parked_unconfigured, not_claimable, bad_mode, mode_mismatch, capsule_unconfigured, capsule_unreachable, unknown_instance, instance_full`.

**Kein eigenes `/join`** — Join läuft über `POST /solo {instance}`.

### `POST /ready` (`:2137-2226`)
Proxyt an Capsule `POST /capsule/ready` (resume + Cycle `/start`). Ohne Capsule → `503 capsule_unconfigured`. Den Countdown-Text in den Chat schickt der **Announcer**, nicht der Relay.

### `POST /queue` — Casual-Pairing (#998)
Ist `--queue-url`/`RBB_QUEUE_URL` gesetzt (Token `RBB_QUEUE_TOKEN`), proxyt `POST /queue` an den Queue-Dienst (`POST /queue/join`). Der Dienst paart FIFO (aktiv 1v1), provisioniert **kalt** zwei frische Welten A/B und registriert beide Spieler im Referee. Bei einer Match-Antwort pinnt der Relay **alle** Teilnehmer auf ihre **verschiedenen** GNS-Endpoints. Fehler-`reason`s: `queue_unconfigured, queue_unreachable, bad_request, bad_mode, already_matched`. Ohne Queue → `503 queue_unconfigured`.

---

## 3 · Zustandsmodell (für Badges)

`GET /sessions` je Session (`buildSessionsJson` `:1266-1313`):

```json
{ "identity":"…", "kind":"…", "ip":"…", "name":"…",
  "state":"held|waiting|connected|closed|routed",
  "target":"ip:port", "connected":true, "pinned":true,
  "messages":N, "age_seconds":N, "held_seconds":N,
  "soloPhase":"provisioned|underway|in_game_paused|running",   // nur wenn geclaimt
  "soloInstance":"…", "soloEndpoint":"…",
  "soloMembers":["…"], "soloMemberCount":N, "soloMaxPlayers":N,
  "queuePhase":"queued|matched|provisioning|ready",   // nur wenn Queue-Zustand
  "queuePosition":N, "matchId":N, "vsWorld":"A|B" }
```

**`state`** (Verbindung/Routing): `held → wartet` · `waiting/closed → getrennt` · `connected → verbunden` · `routed → geroutet`.

**`soloPhase`** — abgeleitet (`api_util.h:330-341`):

| soloPhase | Bedingung | Badge |
|---|---|---|
| `provisioned` | geclaimt, Client noch nicht verbunden | *provisioniert* |
| `underway` | Client verbunden, Backend-Connect läuft | *lädt* |
| `in_game_paused` | Client+Backend da, Spiel pausiert | *läuft (pausiert)* |
| `running` | Client+Backend da, Spiel läuft | *läuft* |
| *(kein Claim)* | – | *wartet* |

> **Offen:** ein „fertig/Sieger"-Signal existiert serverseitig **noch nicht** (`STATUS.fertig` in der UI ist ohne Server-Signal). → #999.

**`queuePhase`** (Issue #998, additiv — nur wenn ein Queue-Zustand existiert):
`queued` (wartet) · `matched` (gepaart, auch `finished`/`failed`) · `provisioning`
(kalte Welten fahren hoch) · `ready` (A/B provisioniert, Lobby registriert).

> **UI-Progression (Issue #1000)** leitet die Phase im UI-Takt bevorzugt **live**
> aus `GET /queue/status` ab (Match-Zuordnung → Phase aus `match.state`; sonst
> Queue-Treffer → `queued`), weil der Relay-`queuePhase` nach dem Join nicht
> nachgeführt wird. Label-Kette: `In Queue … (X warten)` → `Match gefunden` →
> `provisioniert` → `läuft`; Abbruch (`POST /queue/leave`) nur in Phase `queued`.

---

## 4 · Interne Backends

### Parked-Pool (`deploy/parked/`) — Bearer
| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/status` | Pool-Snapshot (`entries[]`: instance, container, bridge_url, cycle_url, gns_endpoint, mode, state, rounds, parked_seconds) |
| POST | `/claim` | Instanz übergeben (FIFO) → `{instance,bridge_url,cycle_url,gns_endpoint,state,resumed,handover_seconds}` |
| POST | `/recycle` | nach Runde zurück in Pool (`{instance_id,keep_warm?,result?}`) |
| POST | `/reap`,`/reconcile` | Auslaufschutz / Orphan-Cleanup |

### Capsule-Flow (`deploy/capsule/`) — Bearer
| Methode | Pfad | Zweck | Phase |
|---|---|---|---|
| GET | `/health` · `/capsule/status` | Liveness / Snapshot | – |
| POST | `/capsule/open` | Parked claim **ohne** resume (Welt pausiert) | `claimed` |
| POST | `/capsule/ready` | Bridge `resume_game` + Cycle `/start` | `warmup` |
| POST | `/capsule/finish` | Parked recycle | `parked` |
| POST | `/capsule/auto` | Bridge `/pause_game {op:auto}` | – |

> Relay nutzt nur `open` + `ready`. `finish`/`auto` sind **operator-/Cockpit-getrieben** (kein Auto-Finish im Lobby-Flow).

### Attack-Cycle-Control — keine Auth, loopback
`GET /status` · `POST /queue_send` · `POST /start` · `POST /ready {on:0|1}`

### IO-Bridge (`server/pipe-bridge/`) — keine Auth, loopback
Spiel-start-relevant: `POST /start` · `POST /ready` · `POST /resume_game`/`/pause_game` · `POST /round_reset` · `POST /send_chat` · `POST /end_game {result}` · `POST /get_state` · `GET/POST /game_config`. (Dazu viele Cockpit-/Debug-Endpunkte — für den Spieler-Start irrelevant.)

---

## 5 · Gaps (was ein UI-Konzept einplanen muss)

| Bedarf | Heute | Issue |
|---|---|---|
| Queue join/leave (`/queue/*`) | **done** (Relay-Proxy + Dienst) | #998 |
| Queue-Status (`inQueue`, Position, `matchFound`) | **done** (#1000): Labelkette + Zähler aus `/queue/status`, Leave-Button in `/sessions` (`queuePhase`/`queuePosition`/`matchId`/`vsWorld`) | #1000 |
| Match-Result / Sieger (`/matches`) | **fehlt** | #999 |
| VS-Flow (gemeinsamer Start/Ready, Pause-Fan-out) | **fehlt** (Relay kennt nur Solo) | #995–#997 |

---

## 6 · Gotchas

1. **Basic-Auth** nur am Edge-Caddy (`/`, `/sessions`, `/solo`, `/ready`). Wer `127.0.0.1:9200` direkt erreicht, steuert alles (Relay ist auth-frei). Parked/Capsule = Bearer; Bridge/Cycle = keine (nur loopback).
2. **Kein `/join`** → `POST /solo {instance, self_send}` (idempotent; Kapazität `--max-players`, Default 4 → `409 instance_full`).
3. **Namens-Suffix-Routing** (GNS, nicht HTTP): `…-dev → :6324`, `…-staging → :6323`, sonst prod `:6322`. Der Spielername im Client wählt die Welt.
4. **Singleton vs. per-Env:** Relay-API `9200`, GNS-Einstieg UDP `6321`, Parked `9201` sind konstant; Bridge (`9001/2/3`) und Cycle (`9102/3/4`) sind per Env.
5. **Polling 1500 ms** (`/sessions`); nach Aktionen verzögertes Re-Poll (250–300 ms).
6. **Anzeige:** pro Spieler **einen** Badge aus `soloPhase` ableiten; Header-Zähler nutzen `state`.
7. **Capsule ist Vorrang-Pfad:** erreichbar → `/solo` nutzt `/capsule/open`; sonst Parked `/claim`.
