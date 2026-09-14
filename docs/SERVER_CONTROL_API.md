# RIFT BATTLE — Server-Control-API (Plane B, Host-Agent)

Der **Server-Control-Agent** steuert und überwacht den **Dedicated-Server-Container**.
Bewusst getrennt vom In-Game-I/O-Kanal (Plane A: `pipe_bridge` → `rbbridge.dll`),
denn er muss auch dann funktionieren, wenn das Spiel hängt oder gerade neu startet —
genau dann braucht man den Restart.

- **Implementierung**: `tools/server-control/server_control.py` (Python,
  Standardbibliothek). Doku/Endpunkt-Tabelle: [tools/server-control/README.md](../tools/server-control/README.md)
- **Deploy**: Ansible-Rolle `deploy/roles/server-control` (Muster `tournament-server`),
  verdrahtet in `deploy/site.yml` (Tag `server`); `config.cfg`-Rendering nutzt
  dieselbe Vorlage `config.cfg.j2` wie die Rolle `riftbreaker-server`.
- **Issue**: #424 (Plane B) · Refs #363 (Plane A), #298 (API-Härtung), #394 (Full-Chain)

## Endpunkte

Alle Routen liegen unter `/server/*` und verlangen **immer** `Authorization: Bearer <token>`.

| Route | Wirkung |
|---|---|
| `GET  /server/status` | `{state, restarting, health, uptime, uptime_seconds, started_at}` (aus `docker inspect`) |
| `GET  /server/logs?tail=N` | `{lines: [...], tail: N}` (`docker logs --tail N`, `N` ≤ 5000) |
| `POST /server/restart` | `docker restart <container>` |
| `POST /server/start` | `docker start <container>` |
| `POST /server/stop` | `docker stop <container>` |
| `POST /server/config` | `config.cfg` rendern (`mode`/`mission`/`difficulty`/`seed`/`mission_save`) + Restart |

## Netz- und Auth-Topologie

```
Internet ──► Host-Caddy (mellon-caddy) ──► rift-caddy (127.0.0.1:8787, plain HTTP)
                                              └─ handle /server/* ──► 127.0.0.1:8092 (Agent)
```

* Der Agent bindet **ausschließlich** `127.0.0.1` (`server_control_bind`).
* Der `rift-caddy` reicht `/server/*` **unverändert** durch (kein `handle_path`
  — der Agent bedient seine Routen mit Präfix).
* Der Bearer-Token wird **im Agenten** geprüft (`hmac.compare_digest`), nicht im
  Caddy — deshalb ist die Prüfung auch beim Direktzugriff auf den Port wirksam.
* Ohne konfigurierten Token startet der Dienst **nicht** (fail-closed, Exit 2)
  und die Ansible-Rolle bricht mit klarer Meldung ab. Es gibt keinen offenen
  Restart-Endpunkt.

## Konfiguration

| Env | Default | Bedeutung |
|---|---|---|
| `SERVER_CONTROL_TOKEN` | — (**Pflicht**) | Bearer-Token, aus dem Vault (`vault_server_control_token`) |
| `SERVER_CONTROL_CONTAINER` | — (**Pflicht**) | Container-Name (`riftbreaker_server_container`) |
| `SERVER_CONTROL_BIND` | `127.0.0.1` | Bind-Adresse |
| `SERVER_CONTROL_PORT` | `8091` | Port (planet: `8092`) |
| `SERVER_CONTROL_DOCKER` | `docker` | Docker-Binary |
| `SERVER_CONTROL_TIMEOUT` | `60` | Timeout je docker-Aufruf (s) |
| `SERVER_CONTROL_CONFIG_PATH` | — | Ziel-`config.cfg` |
| `SERVER_CONTROL_CONFIG_TEMPLATE` | — | deployte `config.cfg.j2` |
| `SERVER_CONTROL_CONFIG_VARS` | — | JSON der Basiswerte (Ansible-gerendert) |

## Befehle

```bash
TOKEN=$(grep SERVER_CONTROL_TOKEN /etc/rbmods/server-control.env | cut -d= -f2-)
curl -fsS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8092/server/status
curl -fsS -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8092/server/logs?tail=50"
curl -fsS -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8092/server/restart
```

## Verifikation (ohne Spieler)

* `GET /server/status` + `GET /server/logs?tail=N` liefern live (planet, verifiziert).
* `POST /server/restart` startet den Container real neu (`StartedAt` ändert sich, verifiziert).
* Auth erzwungen: ohne/mit falschem Token 401 auf **allen** Routen (verifiziert).
* `POST /server/config` rendert + restartet (Sandbox-Config, verifiziert).
* Ohne Token startet der Agent nicht (fail-closed, verifiziert).

**Offener Punkt:** Das Web-UI-Panel (Status/Logs/Buttons) ist ein eigenes Issue (#422)
und hier bewusst nicht gebaut.
