# Spieler-Profil-Datenmodell (ELO, Bilanz, Historie)

Entwurf für Issue **#129** (Sub-Issue von [#128] Konzept: ELO-Rangsystem &
Spieler-Profile). Dieses Dokument ist die **Entscheidungsvorlage**: es legt
Felder + Typen fest, begründet den Speicherort und zeigt, dass der bestehende
Tournament-API-v1-Vertrag unverändert bleibt.

Kein v1-Blocker — reine Grundlage für die Folge-Issues [#130] (Login/Identität),
[#131] (ELO-Berechnung) und [#132] (Leaderboard). Refs: [#128], [#65], [#24].

## 1. Datenmodell

### 1.1 `player_profile` — der persistente Datensatz

| Feld | Typ | Null? | Default | Bedeutung |
|---|---|---|---|---|
| `player_id` | TEXT | nein | — | **Primärschlüssel** — stabile Identität aus dem Login (#130). Bevorzugt die SteamID64, Fallback normalisierter Name (`lower(trim(name))`). |
| `display_name` | TEXT | nein | — | Frei änderbarer Anzeigename (HUD, Leaderboard). |
| `identity_source` | TEXT (`steamid` \| `name`) | nein | `name` | Woher `player_id` stammt — macht den Übergang von freiem Namen auf Steam-Login (#130) nachvollziehbar. |
| `elo` | INTEGER | nein | `1200` | ELO-Rating; Startwert 1200 (Schach-Analogie, #128/#131). Untergrenze `ELO_MIN` = 100 (Floor, damit Ratings nicht negativ/kollabieren). |
| `wins` | INTEGER | nein | `0` | gewonnene Matches. |
| `losses` | INTEGER | nein | `0` | verlorene Matches. |
| `draws` | INTEGER | nein | `0` | Unentschieden. |
| `matches_played` | INTEGER | nein | `0` | Gesamtzahl gewerteter Matches; **Invariante**: `= wins + losses + draws`. |
| `created_at` | TEXT (RFC 3339 UTC) | nein | `now` | Anlagezeitpunkt des Profils. |
| `updated_at` | TEXT (RFC 3339 UTC) | nein | `now` | Zeitpunkt der letzten Änderung (ELO/Bilanz). |

`player_id` ist der **einzige** Identitätsanker (UNIQUE): Profil-Lookups,
Bilanz-Fortschreibung und Match-Zuordnung laufen ausschließlich über ihn.
`display_name` ist bewusst *nicht* eindeutig.

### 1.2 `match_record` — Historie je Spieler

Historie ist 1:n zum Profil und liegt in einer eigenen Tabelle (nicht als
JSON-Array am Profil), damit sie unbegrenzt wächst und #132 sie abfragen kann.

| Feld | Typ | Null? | Bedeutung |
|---|---|---|---|
| `match_id` | TEXT | nein | Match-Bezeichner (Teil des Composite-Keys). |
| `player_id` | TEXT | nein | FK → `player_profile.player_id`. |
| `opponent_id` | TEXT | nein | `player_id` des Gegners (`MIRROR` im SP-Mode, #44). |
| `result` | TEXT (`win` \| `loss` \| `draw`) | nein | Ergebnis aus Sicht von `player_id`. |
| `elo_before` | INTEGER | nein | Rating vor dem Match. |
| `elo_after` | INTEGER | nein | Rating nach dem Match. |
| `elo_delta` | INTEGER | nein | `elo_after − elo_before` (Beleg für #131). |
| `mode` | TEXT (`duel` \| `sp`) | nein | Match-Modus (SP-Matches tragen 0 ELO-Änderung, siehe §3). |
| `rounds` | INTEGER | nein | gespielte Runden bis HQ-Tod. |
| `finished_at` | TEXT (RFC 3339 UTC) | nein | Match-Ende. |

Primärschlüssel: (`match_id`, `player_id`) — ein Match erzeugt **zwei** Zeilen
(eine je Spieler).

### 1.3 Beispiel (JSON-Antwort eines künftigen `GET /profile/{player_id}`)

```json
{
  "player_id": "76561198000000000",
  "display_name": "momo",
  "identity_source": "steamid",
  "elo": 1216,
  "wins": 3, "losses": 2, "draws": 0, "matches_played": 5,
  "created_at": "2026-09-10T19:00:00Z",
  "updated_at": "2026-09-10T20:12:34Z"
}
```

### 1.4 Invarianten (Grundlage für #131 und für spätere Tests)

1. `matches_played == wins + losses + draws`
2. `elo >= ELO_MIN` (100)
3. `updated_at >= created_at`
4. `player_id` ist UNIQUE und unveränderlich; Namensänderungen ändern nur `display_name`.
5. Jede `match_record`-Zeile: `elo_after == elo_before + elo_delta`, und `mode = "sp"` ⇒ `elo_delta == 0`.

## 2. Speicher-Entscheidung

**Gewählt: SQLite** — eine Datei, eingebettet im Tournament-Server
(`rusqlite`), Pfad per Env `TOURNAMENT_DB_PATH` (Default
`./data/rbbattle.db`), `journal_mode=WAL`.

Begründung und verworfene Alternativen:

| Option | Bewertung |
|---|---|
| **In-Memory (Status quo der Tournament-API v1)** | **Verworfen.** `docs/TOURNAMENT_API.md` hält v1 bewusst zustandslos; ein Prozess-Restart (Deploy, Crash, `:6321`-Redeploy) löscht Profile. Für ELO/Bilanz über Sessions hinweg (Kern von #128, vgl. Persistenz-Lehre aus #65) reicht das nicht. |
| **Externe DB (Postgres/MariaDB)** | **Verworfen.** Zusätzlicher Dienst + Credentials + Backups + Migrations für eine Handvoll Spieler; widerspricht dem Ein-Host-Deploy (`deploy/site.yml`) und dem Env-only-Config-Prinzip des Servers. |
| **JSON-Datei als Store** | **Verworfen.** Keine Transaktionen/Atomarität bei parallelen Requests, kein Index — ungeeignet für gleichzeitige Match-Enden; #132 müsste alles im Speicher sortieren. |
| **SQLite (gewählt)** | Zero-Ops (keine eigene Dienst-Instanz, keine Credentials), transaktional, übersteht Restarts, eine Datei = trivial in den bestehenden restic-Backup aufzunehmen, und `ORDER BY elo` liefert #132 das Leaderboard direkt. `rusqlite` ist eine reine Rust-Abhängigkeit ohne Runtime. |

Abgrenzung: Die Match-State-Machine bleibt **in-memory**; die Profil-DB ist ein
**orthogonaler** Store. Es wird kein Match-State dupliziert — Profile werden nur
bei Match-Ende fortgeschrieben.

## 3. Kein Breaking Change am Tournament-API-v1-Vertrag

- Alle bestehenden Endpunkte (`/lobby`, `/ready`, `/go`, `/send`, `/report`,
  `/rematch`, `/sp`, `/state`, `/events`, `/health`) behalten Pfad, Body und
  Antwortformat exakt wie in `docs/TOURNAMENT_API.md` dokumentiert.
- **Additiv, nicht modifizierend:** Profil-Endpunkte (z. B.
  `GET /profile/{player_id}`) kommen erst mit #131/#132 hinzu. Neue Antwortfelder
  sind unkritisch, da der Vertrag unbekannte Felder bereits ignoriert
  („vorwärtskompatibel").
- **Registrierung bleibt kompatibel:** `POST /lobby` akzeptiert weiterhin den
  freien Textnamen `{"player": "momo", "world": "A"}`. Die Verknüpfung zum
  Profil entsteht über ein *optionales* `player_id`-Feld (aus #130), nie durch
  ein neues Pflichtfeld.
- **SP-Mode:** `mode: "sp"`-Matches erzeugen Historie, aber `elo_delta = 0`
  (Selbst-Duell, kein fairer Gegner) — der bestehende `POST /sp`-Vertrag bleibt
  unberührt.
- Die Profil-DB wird **nicht** Teil des Referee-Match-States; `GET /state`
  ändert sich dadurch nicht.

## 4. Offene Punkte (Folge-Issues)

- **#130** — Identitätsquelle `player_id` (SteamID64 vs. Name) + Normalisierung;
  dieses Dokument fixiert nur den *Typ* (TEXT, UNIQUE), nicht die Quelle.
- **#131** — ELO-Formel, K-Faktor, Draw-Behandlung; nutzt `elo_before/after/delta`
  aus `match_record`.
- **#132** — Leaderboard-Query (`SELECT … ORDER BY elo DESC`).
- **Betrieb** — `TOURNAMENT_DB_PATH` in `deploy/` + restic-Backup aufnehmen,
  sobald die DB produktiv läuft (Post-1v1).

[#24]: https://github.com/momokli/riftbreaker-battle-mod/issues/24
[#65]: https://github.com/momokli/riftbreaker-battle-mod/issues/65
[#128]: https://github.com/momokli/riftbreaker-battle-mod/issues/128
[#130]: https://github.com/momokli/riftbreaker-battle-mod/issues/130
[#131]: https://github.com/momokli/riftbreaker-battle-mod/issues/131
[#132]: https://github.com/momokli/riftbreaker-battle-mod/issues/132
