# Riftbreaker Dedicated Server (Image-Rezept)

Vendored, MIT-lizenziertes Dedicated-Server-Image von
[j3n5-group/riftbreaker-docker](https://gitlab.com/j3n5-group/riftbreaker-docker)
(Copyright 2026 Jens Schiller). Ursprünglicher Einsatzort: planet, `/srv/riftbreaker`.

Scripts, Entrypoint und Config stammen aus dem Community-Rezept; das
Basis-Image wurde auf `scottyhardy/docker-wine:latest` (gepflegtes Wine-Setup:
winehq-stable + winetricks + Xvfb + gosu) umgestellt. Der Game-Content und das
SteamCMD-`install-update.sh` des Upstream-Rezepts entfallen — der Content kommt
als Volume/Bind-Mount nach `/opt/riftbreaker`.

Dieses Verzeichnis ist die **Vorlage für das Laufzeit-Image des :6321-Servers** —
es ersetzt das frühere `tools/headless-client/`-Image als Server-Runtime (Issue #241).

## Warum dieses Rezept statt `rb-headless-client`

Der Dedicated-Server (`DedicatedServer.exe`, Windows x86) braucht eine andere
Wine-Laufzeit als der headless Client:

- **winehq-stable** (nicht Debian-Wine 8.0) → liefert u. a. den Builtin-Stub für
  `api-ms-win-core-psm-appnotify-l1-1-0.dll`.
- **Wine-Prefix-Init** (`wine-init.sh`): Windows ≥ 10 + `vcrun2022` +
  `d3dcompiler_47`.
- **`WINEESYNC=0` / `WINEFSYNC=0`** (im Compose gesetzt): mit esync/fsync crasht
  der Server im Boost-Thread, bevor er den UDP-Port bindet.
- **`config=config.cfg`** statt Einzel-Args: die App löst die Config relativ zum
  Install-Root auf.
- **`steamuser` + `gosu`** statt root.

## Inhalt

| Datei | Zweck |
|---|---|
| `Dockerfile` | Erweitert `scottyhardy/docker-wine:latest` (winehq-stable, winetricks, Xvfb, gosu aus dem Base) um `procps` + `iproute2` und legt den `steamuser` an |
| `scripts/docker-entrypoint.sh` | root → `gosu steamuser` → entrypoint |
| `scripts/entrypoint.sh` | Config kopieren, Logs streamen, Port-/Startup-Watch, Trainer-I/O-Supervisor (Injection + Bridge), Server starten |
| `scripts/wine-init.sh` | Einmalige Prefix-Init (win10, vcrun2022, d3dcompiler_47) |
| `config/config.cfg.example` | Vorlage Server-Config (LAN/Direct-IP-Modus) |

Hinweis: `scripts/install-update.sh` (SteamCMD `app_update 4114030`) ist
**nicht** vendored — es läuft kein SteamCMD im Image. Der Game-Content wird vom
Deploy als Bind-Mount nach `/opt/riftbreaker` gelegt. `iproute2` liefert `ss`,
das `watch_server_port()` im Entrypoint für den UDP-6321-Check braucht.

## Build

```bash
docker build -t rb-dedicated tools/dedicated-server
```

Der Entrypoint erwartet beim Start `config.cfg` unter `/data/config/config.cfg`
(vom Compose als read-only gemountet).

## Trainer-I/O im Container (Issue #265)

Der Dedicated-Server ist ohne Injection ein reiner Spielserver. Fuer den
Trainerkommando-Kanal (GO/Wave vom Tournament-Server) braucht er:

1. **Die Tools** (`injector.exe`, `rbbridge.dll`, `pipe_bridge.exe`) unter
   `/opt/rbtools` — vom Deploy als read-only Volume gemountet. Gebaut werden sie
   auf dem Zielhost aus `bausteine/04-trainer-io/`
   (`scripts/build_rbbridge_tools.sh`, Rolle `rbtools`).
2. **Injection + Bridge** — der Entrypoint startet vor dem Server-`exec` einen
   Supervisor: eigenes `Xvfb :99` (Readiness per Socket, `xdpyinfo` fehlt im
   Image), warten auf `DedicatedServer.exe`, dann
   `wine injector.exe DedicatedServer.exe 'Z:\opt\rbtools\rbbridge.dll'` mit
   Retry/Backoff (bis `INJECT_TIMEOUT_SECS`, Default 180 s), zuletzt
   `wine pipe_bridge.exe`. Fehlt `/opt/rbtools`: nur Warnung, der Server laeuft
   normal weiter.
3. **Der HTTP-Endpunkt** — `pipe_bridge.exe` lauscht im Container auf 9001;
   das Compose publiziert `127.0.0.1:9001:9001`. `POST /exec` uebersetzt in
   exec-Zeilen auf `\\.\pipe\rbbattle` (der Wine-Named-Pipe ist nur aus Wine
   erreichbar). Damit ist `RBBRIDGE_A_URL=http://127.0.0.1:9001/exec` des
   Tournament-Servers kein toter Endpoint mehr.

Verdrahtung, Protokoll und Testanleitung: `docs/INGRESS_IO.md`.

Smoke-Test im Container (Beispiel):
```bash
curl -s http://127.0.0.1:9001/health
curl -s -X POST http://127.0.0.1:9001/exec -d '{"command":"rb_wave 3"}'
```
