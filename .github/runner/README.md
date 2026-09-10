# CI-Runner (planet) — Setup-as-Code

Dieser Ordner macht den self-hosted GitHub-Actions-Runner auf **planet**
(Label `planet`) reproduzierbar: die Build-/Test-Dependencies für `test` und
`build` aus [`.github/workflows/ci.yml`](../workflows/ci.yml) werden nicht mehr
per Job nachinstalliert, sondern einmalig per Skript auf den Host gezogen.

## Warum (Issue #153)

Vorher installierte der `build`-Job bei **jedem** Run
`gcc-mingw-w64-x86-64` per `sudo apt-get install` → Overhead plus ein
sudoers-NOPASSWD-Hack (`/etc/sudoers.d/runner-apt`). Dazu lief jeder Build
ohne Compiler-/Dependency-Cache kalt, und Host-Deps (Node, Python, npm,
MinGW) waren nirgends als Code versioniert.

## Aufbau

| Datei | Zweck |
| --- | --- |
| [`setup.sh`](setup.sh) | Idempotentes Setup: MinGW-w64, ccache, zip installieren; ccache als Compiler-Wrapper registrieren; Node/npm/Python verifizieren. |
| [`README.md`](README.md) | Diese Doku (Reproduktion, Caches, offene Punkte). |

## Setup auf frischem Host (< 10 min)

```bash
# 1. Runner registrieren (siehe CONTRIBUTING.md, Abschnitt "CI-Runner (planet)").
# 2. Toolchain als Code aufziehen:
bash .github/runner/setup.sh

# 3. Verifikation: MinGW-Cross-Compiler + ccache-Wrapper vorhanden
command -v x86_64-w64-mingw32-gcc
ls -l /opt/ccache-rbbattle/bin
```

`setup.sh` ist idempotent und kann beliebig oft laufen. Es entfernt bewusst
**keine** sudoers-Regeln und macht keine Mount-/Runner-Registrierungs-Änderungen.

## Caches (warme Builds)

- **npm-Store** (`actions/cache`, Pfad `~/.npm`): beide npm-Projekte
  (`tests/lua-static`, `tests/e2e-vollkette`) teilen sich `fengari` + `luaparse`;
  `npm ci` zieht die Pakete aus dem Store statt aus dem Netz. Key basiert auf den
  `package-lock.json`-Hashes.
- **ccache** (`actions/cache`, Pfad `~/.cache/ccache`): der MinGW-C++-Build
  (rbbridge.dll/injector.exe/rbbridge_standalone.exe) wird über den
  ccache-Wrapper (`/opt/ccache-rbbattle/bin`) kompiliert; Key basiert auf den
  C-Quellen unter `bausteine/04-trainer-io/**/*.c`.

## Offen (bewusst nicht in diesem PR)

1. **Parallelität (Scale auf ≥2 Runner):** Aktuell 1 Runner = 1 Job gleichzeitig.
   Strategie: zweite Runner-Instanz mit demselben Label `planet` registrieren
   (systemd-Unit `actions-runner-rbbattle.service` duplizieren), oder ein
   zweites Label einführen und `runs-on` auf eine Matrix umstellen.
2. **Health/Monitoring:** Runner-Heartbeat (z. B. `systemctl`-Status +
   `uptime`/Job-Queue) an das bestehende Monitoring hängen; Alarm bei offline
   Runner oder stuck Jobs.
3. **tmpfs für `_work`:** Arbeitsverzeichnis des Runners auf tmpfs/fast Disk
   legen, um Checkout/Cache-IO zu beschleunigen.
4. **ccache-Hit-Rate messen:** Vorher/nachher-Zahlen (Buildzeit P50,
   `ccache -s` Hit-Rate) stehen noch aus und sollen den PR ergänzen.
5. **sudoers-Hack entfernen:** `ci.yml` braucht kein sudo mehr; die Löschung von
   `/etc/sudoers.d/runner-apt` erfolgt manuell, sobald die alte Setup-Kette
   verifiziert obsolet ist.
