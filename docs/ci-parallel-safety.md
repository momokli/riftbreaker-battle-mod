# CI — Parallel-Sicherheit der planet-Jobs (3-Runner-Readiness)

> Welche Jobs mit `runs-on: [self-hosted, planet]` dürfen gleichzeitig laufen,
> ohne sich über feste Ports, Container-/Netz-Namen, Host-Pfade oder
> nicht-atomares Setup zu kollidieren? Audit, verbleibende Rest-Lücke und Fix
> (Issue #318). Skalierung/Registrierung der Runner selbst: Issue #304.
> Host-Sizing: [`SERVER_SIZING.md`](SERVER_SIZING.md).

## Runner-Topologie (Ist)

Stand planet, 2026-09-13:

| systemd-Dienst | Runner-Name | User | `_work` |
|---|---|---|---|
| `actions-runner-rbbattle.service` | `planet-rbbattle` | `runner` | `/opt/actions-runner-rbbattle/_work` |
| `planet-rbbattle-2.service` | `planet-rbbattle-2` | `runner` | `/opt/actions-runner-rbbattle-2/_work` |

Beide Instanzen laufen als **derselbe User `runner`** mit je eigenem `_work`
(„Variante A" aus Issue #318): Checkouts und Build-Verzeichnisse sind pro
Instanz getrennt, **`$HOME` (`/home/runner`) ist dagegen geteilt**. Genau daraus
entstand die Rest-Lücke unten. Jede Instanz ist per systemd begrenzt
(`CPUQuota=1000%`, `MemoryMax=8G`, `MemoryHigh=7G`, `Nice=10`).

### Variante B (Alternative, Operator-Entscheidung)

Drei getrennte User (je eigenes `$HOME` und eigene Caches) vermeiden geteilte
Setup-Pfade ganz, kosten aber kalte npm-/ccache-/rust-Caches je User.
**Empfehlung: Variante A beibehalten** — das einzige geteilte Setup-Artefakt
(Ansible-Venv + Rust-Toolchain) ist seit diesem Change atomar provisioniert.
Anlage weiterer Runner-User ist Host-Provisionierung (nicht Repo-Code), siehe
Issue #304.

## Audit: planet-Jobs

| Job (Workflow) | Geteilte Ressource | Parallel-sicher? | Beleg |
|---|---|---|---|
| `test` (`ci.yml`) | Ports ephemer bzw. `freePort()`; Temps via `mktemp`/`TemporaryDirectory`; npm-Store + ccache über `actions/cache` (concurrency-safe) | ✅ | Random-Ports in den E2E-Skripten, `mktemp -d` |
| `build` (`ci.yml`) | `dist/` im eigenen `_work`; ccache-Wrapper in `/opt` (concurrency-safe) | ✅ | je Instanz eigenes `_work` |
| `boot-test` (`boot-test.yml`) | Container/Netz/Volumes/Unit/Host-Pfade/Ports je Lauf über `github.run_id` | ✅ nach Fix | #307/#317 + #318 (Session-Sidecar) |
| `deploy-check` (`deploy-check.yml`, planet) | read-only `ansible --check`; Ansible-Venv | ✅ nach Fix | vorher nicht-atomares Venv-Setup |
| `deploy-dev` (`deploy.yml`) | serialisiert durch `concurrency: cd-dev`; kein Shared-Setup | ✅ | eine Deploy-Spur |
| `deploy-check-local` (`deploy-check.yml`) | GitHub-hosted, frische VM je Job | ✅ | kein geteiltes `$HOME` |

Alle übrigen Jobs (`tournament-test`, `lint`, `pr-quality`, …) laufen auf
`ubuntu-latest` und sind nicht betroffen.

## Rest-Lücken + Fix

### A) Nicht-atomares Toolchain-Setup (behoben)

Vorher erzeugten `boot-test` und `deploy-check` (planet) ihr Ansible-Venv selbst:

```bash
if [ ! -x "$VENV/bin/ansible-playbook" ]; then
  python3 -m venv "$VENV"; "$VENV/bin/pip" install ... "ansible-core==2.19.*" ...
fi
```

Prüfen und Erstellen sind nicht atomar. Zwei gleichzeitige Jobs (gleicher User,
geteiltes `$HOME`) greifen auf dasselbe `$VENV` zu → halbfertiges/korruptes Venv.
Analog installierte `boot-test` die Rust-Toolchain per `curl | sh` in
`$HOME/.cargo`.

**Nachher:**

- `.github/runner/setup.sh` provisioniert Ansible-Venv (`ansible-core` +
  `yamllint`) und Rust-Toolchain **einmalig**, idempotent, unter `flock` und
  atomar (temp-Venv → `mv`), im `$HOME` des Runner-Users.
- `boot-test` und `deploy-check` (planet) **verifizieren** nur noch und brechen
  mit klarer Meldung ab, wenn das Host-Setup fehlt — kein Schreibzugriff mehr
  im Job.
- `deploy-check-local` (GitHub-hosted) erzeugt sein Venv weiter lokal
  (ephemärer VM, kein geteiltes `$HOME`).

### B) Fester Container-Name des Session-Sidecars (behoben)

Beim Nachweis (zwei parallel dispatchte `boot-test`-Läufe) kollidierte der
Session-Recorder-Sidecar aus Issue #280:

```
Error response from daemon: Conflict. The container name
"/riftbreaker-sessions-test" is already in use by container "024982fdf1c6".
```

`deploy/test-vars.yml` setzte `riftbreaker_sessions_container:
riftbreaker-sessions-test` und `riftbreaker_sessions_dir:
/srv/rbmods-sessions-test` **ohne** `rbbattle_run_suffix` — alle übrigen
Test-Ressourcen sind seit #307/#317 run-scoped, der (später ergänzte) Sidecar
wurde übersehen. Ein Compose-`container_name` ist global, daher kollidieren zwei
Läufe trotz getrennter Compose-Projekte.

**Nachher:** Sidecar-Name und -Verzeichnis laufen mit `rbbattle_run_suffix`; der
Teardown in `boot-test.yml` entfernt beides zusätzlich explizit (inkl. der
festen Altlast `riftbreaker-sessions-test` / `/srv/rbmods-sessions-test`).

## Kapazität (planet)

Messung 2026-09-13 (read-only): **20 vCPU**, **62 GiB RAM** (34 GiB verfügbar;
Swap 23/31 GiB belegt), `/` **87 %** (118 GB frei); Docker: 125 Images 83 GB
(64,8 GB reclaimable), 100 Volumes 51 GB (24,6 GB reclaimable).

Faustformel aus [`SERVER_SIZING.md`](SERVER_SIZING.md): je Dedicated-Instanz
≈ **1,5 vCPU + 1,5 GiB + 0,7 GB**. Ein `boot-test` bootet genau eine solche
Instanz (plus run-scoped Image/Build-Kontext).

| N parallele boot-tests | vCPU (Dedicated) | RAM (Dedicated) | Hinweis |
|---|---|---|---|
| 2 | ~3 | ~3 GiB | + Basisrauschen ~150 MB |
| 3 | ~4,5 | ~4,5 GiB | + transienter Image-Build (MinGW) |

Die reine Boot-Last bleibt moderat. Die systemd-Caps erlauben je Instanz bis zu
10 vCPU/8 GiB, d. h. 3 Instanzen können 30 vCPU/24 GiB anfordern → Überzeichnung
gegenüber 20 Kernen. **Empfehlung:** Caps bei der 3. Instanz auf ≤6 vCPU/8 GiB
senken **oder** die Nice-Überzeichnung bewusst akzeptieren; Swap- und
Disk-Stand (#301) im Blick behalten.

## Nachweis (Akzeptanz)

Zwei identische Läufe gleichzeitig fahren und Grün abwarten — z. B. zwei
`Boot-Test`-Dispatches (oder zwei `Deploy Check`) auf dieselbe Ref:

```bash
gh workflow run boot-test.yml --ref <ref>   # 2× kurz hintereinander
gh run list --workflow boot-test.yml --limit 4
```

Zu prüfen: beide Läufe gleichzeitig `in_progress` auf verschiedenen
Runner-Instanzen, beide `success`, keine Port-/Container-Kollision im Log.
Ergebnis (Run-URLs) im PR verlinken.

## Offene Operator-Punkte

1. **3. Runner-Instanz** registrieren (`planet-rbbattle-3`, Muster Variante A) —
   Host-Aktion, siehe Issue #304.
2. **Kapazitäts-Entscheidung** aus der Tabelle oben umsetzen (CPU-Quota/Swap/Disk).
3. **Nachweis** mit 2 (vorhanden) bzw. 3 (nach Registrierung) parallelen Läufen
   fahren und Run-URLs dokumentieren.
