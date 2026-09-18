# RIFT BATTLE — 1.0 Play-Test (Abnahme-Protokoll / Baseline `v1.0.0`)

> **Status:** Entwurf (Issue [#320](https://github.com/momokli/riftbreaker-battle-mod/issues/320)).
> **Test-Hoheit:** Momo. Der Agent liefert Deploy, Logs und Beweismittel — **die
> Abnahme-Entscheidung trifft der menschliche Test.**
> **Bezug:** Release-Plan [#319](https://github.com/momokli/riftbreaker-battle-mod/issues/319)
> (Welle 3 → `v1.0.0`), Core-IO-Gate [#289](https://github.com/momokli/riftbreaker-battle-mod/issues/289),
> Milestone [`1.0`](https://github.com/momokli/riftbreaker-battle-mod/milestone/8).

## 0. Zweck

`v1.0.0` wird getaggt, damit es eine **feste Baseline** für alle weiteren
Arbeiten gibt. Damit der Tag mehr ist als ein Commit-Marker, braucht es **eine
verbindliche Abnahme**: den **1.0-Play-Test durch Momo**.

Warum das nicht die CI leisten kann: Das automatische Gate
(`tests/core-io/`, CI-Required-Check `boot-test`) beweist **C1–C3 und
C4-reached** headless — aber **nicht** `spawned>0`, also „**der Spieler sieht
den Boss/die Welle**“. Headless findet der Mod keinen Bord-Spawner und keinen
Mech (`event=wave … status=no_player`). Genau dieser Fall ist als **offener
Punkt** in [`tests/core-io/README.md`](../tests/core-io/README.md)
dokumentiert und **nur manuell** abnehmbar.

**Kern-Versprechen, das hier abgenommen wird:**

> „Ich starte den gehosteten Server, verbinde mich, platziere mein HQ — und
> **von außen** (Web-Knopf / Server-Aktion) lässt sich eine Welle auslösen, die
> ich **im Spiel sehe**. Fällt mein HQ, endet das Match nachvollziehbar, und ich
> kann sofort eine neue Runde starten.“

Dieses Dokument legt fest, **was dafür funktionieren muss** (Muss-Kriterien),
**wie** es geprüft wird (Szenarien), **welche Beweise** zählen und **wie** das
Ergebnis signiert wird.

### 0.1 Scope von 1.0 (von Momo bestätigt, 2026-09-12)

- **1.0 ist solo:** **ein** Dedicated-Server, dazu der **Tournament-Server**.
- **Der Game-State und das Control MÜSSEN von extern kommen** (Referee/Web —
  nicht in-game-Lua). Das ist der wichtigste Punkt der Baseline: Server starten
  → der Referee führt, das Spiel gehorcht, der Spieler sieht es.
- **Echtes 1v1 mit zweitem Spieler ist NICHT Teil von 1.0** (S11 = `n/a`).
- **Runde 2 / Round-Reset (M8) ist 2026-09-19 nach 1.1 verschoben** — nicht 1.0-blockierend. Das Core-IO-Gate (#289) ebenfalls → 1.1.
- **Telemetry (Session-Mitschnitt + Metriken, #280) ist Core-Dev-Feature von
  1.0 und zwingend Muss** (M10).
- Der Website-Proxy (#322, Preflight P4) ist **erledigt**: PR #326 (eigener
  `rift-caddy`) am 2026-09-12 gemergt, P4 **grün** (2026-09-13).

### 0.2 1.0-Voraussetzungen (Release-Blocker)

Diese Punkte müssen **vor** dem Play-Test erledigt und deployt sein — sie sind
Teil des 1.0-Presets, nicht „nice to have“:

| Voraussetzung                                                                | Issue/PR                                               | Nachweis                                     |
| ---------------------------------------------------------------------------- | ------------------------------------------------------ | -------------------------------------------- |
| Website liefert `/tournament/*` (ein Host-Caddy-Eintrag, eigener Rift-Caddy) | **#322** ✅ PR #326 (gemergt 2026-09-12), Preflight P4 | `/tournament/health` → `200` (2026-09-13 ✅) |
| Runde 2 / Round-Reset                                                        | **#281** (PR #285), M8 → **1.1** (2026-09-19)          | Reset + saubere Runde 2                      |
| Telemetry / Session-Mitschnitt                                               | **#280** (PR #283), M10                                | Session-Artefakt liegt vor                   |
| Welle 1–3 abgearbeitet                                                       | **#319**                                               | Release-Plan abgehakt                        |
| Deployter Stand == Commit                                                    | —                                                      | Traceability-Block (Abschnitt 10)            |

### 0.3 Abnahme-Regel: **ein Milestone = ein Play-Test**

- **Ein Milestone ist abgenommen, wenn sein Play-Test „happy“ ist** (Momo).
  Nicht „alle Issues zu“, nicht „CI grün“ — der Play-Test ist die Abnahme.
- **`1.0`** = dieses Dokument = Milestone [`1.0`](https://github.com/momokli/riftbreaker-battle-mod/milestone/8).
  Alles danach clustert in **`soon`** (Milestone 9, semver 1.X) — die groben
  Modi-Ideen bleiben über Labels sichtbar (`mod:solo` → `mod:1v1` → `mod:2v2`
  → `mod:3v3` → `mod:4v4`), auch wenn sie noch nicht ausgearbeitet sind.
- Semver-Mapping: Milestone-Cluster = Version. Taggen erst nach „happy“.
- Umgesetzt am 2026-09-12 (Momo-Call): Alt-Milestones `1`, `3`, `4`, `5`, `6`, `7`
  geschlossen; es gibt **genau zwei** offene Cluster (`1.0`, `soon`).

---

## 1. Baseline-Mechanik — was der Tag einfriert

- `v1.0.0` ist ein **reiner Marker** (Issue #209): **kein** GitHub-Release,
  **keine** Release-Assets. Was zählt, ist der **Commit** plus der daraus
  **deployte Stand**.
- **Tag ≠ Abnahme.** Der Tag friert den Prüfstand ein; der Play-Test bestätigt
  ihn. Fällt der Test durch, gilt die Baseline als **nicht bestätigt**:
  Funde werden zu Issues, der nächste Kandidat wird getaggt (z. B. `v1.0.1`).
- Getestet wird gegen den **deployten** Stand (nicht gegen einen lokalen
  Checkout): `:6321` (Dedicated-Server), `:8081` (Tournament-Server/Referee),
  ausgeliefertes `rbbattle.zip`.

### 1.1 Versionsstempel (beim Test ausfüllen)

| Feld                                                                            | Wert          |
| ------------------------------------------------------------------------------- | ------------- |
| Tag / Kandidat                                                                  | `v1.0.0`      |
| Commit (SHA)                                                                    | `<ausfüllen>` |
| Mod-Version (`client-mod/*.manifest` → `version`)                               | `<ausfüllen>` |
| `rbbattle.zip` md5 (lokal == deployt == Download-URL)                           | `<ausfüllen>` |
| Deployter Mod-Stand (`riftbreaker-dedicated`, `/opt/riftbreaker/mods/rbbattle`) | `<ausfüllen>` |
| Tournament-Server (Binary-/Commit-Stand)                                        | `<ausfüllen>` |
| Website-Stand (`solo.html`)                                                     | `<ausfüllen>` |
| Datum / Uhrzeit (UTC) / Testdauer                                               | `<ausfüllen>` |
| Tester                                                                          | Momo          |
| Mitspieler (falls 1v1)                                                          | Matheo        |

---

## 2. Rollen

| Rolle                   | Wer    | Aufgabe                                                         |
| ----------------------- | ------ | --------------------------------------------------------------- |
| **Tester**              | Momo   | Führt Szenarien aus, urteilt Muss/Soll, signiert das Protokoll. |
| **Mitspieler**          | Matheo | Für 1.0 **nicht** nötig (solo); 1v1 ist Post-1.0.               |
| **Operator/Beobachter** | Agent  | Deploy, Log-Ernte, Beweismittel, Funde als Issues anlegen.      |

---

## 3. Vorbedingungen (Preflight — alle **Muss**)

Ohne diese Punkte ist der Test nicht aussagekräftig. Ein fehlgeschlagener
Preflight-Punkt ist selbst ein 1.0-Blocker.

| #   | Prüfung                                                                              | Kommando                                                                                                                                                                                               | Erwartung                                                          |
| --- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------ |
| P1  | Download == deployter Stand (**zwei** Vergleiche, s. Hinweis)                        | **a)** `ssh planet 'md5sum /srv/rbmods-site/mods/rbbattle.zip'` == Vergleichs-md5 des Kandidaten-Zips (**b)** `bash scripts/mod_version.sh` == `version=` in der deployten `event=mod_load`-Zeile (P5) | **a)** zwei md5 **identisch**; **b)** zwei Versionen **identisch** |
| P2  | Dedicated-Server gesund                                                              | `ssh planet 'docker ps --filter name=riftbreaker-dedicated --format "{{.Status}}"'`                                                                                                                    | `Up … (healthy)`, **kein** Restart-Loop                            |
| P3  | Tournament/Referee erreichbar                                                        | `ssh planet 'curl -s http://127.0.0.1:8081/health'`                                                                                                                                                    | `{"ok":true,"phase":"lobby"}`                                      |
| P4  | Web-UI **inkl. API-Pfad** erreichbar — #322 ✅ (PR #326 gemergt, `rift-caddy` läuft) | `curl -s -o /dev/null -w '%{http_code}\n' https://rift.projectmellon.de/solo.html` **und** `curl -s -o /dev/null -w '%{http_code}\n' https://rift.projectmellon.de/tournament/health`                  | `200` **und** `200`                                                |
| P5  | Log-Ernte möglich (kanonisch: `docker logs`, s. §8)                                  | `ssh planet 'docker logs riftbreaker-dedicated 2>&1 \| grep -a RBBATTLE \| tail -5'`                                                                                                                   | `[RBBATTLE] event=mod_load version=<V> status=ok`                  |
| P6  | Bridge/Relay erreicht das Spiel                                                      | `ssh planet 'curl -s http://127.0.0.1:9001/health'`                                                                                                                                                    | `{"ok":true,"pipe":true}`                                          |
| P7  | Spieler-Kanal frei                                                                   | `:6321` ohne fremde Spieler; Server für den Test reserviert                                                                                                                                            | ja                                                                 |

> **P1-Hinweis:** Die drei Kommandos aus dem Entwurf liefern **verschiedene
> Werttypen** (Version · HTTP-ETag · md5) und sind daher **nicht** direkt
> vergleichbar. Geprüft werden deshalb **zwei getrennte** Paritäten:
> **(a)** md5 des ausgelieferten/auszuliefernden Zips == md5 der Datei auf dem
> Server; **(b)** Mod-Version aus dem Manifest == Version in der real
> deployten `event=mod_load`-Logzeile. Der ETag-Header (`curl -sI …`) ist
> **kein** md5 und taugt nicht als Vergleichswert.

> **Ist-Stand (read-only geprüft 2026-09-13):** P1–P6 **grün** — deployte
> Mod-Version `0.34.3`, Container healthy, Referee `lobby`, Bridge `pipe:true`,
> **P4 erledigt:** `https://rift.projectmellon.de/tournament/health` → `200`
> `{"ok":true,"phase":"lobby"}` (PR #326 am 2026-09-12 gemergt, `rift-caddy`
> läuft). Der datierte Snapshot vom 2026-09-12 (Abschnitt 9) bleibt als Chronik
> erhalten; die **heute** gültigen Vorbedingungen stehen in dieser Zeile.

---

## 4. Muss-Kriterien (1.0-blockierend)

**Alle M-Kriterien müssen grün sein, sonst ist `v1.0.0` nicht bestätigt.**

| #   | Kriterium                                                                                                                                   | Szenario | Beweis                                                                                              |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------- | -------- | --------------------------------------------------------------------------------------------------- |
| M1  | **Boot/Host:** Server startet aus dem Kaltstart, Mod lädt **genau einmal** in erwarteter Version, keine `handler_errors`/`event_unreadable` | S1       | `docker ps` + Log: genau **eine** `event=mod_load … status=ok`-Zeile                                |
| M2  | **Connect:** Spieler verbindet sich ohne „different set of mods“                                                                            | S2       | Spiel lädt, Log `event=mod_load` im Client, keine Lobby-Ablehnung                                   |
| M3  | **Commence:** HQ platzieren → Setup-Phase endet, Wellen-Progress startet                                                                    | S3       | `event=commence status=ok`, danach `event=setup`/`event=wave`                                       |
| M4  | **Ingress-Invariante:** `ok:true` **⟹** nachweisbarer Effekt im Game-Log (kein Falsch-Grün)                                                 | S4       | `/exec` → `{"ok":true}` **und** `event=status …` im Log                                             |
| M5  | **Server-Wave sichtbar (Kern!):** Vom Server/Web-Knopf ausgelöster Spawn erzeugt Kreaturen, die der Spieler **sieht**                       | S5       | Log `event=wave level=3 status=done spawned>0` **und** Sicht-Check Momo                             |
| M6  | **Egress/State:** Der Referee kennt den laufenden Spielzustand (Score/Wave/HQ)                                                              | S6       | `GET /state` zeigt plausible Werte; Feed-Einträge                                                   |
| M7  | **HQ-Tod erkannt:** In-Game-HQ-Verlust endet das Match nachvollziehbar                                                                      | S7       | `event=hq_dead status=match_end` + `event=match_end reason=hq_destroyed`, `/state` `phase=finished` |
| M8  | **Runde 2 spielbar → 1.1 (2026-09-19):** Nach Niederlage Reset auf 0 und eine neue Runde startet sauber                                     | S8       | Log `rb_reset`/Round-Reset + HQ wieder 100, Wave-Zähler zurück                                      |
| M9  | **Fehlerverhalten:** Toter Kanal/Timeout gibt eine klare Fehlermeldung statt Hänger oder Falsch-Erfolg                                      | S9       | `/wave` mit gestoppter Bridge → Fehlerantwort ≤ ~3 s, **kein** `ok:true`                            |
| M10 | **Telemetry (Core-Dev, bestätigt):** Jede Spiel-Session wird persistent mitgeschnitten (Metriken), zuordenbar zu Match/Session              | S13      | Session-Artefakt (Log/Metrik-Datei) liegt vor + Pfad dokumentiert                                   |

---

## 5. Soll-Kriterien (Beobachtung, **kein** Gate für 1.0)

Diese Punkte werden dokumentiert und fließen in Follow-ups — sie blockieren
`v1.0.0` **nicht**.

| #   | Kriterium                                                                                                          | Szenario |
| --- | ------------------------------------------------------------------------------------------------------------------ | -------- |
| S1  | Economy-Loop spielbar: farmen → `rb_convert` → Boost/Reveal fühlt sich rund an                                     | S12      |
| S2  | HUD/Click-HUD bedienbar (`rb_hud_ui`, `rb_quick`, `rb_quick_step`)                                                 | S12      |
| S3  | Latenz „Knopfdruck → sichtbarer Spawn“ < ~2 s                                                                      | S5       |
| S4  | Balance-Feedback festgehalten (Wellen-Gefühl, HQ-HP-Kurve, Preise)                                                 | S12      |
| S5  | Stabilität/Dauerlauf — **„note for later“ (Momo): nicht 1.0-relevant**, ist bereits proven und zeigt sich im Spiel | S10      |

---

## 6. Nicht Teil von 1.0 (`n/a`)

- **Echtes 1v1/2-Spieler-Duell** (S11) — 1.0 ist solo (ein Server + Referee).
- 2v2 / 3v3 / 4v4, Team-Lobby, Matchmaking.
- ELO / Rangliste / Spielerprofile (Post-1v1).
- Balancing-Feintuning (Werte im GDD sind explizit „braucht Live-Test“).
- macOS-Support, UI-/HUD-Feinschliff, Accessibility.
- Ressourcen-Injektion im God-Panel (geparkt, #159), Auth/Multi-Match (v1-Grenze).

---

## 7. Szenarien

Legende: **Erwartung** = beobachtbar/prüfbar. **Beweis** = was ins Protokoll
kommt. Alle Log-Kommandos siehe Anhang (Abschnitt 8).

### S1 — Kaltstart & Host (C1) → M1

- **Vorgehen:** Container frisch starten (oder Deploy auslösen); warten, bis healthy.
- **Erwartung:** `Up … (healthy)`; im Log **genau eine** `event=mod_load version=<V> status=ok`;
  **keine** `handler_errors`, **keine** `event_unreadable`; `mods/` enthält nur `rbbattle/`.
- **Beweis:** `docker ps`-Zeile + die `mod_load`-Zeile + `RestartCount`.

### S2 — Spieler verbindet sich (Setup) → M2

- **Vorgehen:** Mod-Zip-Stand prüfen (P1); Server `:6321` im Spiel joinen.
- **Erwartung:** Join gelingt (keine „different set of mods“-Ablehnung); Welt lädt;
  `event=mod_load` erscheint im **Client**-Log.
- **Beweis:** Client-Logzeile + Screenshot Lade-/Startbild.

### S3 — Commence-Flow (HQ platzieren) → M3

- **Vorgehen:** Spiel startet **ohne** Auto-HQ; HQ platzieren (Commence-Flow #158).
- **Erwartung:** `event=commence status=pending` (vor HQ) → `event=commence status=ok`
  nach Platzierung; danach beginnt der Wellen-Progress.
- **Beweis:** beide Log-Zeilen + Screenshot platziertes HQ.

### S4 — Ingress-Effekt (C2, Invariante) → M4

- **Vorgehen:** Kommando **von außen** auf den laufenden Prozess:
  `POST /exec {"command":"rb_status"}` an die Bridge.
- **Erwartung:** Antwort `{"ok":true,"results":[{"command":"rb_status","ok":true}]}`
  **und** im **Game-Log** steht der ausgelöste Effekt (`event=status …`).
  `ok:true` ohne Effekt-Zeile = **rot** (#288-Fall).
- **Beweis:** `/exec`-Antwort + zugehörige `event=status`-Zeile.

### S5 — Server-Wave sichtbar (**C4-spawn, der Kern**) → M5, S3

- **Vorgehen:** Im Spiel einloggen (HQ platziert), dann **aus dem Spiel heraus**
  den Web-Knopf nutzen: `solo.html` → **„Welle spawnen“** (sendet
  `POST /wave {"world":"A","n":3}` → Referee → Bridge → Pipe → `exec rb_wave 3`).
  Alternativ per `curl` (siehe Anhang).
- **Erwartung:**
  1. Referee-Antwort: `{"ok":true,"exec_ok":true,"command":"rb_wave 3",…}`;
  2. Log: `event=wave level=3 status=done spawned>0` (`anchor=border|mission|mech`);
  3. **kein** `status=no_player` und **kein** `status=no_border_spawners`;
  4. **Sicht-Check Momo:** die Kreaturen erscheinen sichtbar (am Kartenrand / Boss im Bild).
- **Beweis:** Referee-Antwort **+** Log-Zeile **+** Screenshot der sichtbaren Welle.
- **Hinweis:** Punkte 1–3 sind automatisiert (C4-reached); **Punkt 4 ist der
  eigentliche 1.0-Beweis** und nur manuell zu erbringen.
- **Security-Hinweis (#298):** `POST /wave`, `/rematch`, `/report` sind über den
  Public-Proxy **unauthentifiziert** erreichbar. Die §8-Kommandos laufen über
  `ssh`/`127.0.0.1`; der Web-Knopf-Pfad in diesem Szenario geht aber über die
  **öffentliche** URL. Für den Test den Zugang absichern (Basic-Auth am
  Rift-Caddy oder Netz-Sperre) oder den scharfen Public-Pfad bewusst
  akzeptieren, bis #298 gefixt ist.

### S6 — Egress/State (C3) → M6

- **Vorgehen:** Nach Wellenstart `GET /state` abrufen; Web-Feed/Dev-Log beobachten.
- **Erwartung:** `/state` zeigt `phase=running`, plausiblen `round`, `teams.A.hq_hp`,
  Wave-/Score-Werte und Feed-Einträge; ein `score_update`/State-Snapshot kommt an.
- **Beweis:** `/state`-JSON (gekürzt) + Feed-Ausschnitt.

### S7 — HQ-Tod wird erkannt (C4) → M7

- **Vorgehen A (design-treu):** echte Leaks zulassen, bis HQ fällt.
- **Vorgehen B (Operator-Abkürzung):** God-Panel → **„HQ zerstören (Test)“**
  (`POST /report {"world":"A","event":"hq_hp","hp":0}`).
- **Erwartung:** `event=hq_hp hp=…` → bei 0 `event=hq_dead status=match_end hp=0`
  und `event=match_end reason=hq_destroyed`; In-Game-Annonce „GAME OVER — HQ destroyed“;
  `/state` `phase=finished` + `winner`.
- **Beweis:** Logzeilen + `/state` + Screenshot End-Screen.

### S8 — Runde 2 / Reset auf 0 → M8

> Hängt an Welle 2 (PR #285, `rb_reset` / #281). Falls zum Testzeitpunkt nicht
> gemergt: als `n/a (nicht implementiert)` markieren — **dann ist M8 nicht
> erfüllbar und 1.0 wird nicht bestätigt.**

- **Vorgehen:** Nach Match-Ende Reset auslösen (`rb_reset` aus dem Spiel bzw.
  Referee-Restart) und eine neue Runde spielen.
- **Erwartung:** HQ wieder auf Startwert (100), Runden-/Wave-Zähler zurück auf 0/1,
  Send-Queues leer, Spiel läuft ohne Neustart des Containers weiter; Wellen
  laufen in Runde 2 erneut.
- **Beweis:** Logzeilen (Reset + erster Wellenstart Runde 2) + Screenshot.

### S9 — Negativfall / Recovery → M9

- **Vorgehen:** Bridge absichtlich unerreichbar machen (z. B. Relay/Bridge im
  Dedicated-Container stoppen — **nur** auf dem Test-Stack, nicht auf Prod),
  dann `POST /wave`.
- **Erwartung:** Antwort **Fehler** (`ok:false`, `exec_ok:false`) innerhalb
  ≤ ~3 s (Timeout), **kein** `ok:true`, **kein** Hänger der UI; nach Rückkehr
  der Bridge funktioniert S5 wieder.
- **Beweis:** Fehlerantwort + Zeitstempel; anschließend grüner Wiederholungslauf.

### S10 — Stabilität / Dauerlauf (mehrere Wellen am Stück) → Soll („note for later“, **nicht** 1.0-blockierend)

> Momo (2026-09-12): **nicht wichtig für 1.0** — Stabilität ist bereits proven und
> zeigt sich im Test. Nur beobachten, kein Gate.

- **Vorgehen:** Mehrere Wellen am Stück spielen (Vorschlag ≥ 15 Min), Container-Status beobachten.
- **Erwartung:** kein Crash, kein Restart (`RestartCount` gleich), keine
  `handler_errors`; FPS/Spielgefühl nicht eingebrochen.
- **Beweis:** `docker ps`/`inspect` vor+nach (RestartCount), Log-Auszug.

### S11 — 1v1 mit zweitem Spieler → **`n/a` für 1.0** (Post-1.0)

- **Vorgehen:** Zwei Spieler (`/lobby` A+B bzw. zwei Dedicated-Welten), beide
  ready → `POST /go` (bzw. AUTO_GO).
- **Erwartung:** synchroner Start (`debug_dom_resume`), Send-Routing A→B/B→A,
  Reveal bei Wellenstart, Match-Ende bei HQ-Tod einer Seite.
- **Beweis:** `/state` (teams, reveal) + Screenshots beider Seiten.
- **Hinweis:** 1.0 ist **solo** (Momo, 2026-09-12): **ein** Dedicated-Server +
  Tournament-Referee. Der Solo-SP-Modus (`POST /sp`, MIRROR-Welt) ist der
  1.0-Pfad; dieses Szenario dient nur dem späteren Duell.

### S12 — Economy/Send-Loop & Balance-Eindruck (Soll) → S1, S2, S5

- **Vorgehen:** Farmen → `rb_convert` → `rb_boost`/`rb_shop` → Reveal bei Wellenstart;
  Click-HUD (`rb_hud_ui`, `rb_quick`, `rb_quick_step`) bedienen.
- **Erwartung:** Loop nachvollziehbar, Reveal zeigt Built-Values + eingehende Sends;
  **Eindruck notieren** (nicht bewerten als Gate): Wellen-Gefühl, HQ-HP-Kurve, Preise.
- **Beweis:** Screenshots + Freitext-Eindruck.

### S13 — Telemetry / Session-Mitschnitt → **M10 (Muss, #280)**

- **Vorgehen:** Eine vollständige Spiel-Session fahren; danach prüfen, ob der
  Recorder-/Telemetrie-Layer den Lauf persistiert hat.
- **Erwartung:** Session-Log/Metrik-Datei liegt vor, ist der Session/Match
  zuordenbar (Runde, Dauer, Wave/Score-Spuren) und übersteht Container/Prozess-Ende.
- **Beweis:** Datei/Link + Pfad + kurzer Ausschnitt (Metrik-/Eventzeilen).

---

## 8. Beweismittel-Kommandos (Anhang)

> Alle Kommandos sind **read-only** außer den ausdrücklich markierten
> Sende-Kommandos (`/exec`, `/wave`, `/report`). Quellen:
> [`docs/DEPLOYMENT.md`](DEPLOYMENT.md), [`tests/core-io/README.md`](../tests/core-io/README.md).

```bash
# --- P1 Mod-/Download-Parität -------------------------------------------------
bash scripts/mod_version.sh
curl -sI https://rift.projectmellon.de/mods/rbbattle.zip | head -3
ssh planet 'md5sum /srv/rbmods-site/mods/rbbattle.zip'

# --- P2 Host ---------------------------------------------------------------
ssh planet 'docker ps --filter name=riftbreaker-dedicated \
  --format "{{.Names}} {{.Status}} {{.Ports}}"'
ssh planet 'docker inspect riftbreaker-dedicated --format "RestartCount={{.RestartCount}}"'

# --- P3 Referee ------------------------------------------------------------
ssh planet 'curl -s http://127.0.0.1:8081/health'

# --- P4 Web-UI + API-Pfad --------------------------------------------------
curl -s -o /dev/null -w 'solo.html %{http_code}\n'  https://rift.projectmellon.de/solo.html
curl -s -o /dev/null -w 'api       %{http_code}\n'  https://rift.projectmellon.de/tournament/health

# --- P5 Log (kanonisch: `docker logs`; Entrypoint tailt exor_logs.txt -> stdout) ---
# Kanonisch ist der Container-Stdout: der Entrypoint des Community-Images tailt
# exor_logs.txt nach stdout, daher stehen alle [RBBATTLE]-Zeilen in `docker logs`.
# (Das alte Image bis #241 hatte exor_logs.txt unter /root — das existiert nicht mehr.)
ssh planet 'docker logs riftbreaker-dedicated 2>&1 | grep -a RBBATTLE | tail -30'
# gezielt:
ssh planet 'docker logs riftbreaker-dedicated 2>&1 | grep -a "event=wave" | tail -5'
ssh planet 'docker logs riftbreaker-dedicated 2>&1 | grep -a "event=hq_dead\|event=match_end" | tail -5'
# optional die Datei selbst (Wine-Documents-Pfad, NICHT /root/exor_logs.txt):
ssh planet 'tail -30 "/srv/riftbreaker/data/wine/drive_c/users/steamuser/Documents/The Riftbreaker/exor_logs.txt"'

# --- P6 Bridge -------------------------------------------------------------
ssh planet 'curl -s http://127.0.0.1:9001/health'          # {"ok":true,"pipe":true}

# --- S4 Ingress (sendet!) --------------------------------------------------
ssh planet 'curl -s -X POST http://127.0.0.1:9001/exec \
  -H "Content-Type: application/json" -d "{\"command\":\"rb_status\"}"'

# --- S5 Wave (sendet!) -----------------------------------------------------
# Web-Knopf: https://rift.projectmellon.de/solo.html  →  „Welle spawnen“
ssh planet 'curl -s -X POST http://127.0.0.1:8081/wave \
  -H "Content-Type: application/json" -d "{\"world\":\"A\",\"n\":3}"'

# --- S6/S7 State (read-only) ----------------------------------------------
ssh planet 'curl -s http://127.0.0.1:8081/state | python3 -m json.tool | head -60'

# --- S7 HQ-Tod per Referee (sendet!) --------------------------------------
ssh planet 'curl -s -X POST http://127.0.0.1:8081/report \
  -H "Content-Type: application/json" -d "{\"world\":\"A\",\"event\":\"hq_hp\",\"hp\":0}"'

# --- S8 Restart (sendet!) --------------------------------------------------
ssh planet 'curl -s -X POST http://127.0.0.1:8081/rematch \
  -H "Content-Type: application/json" -d "{}"'

# --- Automatisches Gate (read-only, zusätzlicher Beleg) --------------------
python3 tests/core-io/core_io_probe.py --remote "ssh planet" \
  --container riftbreaker-dedicated \
  --bridge-url http://127.0.0.1:9001/exec
```

**Erwartete Logzeilen (Mod, `client-mod/lua/rbbattle_autoexec.lua`):**

```
[RBBATTLE] event=mod_load version=<V> status=ok mode=<sp|duel> econ_source=…
[RBBATTLE] event=setup difficulty=<n> … timer_cap=…
[RBBATTLE] event=commence status=pending|held|ok …
[RBBATTLE] event=wave level=N status=start
[RBBATTLE] event=wave level=N status=done spawned=<X> skipped=<Y> anchor=<border|mission|mech>
[RBBATTLE] event=wave_spawners count=<N>
[RBBATTLE] event=hq_hp hp=<..> dead=<true|false>
[RBBATTLE] event=hq_dead status=match_end hp=0
[RBBATTLE] event=match_end reason=hq_destroyed
```

**Rote Flaggen (jede einzelne = Fund):**
`event=wave … status=no_player` · `status=no_border_spawners` · `status=no_spawns`
(`spawned=0`) · `status=no_position` · `status=invalid_level` ·
`handler_errors` / `event_unreadable` · `event=mod_load` **doppelt oder fälschlich** ·
`ok:true` ohne Effekt-Zeile · UI-„OK“ bei unklarer `/wave`-Antwort.

---

## 9. Bekannte Lücken (beim Anlegen dieses Dokuments)

Diese Punkte sind **belegt** und beeinflussen den Testablauf:

1. **`/tournament/*`-Proxy der Website — ✅ erledigt (P4 grün).**
   Ursprünglich (read-only geprüft 2026-09-12) lieferte
   `https://rift.projectmellon.de/tournament/health` **404**: Das laufende
   Caddy-Snippet fehlte im `mellon-caddy`-Container. Der Fix (#322, PR #326, am
   2026-09-12 gemergt) hat dem Riftbreaker-Stack einen **eigenen `rift-caddy`**
   gegeben (eigener Container, plain HTTP, reines Durchreichen von Statics +
   `/tournament/*`), sodass der Deploy-Host-Caddy nur **EINEN** Eintrag braucht
   (`rift.projectmellon.de → reverse_proxy 127.0.0.1:<rift-caddy>`).
   **Ist-Stand (2026-09-13):** `rift-caddy` läuft;
   `https://rift.projectmellon.de/tournament/health` → **`200`**
   `{"ok":true,"phase":"lobby"}`, `/tournament/state` → **`200`**, `solo.html` → `200`.
   P4 ist damit **grün**; ein 1.0-Blocker ist das nicht mehr.
2. **C4-spawn ist headless nicht beweisbar** → deshalb ist S5/M5 der
   entscheidende manuelle Beweis (`tests/core-io/README.md`).
3. **Egress bis in den Referee ist nur teilweise belegt:** Auf dem
   Dedicated-Server läuft kein Relay, das die Game-Log-Events als
   `POST /event` einliefert (#13/#265). Für M6 zählt daher der
   nachweisbare State-/Feed-Fluss, nicht „jede Mod-Zeile kommt im Server an“.
4. **M8 (Runde 2) hängt an #281/PR #285** — von Momo als **zwingend** bestätigt;
   muss zum Testzeitpunkt auf `main` und deployt sein.
5. **Economy-Fallback:** `event=economy_source source=tick status=fallback`
   kann auftreten (bekannt, #242) — **kein** 1.0-Blocker, aber notieren.

---

## 10. Ergebnis-Protokoll (ausfüllen)

### Muss-Kriterien

| #   | Ergebnis (`pass`/`fail`/`n/a`) | Beweis (Logzeile/URL/Screenshot) | Notiz |
| --- | ------------------------------ | -------------------------------- | ----- |
| M1  |                                |                                  |       |
| M2  |                                |                                  |       |
| M3  |                                |                                  |       |
| M4  |                                |                                  |       |
| M5  |                                |                                  |       |
| M6  |                                |                                  |       |
| M7  |                                |                                  |       |
| M8  |                                |                                  |       |
| M9  |                                |                                  |       |
| M10 |                                |                                  |       |

### Soll-Beobachtungen

| #                  | Beobachtung | Follow-up-Issue |
| ------------------ | ----------- | --------------- |
| S1                 |             |                 |
| S2                 |             |                 |
| S3                 |             |                 |
| S4                 |             |                 |
| S5 (S10/Dauerlauf) |             |                 |
| S5                 |             |                 |

### Funde / Störungen

| Fund | Schwere | Issue |
| ---- | ------- | ----- |
|      |         |       |

### Beweisformat (von Momo bestätigt)

- **Die Abnahme ist Momos Urteil:** „Ich bin happy.“ **oder** „Ich bin nicht happy“ —
  das ist der bindende Beweis, keine Checkbox.
- Dazu wird die **Nachvollziehbarkeit** angehängt, damit klar ist, **welches
  Deployment zu welchem Commit gehört**: Commit-SHA, Mod-Version, `rbbattle.zip`-md5,
  deployter Stand (Container/`mod_load`-Zeile), PR-/Run-URLs. Logs/Screenshots sind
  Belege, **kein** Ersatz für das Urteil.

### Urteil

- [ ] **Momo: happy** → **`v1.0.0` als Baseline bestätigt.** (Traceability angehängt)
- [ ] Muss-Kriterien offen / nicht happy → **Baseline nicht bestätigt**; Funde als
      Issues, nächster Kandidat (`v1.0.1`).

**Sign-off (Tester):** Momo **\*\*\*\***\_\_**\*\*\*\*** **Datum:** \***\*\_\_\_\_\*\***

**Angehängte Traceability:** Commit `____________` · Mod `________` ·
`rbbattle.zip` md5 `____________` · Deployter Stand `____________`

---

## 11. Entscheidungsregeln

1. **Test-Hoheit liegt bei Momo.** Das bindende Urteil ist Momos „happy“ bzw.
   „nicht happy“; ein „grün“ der CI ersetzt die Sicht-Abnahme nicht, ein `fail`
   in S5/M5 blockiert 1.0 auch bei sonst grünem CI.
2. **Muss-Kriterien sind binär.** „geht meistens“ = `fail` (mit Notiz).
3. **Jeder Fund wird ein Issue** (Label `bug`/`follow-up`) und im Protokoll
   verlinkt — nichts wird nur mündlich festgehalten.
4. **Der Tag friert, der Test bestätigt.** Nach bestätigtem Test bleibt
   `v1.0.0` unverändert (keine „nachträglichen“ Commits am Tag).
5. **Player-Test zählt doppelt:** Für M5/M7 genügt **kein** Log — es braucht
   Log **und** Sicht.

---

## 12. Entscheidungen (Momo, 2026-09-12) & Rest-Offenes

**Entschieden:**

1. **Scope:** 1.0 ist **solo** (ein Dedicated-Server + Tournament-Referee);
   Game-State + Control **müssen von extern** kommen. 1v1 nicht Teil von 1.0.
2. **Round-Reset (#281):** war Core-Game-Loop → **Muss** (M8) — **2026-09-19 nach 1.1 verschoben**.
3. **Website-Proxy (#322):** ✅ erledigt — PR #326 gemergt (2026-09-12),
   eigener `rift-caddy`, P4 **grün** (2026-09-13).
4. **Telemetry (#280):** Core-Dev-Feature 1.0 → **Muss** (M10).
5. **Beweisformat:** **Momos „happy“** + Traceability (Commit/Mod/md5/Deploy).
6. **Stabilität/Dauerlauf (S10):** **„note for later“** — nicht 1.0-relevant,
   bereits proven; wird im Test nur beobachtet (Soll S5).

**Noch offen:**

1. **Timing:** Test nach Welle 2/3 aus #319, dann Tag `v1.0.0` — oder erst
   taggen und dann testen? (Der frühere P4/#322-Vorbehalt ist entfallen.)
2. **Security (#298):** Der Tournament-API-Pfad ist öffentlich unauthentifiziert
   erreichbar (`POST /wave`/`/rematch`/`/report`) — vor dem Test absichern
   (Basic-Auth/Netz-Sperre) oder bewusst akzeptieren?

Refs #319, Refs #320, Refs #289, Refs #266, Refs #267, Refs #281, Refs #280, Refs #298
