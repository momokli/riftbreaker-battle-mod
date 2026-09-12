# tournament — RIFT BATTLE Referee-Server + Web-UI

Zentraler Referee zwischen den zwei Riftbreaker-Dedi-Welten (Issues #29/#30):
Lobby, Ready-Check, GO-Start, Wave-Routing, Reveal und Match-Zustand bis zum
HQ-Tod. Rust/axum; die statische Web-UI (Lobby + Spectator-Dashboard) wird vom
Dienst mit ausgeliefert.

```text
tournament/
├── Cargo.toml
├── src/
│   ├── main.rs        Einstieg, Env-Konfiguration (keine Hardcodes)
│   ├── state.rs       Match-State-Machine (pure Logic) + Unit-Tests
│   ├── referee.rs     Server-Referee: Spiel-Events → Commands (pure Logic, Issue #268) + Unit-Tests
│   ├── api.rs         HTTP-API (axum) + HTTP-Level-Tests
│   └── broadcast.rs   minimaler HTTP/1.1-Push-Client (GO-Broadcast)
├── web/               Lobby-UI + Spectator-Dashboard (Terminal-Stil)
├── bridge/
│   ├── poller_example.py   Referenz-Poller für die rbbridge-Seite (v1)
│   └── telegram_feed.py    Feed-Bridge: Events → Telegram-Topic (Issue #44)
└── .gitignore
```

## Build & Test

```bash
cargo build --release    # Binary: target/release/tournament-server
cargo test               # State-Machine + API-Level (Mock-Endpoints)
cargo clippy --all-targets && cargo fmt --check
```

## Start

```bash
TOURNAMENT_PORT=8080 RBBRIDGE_A_URL=http://10.0.0.5:9001/exec \
RBBRIDGE_B_URL=http://10.0.0.6:9001/exec ./target/release/tournament-server
```

Web-UI: `http://<host>:8080/` · Zustand: `GET /state` · Health: `GET /health`

## SP-Mode (Issue #44) — Server-only, Mirror

Der Mod läuft nur auf dem Server; ein Client joint ohne Mod. Ein einzelner
Spieler (P1, Welt A) tritt gegen eine serverseitig erzeugte Spiegel-Seite
(MIRROR, Welt B) an — man duelliert sich gegen sich selbst. Start:

```bash
curl -X POST http://<host>:8080/sp -H 'content-type: application/json' \
     -d '{"player": "momo"}'
```

Sends von P1 werden gespiegelt (Original → MIRROR, Spiegel → P1); `wave_start`
von P1 lockt beide Seiten und spiegelt den Built-Value; HQ-HP von P1 wird auf
die MIRROR-Seite gespiegelt. Bei HQ-Tod endet das Match mit `match_end` und dem
Hinweis „nächster Spieler kann joinen“. Siehe `docs/TOURNAMENT_API.md`.

## Telegram-Feed (Issue #44)

`bridge/telegram_feed.py` pollt `GET /events?since=<cursor>` und postet neue
Referee-Events (Runde, Send/Boost, HQ-HP, Match-Ende, …) in ein Telegram-Topic.

```bash
TELEGRAM_BOT_TOKEN=<token> TELEGRAM_CHAT_ID=@channel TELEGRAM_TOPIC_ID=42 \
TOURNAMENT_EVENTS_URL=http://<host>:8080/events \
python3 bridge/telegram_feed.py

python3 bridge/telegram_feed.py --self-test   # Syntax-/Unit-Check ohne Netz
```

## Referee (Issue #268) — Server als autoritative Quelle

Der Server ist das Gehirn, die in-game Lua reiner **Executor**. Der Referee
(`src/referee.rs`) konsumiert Spiel-Events (`POST /referee/event`:
`ready`/`wave_done`/`hq_destroyed`) und gibt daraus Commands aus (`rb_wave N`,
`restart`) — deterministisch, ohne Uhr/I/O, damit ohne Spieler testbar
(Event-In → Command-Out). Abholen: `GET /referee/poll?world=A`. Die Wellen
ruhen nach `hq_destroyed` bis zum nächsten `ready`; Duplikate sind idempotent.

```bash
# ready → rb_wave 1
curl -s -X POST localhost:8080/referee/event -d '{"world":"A","type":"ready"}'
# wave_done → rb_wave 2
curl -s -X POST localhost:8080/referee/event -d '{"world":"A","type":"wave_done","level":1}'
# hq_destroyed → restart (Runde +1)
curl -s -X POST localhost:8080/referee/event -d '{"world":"A","type":"hq_destroyed"}'
```

Konzept, Zustandsmaschine, Test-Split (OHNE Player erledigt, Player-Loop
OFFEN) und offene Punkte: [`docs/REFEREE.md`](../docs/REFEREE.md).

## Protokoll

Komplette API- und Ablauf-Dokumentation (Lobby → Ready → GO → Runden/Reveal →
Rematch, Bridge-Poll-Protokoll, Env-Tabelle): [`docs/TOURNAMENT_API.md`](../docs/TOURNAMENT_API.md)

## Einordnung

Der Dienst ist die Server-Seite der Turnier-Architektur; Baustein 06
(`bausteine/06-tournament-server`) war der Node-Prototyp dafür — diese
Implementierung ist der echte Rust-Dienst mit Rift-Battle-Semantik
(2 Welten, HQ-HP, Reveal, Rematch). rbbridge auf den Dedi-Servern (trainer/)
pollen `GET /state` und führen Kommandos via exec-Kanal aus; der Lua-Mod/
RE-Layer meldet Wellenstart und HQ-HP über `POST /report`
(send_state-Egress, Issue #13 — konzeptionell übernommen).

## Ausblick — Spieler-Profile (Post-1v1)

Persistente Spieler-Profile (ELO, Bilanz, Historie) für das Rangsystem (#128)
sind entworfen, aber noch nicht implementiert: Felder, Typen,
Speicher-Entscheidung (SQLite) und der Nachweis, dass der API-v1-Vertrag
unverändert bleibt, stehen in [`docs/PLAYER_PROFILE_MODEL.md`](../docs/PLAYER_PROFILE_MODEL.md).
