# 518 — Client-Mod-Datenquelle: HUD ← State

**Entscheidungsvorlage** (Issue #518, Spike, Timebox 0,5 Tag) · Stand 2026-09-15 ·
Build 2.0.58485 · reine Recherche/Entscheidung, **kein** Live-Test auf `planet`.

Gegenstand: *Wie bekommt der Lua-HUD die State-Daten?* — HTTP-Pull aus der Mod,
LogService-Events (+ Sidecar) oder Cockpit-only. Dazu: **was die Lua-Sandbox
kann und nicht kann**.

Referenzen: #378, #483, `docs/1.0-COMPONENTS.md` (offener Punkt
„Client-Mod-Datenquelle“), `docs/research/dedicated-io-thread-model.md`,
`docs/research/dedicated-io-re-findings.md`, `docs/SEND_HOOK.md`,
`docs/findings.md`, `docs/ASSUMPTIONS.md`, `mod/lua/rbbattle_autoexec.lua`.

---

## 1. Frage

Der Lua-HUD (Komponente 1, `mod/`) ist seit #378 **reine HUD-/Display-Schicht**
ohne Business-Logik. Er soll Werte anzeigen (Wave-Counter, HQ-HP, Ressourcen,
Runden-/Queue-Zustand). Offen war, **woher** diese Werte in-Game kommen.

## 2. Randbedingungen: was die Lua-Sandbox kann / nicht kann

Belegt aus den Repo-Findings (nicht neu vermessen); „NEIN“ ist jeweils die
harte Grenze, die eine Option ausschließt.

| Fähigkeit                                   | Kann?                 | Beleg                                                                            |
| ------------------------------------------- | --------------------- | -------------------------------------------------------------------------------- |
| **Datei-I/O (`io.*`)**                      | **NEIN — crasht hart** | `docs/findings.md` #16 (`io.open` beendet den Prozess); `docs/ASSUMPTIONS.md` A9  |
| **HTTP / Socket / Netz**                    | **NEIN**              | `docs/findings.md` #2: 0 Treffer für `http`/`socket` in der offiziellen Mod-API   |
| **`os.getenv`**                             | **NEIN**              | `mod/lua/rbbattle_autoexec.lua` (Review-F2: live `env=unknown`), #483            |
| `LogService:Log` → `exor_logs.txt`          | JA                    | `docs/findings.md` #7                                                            |
| `ConsoleService:Write` / `:RegisterCommand` | JA                    | `docs/findings.md` #4/#12/#15                                                    |
| `RegisterGlobalEventHandler`                | JA                    | `docs/findings.md` #14                                                           |
| `GuiService` (Popup/HUD-Text)               | JA (eingeschränkt)    | `docs/findings.md` #6 (gültige HUD-`id`s undokumentiert)                         |
| `GetConfig(...)`                            | JA (statisch)         | `docs/findings.md` #4 — Config, **kein** Live-State                              |
| Direkter Spielobjekt-Read (Lua-API, Services/Globals) | JA           | #378 KEEP-Liste; historisch `BuildStateJson`/`PatchDomCapture`                   |

**Thread-Kontext (relevant):** Der Mod läuft in der Autoexec auf dem **Lua/main-
Thread**. Innerhalb dieses Threads sind Lua-Reads unkritisch. Die Sandbox ist
aber **nach außen komplett zu** — der Mod hat keinen eigenen I/O-Kanal
(`docs/concept.md`, „Trainer-only“; Entscheidung 08.09.2026).

## 3. Optionen

### Option A — `get_state` via HTTP aus der Mod

**A1 (Lua-Client): nicht machbar.** Der Mod kann kein HTTP sprechen (kein
`http`/`socket`-Binding, #2) und keinen `io.open`-Umweg nehmen (crasht, #16).
Ein Lua-seitiger `get_state`-Pull existiert damit nicht.

**A2 (DLL → Lua-Push): machbar, aber teuer.** Damit der HUD überhaupt
DLL-/Bridge-State sehen kann, muss **die DLL** den State in Lua schieben: eine
DLL-registrierte Lua-C-Funktion (`_G.rbbridge_get_state()` → gecachter
JSON-String) bzw. ein Push in die HUD-Render-Funktion — jeweils mit `lua_*` auf
dem **Lua/main-Thread** über einen Lua-Hook. Genau das war der #376-Bau
(`rbbridge_capture_state` + `dom_mananger:Update`-Hook), der mit dem
C++-direct-only-Umbau **entfernt** wurde (`c4db642`/`f022f35`, #387/#446;
`dedicated-io-thread-model.md` „Was `main` nicht mehr hat“). Reaktivierung
bedeutet: `lua_*`-Pfad + Lua/main-Thread-Hook wieder einführen, Thread-Modell-
Beweis (#378) erneut führen. **Kein** Fall für 1.0.

> Wichtig: „Bridge-HTTP“ hat im Sandbox-Modell **keinen** Lua-seitigen Client.
> Der einzige HTTP-fähige Akteur ist die DLL/`pipe_bridge` (außerhalb der
> Sandbox). Die Option degeneriert damit zu A2.

### Option B — LogService-Events (`[RBBATTLE] event=…`) + Sidecar

**Richtung ist Egress (Spiel → außen), nicht HUD ← State.** Der Mod emittiert
`[RBBATTLE] …`-Zeilen via `LogService:Log` nach `exor_logs.txt`; ein Sidecar
tailt und parst sie zu JSON. Belegt und gebaut: `bausteine/03-log-bridge/tail_events.py`,
`tools/referee-egress/referee_egress.py`, Live-Belege in `docs/INGRESS_IO.md`
(„`[RBBATTLE] event=wave level=3 status=start`“). Das ist die **tragende
1.0-Mechanik für Cockpit/Referee/Traceability** — sie liefert dem **HUD** aber
**keine** State-Daten (Log-Zeilen sind uni-direktional nach draußen,
Latenz = Log-Flush, kein Inbound in die Lua).

### Option C — Cockpit-only

Der autoritative State wird von `rbbridge.dll` (`get_state`, nativ) gelesen und
im **Web-UI Cockpit** angezeigt (`bausteine/08-control-ui/cockpit.html` →
`post("get_state")`). In-Game rendert der HUD nur, was er **lokal** lesen kann.
Kein neuer Kanal, kein Sandbox-Risiko, deckungsgleich mit „Backend 1.0 = DLL +
pipe_bridge + Cockpit als manueller Operator“ (`docs/1.0-COMPONENTS.md`).

**Konvergenz-Argument:** Bridge-Writes landen im **selben** Spielzustand, den der
Mod liest (`add_resource` mutiert denselben Basket; `io-write-poc.md`: „Write
sofort in `get_state` sichtbar (curl + HUD)“). Ein HUD, der **lokale Reads**
rendert, spiegelt damit auch Operator-Änderungen — ohne eigenen Bridge-Kanal.

## 4. Entscheidung (Empfehlung)

**1.0 = Cockpit-only (C).**

- **Autoritativer State** = `get_state` (DLL) im **Cockpit**. (Read-Pfad: #479-
  readiness-gated, `dedicated-io-thread-model.md`.)
- **Lua-HUD** rendert ausschließlich **lokal lesbare** Spielwerte (read-only,
  Lua/main-Thread) und bleibt reine Display-Schicht (#378). Er spiegelt
  Bridge-Writes, weil beide auf denselben Game-State gehen.
- **Mod → außen** bleibt **Option B** (LogService-Events + Sidecar) — als
  Egress-Kanal für Cockpit/Referee/Traceability, **nicht** als HUD-Datenquelle.
- **Kein** Mod-HTTP/`get_state`-Pull (A1) — die Sandbox kann es nicht.

**1.1-Option (nur bei echtem Bedarf an einem autoritativen In-Game-HUD):** A2
(DLL → Lua-Push). Braucht die Reaktivierung des #376-/Lua-Pfads + Thread-
Modell-Beweis → eigenes Issue, nicht Teil dieses Spikes.

## 5. Machbarkeitsnachweis (Umfang dieses Spikes)

- **Belegt durch Aktenlage** (Repo-Findings/Docs + bestehender Code):
  Lua-Sandbox-Grenzen (#2/#16/#9-A9, Review-F2), Event-/Sidecar-Pfad
  (`tail_events.py`, `referee_egress.py`, `INGRESS_IO.md`), Cockpit-`get_state`-
  Verbraucher (`cockpit.html`), Thread-/Ist-Stand (`dedicated-io-thread-model.md`),
  entfernte A2-Architektur (#376/#446).
- **Nicht behauptet** (kein Live-Test in dieser Timebox): reales HUD-Rendering
  im laufenden Spiel; konkrete Latenz des Log-Sidecars unter Last. Beides ist
  erst mit Player auf `planet` messbar (Player-Test, „Offener Punkt“).

**Fazit der Machbarkeit:** A1 ausgeschlossen, B ist Egress (nicht HUD),
C ist ohne neue Kanal-/Sandbox-Arbeit sofort tragfähig → 1.0 geht mit C.

## 6. Offene Punkte / Follow-ups

- [ ] **A2 nur bei Bedarf** als eigenes Issue aufsetzen (DLL→Lua-Push,
      `lua_*` + Lua/main-Thread-Hook, Thread-Modell-Beweis) — sonst n/a.
- [ ] **HUD-Feature-Scope 1.0** präzisieren: welche lokal lesbaren Werte zeigt
      der HUD (Wave/HQ/Resource/Queue), welche bleiben Cockpit-only?
- [ ] **Sidecar-Latenz** im Player-Test messen (Log-Flush vs. Cockpit-Poll).
- [ ] `docs/1.0-COMPONENTS.md` offener Punkt „Client-Mod-Datenquelle“ nach
      Merge dieser Entscheidung abhaken/verweisen.

## 7. Refs

#518 · #378 · #483 · #376 · #387 · #446 · #479 · `docs/1.0-COMPONENTS.md` ·
`docs/research/dedicated-io-thread-model.md` · `docs/SEND_HOOK.md`
