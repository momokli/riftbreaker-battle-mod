# S1 — Root-Pointer für World/Services (Vorarbeit, Issue #1035)

Status: **Teilergebnis** — zwei scan-freie Root-Mechanismen gefunden (RVAs belegt),
Welt-Kette identifiziert; **Live-Beweis + App→World-Offset noch offen.**

Build **2.0.58485**, PDB `/opt/rb-re/bin/riftbreaker_dll_win_release.pdb`
(planet). Kontext: RFC `docs/rfc/0001-dedicated-io-state-pipeline.md`,
Motivation: `get_state` löst heute **pro Poll** die Service-Instanzen über einen
Full-Process-Heap-Scan auf (`server/dll/rbbridge.c:6180`, `:6033`).

## Ergebnis (TL;DR)

1. **`Exor::Singleton<T>::GetSingleton()` legt die Instanz in einem
   funktionslokalen Static ab** — dessen **Adresse steht im PDB**. Damit sind
   Exor-Singletons **scan-frei** per einem Deref lesbar
   (`*(base + RVA)`), z. B. `FrameTimeTracker` (→ relevant für S3/S6) und
   `Profiler`.
2. **`Exor::ApplicationAbstract::ms_singleton`** ist ein statisches Datenmember
   (`.data`) und ein stabiler **Root auf die Application**.
3. **Welt-Kette identifiziert:** `Riftbreaker::GameplayState::GetWorldState(WorldType)`
   → `WorldState*`; `Riftbreaker::WorldStatesHolder::GetWorld(WorldType)` → `World&`.
   Über `World::GetSystem<T>()` (bekannt: `LuaSystem` RVA `0x194EDA0`) sind die
   Services (PlayerService/HealthService/FindService) erreichbar.
4. **Unsere Service-Typen sind World-Systeme, keine Exor-Singletons** — der
   gesuchte Root ist daher `World*` (nicht ein Service-Singleton).

## RVAs (belegt, Build 2.0.58485)

RVA-Formel: `.text` = `0x1000` + dez. Offset, `.rdata` = `0x2DA2000` + Off,
`.data` = `0x3EE8000` + Off (siehe Skill `riftbreaker-re`).

| Symbol | Sektion:Offset (PDB) | RVA | Typ |
| --- | --- | --- | --- |
| `Exor::ApplicationAbstract::ms_singleton` | `0003:9635808` | **`0x48187E0`** | `ApplicationAbstract*` (.data) |
| `Exor::Singleton<FrameTimeTracker>::singleton` | `0003:9424296` | **`0x47E4DA8`** | `FrameTimeTracker*` (.data) |
| `Exor::Singleton<Profiler>::singleton` | `0003:9396896` | **`0x47DE2A0`** | `Profiler*` (.data) |
| `Riftbreaker::GameplayState::GetWorldState(WorldType)` | `0001:27278384` | `0x1A04C30` | Fn |
| `Riftbreaker::WorldStatesHolder::GetWorld(WorldType)` | `0001:26151264` | `0x18F1960` | Fn |

### Mechanismus A — Exor-Singleton-Statics

`dump -publics` liefert **Daten-Symbole** der Form

```
?singleton@?1??GetSingleton@?$Singleton@V<T>@<Ns>@@@Exor@@SA...XZ@4PEAV...EA
```

(`?1?...` + `@4` = funktionslokales Static; `PEAV...` = Zeiger auf `T`). Der
Speicherort steht als `addr = 0003:…` dabei → **`*(base + RVA)` ist der
Instanz-Zeiger**. Betrifft ~30 Exor-Singletons (GuiManager, ResourceManager,
Audio2, EventRegistry, BuildingDb, …). Analog: Ogre-`Singleton<T>::ms_Singleton`
(ebenfalls `.data`-Static).

> For dem Hintergrund: die frühere Notiz
> `docs/research/pdb-symbol-validation.md` („keine benannten Service-Singletons")
> gilt für den **Globals**-Stream (`-globals`, 0 `S_GDATA32`). Der **Publics**-
> Stream (`-publics`) enthält diese Statics sehr wohl (mit Adresse).

### Mechanismus B — Application-Root → Welt

`Process(ApplicationAbstract)` → `Riftbreaker::GameplayState`/`WorldStatesHolder`
→ `GetWorld(WorldType)` → `World&` → `World::GetSystem<T>()`.

**Offen:** der Offset/Feld auf `GameplayState`/`WorldStatesHolder` innerhalb der
Application (bzw. der App-State) — braucht Disasm (`tools/re/disasm.py`).

## Live-Beweis (offen) — Rezept

Externer Read ist auf planet blockiert: `/proc/<pid>/mem` liefert **EIO**
(Wine setzt den Prozess non-dumpable), zusätzlich `yama.ptrace_scope=1`, und
`gdb`/`lldb` sind nicht installiert. Praktikabel:

1. **Kleinster Weg (empfohlen):** temporärer Read im Bridge-Probe —
   `read_u64(base + 0x48187E0)` (Application) und `base + 0x47E4DA8`
   (FrameTimeTracker) über das bestehende `/probe`/Pipe ausgeben, im laufenden
   dev/prod-Server (Injection-Zyklus ~2–3 min, Skill `riftbreaker-re`).
2. Alternativ: `gdb` auf planet installieren und per root attachen
   (`x/gx 0x6ffff…`).

Erwartung: Wert ist ein plausibler Heap-Pointer (nicht 0, im Wine-Adressbereich),
der nach Map-Reload **wechselt** aber innerhalb einer Welt stabil bleibt.

## Repro (planet)

```sh
P=/opt/rb-re/bin/riftbreaker_dll_win_release.pdb
llvm-pdbutil-18 dump -publics "$P" > /tmp/rb_pub.txt
grep -A1 -F 'ms_singleton@ApplicationAbstract' /tmp/rb_pub.txt
grep -F 'GetSingleton@?$Singleton@V' /tmp/rb_pub.txt | grep -F '@4PEAV'   # Singleton-Statics
llvm-pdbutil-18 dump -section-headers "$P"
```

## Empfehlung / nächste Schritte

- [ ] **App→World-Offset** diskassemblieren (`GameplayState`/`WorldStatesHolder`
      aus der Application) — oder direkt einen `World*`-Static finden.
- [ ] **Live-Beweis** der Stati (Probe-Snippet), inkl. Stabilität über eine Welt.
- [ ] Ergebnis → RFC §4.3: `World*` einmal pro **Welt-Epoch** auflösen,
      Re-Validierung per vftable-Wort, Scan nur als Cold-Path-Fallback.
- [ ] Falls kein Root: Fallback = Scan-Ergebnis cachen (Tier 0).
