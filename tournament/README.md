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
│   ├── api.rs         HTTP-API (axum) + HTTP-Level-Tests
│   └── broadcast.rs   minimaler HTTP/1.1-Push-Client (GO-Broadcast)
├── web/               Lobby-UI + Spectator-Dashboard (Terminal-Stil)
├── bridge/
│   └── poller_example.py   Referenz-Poller für die rbbridge-Seite (v1)
└── .gitignore
```

## Build & Test

```bash
cargo build --release    # Binary: target/release/tournament-server
cargo test               # 33 Tests: State-Machine + API-Level (Mock-Endpoints)
cargo clippy --all-targets && cargo fmt --check
```

## Start

```bash
TOURNAMENT_PORT=8080 RBBRIDGE_A_URL=http://10.0.0.5:9001/exec \
RBBRIDGE_B_URL=http://10.0.0.6:9001/exec ./target/release/tournament-server
```

Web-UI: `http://<host>:8080/` · Zustand: `GET /state` · Health: `GET /health`

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
