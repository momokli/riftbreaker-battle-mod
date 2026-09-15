# SERVER_SETTINGS — Spiel-seitige Server-Einstellungen + Wirkung/Beleg

Stand: 2026-09-15 (Issue #476). Jede game-seitige Einstellung, die den
Wellen-Takt beeinflusst, steht **deklarativ im Repo** (Rolle
`deploy/roles/riftbreaker-server/` + `deploy/inventory/host_vars/<host>/vars.yml`)
und wird über `config.cfg.j2` gerendert. Keine Handeingriffe auf dem Host.

## 1. Blank Slate / Free Play: Vanilla-Naturwellen aus

### Schalter (deklarativ, pro Host opt-in)

`set difficulty "sandbox"` (Config → `riftbreaker_server_difficulty`,
gerendert in `config.cfg.j2`).

Der Rollen-Default ist `coop_normal` (Vanilla-Survival, Naturwellen **an**).
Der `planet`-Host bleibt in diesem PR ebenfalls auf `coop_normal`: der frühere
Live-Default-Flip auf `sandbox` wurde im Review (PR #478) **de-scoped** und
landet getrennt nach Verifikation (siehe Abschnitt 4). Damit ändert dieser PR
das Live-Spielverhalten nicht.

### Wirkung (RE-belegt)

Das Difficulty-Preset `sandbox` hat in
`scripts/difficulty/difficulties.difficulty` (VFS-Pack `00_win_data.zip`):

```
DifficultyDef { name "sandbox"  wave_strength "sandbox"
                mission_duration "0"  mission_infinite "1"  debug_menu "1" }
```

`dom_manager.lua` (v2) liest beim Init und in `OnLoad`:

```lua
self.pauseAttacks = DifficultyService:AreWavesDisabled()
if ( self.pauseAttacks == false ) then self.pauseAttacks = self.rules.pauseAttacks end
local waveStrength = DifficultyService:GetWaveStrength()
if ( waveStrength == "sandbox" ) then
    LogService:Log(" sandbox mode on - pausing attacks." )
    self.pauseAttacks = true
end
```

Mit `pauseAttacks == true` läuft der `spawner` nur noch im Kreis
`idle <-> dummy_state` bzw. `prepare_spawn`; `OnEnterPrepareSpawn` ruft dann
**kein** `PrepareWave`/`Streaming` → **0 Naturwellen**. Das ist **„aus"**, kein
Freeze (belegt: `dom_manager.lua` Z. 149/156/314/588/636/1223 ff.).

Zusätzlich: `mission_infinite 1` + `mission_duration 0` → kein Sieg-/Niederlage-
Zwang, freies Bauen/Farmen.

> **Namens-Hinweis.** Die Lua-**Klasse** heißt im Spiel `dom_mananger`
> (Eigen-Schreibweise, so auch `class 'dom_mananger' (event_manager)`); die
> **Datei** heißt `dom_manager.lua`. Beide Schreibweisen sind beabsichtigt —
> nicht „vereinheitlichen", sonst schlägt die Log-/Quelltext-Suche fehl.

### Abgrenzung: `SetSuspended` ist Freeze, NICHT „aus"

`LuaGraphNode::SetSuspended(bool)` (Build 2.0.58485, RVA `0x1BA6CB0`) ist
`mov byte [rcx+0xF1], dl ; ret`, und `LuaGraphNode::Update(float)` startet mit
`cmp byte [rcx+0xF1], 0 ; je <body> ; ret` — d.h. bei suspendiertem Knoten
kehrt Update **sofort** zurück. Das friert den DOM-Knoten ein (Timer stehen),
verhindert aber nur solange Wellen, wie die Suspension hält. Es ist damit
**Pause/Freeze**, nicht der vom Spiel vorgesehene „Waves off"-Pfad.

## 2. Natives Primitiv: `natural_waves` (Read + Write)

Damit der Schalter auch **zur Laufzeit** prüfbar/setzbar ist (Full-Chain #394),
liefert die Bridge ein natives C++-Primitiv ohne Lua/Console:

| Ebene | Artefakt |
|---|---|
| Game-C++ (RVA, nur Notiz) | `DifficultyService` → `+0x08` World\* → World-System (TypeHash `0x221d7af2`, Getter `0xC5F4A0`): `+0x08` difficulty-name, `+0x118` wave_strength, `+0x1B9` mission_infinite, **`+0x1BA` waves_disabled** (= `DifficultyService::AreWavesDisabled()`) |
| rbbridge.dll | `dispatch_natural_waves()` + `resolve_diffsys()` (RTTI `.?AVDifficultyService@Riftbreaker@@` + AOB-Signatur `RBBRIDGE_DIFFSYS_GET_SIG`, KEINE feste Adresse) |
| pipe_bridge.c | `POST /natural_waves` `{"op":"status|off|on"}` → Pipe-Cmd `natural_waves` → `natural_waves_result` |
| Web-UI | Cockpit-Sektion *Natural Waves (Vanilla)*: Readout + Buttons `off (next load)`/`on (next load)`/`status` |

**Read (`status`)** liefert
`{"event":"natural_waves_result","ok":true,"op":"status","readback":"ok",
"waves_disabled":<bool>,"wave_strength":"<s>","mission_infinite":<bool>,
"difficulty":"<name>"}`.

**Write (`off`/`on`)** schreibt das `+0x1BA`-Byte und meldet den Ausgang
getrennt (Review #478): `ok:true,"written":true,"readback":"ok"` bei
erfolgreichem Readback; `ok:true,"written":true,"readback":"failed"`, wenn der
Write griff, der Readback aber nicht (dann keine Feldwerte); `ok:false,
"reason":"write_failed"`, wenn der Write nicht griff (nichts geschrieben).

Thread-Modell (#378): native Reads/Writes, kein `lua_*`. **Annahme (#478):**
der per AOB gefundene World-System-Getter ist ein reiner Lookup (kein Lua, kein
Lock, keine Seiteneffekte) und darf daher vom Pipe-Thread aufgerufen werden; im
Live-Test bei parallelem `status`+`off` auf Race/Crash achten. Nicht auflösbar
(kein Modul/RTTI/Getter/Instanz) → `ok:false`, **es wird nichts geschrieben**
(graceful).

### Wichtig: Runtime-Write vs. Boot-Setting

`dom_manager` liest `pauseAttacks` nur bei `__init`/`OnLoad`. Ein Runtime-Write
auf `+0x1BA` wirkt daher erst beim nächsten Mission-/Rules-Load (die
Cockpit-Buttons sind entsprechend `off (next load)`/`on (next load)` gelabelt).
Der **deklarative Boot-Schalter** ist `set difficulty "sandbox"` (Abschnitt 1);
das Primitiv dient Verifikation/Sichtbarkeit und dem Force-Pfad vor dem
Mission-Load.

## 3. Nachbar-Einstellungen

| Var | Wert (planet) | Wirkung |
|---|---|---|
| `riftbreaker_server_campaign` | `mp_survival/mp_survival` | Survival-Welt |
| `riftbreaker_server_mission` | `survival/jungle` | Biome/Karte |
| `riftbreaker_server_difficulty` | `coop_normal` (Default; `sandbox` = opt-in, #476) | `sandbox`: Naturwellen aus, mission_infinite |
| `riftbreaker_server_pause_game_when_empty` | `0` | Lua-Welt läuft headless weiter (#265) |
| `debug_disable_content_version_check` | `1` | Vanilla-Client kann joinen (#44) |

Der Rollen-Default `riftbreaker_server_difficulty: coop_normal` entspricht dem
bisherigen `planet`-Wert und dem Vanilla-Survival-Verhalten; Hosts ohne eigenen
Wert bekommen damit **keine** Verhaltensänderung (Default = vorheriges
Ist-Verhalten). Der Wert wird nur in `config.cfg.j2` konsumiert;
`deploy/roles/server-control/templates/config-vars.json.j2` reicht ihn lediglich
als Kontext an das Control-Panel durch.

## 4. Verifikation

- **Ohne Player (in diesem PR belegt):** Host-Test `tests/rbbridge-hosttest`
  (op-Parser, Signatur-Selbstkontrolle, Wildcard-/E8-Semantik); Tool-Build
  `scripts/build_rbbridge_tools.sh <outdir>` (4 Binaries, mingw). WebUI-Sektion
  als Screenshot unter `docs/screenshots/476/`. Pipe-Endpoint
  `POST /natural_waves {"op":"status"}` liefert bei nicht-injizierter DLL
  graceful `ok:false`.
- **Mit Player (offen, Follow-up):** nach `set difficulty "sandbox"` + Restart
  muss im Log ` sandbox mode on - pausing attacks.` stehen und über ≥10 min **0**
  Naturwellen auftreten; `POST /natural_waves {"op":"status"}` zeigt
  `wave_strength":"sandbox"`. Das gehört zum separat gelandeten
  Live-Default-Flip (de-scoped aus PR #478), nicht zu diesem PR.
