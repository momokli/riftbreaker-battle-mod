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
| Referee (prod-tournament) | `127.0.0.1:8082` | nein | `GET /state` frei · mutierend **Bearer** (`REFEREE_TOKEN`) | Relay (`/referee/*`, Queue) |
| Attack-Cycle-Control | `9102` dev · `9103` prod · `9104` staging | nein | keine | Capsule, Sidecar |
| IO-Bridge | `9001` dev · `9002` prod · `9003` staging | nein | keine | Capsule, Cockpit, Sidecars |

**Belege:** `deploy/roles/website/tasks/main.yml:234-245` (Caddy-Block: `basic_auth` + `reverse_proxy 127.0.0.1:9200`) · `gns_probe.cpp:1037,277` (bewusst auth-frei, nur 127.0.0.1) · `deploy/roles/gns-relay/defaults/main.yml` (parked 9201, capsule 9211, queue 9221, referee 8082) · `deploy/inventory/host_vars/planet/vars.yml:81` (`queue_referee_url` = 8082).

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
| POST | `/ready` | – | **Kontextabh. Ready (#1025):** Solo → Capsule resume; VS (Welt) → Referee `POST /ready {world}` | `{}` o. `{world?:"A"\|"B",identitaet?}` | `{ok}` / Capsule-Body / Referee-Body (`{world,phase,teams}`) / `{ok:false,reason}` | **READY**-Button |
| POST | `/queue` | – | **Queue-Join (vs)** — Proxy an Queue-Dienst | `{identitaet,mode?:"vs"}` | `{ok,status:"queued",position}` / `{ok,status:"matched",match:{…,assignments:[{identitaet,world,instance,target}]}}` / `{ok:false,reason}` | **`[ Queue (vs) ]`**-Button |
| POST | `/queue/leave` | – | Queue-Join zurueckziehen | `{identitaet}` | `{ok,identitaet}` / `{ok:false,reason}` | (Leave) |
| GET | `/queue/status` | – | Queue + Matches (Proxy) | – | Queue-Snapshot | **Queue-Zähler + Phase** (alle 1500 ms, Fallback `/sessions`) |
| GET | `/referee/state` | – | **Referee-Phase/Spieler/Sieger** (Proxy, #1024) | – | State-View (`phase,winner,teams.{A,B}.{player,ready}`) / `{ok:false,reason}` | Referee-Badge/Zeile (alle 1500 ms) |
| POST | `/referee/ready` | – | Welt beim Referee ready melden | `{world:"A"\|"B",identitaet?}` | Referee-Body (`{world,phase,teams}`) / `{ok:false,reason}` | **`READY (Referee)`**-Button |
| POST | `/backends` / DELETE `/backends?name=` | – | Backend registrieren/abmelden | `{name,endpoint}` | `{ok}` | Operator/Deploy (nicht Spieler) |

### `POST /solo` — drei Bedeutungen (`:1966-2131`)
1. **Claim + Provision:** `{identitaet, mode?, env?, self_send?}` → Relay ruft Capsule `POST /capsule/open` (Vorrang) bzw. Parked `POST /claim`, erhält `gns_endpoint` + `instance`, pinnt die Identität.
2. **Join bestehender Instanz (#936):** `{identitaet, instance, self_send?}` → kein neuer Claim; Fehler `409 unknown_instance` / `409 instance_full`.
3. **Main-Screen-Trigger:** `{identitaet, mode, self_send}` mit `mode ∈ {solo_self, solo_persona:<name>}`.

`mode` wird ungeprüft durchgereicht; validiert in Parked/Capsule (`400 bad_mode`).
Fehler-`reason`s: `none_parked, backend_starting, parked_unconfigured, not_claimable, bad_mode, mode_mismatch, capsule_unconfigured, capsule_unreachable, unknown_instance, instance_full`.

**Kein eigenes `/join`** — Join läuft über `POST /solo {instance}`.

### `POST /ready` — kontextabhaengig (#1025)
Der Relay verzweigt anhand des Kontexts (`rbref::resolveReadyRoute`, host-getestet in `test_referee_bridge.cpp`):

- **Solo** (kein `world` und kein `vsWorld` der Session) → **bit-identisch** zum bisherigen Verhalten: Proxy an Capsule `POST /capsule/ready` (resume + Cycle `/start`); ohne Capsule → `503 capsule_unconfigured`. **Kein** Referee-Call.
- **VS** (explizites `world:"A"|"B"` im Body **oder** aufgeloestes `vsWorld` aus dem Queue-Kontext `g_queueState[identitaet]`) → Referee `POST /ready {world}` (Bearer `RBB_REFEREE_TOKEN`), dieselbe Route wie `POST /referee/ready`. Antwort/Fehler wie dort (`503 referee_unconfigured`, `502 referee_unreachable`, Backend-Status durchgereicht).

Die UI (`ready()`) haengt `{world: s.vsWorld}` an, wenn die Session eine VS-Welt hat, sonst `{}` (Solo unveraendert). Den Countdown-Text in den Chat schickt der **Announcer**, nicht der Relay.

### `GET /referee/state` + `POST /referee/ready` — Referee-Bruecke (#1024)
Ist `--referee-url`/`RBB_REFEREE_URL` gesetzt (nur IPv4-Literal; Token aus `RBB_REFEREE_TOKEN`, **nicht** argv), proxyt der Relay Web-UI-Aktionen an den internen Referee (`tournament/src/api.rs`): `GET /referee/state` reicht `GET /state` durch (auth-frei) — die UI zeigt Phase (`Lobby|Ready|Running|Finished`), Sieger und „beide ready" additiv als eigene Zeile (überschreibt `soloPhase`/`queuePhase` nicht); `POST /referee/ready` reicht `{world}` an `POST /ready` (Bearer) durch. Ohne Config → `503 referee_unconfigured` (kein Outbound-Versuch, keine offene Route); nicht erreichbar → `502 referee_unreachable`; fehlendes/fremdes `world` → `400 bad_request`; Referee-Fehler (z. B. `401` ohne Token) werden unverändert durchgereicht. Fehler-`reason`s: `referee_unconfigured, referee_unreachable, bad_request`.
**Belege:** Dispatch `gns_probe.cpp:2762-2765`; Handler `:2546` (`state`) / `:2570` (`ready`); Config `:3114`/`:3225-3253`; reine Logik `tools/gns-proxy/referee_bridge.h` (`rbref::`), Host-Test `test_referee_bridge.cpp`.

### `POST /queue` — Casual-Pairing (#998)
Ist `--queue-url`/`RBB_QUEUE_URL` gesetzt (Token `RBB_QUEUE_TOKEN`), proxyt `POST /queue` an den Queue-Dienst (`POST /queue/join`). Der Dienst paart FIFO (aktiv 1v1), provisioniert **kalt** zwei frische Welten A/B und registriert beide Spieler im Referee. Bei einer Match-Antwort pinnt der Relay **alle** Teilnehmer auf ihre **verschiedenen** GNS-Endpoints. Fehler-`reason`s: `queue_unconfigured, queue_unreachable, bad_request, bad_mode, already_matched`. Ohne Queue → `503 queue_unconfigured`.

**Ready-Egress (#1025):** Nach der Provisionierung **beider** kalter Welten und **beiden** `/lobby`-Registrierungen postet `deploy/queue/` je **distinct** Welt genau **ein** `POST /ready {world}` an den Referee (erst beide `/lobby`, dann Ready — sonst `404 not_found`). Der **zweite** Ready loest mit `TOURNAMENT_AUTO_GO=true` den **gemeinsamen GO-Broadcast an beide Bridges** aus (siehe `docs/VS_MATCH.md` §6.5). Bei nur einem `join` (= `queued`) gibt es **kein** Ready; scheitert ein Ready, greift der bestehende Rollback (beide Instanzen `stop`, Match `failed`).

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

### Referee (`tournament/`, prod-tournament-server) — `GET` frei, mutierend Bearer
| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/state` | Match-Zustand (`phase,winner,teams.{A,B}.{player,ready,hq_hp}`, Feed) — auth-frei |
| GET | `/events` · `/matches/{id}` | Event-Feed / Match-Record — auth-frei |
| POST | `/lobby` | Spieler in einer Welt registrieren |
| POST | `/ready` | Welt ready melden (`{world}`); bei beiden ready ggf. Auto-GO |
| POST | `/go` · `/pause` · `/resume` · `/send` · `/report` | Match-Steuerung (Bearer) |

> Relay nutzt `GET /state` + `POST /ready` (Lobby-Bruecke #1024) und queue-seitig `POST /lobby`/`/go`. Übrige mutierende Routen sind operator-/cockpit-getrieben.

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
| Match-Result / Sieger (`/matches`) | **teilweise** (#1024): Referee-Phase/Sieger via Relay `GET /referee/state` (`tournament /state`); Match-Record weiter offen | #1024, #999 |
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
