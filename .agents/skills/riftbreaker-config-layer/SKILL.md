---
name: riftbreaker-config-layer
description: Zieht eine neue konfigurierbare Dimension durch die drei Schichten des Riftbreaker-Attack-Cycle — Bridge (pipe_bridge.c) → Sidecar-Sync (attack_cycle.py) → Cockpit-Editor (cockpit.html). Nutze, wenn eine neue Config/Option im Cockpit editierbar und vom Attack-Cycle nutzbar sein soll.
---

# Riftbreaker Config-Layer (3 Schichten)

Eine konfigurierbare Dimension (Toggle, Zahl, Liste, Regelsatz) lebt in **drei** Schichten. Das Muster ist bei `personas` (#788), `natural_attack_rules` (#819) und `game_config` (#828) identisch — kopiere es.

```text
Cockpit (HTML/JS)  ──HTTP──>  pipe_bridge.c (Port 9001, C-Config-Speicher)  ──HTTP──>  attack_cycle.py (Sidecar)
                                        └──Named-Pipe──>  rbbridge.dll (in-game)
```

## Schicht 1 — Bridge (`server/pipe-bridge/pipe_bridge.c`)

1. Globale Variable `static char g_<name>[N];` + `static CRITICAL_SECTION g_<name>_cs;` mit Default (JSON-String bei Objekten/Listen; sonst int/bool). Muster: `g_personas`, `g_natural_attack_rules`, `g_game_config`.
2. `handle_get_<name>(SOCKET c)` — liest die Variable (CS) und antwortet mit dem **rohen** JSON (`http_respond(c, 200, "OK", ...)`).
3. `handle_post_<name>(SOCKET c, const char *body)` — speichert den rohen Body (CS) + `blog("... -> gesetzt (%d bytes)", ...)`.
4. Routing in `handle_client`: `else if (strcmp(method, "GET") == 0 && strcmp(path, "/<name>") == 0) { handle_get_<name>(c); }` bzw. für POST den Body in einen `malloc`-Puffer kopieren, Handler rufen, `free`.
5. `InitializeCriticalSection(&g_<name>_cs);` im Init-Block von `mode_server`.
6. Syntax-Check: `x86_64-w64-mingw32-gcc -fsyntax-only -I server/pipe-bridge server/pipe-bridge/pipe_bridge.c`.
7. **Lesbare Zustände:** Ein reines „Ack" (z. B. `POST /start` → `{"ok":true}`) reicht NICHT, wenn der Sidecar den Zustand pollen muss. Halte einen **Zähler/Flag** (z. B. `g_start_epoch`), inkrementiere ihn beim POST und liefere ihn im GET (Edge-Erkennung). Sonst kann der Sidecar den Übergang nie sehen.

## Schicht 2 — Sidecar (`deploy/attack-cycle/attack_cycle.py`)

1. Konstruktor-Feld + Default. CLI/ENV sind nur der **Start-Fallback**; die Bridge ist die Laufzeit-Wahrheit.
2. `sync_<name>()`: pollt `GET /<name>` via `self._getter("/<name>")`; `json.loads`; **jedes Feld einzeln validieren** (ungültig/fehlend → alter Wert bleibt); Ergebnis unter `self._lock` übernehmen.
3. In `run()` die `sync_<name>()`-Schleife ergänzen.
4. In `status()` das Feld mit ausgeben (Observability — die Bridge/Web-UI liest es zurück).
5. Für Schreib-Reaktionen (z. B. Reset/Start): `self._poster(...)`.
6. Test: `python3 -m unittest test_attack_cycle` (im Ordner `deploy/attack-cycle`). Fake-Poster/-Getter/-Clock sind injizierbar (`_poster/_getter/_clock/_rng`).

## Schicht 3 — Cockpit (`cockpit/cockpit.html`)

1. Neuer Tab-Button `<button id="tab_<x>" class="tab" role="tab" aria-controls="<x>_editor" tabindex="-1">` in `nav.tabs`, plus `<section id="<x>_editor" class="persona-editor" role="tabpanel" aria-labelledby="tab_<x>" hidden>`.
2. Eintrag im `TABS`-Array + `onclick`/`onkeydown`-Kette (roving tabindex, Links/Rechts-Pfeile).
3. JS als **testbarer Marker-Block** `// --- <x> editor (testable) ---` … `// --- end <x> editor ---`: `create<X>Editor(deps)` mit `load()` (GET), `save()` (POST), `restore()`, plus feldspezifischen Settern. Muster: `createGameConfigEditor`, `createNaturalAttackEditor`, `createPersonaEditor`.
4. `tests/<x>-editor/` (`package.json` mit `"test": "node --test"` + Test, analog `tests/game-config-editor/`) + CI-Schritt in `.github/workflows/ci.yml`.
5. UI-Post-Check: siehe Skill **cockpit-ui-review** (Format-Falle + `tools/cockpit-ui-review/shoot.js`).

## Interface-Stabilität (wichtig für Parallelarbeit)

Die drei Schichten sind **entkoppelt über das Endpoint-Interface** (Pfad + JSON-Shape). Definiere es ZUERST (Keys, Typen, Endpoint-Namen, Defaults) — dann können die Schichten parallel gebaut werden (auch von Sub-Agents mit disjunkten Dateien: `pipe_bridge.c` / `attack_cycle.py` / `cockpit.html`+`tests/`).

## Refs

Beispiele: `personas` (#788), `natural_attack_rules` (#819), `game_config` (#828). Spec: `docs/GAME_FLOW.md`. Repo-Regeln: `AGENTS.md`.
