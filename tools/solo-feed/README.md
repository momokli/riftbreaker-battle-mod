# tools/solo-feed — Solo-Dev-Feed (Telegram Topic 312) + Auto-Restart

Watcher für den Solo-Dev-Server (`riftbreaker-dedicated` auf planet, Port
6321). Tailt `docker logs`, postet Spiel-Events in den Telegram-Topic 312 und
startet nach HQ-Zerstörung **genau einmal** den Server neu (Issue #157).

Versionierte Quelle für `rbbattle-solo-feed.service` (planet,
`/home/momo/rbbattle-solo-feed/feed.py`). Nur Standardbibliothek.

## Events → Telegram

| Log-Signal | Nachricht |
|---|---|
| `[entrypoint] UDP port 6321 is open` / `[NetServerGNS]` | 🟢 Server verfügbar (join 65.21.27.234:6321) |
| `OnNetPlayerCreateRequest` / `Player '…'` | 👤 `<name>` gejoint |
| `GameplayState::ResumeGame` | ▶️ Spiel gestartet |
| `[RBBATTLE] event=wave …` | 🌊 Welle `<n>` |
| `[RBBATTLE] event=hq_dead …` | 💀 GAME OVER — HQ destroyed |
| nach einem Restart (nächster Server-up/start) | 🆕 New game started — join now |

## Restart-Guard (#157)

`event=hq_dead` (vom Mod, `mod/lua/rbbattle_autoexec.lua`) → Telegram-Announce
+ **genau ein** `docker restart riftbreaker-dedicated` pro Match-Ende.
`RESTART_COOLDOWN_S` (60 s) verhindert Flapping: ein weiteres `hq_dead`
innerhalb der Frist löst **keinen** zweiten Restart aus. Der Mod selbst hat
keinen I/O-Kanal und kann den Prozess nicht neu starten — der Restart liegt
ausschließlich hier.

## Lokale Checks (ohne Docker/Netz)

```bash
python3 tools/solo-feed/feed.py --selftest   # classify + Restart-Guard
python3 -m unittest tools/solo-feed.test_feed -v
# oder aus dem Verzeichnis:
cd tools/solo-feed && python3 -m unittest test_feed -v
```

`--replay <log>` simuliert einen Log-Durchlauf inkl. Dedupe und Restart-Guard
(keine echten Messages/Restarts). `--check` validiert env/docker/Zugriff auf
dem Ziel-Host.

## Deploy (offen — Operator/rolling, nicht Teil des PR)

1. `feed.py` aus diesem Repo nach `/home/momo/rbbattle-solo-feed/feed.py`
   kopieren (`.env` + `cursor` bleiben unverändert).
2. `systemctl restart rbbattle-solo-feed` und `systemctl status
   rbbattle-solo-feed` prüfen.

In-Game-/E2E-Nachweis auf `:6321` (HQ killen → Log + Telegram + Rejoin nach
Restart) ist ein Operator-Lauf und im PR als offen markiert.
