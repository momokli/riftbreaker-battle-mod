# S1 — Root-Pointer für World/Services (Vorarbeit, Issue #1035)

Status: **gelöst auf Disasm-Ebene** — scan-freier `World*`-Root gefunden und
verifiziert; **Live-Beweis noch offen** (braucht Deploy).

Build **2.0.58485**, PDB+DLL: `/opt/rb-re/bin/riftbreaker_dll_win_release.{pdb,dll}`
(planet). Kontext: RFC `docs/rfc/0001-dedicated-io-state-pipeline.md`; Problem:
`get_state` löst heute **pro Poll** Service-Instanzen über einen
Full-Process-Heap-Scan auf (`server/dll/rbbridge.c:6180`, `:6033`).

## Ergebnis (TL;DR)

**Der Game-Thread-Hook hat den Root schon in der Hand.** `rbbridge.c` hängt
bereits `Riftbreaker::GameplayState::UpdateGameplayLogic` ab
(`RBBRIDGE_RVA_GAMEPLAY_UPDLOGIC = 0x1A1C100`, `install_game_pause_hook`,
`server/dll/rbbridge.c:5671`). Dort ist `self` die `GameplayState`-Instanz
(auf dem Dedi konkret `ServerGameplayState`), und daraus ist `World*`
**scan-frei** lesbar:

```
World* = *(void**)((char*)self + 0x358 + WorldType * 0x58)
```

⇒ Kein Heap-Scan, kein off-thread Game-Call, ganz auf dem Game-Thread
(genau RFC §4.2). Über `World::GetSystem<T>()` (bekannt: `LuaSystem`
`0x194EDA0`) sind die Services (PlayerService/HealthService/FindService)
erreichbar.

## Beweis (Disasm, lokal)

`GameplayState::GetWorldState` **RVA `0x1A04C30`**:

```
add  rcx, 0x358          ; this += 0x358   (eingebetteter WorldStatesHolder)
jmp  0x18F1970           ; = WorldStatesHolder::GetWorldState(WorldType)
```

`WorldStatesHolder::GetWorldState` **RVA `0x18F1970`**:
`movsxd rax, edx ; imul rax,rax,0x58 ; add rax,rcx ; ret`
⇒ Elementgröße **0x58**.

`WorldStatesHolder::GetWorld(WorldType)` **RVA `0x18F1960`**:

```
movsxd rax, edx
imul   rax, rax, 0x58
mov    rax, [rax + rcx]  ; World* steht an Offset 0 des Elements
ret
```

**Kombiniert:** `GameplayState + 0x358` = das WorldStatesHolder-Array;
`World* = *(GameplayState + 0x358 + type*0x58)`.

### Warum das auf `ServerGameplayState` gilt (doppelt belegt)

1. **Die gehookte Funktion selbst nutzt `+0x358`:** Disasm von
   `UpdateGameplayLogic` (`0x1A1C100`) zeigt `mov r14, rcx` (= `this`) und
   `lea r13, [r14 + 0x358]` — derselbe Zeiger, den `GameplayState::GetWorldState`
   als WorldStatesHolder verwendet. In der Funktion wird `r13` dann
   mehrfach an Aufrufe übergeben. ⇒ `this + 0x358` ist **derselbe** Holder.
2. **Offset-Konsistenz:** Der Hook nutzt auf demselben `self` bereits
   `RBBRIDGE_SGS_PAUSEFLAG_OFF = 0x534`, `RBBRIDGE_SGS_PAUSEBITS_OFF = 0x35CC`,
   `SESSIONS = 0x768`, und `GameplayState::GetWorldState` nutzt `this+0x358 /
0x530 / 0x549` — konsistentes Layout (GameplayState-Basis bei Offset 0,
   `ServerGameplayState` als Subklasse). `GetWorldState` ist **nicht**
   überschrieben (nur `GameplayState` und `WorldStatesHolder` definieren es).

### `WorldType` (Index)

Enum-Werte sind nicht aus dem PDB auflösbar (Typeinfo gestrippt). Der Konsument
liest den Slot also robust: `type = 0..3` durchgehen und den **nicht-NULL**
`World*` nehmen, oder direkt `WorldStatesHolder::GetWorld(self+0x358, type)`
(`0x18F1960`) aufrufen (Mini-Funktion, pure read).

## Belegte RVAs (Build 2.0.58485)

RVA-Formel: `.text` = `0x1000` + dez. Offset, `.data` = `0x3EE8000` + Off.

| Symbol                                                             | Sektion:Offset  | RVA             | Rolle                                    |
| ------------------------------------------------------------------ | --------------- | --------------- | ---------------------------------------- |
| `Riftbreaker::GameplayState::UpdateGameplayLogic(float,float,u64)` | `0001:27373824` | **`0x1A1C100`** | **Hook-Target** → `self` = GameplayState |
| `Riftbreaker::GameplayState::GetWorldState(WorldType)`             | `0001:27278384` | `0x1A04C30`     | `(this+0x358) + type*0x58`               |
| `Riftbreaker::WorldStatesHolder::GetWorld(WorldType)`              | `0001:26151264` | `0x18F1960`     | `*(this + type*0x58)`                    |
| `Exor::World::GetSystem<Exor::LuaSystem>()`                        | —               | `0x194EDA0`     | World → System (bekannt)                 |

### Nebenbefund (für S3/S6 nutzbar)

`Exor::Singleton<T>::GetSingleton()` legt die Instanz in einem
**funktionslokalen Static** ab, dessen Adresse im PDB steht → Exor-Singletons
scan-frei per einem Deref lesbar:

| Static                                         | Sektion:Offset | RVA         |
| ---------------------------------------------- | -------------- | ----------- |
| `Exor::Singleton<FrameTimeTracker>::singleton` | `0003:9424296` | `0x47E4DA8` |
| `Exor::Singleton<Profiler>::singleton`         | `0003:9396896` | `0x47DE2A0` |
| `Exor::ApplicationAbstract::ms_singleton`      | `0003:9635808` | `0x48187E0` |

(Für die Services selbst irrelevant — die sind World-Systeme —, aber nützlich
für Tick/Clock (S3) und Observability (S6).)

## Live-Beweis (offen)

Der Root wird **im Hook** gelesen, wenn ein `get_state`/Snapshot das nächste Mal
läuft — `dbg("snapshot: self=%p world=%p", self, world)` genügt (read-only,
Game-Thread, kein Game-Call). Blocker: Die frühere Dev-Umgebung
(`/opt/rbmods/rbtools/dev`) existiert **nicht mehr** (dev/staging abgebaut);
Deploy müsste auf einen Test-/`880`-Slot oder einen CI-Boot-Test-Stack, um prod
nicht zu stören. Externer Read ist keine Option (`/proc/<pid>/mem` → EIO unter
Wine/non-dumpable, `yama.ptrace_scope=1`, kein `gdb`/`lldb`), und der
GameplayState-Zeiger ist von außen ohne Hook ohnehin nicht erreichbar.

## Empfehlung / nächste Schritte

- [ ] **Probe** minimal in `gameplay_updlogic_hook`: `world = *(self+0x358+type*0x58)`
      einmalig `dbg()`-loggen (read-only) → Live-Beweis auf einem Test-Slot.
- [ ] Ergebnis → RFC §4.3 erfüllt: `World*` kommt **scan-frei** aus dem Hook;
      Services via `World::GetSystem<T>()`. Der Epoch-/Revalidierungs-Aufwand
      entfällt für den Snapshot-Pfad (der Hook hat den Zeiger ohnehin).
- [ ] Fallback nur noch nötig für Pfade außerhalb des Game-Threads (Pipe-Thread
      liest den fertigen Snapshot, RFC §4.4).
