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
| `scripts/entrypoint.sh` | Config kopieren, Logs streamen, Port-/Startup-Watch, Server starten |
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
