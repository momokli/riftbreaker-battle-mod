# tools/gns-proxy — GNS-terminierender Proxy (Spike zu Issue #831)

Ziel: Routing **ohne** Drop/Kick/Reconnect — mehrere Backends hinter **einer**
oeffentlichen IPv4. Der Client ist hart auf `IPv4:6321` festgelegt (Format-String
`ip:%s:6321`; eine Eingabe _mit_ Port scheitert still), also muss der Router die
Client-Verbindung **terminieren** (selbst der GNS-Gegenspieler sein).

```text
                     +------ gns_probe (= "Server" fuer den Client) ------+
Client (GOG,         |  accept -> Identitaet/Name lesen -> Ziel waehlen    |
 unveraendert) --GNS--:6321  ConnectByIPAddress + Message-Relay  <--GNS-->  |--> prod    :6322
                                                                           |--> staging :6323
                     +---------------------------------------------------+
```

**Ergebnis (2026-09-21, live):** Ein echtes 1v1-Match lief durch das Relay.
Client ohne jede Aenderung, Name `momos` wurde per Re-Route auf staging gelegt,
676 Nachrichten/36 s, 0 Fehler. Backends hatten den Spieler:
`ServerGameplayState: Player '0':'momos'!` + `player_connected.logic`.

## Kernbefund: warum die Terminierung zuerst scheiterte (AppID)

Das Spiel nutzt **kein Steam-Networking**: Die mitgelieferte
`GameNetworkingSockets.dll` ist ein **Open-Source-Build** (`strings`:
`opensource Sep 13 2023`), die Game-DLL importiert nur fuenf GNS-Symbole und
**nie** `steam_api64.dll` (nur als String/dynamisch). Die Client-Identitaet ist
ein anonymer generic-string (`str:<hex>`), keine SteamID — deshalb funktionieren
GOG-Clients.

Trotzdem bindet GNS jedes (selbst-signierte) Zertifikat per `CheckCertAppID` an
`GetAppID()`: Open-Source-Default **0**, der Client sendet aber **780310**
(Riftbreakers Steam-AppID; steht in der DLL nur als Store-URL, _nicht_ als
Immediate). Fehlerbild:

```text
Failed to accept connection from <client>.  Cert is not authorized for appid 0, only 780310
```

`IP_AllowWithoutAuth` (0/1/2), `Unencrypted` und eine Identity aendern daran
nichts — die Pruefung laeuft **vor** der Unsigned-Cert-Ausnahme.

**Fix:** `m_nAppID` liegt im statischen Utils-Objekt bei **Offset 0x8**; disasm-
verifiziert via `inspect_gns.py` (vtable-Slot 26 = `mov eax,[rcx+8]; ret`).
Ein 4-Byte-Write auf 780310 genuegt — danach ist `CheckCertAppID` in **beiden**
Richtungen erfuellt (der Client akzeptiert unser selbst-signiertes Zertifikat,
weil der Open-Source-GNS unsigned certs beidseitig erlaubt).

```text
utils-interface = ...
m_nAppID vorher = 0 -> setze 780310
E1 GRUEN: state=Connected — der Client akzeptiert einen fremden GNS-Server
```

Fuer den Produktivpfad gehoert das in einen eigenen GNS-Build statt in einen
Runtime-Patch.

## Routing-Keys

| Key                        | Verfuegbar                          | Bemerkung                                               |
| -------------------------- | ----------------------------------- | ------------------------------------------------------- |
| GNS-Identitaet `str:<hex>` | **sofort** beim Connect             | stabil pro Installation → direktes Routing, kein Replay |
| Spielername                | erst **nach** der Handshake-Antwort | erzeugt ein Re-Route (Replay des Gesehenen)             |

Der Name steht strukturell erst spaeter auf der Leitung: Auf den ersten
Client-Handshake (`BINSER…`, enthaelt `EXE: <n> DATA: <n>`) antwortet der Server,
_erst dann_ sendet der Client seinen Namen (laengenpraefixierter Klartext, in
unserem Mitschnitt `00 04 6d 6f 6d 6f` = `momo`). Deshalb ist die Identitaet der
bessere Key; der Name ist der Nutzer-sichtbare Komfort (Re-Route) und fuellt
zugleich die Grundlage fuer den Default, falls kein Suffix passt.

> **Kein Identity-Cache (Issue #843).** Ein gelerntes „Identitaet → Backend“
> wurde wieder entfernt: die GNS-Identitaet ist stabil pro Installation, der
> Spielname aber nicht. Live belegt: nach einem `-staging`-Join landete ein
> spaeterer Join **ohne** Suffix wieder auf staging statt auf dem Default. Der
> Name entscheidet jetzt bei jedem Join neu; nur **explizite** Identitaets-Regeln
> (`str:… = …` in der Routen-Datei) routen ohne Umweg.

## Routen-Regeln (1.0)

Routen sind **Daten** (`routes.example`); die Auswertung ist reine Logik in
`route_rules.h` — host-getestet in der CI (`test_route_rules.cpp`), ohne Wine.

```text
*-dev     = 127.0.0.1:6324     # Suffix-Wildcard
*-staging = 127.0.0.1:6323     # Suffix-Wildcard
*         = 127.0.0.1:6322     # Default
```

Reihenfolge: **exakt** > **laengster Suffix** > **Default**. Damit waehlt der
Spielername die Umgebung (`momo-staging` -> staging, `momo-dev` -> dev, sonst
prod); die GNS-Identitaet bleibt als exakter Key fuer bewusst gesetzte Regeln
erhalten.

`dev` muss dafuer von 6321 auf einen freien Port umziehen (6324) — 6321 gehoert
dem Relay.

## Betrieb

```bash
bash build.sh     # mingw (x86_64-w64-mingw32-g++), laeuft auf planet; statisch linken
```

```bash
# Relay: ein Eingang, Default-Backend, Routen-Datei
gns_probe.exe --port 6321 --forward 127.0.0.1:6322 --map-file /etc/rbgns/routes

# Diagnose: Backend-Erreichbarkeit ohne Client testen
gns_probe.exe --dial 127.0.0.1:6322
```

Routen-Datei (`<key>=<ip:port>`, `#` = Kommentar) siehe `routes.example`.

Optional: `--appid N` (Default 780310), `--identity`, `--unencrypted 1`,
`--allow-without-auth {0,1,2}`, `--port`, `--dll`.

## Hold + Operator-UI (Issue #857, PoC)

Statt unentschiedene Joins automatisch auf den Default zu schicken, kann der
Relay sie **halten**; ein Operator sieht sie in einer kleinen Web-UI und schickt
sie per Klick auf ein Ziel:

```bash
gns_probe.exe --port 6321 --map-file /etc/rbgns/routes --hold \
  --api-host 127.0.0.1 --api-port 9200 \
  --target PROD=127.0.0.1:6322 --target STAGING=127.0.0.1:6323 --target DEV=127.0.0.1:6324
```

| Flag         | Bedeutung                                            |
| ------------ | ---------------------------------------------------- |
| `--hold`     | unentschiedene Sessions halten (kein Backend-Aufbau) |
| `--api-port` | HTTP-Port der UI/API (Default-Bind nur `127.0.0.1`)  |
| `--api-host` | Bind-Adresse der UI/API (Default `127.0.0.1`)        |
| `--target`   | `NAME=ip:port`, wiederholbar — die Buttons der UI    |
| `--parked-url` | Ziel des Parked-Pool-Dienstes fuer `POST /solo` (Default: **nicht gesetzt**). **Nur IPv4-Literal** (`http://<IPv4>:port`) — der Outbound-Client nutzt `inet_pton`, also kein Hostname/DNS (`localhost` funktioniert nicht). |
| `--capsule-url` | Ziel des **Kapsel-Flow-Dienstes** fuer `POST /solo` (Issue #931, Default: **nicht gesetzt**). Ist er gesetzt (argv oder `RBB_CAPSULE_URL`), ruft `/solo` `POST /capsule/open` (Claim **ohne** resume → pausiertes Spiel) statt `POST /claim`; ebenfalls nur IPv4-Literal. |

Der Parked-Pfad ist nur aktiv, wenn `--parked-url` **oder** die Umgebungsvariable
`RBB_PARKED_URL` gesetzt ist (argv hat Vorrang); ohne beides antwortet
`POST /solo` mit `503 parked_unconfigured`. `RBB_PARKED_URL` akzeptiert
ebenso nur ein IPv4-Literal.

**Kapsel-Vorrang (Issue #931):** Ist `--capsule-url` **oder** `RBB_CAPSULE_URL`
gesetzt, hat der Kapsel-Dienst Vorrang: `POST /solo` ruft `POST /capsule/open`
an ihm (Claim ohne resume, der Spieler landet in einem **pausierten** Spiel) und
pinnt auf das gelieferte `gns_endpoint`. Ohne Kapsel bleibt der bisherige
Parked-Pfad (#929) unveraendert. Nur wenn **weder** Kapsel **noch** Parked
konfiguriert ist, antwortet `/solo` mit `503 parked_unconfigured`.

`RBB_PARKED_TOKEN` bzw. `RBB_CAPSULE_TOKEN` (Env, **nicht** argv) sind die
Bearer-Token fuer den jeweiligen Dienst; leer = kein Auth-Header.

Suffix-/Identitaets-Regeln aus der Routen-Datei haben **Vorrang**: wer
`*-dev`/`*-staging` heisst oder eine exakte Identitaets-Regel trifft, wird
automatisch geroutet; nur der Rest wartet.

Endpunkte (der Relay selbst hat **keinen** Auth — er bindet daher nur lokal; die
öffentliche Lobby-Domain setzt davor der Host-Caddy mit basic_auth):
`GET /` (Single-File-UI), `GET /sessions` (JSON: wer wartet — jetzt alle
parallelen Sessions),
`GET /targets` (JSON: die Buttons), `POST /route`
`{"identitaet":"…","target":"NAME"}`.
Im Deploy läuft die Rolle `website` die Lobby öffentlich aus:
**https://proxy.rift.projectmellon.de** (Host-Caddy → `127.0.0.1:9200`, basic_auth
`operator`). Lokal ohne Domain: `ssh -L 9200:127.0.0.1:9200 planet`.

**Verhalten:** Der Client bleibt im Loading; seine Nachrichten laufen in die
bestehende Historie. Der Klick baut den Backend-Connect auf und **replayed** die
Historie (derselbe Pfad wie beim Namens-Re-Route). Die Entscheidung wird als
**Pin pro Identitaet** gemerkt — noetig, weil der Client nach ~20 s ohne Antwort
selbst schliesst und neu verbindet; der Pin ueberlebt den Reconnect. Die
**Halte-Dauer** wird beim Trennen geloggt (Kernfrage des PoC).

**Leitplanke:** Der GNS-Zustand bleibt single-threaded — der HTTP-Thread liest
einen mutex-geschuetzten Snapshot (`/sessions`) und schreibt Befehle in eine
Queue, die die Hauptschleife abarbeitet (Muster `server/dll/rbbridge.c`).

## Dynamische Backends + Solo-Claim (Issue #929)

Zwei neue Endpunkte ergaenzen die statischen `--target`-Buttons:

| Endpunkt | Body / Query | Wirkung |
| --- | --- | --- |
| `POST /backends` | `{"name":"…","endpoint":"ip:port"}` | Backend registrieren/aktualisieren (Name ist der Schluessel; doppelter Name aktualisiert). `200`; `400` bei fehlendem `name`/ungueltigem `endpoint`. |
| `DELETE /backends?name=NAME` | — | Backend abmelden. `200`; `400` ohne `name`; `404` bei unbekanntem Namen. |
| `GET /targets` | — | listet die **dynamische** Registry (ohne Relay-Neustart). |
| `POST /solo` | `{"identitaet":"str:…","env"?,"self_send"?}` | fragt den Parked-Pool nach einer geparkten Solo-Instanz, pinnt die Identitaet automatisch auf deren **GNS-UDP-Endpoint** und antwortet mit dem Ziel. Kein Operator-Klick. `self_send` (JSON-Bool, Default `true`) steuert, ob der Spieler sofort mitgeschickt wird. |

```bash
curl -s -X POST 127.0.0.1:9200/backends -d '{"name":"T","endpoint":"127.0.0.1:6324"}'
curl -s 127.0.0.1:9200/targets                 # T erscheint — ohne Neustart
curl -s -X DELETE '127.0.0.1:9200/backends?name=T'
curl -s -X POST 127.0.0.1:9200/solo -d '{"identitaet":"str:<id>"}'
# -> {"ok":true,"identitaet":"str:<id>","target":"127.0.0.1:32768","instance":"parked-1"}
```

**Ablauf `/solo`:** Der Relay ruft `POST /capsule/open` am Kapsel-Dienst (#931,
Bearer aus `RBB_CAPSULE_TOKEN`) **oder** — wenn keine Kapsel konfiguriert ist —
`POST /claim` am Parked-Dienst (Bearer aus `RBB_PARKED_TOKEN`) und liest daraus
`gns_endpoint` (der **GNS-UDP**-Host:Port der Instanz — nicht die HTTP-Bridge).
Das Ziel wird per Command-Queue in die Hauptschleife gegeben und als **Pin fuer
die Identitaet** gesetzt (gleiche Semantik wie `POST /route`, aber mit
aufgeloestem Endpoint). Der Claim-Pfad ist dabei konfigurierbar
(in `runSoloClaim(..., claimPath)` parametrisiert).

**Fehlercodes `/solo`** (Parked-Semantik wird abgebildet):

| Fall | Antwort |
| --- | --- |
| Erfolg | `200 {ok:true,identitaet,target,instance}` |
| `identitaet` fehlt | `400` |
| Parked `409 none_parked` / `503 bridge_unhealthy` / Connect-Fehler (transient) | Retry mit hartem Latenz-Budget: Deadline-getrieben, Gesamt-Wall-Clock ≤ 4,5 s (`SoloBudget{totalMs=4500, attemptMs=1500, minAttemptMs=250}`), Backoff 250 ms→1 s gegen das Rest-Budget geprueft; nach Erschoepfung `503 {ok:false,reason:"backend_starting",retry:true}` |
| Parked `409 not_claimable` | `409` |
| alles andere (401/500/…, oder 200 ohne `gns_endpoint`) | `502` |
| Parked nicht konfiguriert (weder `--parked-url` noch `RBB_PARKED_URL`, und keine Kapsel) | `503 {reason:"parked_unconfigured",retry:false}` |

Der Retry laeuft **ausschliesslich im HTTP-Request-Thread** — der GNS-Hauptloop
wird nie blockiert. Die Registry (`g_targets`) wird ebenfalls nur in der
Hauptschleife mutiert; `GET /targets` liest einen mutex-geschuetzten Snapshot.
Die dynamische Registry ist bewusst **fluechtig** (Parked ist Source of Truth).

### Solo-Button `[ solo | self-send on ]` (#930)

Jede Session-Karte in der Lobby bekommt einen **solo**-Button mit einem
`self-send`-Toggle daneben. Der Klick ruft `POST /solo {identitaet, self_send}`
und macht beides in einem Schritt: Instanz claimen **und** den Spieler
hinschicken (ohne separaten Ziel-Klick).

- **self-send on** (Default) — wie heute: Claim + sofortiger Pin. Der Client wird
  umgezogen (schon verbunden) bzw. beim Reconnect automatisch geroutet.
- **self-send off** — nur claimen/reservieren: der Relay merkt sich Instanz +
  Endpoint (`g_soloClaims`), pinnt aber **nicht**. Ein spaeterer Ziel-Button
  (`POST /route`) oder ein erneutes `solo` mit `self_send:true` routet dann.
- Fehlende `self_send` im Body = **Default `true`** (heutiges Verhalten, keine
  Breaking-Change; Pfad/Methode/Pflichtfelder von `/solo` unveraendert).

Der Claim-Zustand wird pro Identitaet gehalten (nicht pro Session) und ueberlebt
Reconnects. `/sessions` gibt ihn additiv aus — bestehende Felder bleiben
unveraendert:

| Feld | Bedeutung |
| --- | --- |
| `soloPhase` | `provisioned` / `underway` / `in_game_paused` / `running` |
| `soloInstance` | Name der geclaimten Parked-Instanz |
| `soloEndpoint` | GNS-UDP-Endpoint der Instanz (`ip:port`) |

Phasen (reine Logik in `api_util.h`, host-getestet):

| `soloPhase` | Anzeige | Bedingung |
| --- | --- | --- |
| `provisioned` | provisioniert | geclaimt, aber (noch) kein Client verbunden |
| `underway` | Spieler unterwegs | Client verbunden, Backend-Connect laeuft |
| `in_game_paused` | im Spiel (paused) | Client + Backend verbunden, Spiel angehalten |
| `running` | laeuft | Client + Backend verbunden, Spiel laeuft |

Auch **geclaimte Identitaeten ohne verbundenen Client** werden in `/sessions`
gelistet (eigener Zweig, `state:"waiting"`) — vorher waren sie unsichtbar.

**Pause-Quelle (bevorzugt ohne Deploy-Aenderung):** Ist `--parked-url` gesetzt,
fragt der Relay beim `/sessions`-Poll gedrosselt (TTL 1,5 s, im HTTP-Thread)
den Parked-`GET /status` ab und leitet `in_game_paused` aus dem Instanzzustand ab
(nur Phasen ohne Welt-Fortschritt = angehalten). Fehlt die Quelle, faellt die
Phase sicher auf `provisioned`/`underway`/`running` zurueck (kein Haenger, kein
Crash). `self_send` ist eine reine UI-/Body-Auswahl und aendert die
Parked-Semantik nicht.


Der Relay bedient **N parallele Sessions**: jeder akzeptierte Client bekommt
Client-Conn, Backend-Conn, Historie, Sende-Queues und Backpressure **eigen**.
Vorher lagen diese in globalen Singletonen — ein zweiter Client ueberschrieb sie
und die Sitzung des ersten kollabierte (Blocker fuer 1v1/VS, #875).

```text
Client A --GNS--> gns_relay --+--> prod A   (Session 1: eigene Queues/Backpressure)
Client B --GNS--> gns_relay --+--> prod B   (Session 2: eigene Queues/Backpressure)
```

| Baustein                | je Session | Bemerkung                                                              |
| ----------------------- | ---------- | ---------------------------------------------------------------------- |
| Client-/Backend-Conn    | ja         | die Client-Verbindung besitzt die Session, der Backend zeigt nur drauf |
| Historie (Replay)       | ja         | Re-Route/Backlog flutet nur die eigene Session                         |
| Sende-Queues + Limits   | ja         | die Backpressure einer langsamen Leitung bremst nur sie                |
| Poll-Gruppe je Richtung | ja         | es gibt kein `ReceiveMessagesOnConnection`                             |
| Pin pro Identitaet      | nein       | `g_pins` bleibt global und ueberlebt Reconnects                        |

Eine Poll-Gruppe **je Session und Richtung** ist noetig, weil GNS kein
`ReceiveMessagesOnConnection` exportiert: globales Lesen wuerde die Backpressure
aller Sessions an die langsamste koppeln. `POST /route` pinnt weiter pro
Identitaet und zieht **alle** Sessions dieser Identitaet um; `GET /sessions`
listet sie parallel auf.

## Backpressure (wichtig)

Riftbreaker schickt Weltzustaende von ~500 KiB pro Nachricht. Ohne Gegenmassnahme
kippt das Relay sofort:

```text
send an conn=<client> fehlgeschlagen (EResult=25, 523283 B)   # LimitExceeded
```

Zwei Hebel, beide implementiert:

1. **Backpressure**: pro Richtung eine Queue; `k_EResultLimitExceeded` bleibt in
   der Queue und wird erneut versucht; von der Quelle wird nur gelesen, solange
   die Queue der Gegenseite unter dem Soft-Limit (2 MiB) liegt.
2. **Sendepuffer** pro Verbindung von Default 512 KiB auf 8 MiB
   (`k_ESteamNetworkingConfig_SendBufferSize`) — der Default war **kleiner als
   eine einzelne Weltzustands-Nachricht**.

Ausserdem: die Client-Historie **pro Client zuruecksetzen** (sonst wird das
vorige Match ins neue Backend geflutet und der Handshake verhungert) und das
Default-Ziel darf ein Re-Route **nicht** ueberschreiben.

## Werkzeuge

| Datei                    | Zweck                                                            |
| ------------------------ | ---------------------------------------------------------------- |
| `gns_probe.cpp`          | Relay + Routing + Message-Dump + Hold/Web-UI (`--dial` Diagnose) |
| `api_util.h`             | reine API-Helfer (Target-Spec, JSON, Query/URL, HTTP-Response, Retry) |
| `test_api_util.cpp`      | Host-Test der API-Helfer (CI: `g++ -std=c++17`)                  |
| `route_rules.h`          | Routing-Regeln (exakt / Suffix / Default), reine Logik           |
| `test_route_rules.cpp`   | Host-Test der Regeln (CI: `g++ -std=c++17`)                      |
| `inspect_gns.py`         | findet `m_nAppID` (vtable-Slot-Scan) in der GNS-DLL              |
| `pcap_flow.py`           | UDP-Payloads eines Flows in Reihenfolge aus einem pcap           |
| `replay_first_packet.py` | Replay der ersten GNS-Nachricht (nur Schritt 1 sinnvoll)         |
| `routes.example`         | Vorlage der Routen-Datei                                         |

## Betriebsbefund (eigenes Follow-up)

Ein **abgebrochen beendetes Match nimmt dem Backend seinen 6321-Listener**: der
GNS-Socket liegt danach auf einem ephemeren Port, das Docker-Publishing
(`6322→6321`) laeuft ins Leere, bis der Server neu startet. `--dial` deckt das in
Sekunden auf (vorher sah es wie ein Timeout des Proxys aus).

## Status

- [x] E1 GNS-Terminierung (Client akzeptiert fremden Server)
- [x] AppID-Blockade gefunden + gefixt
- [x] E3 Relay zum Backend (bidirektional, Flags erhalten, Backpressure)
- [x] E4 Zielwahl: explizite Identitaets-Regel (sofort) + Name per Re-Route
      (kein gelerntes Caching — Ursache einer Fehlroute, s. Routen-Regeln)
- [x] Live: 1v1-Match durch das Relay, Namens-Routing auf staging
- [x] 1.0: Suffix-Routing (`-dev`/`-staging`) als Regeln + Host-Test (#843)
- [x] Deployment der Relay-Rolle + dev-Port-Umzug (#843)
- [x] Hold + Operator-Web-UI (#857, PoC): Spieler halten, per Klick routen
- [x] Multi-Session (#877): N parallele Sessions (eigene Queues/Backpressure)
- [x] Dynamische Backend-Registry + `POST /solo` (Claim + Auto-Pin, Retry) (#929)
- [x] Lobby-Solo-Button `[ solo | self-send on ]` + `/sessions`-Solo-Status (#930)
- [ ] `m_nAppID` in eigenen GNS-Build statt Runtime-Patch
- [ ] Rust-Backend/Launcher auf die JSON-API aufsetzen
