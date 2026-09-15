# SERVER_SETTINGS — Spiel-seitige Server-Einstellungen + Wirkung/Beleg

Stand: 2026-09-15 (Issue #476). Jede game-seitige Einstellung, die den
Wellen-Takt beeinflusst, steht **deklarativ im Repo** (Rolle
`deploy/roles/riftbreaker-server/` + `deploy/inventory/host_vars/planet/vars.yml`)
und wird über `config.cfg.j2` gerendert. Keine Handeingriffe auf dem Host.

## 1. Blank Slate / Free Play: Vanilla-Naturwellen aus

### Schalter

`set difficulty "sandbox"` (Config → `riftbreaker_server_difficulty` in
`host_vars/planet/vars.yml`, gerendert in `config.cfg.j2`).

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
| Web-UI | Cockpit-Sektion *Natural Waves (Vanilla)*: Readout + Buttons `off`/`on`/`status` |

Read liefert `{"event":"natural_waves_result","ok":true,"op":"status",
"waves_disabled":<bool>,"wave_strength":"<s>","mission_infinite":<bool>,
"difficulty":"<name>"}`. Write (`off`/`on`) schreibt das `+0x1BA`-Byte.

Thread-Modell (#378): native Reads/Writes, kein `lua_*`; der System-Getter ist
ein reiner Lookup. Nicht auflösbar (kein Modul/RTTI/Getter/Instanz) → `ok:false`,
**es wird nichts geschrieben** (graceful).

### Wichtig: Runtime-Write vs. Boot-Setting

`dom_manager` liest `pauseAttacks` nur bei `__init`/`OnLoad`. Ein Runtime-Write
auf `+0x1BA` wirkt daher erst beim nächsten Mission-/Rules-Load. Der
**deklarative Boot-Schalter** ist `set difficulty "sandbox"` (Abschnitt 1); das
Primitiv dient Verifikation/Sichtbarkeit und dem Force-Pfad vor dem Mission-Load.

## 3. Nachbar-Einstellungen

| Var | Wert (planet) | Wirkung |
|---|---|---|
| `riftbreaker_server_campaign` | `mp_survival/mp_survival` | Survival-Welt |
| `riftbreaker_server_mission` | `survival/jungle` | Biome/Karte |
| `riftbreaker_server_difficulty` | `sandbox` (#476) | Naturwellen aus, mission_infinite |
| `riftbreaker_server_pause_game_when_empty` | `0` | Lua-Welt läuft headless weiter (#265) |
| `debug_disable_content_version_check` | `1` | Vanilla-Client kann joinen (#44) |

## 4. Verifikation

- **Ohne Player:** Host-Test `tests/rbbridge-hosttest` (op-Parser, Signatur-
  Selbstkontrolle, Wildcard-/E8-Semantik); Tool-Build
  `scripts/build_rbbridge_tools.sh <outdir>` (4 Binaries). Pipe-Endpoint
  `POST /natural_waves {"op":"status"}` liefert bei nicht-injizierter DLL
  graceful `ok:false`.
- **Mit Player (offen):** nach `set difficulty "sandbox"` + Restart muss im Log
  ` sandbox mode on - pausing attacks.` stehen und über ≥10 min **0** Naturwellen
  auftreten; `POST /natural_waves {"op":"status"}` zeigt
  `wave_strength":"sandbox"`.