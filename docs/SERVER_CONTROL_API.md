# RIFT BATTLE — Server-Control-API (Plane B, Host-Agent)

Der **Server-Control-Agent** steuert und überwacht den **Dedicated-Server-Container**.
Bewusst getrennt vom In-Game-I/O-Kanal (Plane A: `pipe_bridge` → `rbbridge.dll`),
denn er muss auch dann funktionieren, wenn das Spiel hängt oder gerade neu startet —
genau dann braucht man den Restart.

- **Implementierung**: `deploy/server-control/server_control.py` (Python,
  Standardbibliothek). Doku/Endpunkt-Tabelle: [deploy/server-control/README.md](server-control/README.md)
- **Deploy**: Ansible-Rolle `deploy/roles/server-control` (Muster `tournament-server`),
  verdrahtet in `deploy/site.yml` (Tag `server`); `config.cfg`-Rendering nutzt
  dieselbe Vorlage `config.cfg.j2` wie die Rolle `riftbreaker-server`.
- **Issue**: #424 (Plane B) · Refs #363 (Plane A), #298 (API-Härtung), #394 (Full-Chain), #463 (Route nur bei deploytem Agenten)

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

Der Agent wird **nur von dev** deployt (`deploy/site.yml`, Rolle
`server-control`); **prod deployt (noch) keinen Agenten**
(`deploy/deploy-prod.yml`). Deshalb rendert die Rolle `website` die
`/server/*`-Route nur, wenn das jeweilige Play `server_control_enabled: true`
setzt (Issue #463). Ohne dieses Gate zeigte die prod-Route sonst auf
`127.0.0.1:8092` — auf planet den **dev**-Agenten — und ein Klick auf
*Restart server* im Prod-Cockpit traefe den dev-Container (Regression aus
#456). Prod setzt das Flag in `deploy-prod.yml` explizit auf `false`; die Route
fehlt dort, bis prod einen eigenen Agenten (eigener Port/Unit/Token) bekommt.

```
Browser ──► Host-Caddy (mellon-caddy) ──► rift-caddy (127.0.0.1:8787, plain HTTP)
             (Basic-Auth: operator)          └─ handle /server/* ──► 127.0.0.1:8092 (Agent)
                                                basic_auth (operator, gleicher Realm
                                                wie der Cockpit-Root)
                                                bei gesetztem Token:
                                                header_up Authorization "Bearer <token>"
```

* Der Agent bindet **ausschließlich** `127.0.0.1` (`server_control_bind`).
* Der `rift-caddy` reicht `/server/*` **pfadmäßig** unverändert durch (kein
  `handle_path` — der Agent bedient seine Routen mit Präfix). Auth-mäßig ist die
  Route seit #454 **keine** Durchreiche mehr: der Caddy schützt sie selbst.
* **Zwei Auth-Schichten (Issue #454, Fix #456):** Der Browser authentifiziert
  sich am Caddy per **Operator-Basic-Auth** — dieselben Credentials und derselbe
  Realm wie der Cockpit-Root, der Browser schickt sie also auch auf `/server/*`
  automatisch mit. Den **Bearer** des Agenten kann ein Browser nicht senden;
  ihn setzt der Caddy serverseitig via
  `header_up Authorization "Bearer {{ server_control_token }}"`. Der Token liegt
  damit nur im Caddy-/Host-Netz — nie im Browser, nie im Panel-JS.
* Ist **kein** Token konfiguriert (`server_control_token` leer), rendert die
  Rolle den `header_up` gar nicht erst: ein literales `Bearer ` lehnt der Agent
  ab. Die Basic-Auth bleibt, der Agent antwortet 401 (fail-closed).
* Der Bearer-Token wird **zusätzlich im Agenten** geprüft
  (`hmac.compare_digest`) — deshalb ist die Prüfung auch beim Direktzugriff auf
  den Port wirksam (fail-closed, auch wenn Caddy fällt).
* Ohne konfigurierten Token startet der Dienst **nicht** (fail-closed, Exit 2)
  und die Ansible-Rolle bricht mit klarer Meldung ab. Es gibt keinen offenen
  Restart-Endpunkt.
* Der Bearer kommt aus `vault_server_control_token`; die `website`-Rolle liest
  denselben Vault-Wert wie die `server-control`-Rolle (`server_control_token`,
  Default leer → der Agent antwortet 401, es öffnet sich nie ein Zugang).
* Test/Beleg ohne Spieler: `deploy/tests/server-control-auth/run.sh` (Caddy +
  Mock-Agent, prüft 401-Challenge ohne Credentials, 200 mit Operator-Auth und
  den vom Caddy injizierten Bearer).

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
