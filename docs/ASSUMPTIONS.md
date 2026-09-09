# Annahmen & Design — Rift Breaker Battle Mod

Lebendes Dokument: hält bisherige Annahmen (mit Status) und den aktuell
verifizierten Stand fest. Vertiefungen: Design/Architektur in
[`docs/concept.md`](concept.md), Engine-Fakten in
[`docs/findings.md`](findings.md), Trainer-Protokoll in
[`trainer/protocol.md`](../trainer/protocol.md).

## Stand 2026-09-09 (verifiziert)

Live-Tests gegen die Produktiv-Instanz (Dedicated Server). Damit sind die
Kernannahmen der Turnier-Infra von der Doku in die Praxis überführt:

1. **Vollkette bewiesen:** Bridge (`rbbridge`, injiziert) →
   `ConsoleService::ExecuteCommand` → Lua-Mod → Spawn. Produktiv-Lauf:
   `event=wave level=3 status=done spawned=8 skipped=0` (16:32). Der
   Ingress-Pfad der Trainer-only-Architektur ist real — `dispatch_exec` ist
   kein `TODO(RE)` mehr.
2. **Kein Client nötig für den Game-Loop:** `debug_spawn_fake_client` erzeugt
   serverseitig einen synthetischen Player `'0'` mit Mech
   (`InitialSpawnEnded`) — der Mod-Spawn-Anker existiert ohne echten Client.
3. **Dedicated Server startet die Welt automatisch:** `app_mode server` +
   campaign/mission bootet die Welt beim Serverstart — keine Lobby-GUI-Aktion
   (Host wählen/Karte laden) erforderlich.
4. **`server_pause_game_when_empty=1`:** leere Welt → `PauseGame`; `ResumeGame`
   erst bei `ConnectReq` (ankommender Client/Player).
5. **Server erzwingt Mod-Gleichstand:** der Client braucht exakt dieselben Mods
   lokal in `mods/`.
6. **Ein-Mod-Paket `rbbattle` v0.2.0-single:** ersetzt die bisherigen
   Baustein-Mods 00 (Skeleton) + 01 (Wave-Spawn) auf den Instanzen.
7. **Argumente quoten:** `exec_cmd_client`-Argumente müssen gequotet übergeben
   werden (z. B. `"rb_wave 3"`), nicht als unquotierte Einzel-Tokens.

## Implikationen

- **Turnier-Infra = 2 Dedi-Instanzen + Bridge + Tournament-Server.** Jede
  Partie läuft als eigene Dedicated-Server-Instanz (Welt bootet selbst,
  Game-Loop ohne Client); die injizierte Bridge (`rbbridge`) ist der
  Ingress-Kanal für Mod-Kommandos (`exec`-Kanal, `trainer/protocol.md`); der
  Tournament-Server (Baustein 06) orchestriert. Ein **Client ist nur noch zum
  aktiven Spielen** nötig (echter Gegner, UI-nahe Tests) — nicht für den
  Spielbetrieb.
- **Headless-Client (Xvfb/Proton) nicht mehr nötig für Server-Tests.** Die
  Testkette läuft direkt gegen die Dedicated-Server-Instanz (Fake-Client +
  Bridge, Fakten 1–3). Issue #7 (Epic „Headless Test Environment“) ist damit
  **teils obsolet** durch die Fake-Client-Erkenntnis (Fakt 2) — headless
  Clients bleiben nur für Client-seitige Szenarien relevant.
- **Automatisiertes Setup:** Weltstart ohne GUI-Aktion; Pause/Resume über
  `server_pause_game_when_empty` (Fakt 4) einplanen (leere Instanzen
  pausieren, `ResumeGame` erst bei Connect); alle Instanzen mit demselben
  Ein-Mod-Paket `rbbattle` v0.2.0-single provisionieren (Fakten 5–6);
  Kommandos an die Bridge mit gequoteten Argumenten senden (Fakt 7).
- **Ingress-Kanal ist definiert:** `Server → exec_cmd_client → Bridge →
  ConsoleService::ExecuteCommand → Lua-Mod` (registrierte Kommandos wie
  `rb_wave`). Die in `docs/concept.md` verworfenen Konsole-Routen sind damit
  präzisiert: nicht Buffer-Injektion/UI-Automation, sondern der offizielle
  Engine-Pfad hinter `ExecuteCommand` (per RE-Scan verdrahtet).

## Historische Annahmen & Status

Rekonstruiert aus `docs/concept.md`, `docs/findings.md`, `trainer/README.md`,
`trainer/protocol.md` und Issue #7 (Stand jeweils vor 2026-09-09).

| # | Annahme (vor 2026-09-09) | Status |
|---|---|---|
| A1 | Ein echter Spiel-Client (headless: Wine + Xvfb/Proton) ist nötig, damit der Mod geladen wird und ein Spawn-Anker existiert (Basis: Issue #7). | **überholt:** `debug_spawn_fake_client` erzeugt den Player-Anker serverseitig — kein Client (Fakt 2). |
| A2 | Server-Tests brauchen die Headless-Client-Umgebung (Screenshot-/xdotool-Navigation). | **überholt:** Vollkette läuft direkt gegen die Dedi-Instanz (Fakten 1–3); Headless-Client nur noch fürs aktive Spielen. |
| A3 | Die Welt startet erst nach einer Lobby-GUI-Aktion (Host wählen, Karte laden). | **überholt:** `app_mode server` + campaign/mission bootet die Welt automatisch (Fakt 3). |
| A4 | Ingress-Kommandoausführung in der Bridge ist ungelöst (`dispatch_exec` = TODO(RE)). | **überholt:** per RE-Scan verdrahtet — `ConsoleService::ExecuteCommand` ist der bewiesene Pfad (Fakt 1). |
| A5 | Eine Instanz ohne Client läuft normal weiter; kein Pause-/Resume-Handling nötig. | **überholt:** `server_pause_game_when_empty=1` pausiert leere Welten; `ResumeGame` erst bei `ConnectReq` (Fakt 4). |
| A6 | Mod-Gleichstand Server/Client war offen (Verteilung/Validierung unklar). | **geklärt:** Server erzwingt Gleichstand — Client braucht dieselben Mods in `mods/` (Fakt 5). |
| A7 | Auf den Instanzen werden zwei getrennte Baustein-Mods installiert (00 Skeleton + 01 Wave-Spawn). | **überholt:** Ein-Mod-Paket `rbbattle` v0.2.0-single ersetzt 00+01 (Fakt 6). |
| A8 | Kommandoargumente werden unquotiert durchgereicht (`command="rb_wave 3"` als JSON-String reicht). | **überholt/präzisiert:** `exec_cmd_client`-Argumente müssen gequotet werden, z. B. `"rb_wave 3"` (Fakt 7). |
| A9 | Lua-Mod = reine Spiellogik ohne Datei-I/O (`io.open` crasht hart); I/O nur über die injizierte Bridge. | **weiterhin gültig** — Ingress jetzt durch Fakt 1 belegt; Egress-Design unverändert. |
| A10 | Runtime-only-Injection, keine Game-Datei-Änderungen; Mod bleibt Workshop-tauglich. | **weiterhin gültig.** |
