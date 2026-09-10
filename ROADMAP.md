# Roadmap

Weg von **Solo-MVP** (aktuelles Ziel) über den ersten **1v1-Release** bis zu
größeren Team-Modi (**2v2 → 3v3 → 4v4**). Jede Phase ist ein
[GitHub-Milestone](https://github.com/momokli/riftbreaker-battle-mod/milestones)
mit eigenen Issues — dieses Dokument bündelt Ziel, Schritte und
Release-Kriterien je Phase. Live-Stand der Issues: siehe Milestone-Links
unten oder [docs/PROGRESS.md](docs/PROGRESS.md).

## Bereits erledigt (Fundament, Milestone „RIFT BATTLE v1“)

Game-Design-Kern, Economy/Send-Loop, Reveal-HUD, Win-Condition, Tournament-
Server, Relay ↔ rbbridge-Kopplung (inkl. Antwort-Feedback, #60/#73) und die
CI/CD- & Contributing-Basis (#75) sind gebaut und gemergt — Details in den
geschlossenen Issues des
[Milestones „RIFT BATTLE v1“](https://github.com/momokli/riftbreaker-battle-mod/milestone/1).
Darauf bauen die folgenden Phasen auf.

## Phase 1 — Solo-MVP (aktuelles Ziel)

**Ziel:** Solo-Modus spielbar vs. sich selbst — Spieler joint, verbindet sich
über `solo.html`, spielt gegen sich selbst (Self-Send-Mechanik), sieht live
mit, was passiert; CI ist durchgehend grün.
[Milestone „Solo-MVP“](https://github.com/momokli/riftbreaker-battle-mod/milestone/2)

**Schritte:**

| Issue | Thema |
|---|---|
| [#95](https://github.com/momokli/riftbreaker-battle-mod/issues/95) | `solo.html`: LLM-Prompt-Reste/Anweisungen entfernen |
| [#96](https://github.com/momokli/riftbreaker-battle-mod/issues/96) | `solo.html`: Copy kürzen — lean, weniger Text, klar |
| [#97](https://github.com/momokli/riftbreaker-battle-mod/issues/97) | `solo.html`: Server-Adresse + Verbinden + Spielstart |
| [#98](https://github.com/momokli/riftbreaker-battle-mod/issues/98) | `solo.html`: transparenter DEV-Log (wer spielt, Game-Events, was gespeichert wird) |
| [#99](https://github.com/momokli/riftbreaker-battle-mod/issues/99) | Mod: HUD zum Senden per Klick statt Tippen |
| [#93](https://github.com/momokli/riftbreaker-battle-mod/issues/93) | CI grün halten (Version-Bump-Konsistenz) — ✅ erledigt |
| #91-Anteil | CD auf DEV-Server (main → dev, siehe Phase 2) |

**Release-Kriterium („Solo-MVP ist done“):** `solo.html` ist bereinigt, lean
und hat einen funktionierenden Connect-/Spielstart-Flow; der DEV-Log zeigt
live, wer spielt und was passiert; der Mod hat ein klickbares Send-HUD; CI
(Lint + Test + Build) ist grün auf `main`.

## Phase 2 — 1v1 (erster PROD-Release)

**Ziel:** Release 1v1 — PROD-Deploy via Git-Tag, Balancing abgeschlossen,
Persistenz verifiziert, Infra-Follow-ups erledigt.
[Milestone „1v1“](https://github.com/momokli/riftbreaker-battle-mod/milestone/3)

**Schritte:**

| Issue | Thema |
|---|---|
| [#91](https://github.com/momokli/riftbreaker-battle-mod/issues/91) | CD: `main`→dev / Tag→prod Deploy (Steam + Non-Steam, je mit Website) |
| [#33](https://github.com/momokli/riftbreaker-battle-mod/issues/33) | Balance & Tuning v1: Preisliste, Wellen-Gefühl, HQ-HP-Kurve |
| [#39](https://github.com/momokli/riftbreaker-battle-mod/issues/39) | Send-Boost: nächste Naturwelle prozentual verstärken |
| [#40](https://github.com/momokli/riftbreaker-battle-mod/issues/40) | Send-Währung: Ressourcen-Mapping (Calcium/Ironium) |
| [#41](https://github.com/momokli/riftbreaker-battle-mod/issues/41) | Grundschwierigkeit + Wellen-Takt testen |
| [#65](https://github.com/momokli/riftbreaker-battle-mod/issues/65) | RE-Verifikation: Persistenz des Spar-Pools über Session-/Map-Reload |
| [#67](https://github.com/momokli/riftbreaker-battle-mod/issues/67) | Repo-Hygiene: verwaiste Branches aufräumen |
| [#87](https://github.com/momokli/riftbreaker-battle-mod/issues/87) | Follow-up: Pipeline-Artefakt-Dateien aus dem Repo-Root entfernen |
| [#88](https://github.com/momokli/riftbreaker-battle-mod/issues/88) | Follow-up: Deployment-Playbook (Ansible) abschließen |

**Release-Kriterium („1v1 ist done“):** Ein Tag `vX.Y.Z` deployt automatisiert
auf den PROD-Server (1v1-Duell); Preise/Wellen-Takt/Grundschwierigkeit sind
final getunt; der Spar-Pool übersteht Session-/Map-Reloads nachweislich; keine
offenen Infra-Follow-ups aus Phase 1/2.

## Phase 3–5 — 2v2 → 3v3 → 4v4

**Ziel:** Skalierung des Duell-Modus auf Team-Größen 2v2, 3v3 und 4v4 —
Matchmaking/Lobby, Tournament-Server und Reveal-HUD für mehr als zwei
Parteien, entsprechend erweiterte Balance.

Milestones „2v2“, „3v3“, „4v4“ sind angelegt — siehe
[Milestones-Übersicht](https://github.com/momokli/riftbreaker-battle-mod/milestones).

Noch keine Issues zugeordnet — Scope wird nach dem 1v1-Release im Detail
geplant (Team-Lobby, N-seitiges Wave-Routing/Reveal, Balance je Team-Größe).
Jede Phase baut auf der vorherigen auf und bekommt vor Start ihre eigenen
Steps in diesem Dokument.

**Release-Kriterium (je Phase):** Ein Tag deployt einen stabilen
`NvN`-Duell-Modus auf PROD; Matchmaking/Lobby und Reveal-HUD unterstützen die
Team-Größe vollständig; Balance ist für die neue Team-Größe getestet.
