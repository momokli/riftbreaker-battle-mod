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

Suffix-/Identitaets-Regeln aus der Routen-Datei haben **Vorrang**: wer
`*-dev`/`*-staging` heisst oder eine exakte Identitaets-Regel trifft, wird
automatisch geroutet; nur der Rest wartet.

Endpunkte (der Relay selbst hat **keinen** Auth — er bindet daher nur lokal; die
öffentliche Lobby-Domain setzt davor der Host-Caddy mit basic_auth):
`GET /` (Single-File-UI), `GET /sessions` (JSON: wer wartet),
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

> **Grenze:** Der Relay terminiert und relayt die Sitzung fuer ihre ganze Dauer;
> er bedient damit **einen Client zur Zeit**. Mehrere parallele Matches brauchen
> mehrere Sitzungen im Relay (offenes Follow-up).

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
| `api_util.h`             | reine API-Helfer (Target-Spec, JSON lesen/escapen)               |
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
- [ ] `m_nAppID` in eigenen GNS-Build statt Runtime-Patch
- [ ] Mehrere Sitzungen im Relay (parallele Matches hinter einer IPv4)
- [ ] Rust-Backend/Launcher auf die JSON-API aufsetzen
