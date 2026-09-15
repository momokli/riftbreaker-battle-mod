# Nativer Round-Reset (`restart_map`) — RE-Befunde (#516)

Build **2.0.58485** (GOG == Dedi, byte-identisch mac/planet/lan-Extraktion).
Tools: `llvm-pdbutil-18 dump -publics` (PDB, `S_PUB32` names+addresses),
`tools/re/disasm.py` (capstone+pefile), PDB-Section-Header für die
RVA-Konvertierung (`.text` Basis `0x1000`, `.rdata` `0x2DA2000`,
`.data` `0x3EE8000`).

## Fragestellung

Wie lässt sich nach HQ-Tod eine neue Runde (Economy 0, HQ-Placement) **nativ
in C++** auslösen — ohne Lua, ohne `ConsoleService::ExecuteCommand`?
Kandidaten: `MissionService::FinishCurrentMission` (`0xF9A190`) vs. das
`restart_map`-Kommando vs. das entfernte Lua-`rb_reset` (#281).

## Befund: `restart_map` ist ein natives Console-Kommando

- String-Literal `restart_map` liegt im `.rdata`
  (`??_C@_0M@DPMJDBBG@restart_map@`, RVA `0x2F24FF0`).
- Handler `?OnRestart@GameplayState@Riftbreaker@@IEAAX…` — RVA **`0x1A0F200`**.
  `OnRestart` (Console-Command-Handler) und `OnRestartRequest`
  (Event-Handler `RestartRequest`) sind vom Linker auf **dieselbe**
  Adresse gefaltet (identischer Rumpf: `mov rax,[rcx]; jmp [rax+0x60]`).
  Beide Wege setzen also denselben Pending-Zustand.
- `?RestartGame@GameplayState@Riftbreaker@@MEAAXXZ` — RVA **`0x1A18B40`**
  (protected virtual, Slot `+0x50`), Override
  `?RestartGame@ServerGameplayState@…` — RVA `0x1832CE0`. Das ist der
  schwere Workhorse (Map-Neuaufbau), läuft auf dem **Game-Thread**.

## Der eigentliche Trigger: `GameplayState::RequestRestart()`

Public virtual, RVA **`0x1A17E70`** (Slot `+0x20` von
`??_7GameplayState@Riftbreaker@@6BBaseFrameworkState@Exor@@@`, RVA
`0x2F226D0`, und der Server-Variante `0x2F0C4D8`):

```
C6 81 2A 05 00 00 01 C3     mov byte ptr [rcx+0x52A], 1 ; ret
```

Das ist eine **reine Flag-Schreiboperation** → **thread-agnostisch** (kein
`lua_*`, kein Spiel-Thread-Zwang), exakt dieselbe Risikoklasse wie
`LuaGraphNode::SetSuspended` (`0x1BA6CB0`).

Der **Konsum** erfolgt im Gameplay-Update (RVA `0x1A1C378`, im `.text`
**genau 1 Treffer** — der eindeutige Anker):

```
41 80 BE 2A 05 00 00 00     cmp byte ptr [r14+0x52A], 0
74 14                       je  +0x14
49 8B 06                    mov rax, [r14]
49 8B CE                    mov rcx, r14
FF 90 90 00 00 00           call qword ptr [rax+0x90]   ; Restart-Routine
41 C6 86 2A 05 00 00 00     mov byte ptr [r14+0x52A], 0
```

Daraus folgen zwei belegte Fakten:

1. **Flag-Offset** `+0x52A` (wird gesetzt, gelesen, gelöscht).
2. **Restart-Routine** = vtable-Slot `+0x90` (RVA `0x1A10860`) — die
   eigentliche Map-Restart-Implementierung, aufgerufen auf dem Game-Thread.

> Die benachbarten Flags `+0x52B` / `+0x52C` (vom selben Update-Block
> konsumiert) gehören zu anderen Pending-Requests (`call 0x18196C0B0` bzw.
> Slot `+0x98`); sie sind **nicht** der Round-Reset.

## Warum nicht `FinishCurrentMission`?

`MissionService::FinishCurrentMission(MissionStatus)` (`0xF9A190`,
PDB `0001:16355728` → RVA bestätigt) **beendet** die Mission (win/lose). Das
ist der `end_game`-Pfad (bereits als natives Primitiv vorhanden), **kein**
Round-Reset: es baut keine neue Runde/kein HQ-Placement auf. Für #516 ist
`RequestRestart()` der korrekte native Hebel.

## Natives Primitive in `rbbridge.c`

`restart_map` (Pipe-Cmd) mit `op = status|reset`. Auflösung **ohne feste
Adresse**:

1. **Konsum-Muster per AOB** (`RBBRIDGE_RESTART_CONSUMER_SIG`, 30 B, alle
   disp32 + `je rel8` als Wildcards) → liefert Flag-Offset **und**
   Restart-Slot. Genau 1 Treffer im `.text`.
2. **Setter-Suche**: unter allen maskierten Setter-Treffern
   (`RBBRIDGE_RESTART_SIG` — 66 Treffer, daher allein *nicht* eindeutig) wird
   derjenige gewählt, dessen dekodierter Offset zum Konsum-Offset passt
   (`restart_find_setter`) → `RequestRestart`.
3. **vtables**: aus dem Image abgeleitet (`restart_find_vtables`: QWORD == fn,
   minus Slot-Offset `0x20`) → GameplayState **und** ServerGameplayState.
4. **Instance**: QWORD-Scan (8-Byte-aligniert) nach der vtable.

`reset` schreibt `[instance+0x52A] = 1` (nur auf beschreibbar gemappte
Seiten, `restart_write_u8`). Nicht-Fund auf **jeder** Stufe →
`{"ok":false,"reason":"not_resolvable"}` — **kein** Schreibzugriff.

## Graceful-Nicht-Fund (host-getestet)

`tests/rbbridge-hosttest` deckt die reinen Helfer ab: `restart_decode`
(gültig/falsches imm/falscher Opcode/NULL/Offset 0), `restart_decode_consumer`
(offset-konsistent / inkonsistent / NULL), `restart_find_vtables`
(2 vtables, out_cap, kein Treffer, NULL), `restart_find_setter` (passender
Offset, erster Kandidat, kein Treffer → NULL, NULL → NULL).

## Offener Punkt (nur mit Player prüfbar)

Der Live-Beweis (HQ zerstören → sichtbar neue Runde) und ob der
`reset`-Write im laufenden Dedicated-Server den Map-Restart sauber auslöst,
ist **nur mit Player** prüfbar → offen (Momo/Matheo), nicht als erledigt
markiert. Host-Test, Pipe-Roundtrip und Build sind grün ohne Player.

## Referenzen

- `docs/research/dedicated-io-write-functions.md` (`FinishCurrentMission`,
  Service-vftables, Thread-Modell)
- `.agents/skills/riftbreaker-re/SKILL.md`
- `progress-281-server-reset.md` (altes Lua-`rb_reset`, #281)
