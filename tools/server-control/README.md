# server-control — Host-Agent (Plane B, Issue #424)

Kleiner HTTP-Dienst (nur Standardbibliothek), der den **Dedicated-Server-Container**
steuert und überwacht: Status, Logs, Restart/Start/Stop und `config.cfg`-Rendering.

Bewusst getrennt vom In-Game-I/O-Kanal (Plane A: `pipe_bridge` → `rbbridge.dll`),
denn der Agent muss **auch dann** funktionieren, wenn das Spiel hängt oder gerade
neu startet — genau dann braucht man den Restart.

## Endpunkte

Alle Routen liegen unter `/server/*` und verlangen **immer** einen Bearer-Token.

| Route | Wirkung |
|---|---|
| `GET  /server/status` | `{state, restarting, health, uptime, started_at}` (aus `docker inspect`) |
| `GET  /server/logs?tail=N` | letzte N Log-Zeilen (`docker logs --tail N`) |
| `POST /server/restart` | `docker restart <container>` |
| `POST /server/start` | `docker start <container>` |
| `POST /server/stop` | `docker stop <container>` |
| `POST /server/config` | `config.cfg` rendern (`mode`/`mission`/`difficulty`/`seed`/`mission_save`) + Restart |

```bash
curl -fsS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8092/server/status
curl -fsS -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8092/server/logs?tail=50"
curl -fsS -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8092/server/restart
curl -fsS -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"difficulty":"hard","seed":"4711"}' http://127.0.0.1:8092/server/config
```

## Sicherheit (gleiche Härtung wie die Tournament-API, #298)

* Bind ausschließlich `127.0.0.1`; der `rift-caddy` proxyt `/server/*` dorthin.
* **Jeder** Request braucht `Authorization: Bearer <token>`
  (`hmac.compare_digest`) — es gibt keinen offenen Restart-Endpunkt.
* Ohne konfigurierten Token startet der Dienst **nicht** (fail-closed, Exit 2).
  Die Ansible-Rolle bricht dann ebenfalls mit klarer Meldung ab.
* `docker` wird immer als Argumentliste aufgerufen (`subprocess`, nie
  `shell=True`); der Container-Name kommt aus der Konfiguration, nie aus dem
  Request.
* Config-Werte werden auf `"`, Steuerzeichen und Newlines geprüft — ein
  Request-Wert kann die `config.cfg`-Struktur nicht aufbrechen.

## Konfiguration (Umgebungsvariablen)

| Variable | Pflicht | Default | Bedeutung |
|---|---|---|---|
| `SERVER_CONTROL_TOKEN` | ✅ | — | Bearer-Token (Vault) |
| `SERVER_CONTROL_CONTAINER` | ✅ | — | Container-Name (`riftbreaker_server_container`) |
| `SERVER_CONTROL_BIND` | | `127.0.0.1` | Bind-Adresse |
| `SERVER_CONTROL_PORT` | | `8091` | Port (planet: `8092`) |
| `SERVER_CONTROL_DOCKER` | | `docker` | Docker-Binary |
| `SERVER_CONTROL_TIMEOUT` | | `60` | Timeout je docker-Aufruf (s) |
| `SERVER_CONTROL_CONFIG_PATH` | | — | Ziel `config.cfg` |
| `SERVER_CONTROL_CONFIG_TEMPLATE` | | — | deployte `config.cfg.j2` |
| `SERVER_CONTROL_CONFIG_VARS` | | — | JSON der Basiswerte (Ansible-gerendert) |

`--check` prüft nur die Konfiguration und beendet sich (für Deploy/Vorabprüfung).

## Tests (ohne Docker, Netz oder Spiel)

```bash
cd tools/server-control && python3 -m unittest test_server_control -v
```

Das Fake-`docker` wird über das Logfile `FAKE_DOCKER_LOG` beobachtet; die HTTP-Tests
sprechen den echten Agenten auf einem Ephemeral-Port an.

## Deploy

Ansible-Rolle `deploy/roles/server-control` (Muster `tournament-server`), verdrahtet in
`deploy/site.yml` (Tag `server`). Der Token kommt **nur** aus dem Vault
(`vault_server_control_token`); die Unit liest ihn aus einer 0600-`EnvironmentFile`.

## Offener Punkt

Das **Web-UI-Panel** (Status/Logs/Buttons) ist ein eigenes Issue (#422) und hier
bewusst **nicht** gebaut.
