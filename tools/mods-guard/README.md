# tools/mods-guard — kein Fremd-Mod in `<server>/mods/` (Issue #212)

Der Riftbreaker-Dedicated-Server lädt **jeden** Unterordner von
`<server>/mods/` mit einer `*.manifest` als eigene External-Content-Mod —
unabhängig vom Ordnernamen. Liegt dort ein Backup-Ordner mit derselben
Content-ID (z. B. `rbbattle.bak-<ts>/`), wird er **zusätzlich** geladen: die
Versionskonstante wird überschrieben und beide Kopien registrieren ihre Handler
doppelt (`handler_errors` / `event_unreadable`).

Dieses Skript prüft einen `mods/`-Ordner darauf, dass **außer dem Ziel-Mod**
kein weiterer Ordner mit `*.manifest` existiert — der gleiche Check, den die
Ansible-Rolle `deploy/roles/riftbreaker-server` als Deploy-Guard fährt.

## Aufruf

```bash
# Prüft <server>/mods/ (Exit 1 = Fremd-Ordner gefunden)
python3 tools/mods-guard/check_mods_dir.py /srv/rbgame/mods

# Anderer Ziel-Mod-Name / nur Fehler ausgeben
python3 tools/mods-guard/check_mods_dir.py /srv/rbgame/mods --expected rbbattle --quiet
```

Als Pre-Gate vor dem Deploy:

```bash
python3 tools/mods-guard/check_mods_dir.py /srv/rbgame/mods \
  && ansible-playbook -i deploy/inventory deploy/site.yml --ask-vault-pass
```

Exit-Codes: `0` = ok, `1` = Verstoß (Fremd-Ordner mit `*.manifest`),
`2` = Aufruf-/IO-Fehler.

**Regel:** Mod-Backups liegen **nie** innerhalb von `mods/` — Zielpfad
außerhalb (z. B. `<server>-backups/`) oder als `.tar.gz` außerhalb von `mods/`.
Details: [`docs/DEPLOYMENT.md`](../../docs/DEPLOYMENT.md) → „Mod-Backups &
mods/-Guard".

## Tests

```bash
cd tools/mods-guard
python3 -m unittest test_check_mods_dir -v
```

Deterministisch und offline: genau ein manifest-tragender Ordner (ok) vs. zwei
(Fehler), Ordner ohne Manifest (ignoriert), verschachteltes Manifest,
fehlender Ziel-Mod, eigenes `--expected`, Exit-Codes. Läuft in CI (`ci.yml`,
Job `test`); `lint.yml` (`compileall` + `ruff`) deckt das Verzeichnis mit ab.
