# RIFT BATTLE — Server-Sizing (Dedicated-Hosts, `:6321`)

> Wie viel Maschine braucht der Stack? Diese Seite beantwortet „welche Kiste
> miete ich" für die verschiedenen Betriebs-Szenarien (Solo-Dev, 1v1-Prod,
> Multi-Match, Dev+CD) — inkl. Messmethodik zum Nachprüfen.
> Ziel-Stack + Betriebsregeln: [`DEPLOYMENT.md`](DEPLOYMENT.md) · Issue #291.

**Herkunft der Zahlen:** Live-Messung auf `planet` am **2026-09-12** durch
momokli (Docker-Stats-Zeitreihen, `ps`/RSS, `docker system df -v`, `df`,
Disk-I/O), Ausgangs-Issue #291. Es sind **gemessene Ist-Werte dieses Stacks**,
keine Herstellerangaben und keine Lastprofil-Extrapolation aus einem echten
1v1-Match mit Spielern — die Idle-Messung ist die belastbare Untergrenze,
die Szenario-Tabelle darauf aufgebaut. Nachmessen: Abschnitt
[Messmethodik](#messmethodik-reproduzierbar).

---

## Recommended Size (Card)

> ### 4 vCPU · 8 GiB RAM · 50 GB NVMe (x86_64)
>
> Prod-tauglich für **1v1**: zwei Dedicated-Instanzen + Tournament-Server +
> Website + Probe-Timer. Kein GPU nötig, kein IOPS-Druck.

Wenn du nur **eine** Zahl brauchst, ist es diese. Details und die günstigeren
bzw. größeren Zuschnitte stehen unten.

---

## Ist-Messung (idle = keine Spieler)

| Komponente | CPU | RAM (Working Set) | Storage |
|---|---|---|---|
| `riftbreaker-dedicated` (`:6321`, Docker + Wine + Xvfb + llvmpipe) | ~1,2–1,8 Cores (Ø ~1,55, Peak 174 %) | 1,42 GiB (RSS `DedicatedServer.exe` ~1,45 GiB, 48 Threads) | 664 MB Content + 5,15 GB Image (3,59 GB shared / **1,56 GB unique je Build**) |
| `tournament-server` (Rust/axum, systemd, `:8081`) | ~0 % | ~4 MB | 9 MB |
| `mellon-caddy` (Website `:443`) | ~0 % | ~50–100 MB | 585 MB Docroot (`website_docroot`, host-seitig `/srv/rbmods-site`; im Container gemessen unter `/opt/http`) — v. a. Mod-Zips |
| Probe-Timer (2-Min-Takt) + `status.json` | ~0 % | ~10 MB | — |

**Der Dedicated Server ist praktisch die gesamte Last.** Tournament-Server,
Caddy und Probe sind im Rauschen — sie bestimmen die Größe der Kiste nicht.

Wichtig an der CPU-Zahl: **~1,5 Cores im Leerlauf** sind kein Messfehler.
Der Server rendert unter Wine in ein virtuelles Display (Xvfb + Mesa-llvmpipe,
also Software-Rendering auf der CPU) und dreht auch ohne Spieler weiter —
seit `server_pause_game_when_empty=0` (nötig für Ingress/Referee, #265/#269)
erst recht. Wer hier mit „idle ≈ 0 %" plant, hat die Kiste zu klein gekauft.

### Was NICHT gebraucht wird

| | Befund |
|---|---|
| **GPU** | nicht nötig — Mesa-llvmpipe rendert in Xvfb auf der CPU |
| **Schnelle Platte (IOPS)** | Disk-I/O seit Start nur **455 MB read / 18 MB write** — keine laufende DB, kein IOPS-Druck. NVMe ist *nice to have* (Image-Builds), keine Anforderung aus dem Laufzeitbetrieb |
| **Dicker Uplink** | idle **~100 kB/s**. Extern war zum Messzeitpunkt nur **UDP 6321** offen — das ist Host-Firewall-Zustand, **nicht** aus dem Repo nachprüfbar (es gibt keine Firewall-Rolle unter `deploy/roles/`), gilt also nur für diesen Host zu diesem Zeitpunkt |
| **32-bit-Support** | x86_64; SteamCMD-Modus (braucht `lib32gcc-s1`) ist ohnehin deaktiviert, siehe `deploy/inventory/host_vars/planet/vars.yml` |

---

## Szenarien

| Szenario | CPU | RAM | Storage | Anmerkung |
|---|---|---|---|---|
| **Solo-Dev-Server** (1 Instanz `:6321`, Mod-Iteration) | 2 vCPU | 4 GiB | 30 GB | getrennt vom Prod-/Duell-Host halten — siehe unten |
| **1v1-„Prod"/Duelle** (2 Instanzen + Tournament + Website) | **4 vCPU** | **8 GiB** | **50 GB** | ⭐ Recommended Card |
| **Multi-Match / Turnier** (mehrere parallele Welten) | 4–6 vCPU | 8–16 GiB | 50–100 GB | **hochgerechnet** über die Faustformel unten — nicht mit N>2 gemessen |
| **Dev + CD-Builds auf demselben Host** (Image-Build, `cargo`, MinGW) | **6 vCPU** | **16 GiB** | **100 GB** | Builds fressen kurzzeitig alle Kerne + Build-Cache |
| **Doku/Website-only** (Caddy + statics) | 1 vCPU | 512 MB–1 GiB | 5 GB | unabhängig skalierbar |

### Faustformel zum Hochrechnen

```
je zusätzliche Dedicated-Instanz:  ~1,5 vCPU  +  ~1,5 GiB RAM  +  ~0,7 GB Content
Grundrauschen (Tournament + Caddy + Probe):  ~0 vCPU  +  ~150 MB RAM
Images (Leitplanke „1 aktuelles je Container"):  ~7–8 GB   (+1,56 GB je Rollback-Tag)
```

Die Instanzen teilen sich Image-Layer (3,59 GB shared) — die zweite Instanz
kostet also Storage-seitig fast nur ihren Content und ihre Volumes, nicht
noch einmal das ganze Image.

---

## Szenario im Detail: Solo-Dev-Server

Ein **eigener Server mit immer aktuellem `main`-Stand zum Testen** ist ein
wiederkehrendes Bedürfnis (Mod-Iteration: ändern → mergen → live), kein
Nebenprodukt des Prod-Betriebs. Deshalb hier explizit als eigenes Szenario.

**Zuschnitt:** 2 vCPU · 4 GiB RAM · 30 GB — eine Instanz, kein Tournament,
keine zweite Welt.

**Separation vs. Ko-Lokation — Empfehlung: getrennt betreiben.** Gründe:

1. **CD rollt bei jedem Merge aus** und startet den Container neu (#91).
   Auf einem gemeinsamen Host trifft das eine laufende Duell-Session mit —
   genau das Problem, das #236 und #238 adressieren (Park-Logik „nur bei 0
   Spielern", aktuell **nicht** in der Pipeline verdrahtet).
2. **Ressourcen-Konkurrenz ist real, nicht theoretisch:** ~1,5 Cores pro
   Instanz im Leerlauf; dazu CD-Builds (Image-Build/`cargo`/MinGW), die
   kurzzeitig alle Kerne ziehen. Ein Prod-Match auf derselben Kiste
   konkurriert mit dem Build.
3. **Blast-Radius:** ein kaputter Dev-Stand (Mod-Regression, hängender
   Wine-Prozess) darf die Duell-Instanz nicht mitreißen.

**Wenn es doch dieselbe Box sein muss:** Zuschnitt der Zeile „Dev + CD-Builds"
nehmen (6 vCPU / 16 GiB / 100 GB), Instanzen sauber trennen (eigene Ports,
eigene Volumes, eigener Compose-Stack — vgl. #290 Multi-Instanz-Hosting) und
die Deploy-Park-Logik (#238) verdrahten, bevor echte Duelle darauf laufen.

---

## Storage: was es wirklich treibt

Der Stack selbst ist klein (Content 664 MB + Website 585 MB + Binaries).
Was Platten füllt, sind die **Rolling-CD-Images**:

- Stand der Messung: **119,6 GB Images, davon 98,8 GB reclaimable (82 %)**,
  `/` bei **91 %** belegt.
- **Leitplanke „1 aktuelles Image je Container"**: ~7–8 GB gesamt.
  Jeder zusätzlich behaltene Rollback-Tag kostet **+1,56 GB** (unique layer).
- Mit **reproduzierbaren Builds (#247)** werden weitere Tags praktisch
  kostenlos (`0 B unique`), weil identische Layer geteilt werden.

Aufräumen (prüfen, was reclaimable ist, dann gezielt entfernen):

```bash
docker system df -v                 # was belegt wie viel, was ist shared/unique
docker image prune -a --filter 'until=168h'   # vorher df -v lesen, nicht blind
```

### Host-Hygiene (unabhängig vom Stack)

Auf `planet` lagen zum Messzeitpunkt **`syslog*` 5,4 GB + Journal 4 GB** ohne
Limit. Gehört auf jedem Host begrenzt, sonst frisst es genau den Platz, den
die Image-Leitplanke gerade freigeräumt hat:

- `logrotate` für `/var/log/syslog*`
- `SystemMaxUse=` in `/etc/systemd/journald.conf` (z. B. `1G`), danach
  `systemctl restart systemd-journald`

---

## Messmethodik (reproduzierbar)

Alle Werte oben stammen aus diesen Befehlen auf dem Host (`planet`):

```bash
# CPU/RAM/Netz/Block-I/O je Container — Zeitreihe statt Momentaufnahme:
docker stats --no-stream                      # Snapshot
docker stats                                  # live; über mehrere Minuten beobachten

# Working Set / Threads des Spielprozesses im Container:
docker exec riftbreaker-dedicated ps -eo pid,rss,nlwp,comm --sort=-rss | head

# Storage: Images/Container/Volumes inkl. shared vs. unique:
docker system df -v

# Platte gesamt + Füllstand:
df -h /

# systemd-Dienste (Tournament-Server) — RAM/CPU:
systemctl status tournament-server
systemd-cgtop -n1

# Host-Logs (Hygiene-Check):
du -sh /var/log/syslog* /var/log/journal
journalctl --disk-usage
```

Für die CPU-Zahl gilt: `docker stats` zeigt Prozent **je Kern-Summe**
(174 % ≈ 1,74 Cores). Idle über mehrere Minuten mitteln — ein einzelner
Snapshot trifft gerne einen Peak.

---

## Offene Punkte (bewusst nicht behauptet)

- **Last mit echten Spielern ist nicht gemessen.** Alle Zahlen sind Idle
  (bzw. Idle mit laufender Welt). Ein 1v1 mit voller Wellen-Last kann darüber
  liegen — die Szenario-Tabelle hat dafür Luft, ist aber keine Messung.
- **Multi-Match-Skalierung** ist die Faustformel (linear je Instanz), nicht
  empirisch mit N>2 Instanzen gemessen.
- **Netz unter Last** (mehrere Clients, Wellen-Traffic) ist offen; idle
  ~100 kB/s sagt darüber nichts.

---

## Referenzen

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — Ziel-Stack, Rollen, CD, Betriebsregeln
- [`../deploy/README.md`](../deploy/README.md) — Playbook, From-zero, Vault
- `deploy/inventory/host_vars/planet/vars.yml`,
  `deploy/roles/riftbreaker-server/` — Pfade, Ports, Volumes
- Issue #291 (dieses Dokument), #247 (reproduzierbare Images),
  #236 (stabiler Test-Server), #238 (Deploy nur bei 0 Spielern),
  #290 (Multi-Instanz-Hosting)
- #301 (Retention/Aufräumen — die Storage-Zahlen hier sind der Anlass),
  #298 (Netz-Exposition ist nicht deklarativ verwaltet — betrifft die
  „nur UDP 6321 offen"-Zeile oben)
