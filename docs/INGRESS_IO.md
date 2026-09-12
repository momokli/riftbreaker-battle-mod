# Ingress-I/O — Trainerkommando-Kanal in den Dedicated-Server (Issue #265)

Wie kommt ein Kommando von **außen** (Tournament-Server / Operator) bis in den
laufenden Rift-Breaker-Dedicated-Server und wird dort ausgeführt?

Ziel-AC (#265): `exec rb_wave 3` von außen → `exec_result ok:true` → Game-Log
`event=wave`.

## Die Lücke (IST vor #265)

- Der Mod `rbbattle` lädt headless, Map + Console-Kommandos sind registriert.
- `rbbridge.dll`/`injector.exe` **fehlten im Container** → keine Injection,
  kein Named-Pipe-Server.
- Der Tournament-Server pusht GO/Wave-Kommandos per HTTP auf
  `RBBRIDGE_A_URL=http://127.0.0.1:9001/exec` — es lauschte aber **niemand auf
  9001** (toter Push-Endpoint).

## Warum eine Wine-x64-Bridge statt `relay.py`?

Der Named-Pipe `\\.\pipe\rbbattle` der injizierten `rbbridge.dll` ist ein
**Wine-internes Objekt** — er ist nur aus einem Windows-(Wine-)Prozess derselben
Wine-Session erreichbar, **nicht** über einen Unix-Socket oder von einem nativen
Linux-Prozess.

`bausteine/07-relay/relay.py` öffnet den Kanal per `os.open(r"\\.\pipe\rbbattle")`
und kann das nur als **Windows**-Python. „Wine-Python" hieße: Windows-Python +
Embeddable-Interpreter ins Image (fragil, `os.open`-auf-Pipe ungeprüft).

Die Bridge `pipe_bridge.exe` ist dagegen ein kleiner, testbarer **Wine-x64-
Prozess ohne Fremd-Deps** (Win32 + `ws2_32`), der den bereits konfigurierten
`POST /exec`-Kontrakt tatsächlich erfüllt und aus dem Host über den publizierten
Port `127.0.0.1:9001` erreichbar ist.

`relay.py` bleibt der Pfad für native Windows-Welten (2-Welten-Setup) und ist
protokollkompatibel (gleiche Pipe-Zeilen).

## Verdrahtung (Deploy as Code)

```
Host (planet)                                      Container riftbreaker-dedicated
──────────────────────────────────────────────     ─────────────────────────────────
scripts/build_rbbridge_tools.sh (MinGW)             entrypoint.sh
  → rbbridge.dll, injector.exe,                      1. Xvfb :99 (Socket-Check)
    rbbridge_standalone.exe, pipe_bridge.exe         2. warten auf DedicatedServer.exe
Ansible-Rolle rbtools:                               3. wine injector.exe DedicatedServer.exe
  Build → /opt/rbmods/rbtools-staging                    'Z:\opt\rbtools\rbbridge.dll' (Retry)
  copy → /opt/rbmods/rbtools (Change-Detection)      4. wine pipe_bridge.exe
  notify: restart riftbreaker-server                 5. exec wine DedicatedServer.exe …
Compose:
  volumes: /opt/rbmods/rbtools:/opt/rbtools:ro
  ports:   127.0.0.1:9001:9001
```

Wine sieht den Container-Root als `Z:` → die DLL liegt als
`Z:\opt\rbtools\rbbridge.dll`; der Server läuft als
`Z:\opt\riftbreaker\bin\DedicatedServer.exe`.

**Kein Binary im Image/Repo:** gebaut wird zur Deploy-Zeit aus dem Checkout
(`bausteine/04-trainer-io/` ist die kanonische Quelle), gestagt über die Rolle
`rbtools`, read-only nach `/opt/rbtools` gemountet.

## Dateien

| Datei | Rolle |
|---|---|
| `bausteine/04-trainer-io/bridge/pipe_bridge.c` | HTTP(9001)→Pipe-Bridge (Quelle, x64) |
| `scripts/build_rbbridge_tools.sh` | baut alle 4 Binaries in ein Staging-Dir |
| `deploy/roles/rbtools/{defaults,tasks}` | Build + Stage nach `/opt/rbmods/rbtools` |
| `deploy/roles/riftbreaker-server/templates/docker-compose.yml.j2` | Mount `/opt/rbtools:ro`, Port `127.0.0.1:9001:9001` |
| `deploy/roles/riftbreaker-server/defaults/main.yml` | `riftbreaker_bridge_port` |
| `tools/dedicated-server/scripts/entrypoint.sh` | Injection-Supervisor + Bridge-Start |

## Bridge-Protokoll (HTTP)

Antworten immer `application/json`, `Connection: close`.

| Request | Antwort |
|---|---|
| `GET /health` | `200 {"ok":true,"pipe":<bool>}` (`pipe` = Pipe erreichbar) |
| `POST /exec` mit `{"command":"rb_wave 3"}` | `200 {"ok":<bool>,"results":[{"command":"rb_wave 3","ok":true}]}` |
| `POST /exec` mit `{"match_id":…,"round":3,"commands":["rb_wave 3"]}` | wie oben, ein Result je Kommando |
| `POST /exec`, Pipe nicht erreichbar | `503 {"ok":false,"reason":"pipe_unavailable"}` |
| sonstiger Pfad/Methode | `404 {"ok":false,"reason":"not_found"}` |

`ok` ist `true`, wenn **alle** Kommandos `ok:true` lieferten. Ein Antwort-Timeout
auf der Pipe ist kein Schreibfehler: das Resultat trägt dann
`"ok":false,"reason":"timeout"`.

Auf der Pipe (v0, line-delimited JSON) schreibt die Bridge je Kommando
`{"cmd":"exec","command":C,"cmd_id":N}\n` und liest bis
`{"event":"exec_result","command":C,…}`.

### Umgebung (Bridge)

| Env | Default | Bedeutung |
|---|---|---|
| `RBB_BRIDGE_BIND` | `0.0.0.0` | Bind-Adresse (im Container; Host published nur 127.0.0.1) |
| `RBB_BRIDGE_PORT` | `9001` | TCP-Port |
| `RBB_BRIDGE_PIPE` | `\\.\pipe\rbbattle` | Pipe-Pfad |
| `RBB_BRIDGE_TIMEOUT_MS` | `5000` | Antwort-Timeout je Kommando |

### Modi (Smoke-Tests)

```
pipe_bridge.exe                 HTTP-Server (Dauerbetrieb)
pipe_bridge.exe --ping          Pipe-Smoke: ping -> pong, Exit 0/1
pipe_bridge.exe --once "<cmd>"  ein Kommando; druckt die exec_result-Zeile
```

## Build

```bash
# Alle 4 Binaries (mingw bevorzugt, zig als Fallback):
bash scripts/build_rbbridge_tools.sh /tmp/rbtools-build
# Ausgabe je Binary: NAME=<datei> PATH=<pfad>
```

Wird über die Rolle `rbtools` (`deploy/site.yml`, Tag `server`) automatisch beim
Deploy auf dem Zielhost gebaut. `-Wl,--no-insert-timestamp` macht die Builds
reproduzierbar, damit die `copy`-Change-Detection der Rolle nur bei echten
Quelländerungen anschlägt.

## Testanleitung

**Ohne Player (Automatik/Host):**

1. Build: `bash scripts/build_rbbridge_tools.sh /tmp/rbtools-build` → alle 4
   Dateien vorhanden.
2. Nach dem Deploy im Container:
   - `docker logs riftbreaker-dedicated` → `[entrypoint] ingress: Injection
     erfolgreich`, `[pipe_bridge] HTTP-Bridge lauscht auf 0.0.0.0:9001`.
   - `curl -s http://127.0.0.1:9001/health` → `{"ok":true,"pipe":true}`.
   - `curl -s -X POST http://127.0.0.1:9001/exec -d '{"command":"rb_wave 3"}'`
     → `{"ok":true,"results":[{"command":"rb_wave 3","ok":true}]}`.
   - Game-Log (`/data/.wine/.../exor_logs.txt`): `event=wave level=3 status=start`
     (+ `status=done`), **kein Crash**.

**Nur mit Player (OFFEN, Momo/Matheo):** „die Welle spawnt sichtbar". Nicht als
erledigt markieren.

## Kernpfad-Gate ohne Player (Issue #288)

Der Live-Befund aus #288: `POST /wave` liefert `exec_result.ok=true`, aber im
Spiel entstehen **0 Kreaturen**. `ok:true` belegt nur, dass
`ConsoleService::ExecuteCommand` lief — **nicht**, dass gespawnt wurde. Der
Ingress-Kanal (`rb_wave 3` kommt an) ist intakt; der Fehler ist die
**Falsch-Gruen-Semantik** von `exec_result` plus die nie getestete
Anker-Aufloesung (`FindService == nil`, kein Spieler).

Zwei Testebenen sichern den Kernpfad **ohne Spieler** ab (CI-faehig):

| Test | Deckt ab |
|---|---|
| `tests/lua-static/wave-anchor.test.js` | Anker-Kette border→mission→mech: `FindService=nil`+kein Spieler → `status=no_player`, **kein** `status=done`, 0 `SpawnEntity`-Aufrufe; Rand-Spawner ohne Spieler → `status=done spawned=8 anchor=border`; nur Mech → `anchor=mech`; Anker da, Spawn schlaegt fehl → `status=no_spawns` (kein Falsch-Gruen) |
| `tests/e2e-vollkette/kern-io-pfad.test.js` | Pipe-Roundtrip `exec → exec_result ok:true` (echter `relay.py` + FIFO-Responder), graceful `ok:false reason=console_service_not_found` (kein Crash), Falsch-Gruen-Kontrakt (`exec_result` traegt keinen Spawn), Egress-Verifikation |

Damit ist `exec_result.ok:true ⟹ spawned>0` **nicht** mehr ungeprueft: der
Spawn-Beweis ist das Game-Log `event=wave level=N status=done` (nur bei
`spawned>0`, sonst `status=no_spawns`), im Live-Betrieb der Player-Test.

### Anker-Regression (eingegrenzt)

- Der **Mech ist der einzige Anker, der einen Spawn tatsaechlich ausfuehrt**,
  wenn keine Rand-/Missions-Spawner existieren; ohne Server-Player → 0 Spawns.
- Im **Tick-Kontext** einer geladenen Session lief `FindService` (13:27–13:28,
  `hq_autodetect status=ok`); die im Live-Fall gemessenen 0 Spawns lagen an
  **nicht geladener Map** (headless, kein Player). Die Rand-/Missions-Anker
  greifen also, sobald die Welt bootet — headless verifizierbar ist das nur auf
  Log-Ebene (Stub-Anker, `tests/lua-static/wave-anchor.test.js`).
- Offen bleibt der **headless ohne Player** nicht erreichbare Teil: ob die
  Live-Welt Rand-Spawner liefert und die Welle **sichtbar** spawnt → Player-Test.

### Egress (verifiziert: offene Flanke, KEINE Regression)

Die dedizierte `pipe_bridge` ist ein **reiner exec-Kanal**: sie liest nur
`exec_result`/`pong` und hat keinen HTTP-Client/Report-Pfad → `score_update`
kann nicht aus dem Spiel heraus. `rbbridge` `send_state` existiert, laeuft aber
nur im `serve_client`-Heartbeat (Dauer-Verbindung), waehrend die Bridge pro
Request verbindet. Der Live-Log zeigt entsprechend **kein** `score_update`.
Das ist kein Regressions-, sondern ein fehlendes Feature → **#13**.
Gepinnt in `tests/e2e-vollkette/kern-io-pfad.test.js` („EGRESS …").

## Live-Belege (planet, 2026-09-12)

Erster Live-Lauf des Kanals auf dem Dedicated-Server (Container
`riftbreaker-dedicated`, LAN-Modus):

- **Injector-Attach:** `injector.exe DedicatedServer.exe 'Z:\opt\rbtools\rbbridge.dll'`
  lädt die DLL in den laufenden Server — **PID vor == PID nach**, DLL als
  HMODULE im Zielprozess sichtbar (kein Neustart, kein Crash).
- **Pipe-Ping:** `pipe_bridge.exe --ping` → `pong` auf `\\.\pipe\rbbattle`
  (Exit 0).
- **Exec-Kanal:** `POST /exec {"command":"rb_wave 3"}` →
  `{"ok":true,"results":[{"command":"rb_wave 3","ok":true}]}`.
- **Game-Log:** `[RBBATTLE] event=wave level=3 status=start`.

**Erkenntnis (Änderung 5, Default angepasst):** Mit
`server_pause_game_when_empty "1"` bleibt die Lua-Welt stehen (Log friert nach
dem Laden ein) → `exec rb_wave 3` erreicht den Mod (`exec_result ok:true`),
erzeugt aber **kein** `event=wave`. Mit `"0"` läuft die Welt headless weiter und
der Kanal wirkt wie oben. Deshalb ist
`riftbreaker_server_pause_game_when_empty: 0` jetzt Default.

**Supervisor-Deadlock:** `start_ingress_supervisor` sammelte die
Injector-Ausgabe per `out="$(...)"` ein; langlebige Wine-Helferprozesse erbten
das Schreib-Ende der Pipe, `$(...)` bekam nie EOF und der Supervisor hing nach
„injiziere rbbridge.dll …" dauerhaft (live belegt: keine injector-/pipe_bridge-
Prozesse, nur die blockierte Bash-Subshell). **Behoben:** Ausgabe in
`/tmp/rbtools-inject.log` umleiten und den Lauf per `timeout` begrenzen; kein
Command-Substitution-Deadlock mehr.

**Offen (nur mit Player):** Echtes Spawnen (`status=done`, sichtbare Welle) ist
im headless-Betrieb **nicht** erreichbar — kein Bord-Spawner / kein Spieler
(`no_border_spawners` / `no_player`; `find_screenshot` schlug als
`find_service_missing` fehl). → Player-Test **Momo/Matheo**. Nicht als erledigt
markieren.

## Risiken (nicht host-seitig entscheidbar)

- **Thread-Marshalling** des `ConsoleService::ExecuteCommand`-Aufrufs (läuft im
  Pipe-Thread, nicht im Spiel-Thread) — offen, siehe
  `bausteine/04-trainer-io/README.md`.
- **Signatur build-gebunden** (Build 2.0.58485) — bei Engine-Update nachziehen.
- Injection unter Wine in `DedicatedServer.exe` ist live verifiziert
  (2026-09-12, siehe Live-Belege): Attach ohne Prozessneustart, Pipe-Ping und
  `POST /exec` funktionieren; offen bleibt nur das sichtbare Spawnen mit Player.
