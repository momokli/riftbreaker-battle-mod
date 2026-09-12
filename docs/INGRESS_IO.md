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
`POST /exec`-Kontrakt tatsächlich erfüllt. Erreichbar ist die Bridge über das
Docker-Netz (Tournament-Container → `http://<dedi-container>:9001/exec`) und in
Prod zusätzlich über den loopback-Publish `127.0.0.1:9001` (Operator-Komfort).

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
  # Container-intern spricht der Tournament-Service die Bridge über Docker-DNS an
  # (http://<dedi-container>:9001/exec). Prod publiziert zusaetzlich loopback
  # (127.0.0.1:9001:9001); die Test-Instanz hat keinen Host-Port (Issue #275).
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
| `deploy/roles/riftbreaker-server/templates/docker-compose.yml.j2` | Mount `/opt/rbtools:ro`; Bridge im Dedi-Container, Publish `{{ riftbreaker_bridge_publish_host }}` (Prod loopback, optional) |
| `deploy/roles/riftbreaker-server/defaults/main.yml` | `riftbreaker_bridge_port_internal` (fix 9001), `riftbreaker_bridge_publish_host` |
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
| `RBB_BRIDGE_BIND` | `0.0.0.0` | Bind-Adresse im Container. **Muss `0.0.0.0` bleiben** — sonst ist die Bridge aus dem Tournament-Container nicht erreichbar (Issue #275, R4). Prod publiziert nur loopback, der Test gar nicht |
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
   - **Interner Weg (maßgeblich, Issue #275):** aus dem Tournament-Container
     `docker exec riftbreaker-dedicated-tournament curl -s http://riftbreaker-dedicated:9001/health`
     → `{"ok":true,"pipe":true}` (Docker-DNS).
   - **Prod-Komfort:** `curl -s http://127.0.0.1:9001/health` → `{"ok":true,"pipe":true}`
     (loopback-Publish; im Test gibt es keinen Host-Port).
   - `docker exec riftbreaker-dedicated-tournament curl -s -X POST http://riftbreaker-dedicated:9001/exec -d '{"command":"rb_wave 3"}'`
     → `{"ok":true,"results":[{"command":"rb_wave 3","ok":true}]}`.
   - Game-Log (`/data/.wine/.../exor_logs.txt`): `event=wave level=3 status=start`
     (+ `status=done`), **kein Crash**.

**Nur mit Player (OFFEN, Momo/Matheo):** „die Welle spawnt sichtbar". Nicht als
erledigt markieren.

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
