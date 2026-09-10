# Contributing

Danke fürs Mitmachen! Dieses Dokument beschreibt den Workflow, die Regeln und die
Quality-Gates in diesem Repo. Sprache: Deutsch oder Englisch — beides ist ok.

## Workflow (Issue → PR)

1. **Issue aufmachen** — nutze die Vorlagen *Bug*, *Feature* oder *Spike*
   (`.github/ISSUE_TEMPLATE/`).
2. **Claimen** — schreibe einen Kommentar mit genau `!claim`. Der Bot weist dir das
   Issue zu, setzt das Label `claimed` und bestätigt. Ein Issue hat **immer nur einen
   Assignee** — so arbeitet niemand doppelt. Nicht mehr dran? `!unclaim` gibt es frei.
3. **Branch erstellen** — `feature/<kurzbeschreibung>` oder `fix/<kurzbeschreibung>`.
4. **Arbeiten & committen** — klein und nachvollziehbar (siehe Commit-Konvention).
5. **Pull Request** — gegen `main`, **immer mit Issue-Link** (`Closes #N` / `Refs #N`) — seit den PR-Gates Pflicht (Hard-Fail, siehe unten).
6. **Review** — mindestens eine Freigabe; CI muss grün sein.
7. **Squash-Merge** durch einen Maintainer.

## Regeln

- **Kein Direct-Push auf `main`.** Alles läuft über einen PR.
- **Nicht an ungeclaimten Issues arbeiten** — erst `!claim`.
- **PR immer mit Issue verlinkt** (`Closes #N` schließt automatisch, `Refs #N` referenziert nur) — Pflicht, sonst schlägt das Issue-Referenz-Gate fehl.
- **Definition of Done**
  - CI grün: **lint + pr-quality + ci**.
  - Neuer Code hat Tests.
  - Keine Debug-Reste, keine Credentials/Secrets im Diff.
- **Commit-Konvention:** [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `chore:`, `ci:`, `refactor:`, `test:`, `build:`, `perf:`,
  `style:`, `revert:`), Message auf Englisch.
- **Releases** macht nur ein Maintainer über einen `v*-Tag` (z. B. `v1.2.0`).

## Issue-Referenz-Pflicht (Hard-Fail)

Jeder PR **muss** ein Issue referenzieren — im Titel oder in der Beschreibung
(`Closes #N` schließt das Issue beim Merge automatisch, `Refs #N` referenziert
nur). Fehlt die Referenz, schlägt der Check `issue-reference` in
[`pr-quality.yml`](.github/workflows/pr-quality.yml) **hart fehl** und setzt
zusätzlich einen Kommentar mit Anleitung an den PR. Sobald Titel oder Body eine
`#N`-Referenz enthalten, wird der Check beim nächsten Lauf automatisch grün.

**Warum so streng?** Nachvollziehbarkeit vom Code zum Kontext, automatische
Changelog-/Milestone-Pflege und konsistente Abschlussberichte — jede Änderung
hat ein nachlesbares Issue. Eingeführt per Dogfooding: Der Hard-Fail selbst
lief über ein Issue. PR-Vorlage:
[`.github/pull_request_template.md`](.github/pull_request_template.md).

## Automatische Follow-up-Issues

Beim **Schließen** eines Issues legt
[`followup-issues.yml`](.github/workflows/followup-issues.yml) automatisch ein
Follow-up-Issue an (Label `follow-up`) — mit einer Checkliste über die
Qualitäts-Dimensionen:

- Code-Optimierung
- Dokumentation
- Accessibility
- UI-Design
- HUD-Design
- Game-Design

Für jede zutreffende Dimension bitte ein **eigenes Issue bzw. einen PR**
aufmachen und im Follow-up verlinken. Nicht zutreffende Dimensionen als `n/a`
markieren; erst wenn alle Dimensionen bewertet und verlinkt sind, das Follow-up
schließen.

Regeln des Automatismus:

- Follow-ups tragen selbst das Label `follow-up` und erzeugen beim Schließen
  **keine weiteren** Follow-ups (keine Ketten).
- Schließt ein Bot ein Issue, entsteht das Follow-up ohne Assignee; sonst wird
  die schließende Person zugewiesen (sofern der Login existiert).

## Zusammenarbeit mehrerer Agents

Neben Menschen arbeiten AI-Agents am Repo. Die verbindlichen Regeln stehen in
[AGENTS.md](AGENTS.md) — u. a.:

- **Issue zuerst:** erst suchen, sonst anlegen.
- **Claimen per `!claim`-Kommentar:** nur ein Agent pro Issue, fremde Claims
  respektieren, kein Doppel-Arbeiten
  ([`issue-claim.yml`](.github/workflows/issue-claim.yml)).
- **Branches** `feature/…` / `fix/…`; niemals direkt auf `main` pushen.
- **PR mit Issue-Referenz** (Hard-Fail, siehe oben).
- **Verifikation:** Remote-Push per `git ls-remote` prüfen, PR-URL im
  Abschlussbericht nennen.

## Branch- & Milestone-Konvention

- **Branches:** `feature/…` für Neues, `fix/…` für Fehlerbehebungen. Für
  Issue-Arbeit hat sich `fix/issue-<n>` als Konvention etabliert.
- **Branch nach Squash-Merge löschen:** Ein gemergter Branch ist ein Rest — den
  Remote-Head nach dem Merge entfernen (`git push origin --delete <branch>`),
  damit `git branch -a` und die Branch-Liste in der UI sauber bleiben.
  Alternativ in den Repo-Settings „Automatically delete head branches“ aktivieren.
- **Milestones** bündeln die Arbeit pro Release. Aktuell: **`RIFT BATTLE v1`**.
- **Releases** werden ausschließlich über Tags `vX.Y.Z` ausgelöst (Workflow `ci.yml`).

## Quality & Fortschritt

### Quality-Gates (CI)

| Workflow | Zweck |
|---|---|
| [`lint.yml`](.github/workflows/lint.yml) | shellcheck, ruff, actionlint |
| [`ci.yml`](.github/workflows/ci.yml) | Tests (Bausteine/E2E/Lua-static) + Build + Release |
| [`pr-quality.yml`](.github/workflows/pr-quality.yml) | Conventional-Commit-PR-Titel (hart) + Issue-Referenz (hart) |
| [`followup-issues.yml`](.github/workflows/followup-issues.yml) | Follow-up-Issues beim Schließen von Issues (Label `follow-up`) |

### Fortschritt

- **Milestones** pro Release (`RIFT BATTLE v1`, später `vX.Y.Z`) — offene vs. erledigte Issues.
- **[docs/PROGRESS.md](docs/PROGRESS.md)** — wöchentlich automatisch generiert
  (`progress.yml`) aus offenen Issues/PRs, letzten Releases und CI-Status.
- **Badges** im [README](README.md): CI-Status, Lint, letztes Release, offene Issues.

## Lint-Baseline

Damit Reviews auf Inhalt statt Format konzentrieren, läuft statisches Linting in
`lint.yml`. Entscheidungen:

- **shellcheck** über die First-Party-Skripte (`scripts/*.sh`,
  `bausteine/*/test_e2e*.sh`, `bausteine/07-relay/fake-log.sh`,
  `tools/headless-client/*.sh`) — ohne `continue-on-error`.
- **ruff** (`E`/`F`, siehe [`ruff.toml`](ruff.toml)) plus `python3 -m compileall` als
  Syntax-Fallback. Kein `--exit-zero`.
- **actionlint** über alle Workflows, mit `-shellcheck=` (die unveränderte `ci.yml`
  soll nicht durch Shell-Lints ihrer `run`-Blöcke rot werden).
- **luacheck bewusst nicht aktiv:** Die Riftbreaker-Mod-API-Globals sind offline nicht
  auflösbar → False Positives. Lua wird stattdessen über `tests/lua-static` (fengari +
  luaparse) geprüft.
- **Einzelfall-Ausnahmen** immer inline begründen: `# shellcheck disable=SCxxxx` bzw.
  `# noqa: <code>`.

## CI-Runner (planet)

Die Jobs `test` und `build` in [`ci.yml`](.github/workflows/ci.yml) laufen auf
einem Self-Hosted-Runner auf **planet** (Label `planet`) statt auf
GitHub-Hosted `ubuntu-latest` — für alle Pull Requests und Push auf `main`.
Der `release`-Job (Release-Tags `v*`) bleibt bewusst auf `ubuntu-latest`.

**Voraussetzung (Host-seitig):** Der Runner muss auf planet registriert sein,
bevor die Checks `test`/`build` grün werden können. Die Toolchain ist als Code
versioniert und wird idempotent per Skript aufgezogen (Issue #153):

1. GitHub-Actions-Runner auf planet installieren (bisher `/opt/actions-runner`,
   bislang nur für `momokli/openclaw-deploy` genutzt) und für dieses Repo
   (`momokli/riftbreaker-battle-mod`) mit dem Label `planet` registrieren —
   `runs-on: [self-hosted, planet]` erfordert genau dieses Label.
2. Toolchain **als Code** aufziehen: `bash .github/runner/setup.sh` installiert
   MinGW-w64 (`gcc-mingw-w64-x86-64`), ccache und zip und registriert ccache als
   Compiler-Wrapper. Node.js 20 wird in CI über `actions/setup-node` geliefert;
   das Skript verifiziert node/npm/python nur. Details: `.github/runner/README.md`.
3. Caches sind in `ci.yml` eingebaut (`actions/cache` für npm-Store und ccache);
   Disk-Platz und Concurrency bleiben Host-Sache — `test` und `build` laufen über
   `needs: test` nacheinander, aber mehrere PRs können parallel anstehen (Scale
   auf ≥2 Runner ist noch offen, siehe `.github/runner/README.md`).

Solange der Runner nicht registriert ist, bleiben die Checks `test`/`build` im
`pending`/`waiting`-Zustand; die übrigen Gates (`lint.yml`, `pr-quality.yml`)
laufen weiterhin auf GitHub-Hosted.

## Fragen?

Issue mit Label `question` aufmachen — oder bei einem bestehenden Issue nachfragen.
