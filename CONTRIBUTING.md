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
5. **Pull Request** — gegen `main`, **immer mit Issue-Link** (`Closes #N` / `Refs #N`).
6. **Review** — mindestens eine Freigabe; CI muss grün sein.
7. **Squash-Merge** durch einen Maintainer.

## Regeln

- **Kein Direct-Push auf `main`.** Alles läuft über einen PR.
- **Nicht an ungeclaimten Issues arbeiten** — erst `!claim`.
- **PR immer mit Issue verlinkt** (`Closes #N` schließt automatisch, `Refs #N` referenziert nur).
- **Definition of Done**
  - CI grün: **lint + test + build**.
  - Neuer Code hat Tests.
  - Keine Debug-Reste, keine Credentials/Secrets im Diff.
- **Commit-Konvention:** [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `chore:`, `ci:`, `refactor:`, `test:`, `build:`, `perf:`,
  `style:`, `revert:`), Message auf Englisch.
- **Releases** macht nur ein Maintainer über einen `v*-Tag` (z. B. `v1.2.0`).

## Branch- & Milestone-Konvention

- **Branches:** `feature/…` für Neues, `fix/…` für Fehlerbehebungen.
- **Milestones** bündeln die Arbeit pro Release. Aktuell: **`RIFT BATTLE v1`**.
- **Releases** werden ausschließlich über Tags `vX.Y.Z` ausgelöst (Workflow `ci.yml`).

## Quality & Fortschritt

### Quality-Gates (CI)

| Workflow | Zweck |
|---|---|
| [`lint.yml`](.github/workflows/lint.yml) | shellcheck, ruff, actionlint |
| [`ci.yml`](.github/workflows/ci.yml) | Tests (Bausteine/E2E/Lua-static) + Build + Release |
| [`pr-quality.yml`](.github/workflows/pr-quality.yml) | Conventional-Commit-PR-Titel (hart) + Issue-Referenz-Hinweis (weich) |

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

## Fragen?

Issue mit Label `question` aufmachen — oder bei einem bestehenden Issue nachfragen.
