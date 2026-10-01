# Screenshots — Issue #1025 (Ready→GO: kontextabhängiger READY in der Lobby)

Frontend-Nachweis für die in `tools/gns-proxy/gns_probe.cpp` (`kUiHtml`)
geänderte Lobby-UI — Reviewer-Blocker 1 zu PR #1052 (AGENTS.md: Screenshots
bei Frontend-Änderungen Pflicht):

- `ready()` (Z. 1680): neuer `world`-Parameter + nutzersichtbare Hinweistexte
  (`referee_unconfigured`, `referee_unreachable`, `bad_request`, „ready
  gesendet: Welt <w>").
- READY-Button (Z. 1884–1889): kontextabhängiger `title`-Tooltip („Welt A beim
  Referee ready melden (POST /ready {world})" bei VS, sonst Kapsel) +
  `onclick` mit `s.vsWorld`.

Ohne laufenden Relay/Referee gerendert: `kUiHtml` aus dem Quelltext extrahiert,
mit einem Mock-Backend (`/targets`, `/sessions`, `/queue/status`,
`/referee/state`, `POST /ready`) serviert und per headless Chromium
(`--force-device-scale-factor=2`) aufgenommen. Die Seite ist die **echte**
eingebettete UI (kein Nachbau); nur die Backends sind gemockt.

**Vorher/Nachher:** „Vorher" ist der Blob
`origin/main:tools/gns-proxy/gns_probe.cpp`, „Nachher" der Arbeitsbaum
(Branch `feature/1025-lobby-ready-referee`).

**Tooltip/Banner:** Der READY-`title` ist ein **natives** Attribut; native
Tooltips erscheinen in headless-Screenshots nicht. Deshalb liest die Demo den
**realen** `title`-Wert des gerenderten READY-Buttons sowie die **reale**
`hint()`-Ausgabe nach dem echten Klickpfad `ready(rbtn, c, s.vsWorld)` aus dem
DOM und zeigt beides im blauen Diagnose-Banner über der Karte an — **kein
nachgebauter Text**, beide Werte stammen aus der realen Branch-Logik.

Reproduktion (aus dem Repo-Wurzelverzeichnis):

    python3 docs/screenshots/1025/render.py

| Datei | Quelle | Session | `POST /ready` | Banner (real) |
|---|---|---|---|---|
| `00-before-solo-ready.png` | before | Solo | 200 | title Kapsel · hint „ready gesendet - Countdown folgt im Chat" |
| `01-after-solo-ready.png`  | after  | Solo | 200 | **unverändert** (Solo-Regression) |
| `02-before-vs-ready.png`   | before | VS (Welt A) | 200 | title Kapsel · hint generisch (kein Welt-Feld) |
| `03-after-vs-ready.png`    | after  | VS (Welt A) | 200 | title **„Welt A …"** · hint **„ready gesendet: Welt A"** |
| `04-after-vs-referee-unconfigured.png` | after | VS | 503 `referee_unconfigured` | hint „Referee-Dienst nicht konfiguriert" |
| `05-after-vs-referee-unreachable.png`  | after | VS | 502 `referee_unreachable` | hint „Referee nicht erreichbar" |
| `06-after-vs-bad-world.png`| after  | VS | 400 `bad_request` | hint „Welt ungueltig" |

Kern: Bei **Solo** ist der Tooltip und der Hinweis vor/nach identisch
(`00` == `01`) — das kontextabhängige Verhalten ändert den Solo-Pfad nicht.
Bei **VS** zeigt der READY-Button vorher den Kapsel-Tooltip (`02`) und nachher
den Welt-Tooltip (`03`) und meldet die Welt im Hinweis.

Mock-Werte (nur Darstellung, keine echten Serverdaten; **keine Tokens**):
`str:AB12`, Instanz `parked-1`, Welt `A`.

Hinweis: Ein Live-Klickpfad (echtes `POST /ready` gegen Relay+Referee) ist
nicht Teil dieses Belegs; die Backend-Verdrahtung ist durch die Tests in
`deploy/queue/` + `tournament/` belegt (siehe PR #1052).
