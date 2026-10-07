# mod-build — Mod-Rollout-Init-Container (Issue #1093)

Init-Container, der das Battle-Mod-Zip (`rbbattle.zip`) aus dem **aktuellen
Checkout** baut und in das Game-Volume ausrollt. Portiert den Mod-Block der
Ansible-Rolle `riftbreaker-server` (`tasks/main.yml`) und die Retention
(`tasks/backup-retention.yml`, #312) nach POSIX sh (`rollout.sh`).

Der Coordinator verdrahtet den Service in `compose.yaml` (diese Datei wird hier
**nicht** angefasst):

```yaml
  mod-build:
    build:
      context: ./deploy/compose/mod-build
    image: rbb-mod-build:${RBB_REF:-dev}
    restart: "no"
    environment:
      RBB_REF: *rbb-ref
      RBB_GAME_DIR: /game
      RBB_BACKUP_DIR: /backups
    volumes:
      - ./:/src:ro          # Read-only-Checkout (wie rbtools-build)
      - ${RBB_HOST_ROOT:-/srv/rbbattle}/game:/game # Game-Volume (Mods)
      - ${RBB_HOST_ROOT:-/srv/rbbattle}/backups:/backups # Backups AUSSERHALB mods/
```

## Ablauf (1:1 aus der Rolle)

1. **Bauen:** `scripts/package.sh` erzeugt `dist/rbbattle.zip` (Content-Root =
   `client-mod/`, d. h. `lua/` + `<GUID>.manifest` an der Zip-Wurzel).
2. **No-Op-Check:** Marker `<game>/rbbattle.md5` (md5 des Zips) lesen; ist er
   gleich dem Zip-md5 **und** existiert `<mods>/rbbattle` mit Manifest → No-Op.
3. **Backup:** bisheriger Mod-Ordner → `$RBB_BACKUP_DIR/rbbattle-<ts>.tar.gz`
   (**außerhalb** `mods/`).
4. **#212-Fremd-Mod-Guard:** jeder andere Ordner mit `*.manifest` in `mods/`
   wird nach `$RBB_BACKUP_DIR/stray-<ts>/` weggeschoben, danach erneut geprüft;
   bleibt einer übrig → **harter Abbruch**.
5. **Rollout:** Zip nach `<mods>/rbbattle` entpacken; Marker schreiben.
6. **Retention (#312):** je nur die `N` neuesten `rbbattle-*.tar.gz` und
   `stray-*/` behalten (N = `RBB_BACKUP_KEEP`), sortiert über den
   **Namens**-Zeitstempel (`YYYYMMDDTHHMMSS`, lexikographisch == chronologisch).
   `mods/` wird dabei **nie** angefasst.

Idempotent (warm == No-Op) und fail-loud (jeder Verstoß → Exit ≠ 0).

## Env

| Variable          | Default                      | Bedeutung                                    |
| ----------------- | ---------------------------- | -------------------------------------------- |
| `RBB_REF`         | `dev`                        | Build-Ref → `RBB_BUILD_REF` für `package.sh` |
| `RBB_GAME_DIR`    | `/game`                      | Game-Volume                                  |
| `RBB_MODS_DIR`    | `$RBB_GAME_DIR/mods`         | `mods/`                                      |
| `RBB_MOD_DIR`     | `$RBB_MODS_DIR/rbbattle`     | Ziel-Mod-Ordner                              |
| `RBB_BACKUP_DIR`  | `/backups`                   | Backup-Ziel **außerhalb** `mods/`            |
| `RBB_BACKUP_KEEP` | `5`                          | je Gruppe behaltene Backups                  |
| `RBB_SRC`         | `/src`                       | Checkout (read-only)                         |
| `RBB_WORK`        | `/work`                      | schreibbare Build-Kopie des Checkouts        |
| `RBB_MARKER_FILE` | `$RBB_GAME_DIR/rbbattle.md5` | Marker-Datei (md5 des Zips)                  |

Der **Marker** liegt bewusst **außerhalb** `mods/` (wie in der Rolle, die ihn
im Deploy-Verzeichnis hält) — in `mods/` würde er sonst als Datei neben der Mod
liegen. Pfad über `RBB_MARKER_FILE` übersteuerbar.

## Bewusste Abweichungen von der Rolle (und warum)

- **Build in einer schreibbaren Kopie (`$RBB_WORK`).** `/src` ist read-only
  gemountet, `package.sh` schreibt aber nach `<repo-root>/dist`. `rollout.sh`
  kopiert den Checkout (ohne `.git`) nach `$RBB_WORK` und baut dort — die
  Semantik (`package.sh` aus dem Checkout, cwd = Repo-Root) bleibt identisch.
- **Kein Zip-Normalisieren mehr (#1101).** `scripts/package.sh` baut den Zip
  jetzt **deterministisch** (sortierte Einträge, fester Zeitstempel `1980-01-01`,
  Rechte `0644`) — bei gleichem Quellstand ist der `rbbattle.zip`-md5 stabil,
  damit der Marker-No-Op real greift. `rollout.sh` braucht dafür keine
  Nach-Normalisierung mehr; der frühere Workaround ist entfernt.
- **Guard ist strenger:** Manifest-Ordner **unterhalb** `RBB_MOD_DIR` werden nie
  als „Fremd-Mod“ verschoben, und `mods/` selbst wird nie verschoben (die Rolle
  würde `mods/` bei einem Manifest direkt darin selbst zum Ziel machen).

## Test

```bash
bash deploy/compose/mod-build/test-rollout.sh
```

Hermetischer Selbsttest (tmpdir, kein Netz, kein Docker): warm-No-Op,
Backup-außerhalb-`mods/`, #212-Guard (weggeschoben **und** harter Fail, wenn
nicht verschiebbar), Retention und der Fail-loud-Guard für ein Backup-Ziel
in/unter `mods/`. Braucht `bash`, `tar`, `find`, `python3` und `zip` **oder**
`python3`.
