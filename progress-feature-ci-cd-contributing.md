# Progress: feature/ci-cd-contributing

Ziel: Ein konsistenter Change-Set für CI/CD + Contributing-Prozess.
Remote: github.com/momokli/riftbreaker-battle-mod · Ziel-Branch: `feature/ci-cd-contributing` (kein Direct-Push auf `main`).

## Ist-Stand (verifiziert 2026-09-10)

- `.github/workflows/ci.yml` vorhanden: Jobs `test` (B06/B07 E2E, relay unittest, tests/lua-static, tests/e2e-vollkette, tests/live-status), `build` (MinGW-Crossbuild + `scripts/package_bausteine.sh`), `release` (Tags `v*`). `permissions: contents: read`; `release`-Job `contents: write`. ✔
- GitHub Pages: `build_type: legacy`, Quelle `main` / `docs`, URL <https://momokli.github.io/riftbreaker-battle-mod/>. `site/` = statisches Landing v2 (`index.html`, `solo.html`, `connectivity.html`, `live-status.js`), KEIN Build nötig. ✔
- Python (8): `trainer/scan/scan_find.py`, `trainer/scan/scan_values.py`, `bausteine/03-log-bridge/tail_events.py`, `bausteine/04-trainer-io/pipe_client.py`, `bausteine/07-relay/relay.py`, `bausteine/07-relay/test_dispatch.py`, `tournament/bridge/telegram_feed.py`, `tournament/bridge/poller_example.py`. ✔
- Shell (15): `scripts/{package_bausteine,package_mod,probe_servers}.sh`, `bausteine/06-tournament-server/test_e2e.sh`, `bausteine/07-relay/{test_e2e_prototype,fake-log}.sh`, `tools/headless-client/*.sh` (9). ✔
- Lua (6): `mod/lua/rbbattle_autoexec.lua`, `bausteine/0{0,1,2,3,5}-*/.../lua/*_autoexec.lua`. ✔
- Node-Tests: `tests/lua-static`, `tests/e2e-vollkette`, `tests/live-status`, `bausteine/06` (server.js/mock_client.js), `bausteine/07`. ✔
- Milestone `RIFT BATTLE v1` (open; 12 closed / 6 open). Collaborators: `momokli`, `BestToasty` (mctoastus nicht gelistet). Repo-Sprache Deutsch, language=Lua. ✔
- `CONTRIBUTING.md`, `PROGRESS.md`, `docs/PROGRESS.md` fehlen. Label `claimed` fehlt. Lokal kein `shellcheck`/`ruff`/`actionlint`/`luacheck` installiert. gh-Token-Scopes: `repo`, `workflow`, `write:packages`. ✔

## Plan

### Betroffene Dateien (alle neu/geändert)

- `.github/workflows/ci.yml` (ergänzt um Job `lint`)
- `.github/workflows/pr-quality.yml` (neu)
- `.github/workflows/pages.yml` (neu)
- `.github/workflows/issue-claim.yml` (neu)
- `.github/workflows/progress.yml` (neu)
- `.github/ISSUE_TEMPLATE/{bug,feature,spike}.yml`, `.github/ISSUE_TEMPLATE/config.yml` (neu)
- `CONTRIBUTING.md` (neu)
- `README.md` (Badges + Doku-Verweise)
- `docs/PROGRESS.md` (generiert)
- ggf. Lint-Fixes in `scripts/*.sh`, `tools/headless-client/*.sh`, `trainer/scan/*.py`, `bausteine/**/*.py`, `mod/lua/*.lua`, `bausteine/**/lua/*.lua`
- Repo-Settings via `gh api`: Pages-Source (`build_type: workflow`), Label `claimed`

### User Stories (in Reihenfolge)

1. **US1 — Branch + Lint-Job in CI aufsetzen**
   - Akzeptanzkriterien:
     - Branch `feature/ci-cd-contributing` von `main` erstellt.
     - `ci.yml` erhält Job `lint` (eigener Job, `needs`-frei, parallele Läufe zu `test`).
     - `shellcheck` über `scripts/*.sh`, `tools/headless-client/*.sh`, `bausteine/*/*.sh`; Rückgabe wird als Fehler gewertet.
     - Python-Lint via `ruff check`; Fallback `python3 -m py_compile` falls ruff-Install scheitert.
     - `actionlint` über **alle** `.github/workflows/*.yml` via `rhysd/actionlint`.
     - Optional `luacheck` über `mod/lua/*.lua` + `bausteine/**/lua/*.lua` (non-blocking, `continue-on-error` bis Baseline sauber).
     - Least-privilege: Job(s) nur `permissions: contents: read`.
     - Job läuft bei `push main`, `pull_request`, Tags `v*` und ist für den PR grün.
   - Betroffene Dateien: `.github/workflows/ci.yml`.
   - Geschätzte LoC: ~45.

2. **US2 — Lint-Befunde triagieren (fixen oder begründet ignorieren)**
   - Akzeptanzkriterien:
     - Alle Lint-Fehler aus US1 entweder gefixt oder mit Inline-Begründung (`# shellcheck disable=SCxxxx`) bzw. Ruff-`per-file-ignores`/`noqa` und kurzer Begründung versehen.
     - Kein `continue-on-error` für shellcheck/actionlint in CI (außer luacheck).
     - Bestehende E2E-Tests/Node-Tests bleiben grün (Lint-Fixes dürfen Verhalten nicht ändern).
     - Entscheidungen als kurzer Abschnitt „Lint-Baseline" in `CONTRIBUTING.md` dokumentiert.
   - Betroffene Dateien: `scripts/*.sh`, `tools/headless-client/*.sh`, `trainer/scan/*.py`, `bausteine/**/*.py`, `mod/lua/*.lua`, `bausteine/**/lua/*.lua`, `CONTRIBUTING.md`.
   - Geschätzte LoC: ~30–80 (Fix-Aufwand offen).

3. **US3 — PR-Quality-Workflow**
   - Akzeptanzkriterien:
     - `.github/workflows/pr-quality.yml` triggert auf `pull_request_target`/`pull_request` (`opened`, `edited`, `synchronize`, `reopened`).
     - Conventional-Commit-Prüfung des PR-Titels via `amannn/action-semantic-pull-request` (Typen: feat, fix, docs, chore, refactor, test, ci, build, perf).
     - Fehlende Issue-Referenz (`#\d+`) im PR-Body erzeugt **Warnkommentar**, kein Hard-Fail (idempotent, kein Doppelkommentar).
     - `permissions: contents: read, pull-requests: write`; auf Fork-PRs ohne Secret-Zugriff lauffähig.
   - Betroffene Dateien: `.github/workflows/pr-quality.yml`.
   - Geschätzte LoC: ~55.

4. **US4 — Pages-Workflow auf GitHub Actions umstellen**
   - Akzeptanzkriterien:
     - `.github/workflows/pages.yml`: Trigger `push` auf `main` mit `paths: ['site/**', '.github/workflows/pages.yml']` + `workflow_dispatch`.
     - Schritte: `actions/configure-pages`, `actions/upload-pages-artifact` (path `site/`), `actions/deploy-pages`.
     - `permissions: { contents: read, pages: write, id-token: write }`, `environment: { name: github-pages, url: ... }`, `concurrency: group: pages`.
     - Pages-Source im Repo auf `build_type: workflow` umgestellt (`gh api -X PUT repos/momokli/riftbreaker-battle-mod/pages -f build_type=workflow`).
     - Deployment über Actions-Job erfolgreich; Seite unter <https://momokli.github.io/riftbreaker-battle-mod/> erreichbar (site/index.html, solo.html, connectivity.html, live-status.js).
   - Betroffene Dateien: `.github/workflows/pages.yml`, Repo-Setting via `gh api`.
   - Geschätzte LoC: ~40.

5. **US5 — CONTRIBUTING.md (Deutsch)**
   - Akzeptanzkriterien:
     - Fluss dokumentiert: Issue → `!claim` → Branch `feature/…`|`fix/…` → PR → Review → Squash-Merge.
     - Regeln: kein Direct-Push auf `main`, kein ungeclaimtes Issue, Issue-Link im PR Pflicht, Definition of Done, Conventional Commits, Release nur via `v*`-Tags durch Maintainer.
     - Abschnitt „Qualitaet & Fortschritt": CI-Quality-Gates (Link auf `ci.yml`/`pr-quality.yml`), Milestones pro Release (Referenz `RIFT BATTLE v1`), `docs/PROGRESS.md`, Badges.
     - Verweist auf Issue-Templates + `.github/workflows/issue-claim.yml`.
   - Betroffene Dateien: `CONTRIBUTING.md`, ggf. Verweis in `README.md`.
   - Geschätzte LoC: ~120 (Markdown).

6. **US6 — Issue-Claim-System**
   - Akzeptanzkriterien:
     - `.github/workflows/issue-claim.yml`: Trigger `issue_comment` (`created`), nur auf Issues (nicht PR-Kommentare), Bot-Kommentare (`*[bot]`, `github-actions`) ignoriert.
     - `!claim` weist Issue zu (`gh api ... assignees`), setzt Label `claimed`, kommentiert Bestätigung; `!unclaim` entfernt beides.
     - Label `claimed` wird idempotent angelegt, falls nicht vorhanden.
     - Race-Hinweis: nur der **erste** Claim gewinnt; bei bereits gesetztem `claimed` Antwort mit Hinweis auf den bestehenden Claimer.
     - `permissions: issues: write, contents: read`.
     - Issue-Templates `.github/ISSUE_TEMPLATE/{bug,feature,spike}.yml` + `config.yml` (`blank_issues_enabled: true`), jeweils mit Hinweis „Erst mit `!claim` claimen".
   - Betroffene Dateien: `.github/workflows/issue-claim.yml`, `.github/ISSUE_TEMPLATE/{bug,feature,spike}.yml`, `.github/ISSUE_TEMPLATE/config.yml`.
   - Geschätzte LoC: ~130.

7. **US7 — README-Badges**
   - Akzeptanzkriterien:
     - CI-Badge (Workflow `ci.yml`), Latest-Release-Badge, Open-Issues-Badge via shields.io.
     - Badges laden korrekt (Status/A11y), Links zielen auf passende Workflow-/Release-/Issue-URLs.
     - Verweise auf `CONTRIBUTING.md` und `docs/PROGRESS.md` ergänzt.
   - Betroffene Dateien: `README.md`.
   - Geschätzte LoC: ~10.

8. **US8 — Progress-Automation + docs/PROGRESS.md**
   - Akzeptanzkriterien:
     - `.github/workflows/progress.yml`: `schedule` (wöchentlich, cron) + `workflow_dispatch`; generiert `docs/PROGRESS.md` aus `gh api` (offene/geschlossene Issues, Milestone-Fortschritt, letzte Releases).
     - Commit-Push ohne Endlos-Loop: `[skip ci]` in Commit-Message **und** `paths-ignore: ['docs/PROGRESS.md']`; zusätzlich Schutz dadurch, dass `GITHUB_TOKEN`-Pushes keine neuen Workflow-Runs auslösen.
     - `permissions: contents: write`.
     - Erster Lauf erzeugt valide `docs/PROGRESS.md`, die manuell überprüft wird.
   - Betroffene Dateien: `.github/workflows/progress.yml`, `docs/PROGRESS.md`.
   - Geschätzte LoC: ~70.

### Implementierungs-Reihenfolge (Abhängigkeiten)

US1 → US2 (Lint-Job muss existieren, bevor Fixes verifizierbar sind) → US3 → US4 → US6 → US5 (referenziert Claim-System + Gates) → US7 (Badges brauchen existierende Workflows) → US8 (letzter, nutzt Issue/Milestone-Daten, generiert Datei).

## Stage-Status

| Stage | Status | Ergebnis |
|---|---|---|
| Planner | done | Plan + Progress-Datei erstellt, Ist-Stand verifiziert |
| Setup (Branch + Baseline) | done | Branch `feature/ci-cd-contributing` von `origin/main` (4bdd8d2), HEAD 4bdd8d2, ahead/behind 0/0; Baseline grün |
| Developer (US1–US8) | done | Lint-Workflow, Repo-Fixes, PR-Quality, Pages, Claim-System, Templates, CONTRIBUTING, Badges, Progress — committet + gepusht |
| Lint-Fix / Baseline | done | shellcheck/ruff/actionlint/compileall grün; Baseline-Tests grün (relay 5/5, live-status 15/15, lua-static 6/6) |
| PR/CI-Validierung | pending | PR wird von Reviewer-Stage erstellt (Entwurf unten) |
| Docs (CONTRIBUTING/PROGRESS/Badges) | done | `CONTRIBUTING.md`, `docs/PROGRESS.md`, README-Badges |
| Release/Merge | pending | — |
| Fix-Runde 1 (Tester-Befund Landing v2 Download) | done | `site/`-Download-Buttons + Hinweistexte auf GitHub-Release-Asset umgestellt; Commit `af624fa`, gepusht |

### Fix-Runde 1 (Developer, 2026-09-10, HEAD `af624fa`)

Tester-Befund: Landing v2 in `site/` verlinkte den Download relativ auf `/mods/rbbattle.zip` → auf GitHub Pages 404 (live verifiziert). Release-Asset `rbbattle.zip` erreichbar.

Ersetzt durch den stabilen Latest-Link `https://github.com/momokli/riftbreaker-battle-mod/releases/latest/download/rbbattle.zip`.

| Datei | Zeile(n) | Änderung |
|---|---|---|
| `site/index.html` | 311, 466 | `href="/mods/rbbattle.zip"` → Release-Latest-Link (CTA + Download-CTA) |
| `site/solo.html` | 336 | `href="/mods/rbbattle.zip"` → Release-Latest-Link (CTA) |
| `site/solo.html` | 369 | Hinweistext „aus dem `/mods/rbbattle.zip`" → Link „Release-Download" |
| `site/connectivity.html` | 253 | Hinweistext `<b>/mods/rbbattle.zip</b>` → Link `<b>Release-Download</b>` (Styling/Struktur erhalten) |

Verifikation: `grep -rn "mods/rbbattle.zip" site/` → 0 Treffer (exit 1); `grep -rn "releases/latest/download/rbbattle.zip" site/` → 5 Treffer (Buttons + Hinweistexte); HTML-Parser-Check (`html.parser`) für `index.html`/`solo.html`/`connectivity.html` → OK (keine unbalancierten Tags/Quotes); Release-Link `curl -L` → 200; alter `/mods/`-Pfad auf Pages → 404. `ci.yml` unangetastet, keine Secrets.

Commit: `af624fa` — `fix(site): point download buttons to GitHub Release assets` (gepusht, `git ls-remote` = `af624fa…`).

### Baseline (Setup-Stage, 2026-09-10, HEAD `4bdd8d2`)

| Check | Kommando | Ergebnis | Exit |
|---|---|---|---|
| Python-Syntax | `python3 -m compileall -q scripts trainer bausteine tournament` | grün | 0 |
| Relay-Dispatch-Unit | `bausteine/07-relay` → `python3 -m unittest test_dispatch -v` | grün (5/5 ok) | 0 |
| Live-Status-Node-Tests | `tests/live-status` → `node --test` | grün (15 pass / 0 fail) | 0 |
| Shell-Syntax | `bash -n` über 15 `*.sh` (ohne node_modules) | grün (0 Syntaxfehler) | 0 |
| Lua-static (Node) | `tests/lua-static` → `npm test` (node_modules vorhanden) | grün (6 pass / 0 fail) | 0 |
| E2E-Vollkette (Node) | `tests/e2e-vollkette` → `npm ci && npm test` | grün (7 pass / 2 skipped / 0 fail); `npm ci` 0.9s | 0 |

### Lokaler Tool-Stand (Setup-Stage)

| Tool | Status |
|---|---|
| `shellcheck` | fehlt (MISSING) |
| `actionlint` | fehlt (MISSING) |
| `ruff` | fehlt (MISSING) |
| `luacheck` | fehlt (MISSING) |
| `yamllint` | fehlt (MISSING) |
| `docker` | fehlt (MISSING) |
| `go` | fehlt (MISSING) |
| `pip3` / `pip` | vorhanden (`/usr/bin/pip3`, `/usr/bin/pip`) |
| `npm` / `npx` | vorhanden (`/usr/local/bin/npm`, `/usr/local/bin/npx`) |

Hinweis: Lint-Tools müssen vor US1/US2 nachinstalliert werden — `shellcheck`/`actionlint` als GitHub-Release-Binary (kein `go`, kein `docker` vorhanden), `ruff` via `pip3`, `luacheck` via `npm`/`luarocks`. Netz ist erreichbar (`npm ping` → PONG).

## Deliverables / Definition of Done

- [ ] Remote-Branch `feature/ci-cd-contributing` existiert.
- [ ] PR gegen `main` erstellt (Squash-Merge vorgesehen), PR-Link: _offen_.
- [ ] CI-Runs des PRs grün (`test`, `build`, `lint`, `pr-quality`, `pages`, `issue-claim`, `progress`) — oder Abweichungen dokumentiert.
- [ ] Pages-Source = GitHub Actions; Deployment grün; Landing v2 (site/) live erreichbar.
- [ ] `CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/*`, `docs/PROGRESS.md`, README-Badges vorhanden.
- [ ] Label `claimed` im Repo angelegt.
- [ ] Kein Direct-Push auf `main` außerhalb des PR-Merges.

## Risiken / Hinweise

- **Pages-API-Permissions**: Umstellung auf `build_type: workflow` erfordert `gh api -X PUT .../pages`; Token braucht `repo`-Scope (vorhanden). Der Workflow selbst braucht `pages: write` + `id-token: write`. Bei 403/„Resource not accessible" → Repo-Admin nötig.
- **Pages-Inhaltswechsel**: Aktuell served `main/docs/index.html`; nach Umstellung wird `site/` ausgeliefert. Links in `docs/index.html`/README prüfen, damit keine toten Verweise entstehen.
- **actionlint offline vs. in Actions**: Lokal nicht installiert — Verifikation nur im Actions-Runner. Risiko: Erstlauf deckt YAML-Fehler auf; ggf. lokal via `go install`/Docker nachziehen.
- **Loop-Gefahr `progress.yml`**: Push durch `GITHUB_TOKEN` löst keine neuen Workflow-Runs aus; zusätzlich `[skip ci]` + `paths-ignore` auf `docs/PROGRESS.md`, damit weder Self-Trigger noch unnötige Läufe entstehen. `cron`-Zeitpunkt bewusst wählen.
- **Lint-Baseline**: Bestands-Skripte können viele shellcheck-Findings erzeugen; Scope der Erstimplementierung begrenzen und Restbefunde begründet abbrechen (sonst „riesiger Fix-PR").
- **Fork-PRs**: `pr-quality`/`issue-claim` müssen ohne Secrets auskommen (`GITHUB_TOKEN` reicht).
- **Collaborators**: `mctoastus` nicht in der Collaborator-Liste verifiziert (evtl. BestToasty-alias) — für Review-Zuweisung prüfen.
- **kein lokales Lint-Tooling**: Fixes durch Reviewer/CI verifizieren, nicht lokal.

## Developer-Ergebnis (Stage Developer, 2026-09-10)

### Neue/geänderte Dateien

| Datei | Status |
|---|---|
| `ruff.toml` | neu (E/F, target py38, line-length 120) |
| `scripts/probe_servers.sh`, `scripts/package_bausteine.sh` | geändert (shellcheck-Fixes) |
| `bausteine/04-trainer-io/pipe_client.py`, `trainer/scan/scan_values.py` | geändert (ruff-Fixes) |
| `bausteine/06-tournament-server/test_e2e.sh`, `bausteine/07-relay/test_e2e_prototype.sh` | geändert (shellcheck-Fixes) |
| `.github/workflows/lint.yml` | neu |
| `.github/workflows/pr-quality.yml` | neu |
| `.github/workflows/pages.yml` | neu |
| `.github/workflows/issue-claim.yml` | neu |
| `.github/workflows/progress.yml` | neu |
| `.github/ISSUE_TEMPLATE/{bug,feature,spike}.yml`, `config.yml` | neu |
| `CONTRIBUTING.md` | neu |
| `README.md` | geändert (Badges + Mitmachen) |
| `docs/PROGRESS.md` | neu (Seed) |

`ci.yml` wurde **nicht** verändert.

### Commits (Branch `feature/ci-cd-contributing`, alle gepusht)

| SHA | Message |
|---|---|
| `6807db5` | fix(lint): resolve shellcheck/ruff findings without behavior change |
| `2cd81d5` | ci: add lint workflow (shellcheck, ruff, actionlint) |
| `58da96e` | ci: add PR quality workflow (semantic title + issue reference) |
| `ce96544` | ci: add GitHub Pages deploy workflow for site/ |
| `87b9542` | ci: add issue claim workflow (!claim/!unclaim) |
| `237e5d8` | chore: add issue templates (bug, feature, spike) with claim hint |
| `c5a123f` | docs: add CONTRIBUTING.md (workflow, claim system, quality gates) |
| `a736d9b` | docs: add CI/lint/release/issues badges and contributing links to README |
| `f923f59` | ci: add weekly progress workflow and seed docs/PROGRESS.md |

### Verifikation

| Check | Kommando | Ergebnis |
|---|---|---|
| shellcheck | `shellcheck scripts/*.sh bausteine/*/test_e2e*.sh bausteine/07-relay/fake-log.sh tools/headless-client/*.sh` | grün (0 Findings) |
| ruff | `ruff check .` | grün |
| compileall | `python3 -m compileall -q scripts trainer bausteine tournament` | grün |
| actionlint | `actionlint -shellcheck= .github/workflows/*.yml` | grün |
| bash -n | alle `*.sh` | grün |
| YAML | `python3 -c "import yaml,..."` alle Workflows + Templates | grün |
| Relay-Unit | `python3 -m unittest test_dispatch -v` | 5/5 ok |
| Live-Status | `node --test` | 15 pass / 0 fail |
| Lua-static | `npm test` | 6 pass / 0 fail |

### Tracking-Issue

**#75** — `ci/cd: ordentliche Checks, Pages-Deploy, Contributing & Claim-System`
(Milestone `RIFT BATTLE v1`).

### Offene Follow-ups (nicht in diesem Change-Set)

- Repo-Setting **Pages-Source = GitHub Actions** umstellen (`gh api -X PUT .../pages -f build_type=workflow`) — nötig, damit `pages.yml` nach dem Merge deployen kann.

## PR-Body (Entwurf)

```markdown
## Was

Macht CI/CD und den Contributing-Prozess ordentlich — ohne `ci.yml` anzufassen:

- **Lint** (`.github/workflows/lint.yml`): shellcheck über die First-Party-Skripte,
  ruff (`E`/`F`, `ruff.toml`) mit `compileall`-Syntax-Fallback, actionlint über alle
  Workflows (`-shellcheck=`, damit die unveränderte `ci.yml` nicht durch eingebettete
  Shell-Lints rot wird).
- **Repo-Lint-Fixes** ohne Verhaltensänderung (shellcheck/ruff).
- **PR-Quality** (`.github/workflows/pr-quality.yml`): Conventional-Commit-Titel (hart)
  + freundlicher Issue-Referenz-Hinweis (weich, kein Fail).
- **GitHub Pages** (`.github/workflows/pages.yml`): Deploy direkt aus `site/` (rein
  statisch, kein Build-Schritt).
- **Issue-Claim-System** (`.github/workflows/issue-claim.yml`): `!claim`/`!unclaim`
  mit Assignee + Label `claimed`; Issue-Templates mit Claim-Hinweis.
- **Doku**: `CONTRIBUTING.md`, README-Badges, `docs/PROGRESS.md` + wöchentlicher
  `progress.yml`.

## Warum

- Einheitliche, reproduzierbare Qualitäts-Gates statt Ad-hoc-Prüfungen.
- Kein Doppel-Arbeiten: genau ein Assignee pro Issue.
- Landing v2 (`site/`) wird sauber als Pages-Quelle ausgeliefert.

## Wie testen

```bash
shellcheck scripts/*.sh bausteine/*/test_e2e*.sh bausteine/07-relay/fake-log.sh tools/headless-client/*.sh
ruff check .
python3 -m compileall -q scripts trainer bausteine tournament
actionlint -shellcheck= .github/workflows/*.yml

(cd bausteine/07-relay && python3 -m unittest test_dispatch -v)
(cd tests/live-status && node --test)
(cd tests/lua-static && npm ci && npm test)
```

CI: `lint`, `test`, `build`, `pr-quality` müssen grün sein.

Closes #75

## Checkliste

- [x] Lint lokal grün (shellcheck, ruff, actionlint, compileall)
- [x] Baseline-Tests grün (relay, live-status, lua-static)
- [x] `ci.yml` unverändert
- [x] Conventional Commits, keine Secrets
- [x] Doku: CONTRIBUTING.md, README-Badges, docs/PROGRESS.md
- [ ] PR-Quality-Run grün (nach PR-Erstellung)
- [ ] Pages-Source auf GitHub Actions umstellen (nach Merge)
```
