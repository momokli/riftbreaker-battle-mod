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
