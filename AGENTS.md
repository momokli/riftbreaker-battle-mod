# AGENTS.md — Kollaborations-Regeln für AI-Agents

Dieses Repository wird von mehreren AI-Agents (parallel) und Menschen bearbeitet.
Diese Datei ergänzt [CONTRIBUTING.md](CONTRIBUTING.md); bei Widerspruch gilt
CONTRIBUTING.md.

## 1. Issue zuerst

Jede Arbeit beginnt mit einem Issue: erst prüfen, ob eines existiert
(`gh issue list --search …`), sonst neu anlegen (Vorlagen in
`.github/ISSUE_TEMPLATE/`). Keine Änderung ohne Issue — der PR-Check
„Issue-Referenz" erzwingt das ohnehin (Hard-Fail).

## 2. Claimen: nur ein Agent pro Issue

Vor der Arbeit das Issue mit einem Kommentar `!claim` übernehmen
(System: [`issue-claim.yml`](.github/workflows/issue-claim.yml); Label `claimed`).
Fremde Claims respektieren — **kein Doppel-Arbeiten**. Nicht mehr dran?
`!unclaim` gibt das Issue wieder frei.

## 3. Branch & Push

Von aktuellem `main` abzweigen: `feature/<kurzbeschreibung>` (neu) oder
`fix/<kurzbeschreibung>` (Bugfix). **Niemals direkt auf `main` pushen** — alles
läuft über Pull Requests.

## 4. PR: Issue-Referenz ist Pflicht

Jeder PR referenziert ein Issue in **Titel oder Beschreibung** (`Closes #N`
schließt beim Merge automatisch, `Refs #N` referenziert nur). Fehlt die
Referenz, schlägt der Check `issue-reference` fehl (**Hard-Fail**) und es wird
ein Kommentar mit Anleitung am PR hinterlassen. Vorlage:
[`.github/pull_request_template.md`](.github/pull_request_template.md).

## 5. Definition of Done

- CI grün: `lint`, `pr-quality`, `ci`.
- Neuer Code hat Tests.
- Keine Secrets, keine Debug-Reste im Diff.
- PR-Titel folgt Conventional Commits.

## 6. Follow-up-Dimensionen

Beim Schließen eines Issues legt
[`followup-issues.yml`](.github/workflows/followup-issues.yml) automatisch ein
Follow-up-Issue an (Label `follow-up`). Prüfe die Dimensionen und lege für
zutreffende Punkte eigene Issues/PRs an und verlinke sie im Follow-up:

- Code-Optimierung
- Dokumentation
- Accessibility
- UI-Design
- HUD-Design
- Game-Design

**Auflösen (DoD):** Jede Dimension kurz bewerten. Zutreffende Dimensionen als
eigenes Issue bzw. PR umsetzen und im Follow-up verlinken (Checkbox abhaken);
nicht zutreffende Dimensionen als `n/a` markieren. Erst wenn alle sechs
Dimensionen bewertet und verlinkt sind, das Follow-up schließen.

Follow-ups tragen selbst das Label `follow-up` und erzeugen keine weiteren
Follow-ups (Label-Guard).

## 7. Verifikation

- Push nicht blind vertrauen: Remote-Stand per
  `git ls-remote origin refs/heads/<branch>` gegen den lokalen HEAD prüfen.
- Der Abschlussbericht enthält immer die **PR-URL** (und bei CI-Themen die
  Run-URLs).

## 8. Kommunikation

Issues, PRs und Kommentare auf **Deutsch oder Englisch** — beides ist ok, aber
je Thread technisch konsistent (gleiche Begriffe, Pfade, Befehle). Vorher
prüfen, ob ein passender Thread existiert, statt einen neuen aufzumachen.
