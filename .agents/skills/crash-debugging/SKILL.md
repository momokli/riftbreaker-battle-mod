---
name: crash-debugging
description: Debug a crash of the Riftbreaker dedicated server on planet. Read the crash bundle (meta.json / context.log / symbolized.txt), verify the symbolizer used the correct per-env DLL, re-symbolize, map the fault RVA to source, and blame the introducing PR. Use whenever a dev/prod server crashes and you need the root cause (not guesswork).
---

# Crash-Debugging — Riftbreaker Dedicated Server

Reproducible workflow to go from "server crashed" to "which line / which PR",
without guessing. This is the **foundation**: follow the steps in order, and
**raise** anything that is missing instead of compensating silently (see
"Raise, don't compensate" below).

## When to use

- `riftbreaker-dedicated` (dev) or `riftbreaker-dedicated-prod` crashed on planet.
- A crash bundle exists under `/opt/rbmods/crashes/` and you need the root cause.
- Someone reports "dev unstable", "prod fine" and you need to isolate which code path.

## Crash bundle anatomy

The collector (`rbmods-crash-collector.sh`, systemd) writes one dir per crash:

```
/opt/rbmods/crashes/<ts>-<uuid>/          # dev
/opt/rbmods/crashes-prod/<ts>-<uuid>/     # prod
```

Files inside:

| file | content |
|---|---|
| `<uuid>.dmp` | Windows minidump (crash memory/threads/modules) |
| `<uuid>.log` / `<uuid>.trace` | game crash log + crash-handler trace (often empty/`function-name not available`) |
| `context.log` | last N container log lines — **the bridge traffic + rbbridge `dbg()` lines live here** |
| `meta.json` | `image`, `git_sha`, `module`, `module_base`, `module_size`, `fault_address`, `fault_rva`, `module_sha256`, `module_paths`, `stack_rvas`, `symbolized{status,frames}` |
| `symbolized.txt` | symbolizer output (header + frames, one line per frame) |
| `rbbridge.dll`, `riftbreaker_dll_win_release.dll` | the DLL **bytes copied at crash time** (#588) — used to verify symbolization |

## Workflow (in this order)

### 1. Find the newest bundle

```bash
ssh -o BatchMode=yes planet 'ls -lt /opt/rbmods/crashes/ | head'
```

### 2. Read `context.log` FIRST — it answers "what was the trigger?"

```bash
ssh -o BatchMode=yes planet 'cat /opt/rbmods/crashes/<ts>-<uuid>/context.log'
```

Look for:
- `[pipe_bridge] POST /get_state body={}` — the Cockpit polls `get_state` every ~3 s.
- `[pipe_bridge] POST /get_send_log body={}` — the send-log poll.
- any WRITE command (`restart_map`, `creatures_difficulty`, `end_game`, …) — usually there is **none**; the crash is typically the automatic `get_state` read, **not** a user button.
- `[rbbridge] ...` `dbg()` lines (e.g. `resolve_hq_service: rva=… Kandidaten=… -> NULL`).
- the game log (`ServerGameplayState`, `CrashHandlerWin32`, Lua lines) right before the crash.

> The bridge logs **every** request. If a crash is triggered by a bridge command, it is visible here.

### 3. Read `meta.json` — the fault identity

```bash
ssh -o BatchMode=yes planet 'cat /opt/rbmods/crashes/<ts>-<uuid>/meta.json'
```

Key fields: `fault_rva`, `module` (which DLL faulted — usually `rbbridge.dll`),
`module_sha256`, `module_paths`, `image`, `git_sha`.

### 4. Read `symbolized.txt` — the fault frame

```bash
ssh -o BatchMode=yes planet 'cat /opt/rbmods/crashes/<ts>-<uuid>/symbolized.txt'
```

Expect exactly one line like `0x2d0c	[rbbridge.dll] FAULT <name>` (default = fault
frame only; stack scan is opt-in via `--stack-scan`).

### 5. ⚠️ VERIFY the symbolizer used the CORRECT DLL — the #1 trap

The symbolizer resolves the fault RVA against a DLL file. If that file is not
**byte-identical** to the DLL the game actually loaded, the symbol name is WRONG.

Check:
```bash
ssh -o BatchMode=yes planet '
  echo "collector copied:"; grep -A2 module_sha256 /opt/rbmods/crashes/<ts>-<uuid>/meta.json
  echo "per-env DLL:";      sha256sum /opt/rbmods/rbtools/<env>/rbbridge.dll
  echo "SizeOfImage:";      /opt/rb-re/venv/bin/python -c "import pefile; print(hex(pefile.PE(\"/opt/rbmods/rbtools/<env>/rbbridge.dll\").OPTIONAL_HEADER.SizeOfImage))"
'
```

The per-env DLL is `/opt/rbmods/rbtools/<env>/rbbridge.dll` (dev/prod/test) — **NOT**
the top-level `/opt/rbmods/rbtools/rbbridge.dll` (that is a stale legacy file).
Cross-check `SizeOfImage` against `module_size` in `meta.json` (they must match).

If the collector copied the wrong DLL (it did until #601), re-symbolize manually:

```bash
ssh -o BatchMode=yes planet 'python3 /usr/local/lib/rbmods/crash/symbolize.py \
  --dmp /opt/rbmods/crashes/<ts>-<uuid>/<uuid>.dmp \
  --dll /srv/rift-<env>/game/bin/riftbreaker_dll_win_release.dll \
  --symbolizer /usr/lib/llvm-18/bin/llvm-symbolizer \
  --dll2 /opt/rbmods/rbtools/<env>/rbbridge.dll --module2 rbbridge.dll \
  --uuid <uuid>'
```

### 6. Map fault RVA → source

```bash
grep -n "<function-name>" server/dll/rbbridge.c
```

Read the function around the fault. The fault RVA is the function entry + offset
(e.g. `0x2d0c` = `resolve_hq_service` entry + `0x2f` = the raw `q[i]` deref in the
`VirtualQuery` scan loop).

### 7. Blame — which PR introduced it

```bash
git log -S "<function-name>" --oneline origin/main -- server/dll/rbbridge.c
```

This shows the introduce / revert / re-apply commits (the `-S` "pickaxe" finds
where the string first appeared). Then decide: revert the introducing PR, or fix.

### 8. (optional) Disasm the exact fault instruction

See the `riftbreaker-re` skill (`tools/re/disasm.py <rva>`) for disasm + RVA
conversion. Only needed if you must know the exact faulting instruction.

## Pitfalls (all hit in real debugging)

- **Stale top-level `rbbridge.dll` vs per-env**: `module_sha256`/`module_paths` in an
  OLD bundle can point at `/opt/rbmods/rbtools/rbbridge.dll` (wrong). Always verify
  against `/opt/rbmods/rbtools/<env>/rbbridge.dll` + `SizeOfImage` vs `module_size`.
  A wrong DLL gives a plausible-but-wrong name (e.g. `dispatch_pause_dom` was really
  `resolve_hq_service`).
- **The crash often faults on a NON-logging path** (a scan loop deref, no `dbg()`
  before it). `context.log` alone can point at the function via earlier `dbg()`
  lines, but the symbolizer is still needed to pin the exact fault RVA.
- `llvm-pdbutil` `addr = SSSS:OOOOOOOO` is **decimal** (see `riftbreaker-re` skill).
- macOS BSD `grep` has no `-P` (CI uses GNU grep).
- `resolve_hq_service` / other vftable-scan resolvers run on the **pipe thread** and
  read game heap via `VirtualQuery` + raw qword deref — a race with the game ECS is
  a recurring crash class (see `docs/research/dedicated-io-thread-model.md`).

## Raise, don't compensate (observability)

Crash debugging is worthless if it becomes guessing. Treat **observability as a
first-class deliverable**, not an afterthought:

- If you notice a component is NOT logged (a bridge response body, a missing
  `dbg()` line, the env/commit not recorded, a DLL not captured), **open an issue**
  for it instead of working around it silently.
- Logging checklist per crash bundle: bridge request (+ response), rbbridge
  resolution `dbg()` lines, game log tail, `meta.json` (fault + `git_sha` +
  `module_sha256`), copied DLL bytes, `symbolized.txt`.
- Bundle path should eventually be `ENV/COMMIT-REF/<ts>-<uuid>` (open follow-up).

## Planet access (read-only by default)

```bash
ssh -o BatchMode=yes planet '...'
```

| what | path |
|---|---|
| crash bundles (dev / prod) | `/opt/rbmods/crashes/` , `/opt/rbmods/crashes-prod/` |
| per-env rbbridge.dll | `/opt/rbmods/rbtools/<env>/rbbridge.dll` |
| game DLL / PDB | `/srv/rift-<env>/game/bin/riftbreaker_dll_win_release.{dll,pdb}` |
| deployed collector / symbolizer | `/usr/local/bin/rbmods-crash-collector.sh` , `/usr/local/lib/rbmods/crash/symbolize.py` |
| container mounts | `docker inspect riftbreaker-dedicated --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'` |

## References

- RE/disasm/RVA: `riftbreaker-re` skill (`tools/re/disasm.py`, `llvm-pdbutil`).
- Collector/symbolizer code: `deploy/crash-collector/` + `deploy/roles/crash-collector/`.
- Thread model (why off-thread reads crash): `docs/research/dedicated-io-thread-model.md`.
