# Riftbreaker Dedicated Server (Image-Rezept)

Vendored, MIT-lizenziertes Dedicated-Server-Image von
[j3n5-group/riftbreaker-docker](https://gitlab.com/j3n5-group/riftbreaker-docker)
(Copyright 2026 Jens Schiller). Ursprünglicher Einsatzort: planet, `/srv/riftbreaker`.

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
| `Dockerfile` | Image: winehq-stable + steamcmd + winetricks + gosu/tini |
| `scripts/docker-entrypoint.sh` | root → `gosu steamuser` → entrypoint |
| `scripts/entrypoint.sh` | Config kopieren, Logs streamen, Port-/Startup-Watch, Server starten |
| `scripts/wine-init.sh` | Einmalige Prefix-Init (win10, vcrun2022, d3dcompiler_47) |
| `scripts/install-update.sh` | steamcmd `app_update 4114030` (Content) |
| `config/config.cfg.example` | Vorlage Server-Config (LAN/Direct-IP-Modus) |

## Build

```bash
docker build -t rb-dedicated tools/dedicated-server
```

Der Entrypoint erwartet beim Start `config.cfg` unter `/data/config/config.cfg`
(vom Compose als read-only gemountet).
