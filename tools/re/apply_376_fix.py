#!/usr/bin/env python3
"""Issue #376 fix: resolve_lua_state ohne racy GetSystem<LuaSystem>().

Der v4-Ansatz crashte beim Boot (page fault in World::GetSystem(TypeHash),
RVA 0x1CDB3C4), weil der pipe thread waehrend des Boots die System-Map des
World las, waehrend der game thread sie noch aufbaute. Fix: lua_State* per
Memory-Read (LuaGraphNode-vftable-Scan + [0x20] luabind-object) holen.
"""

import io
import sys

PATH = "bausteine/rbbridge/dll/rbbridge.c"

# 1) Defines ergaenzen (nach LUA_GLOBALSINDEX).
OLD_DEF = "#define RBBRIDGE_LUA_GLOBALSINDEX     (-10002)\n"
NEW_DEF = (
    "#define RBBRIDGE_LUA_GLOBALSINDEX     (-10002)\n"
    "#define RBBRIDGE_LUAGRAPHNODE_VFTABLE_RVA 0x2F46D70u\n"
    "#define RBBRIDGE_LUAGRAPHNODE_OBJECT_OFF  0x20u\n"
)

# 2) resolve_lua_state ersetzen.
OLD_RESOLVE = r"""/* lua_State* aus World -> LuaSystem -> Lua (RE #376). */
static void *resolve_lua_state(const unsigned char *base, void *world)
{
    if (!world)
        return NULL;
    void *(*get_luasystem)(void *) =
        (void *(*)(void *))(uintptr_t)(base + 0x194EDA0);
    void *luasys = get_luasystem(world);
    if (!luasys)
        return NULL;
    uint64_t lua_obj = 0;
    if (!safe_read_u64((const unsigned char *)luasys + 0x200, &lua_obj) ||
        !lua_obj)
        return NULL;
    uint64_t L = 0;
    if (!safe_read_u64((const unsigned char *)(uintptr_t)lua_obj + 0x10, &L) ||
        !L)
        return NULL;
    return (void *)(uintptr_t)L;
}
"""

NEW_RESOLVE = r"""/* lua_State* via LuaGraphNode-Instanz: vftable-Scan + [0x20] luabind-object
 * (reine Memory-Reads). NICHT World::GetSystem<LuaSystem>() - das liest die
 * System-Map des World und ract beim Boot mit dem game thread (page fault in
 * World::GetSystem(TypeHash), s. Issue #376). */
static void *resolve_lua_state(const unsigned char *base)
{
    const uint64_t needle =
        (uint64_t)(uintptr_t)(base + RBBRIDGE_LUAGRAPHNODE_VFTABLE_RVA);
    uintptr_t addr = 0;

    for (;;) {
        MEMORY_BASIC_INFORMATION mi;
        if (VirtualQuery((const void *)addr, &mi, sizeof(mi)) == 0)
            break;
        uintptr_t next = (uintptr_t)mi.BaseAddress + mi.RegionSize;
        if (next <= addr)
            break;
        addr = next;
        if (!is_readable_region(&mi))
            continue;

        const uint64_t *q = (const uint64_t *)mi.BaseAddress;
        size_t nq = mi.RegionSize / sizeof(uint64_t);
        for (size_t i = 0; i < nq; i++) {
            if (q[i] != needle)
                continue;
            const unsigned char *inst = (const unsigned char *)&q[i];
            uint64_t L = 0;
            if (safe_read_u64(inst + RBBRIDGE_LUAGRAPHNODE_OBJECT_OFF, &L) && L)
                return (void *)(uintptr_t)L;
        }
    }
    return NULL;
}
"""

# 3) register_dom_capture: Signatur + resolve-Aufruf.
OLD_REG = "static void register_dom_capture(const unsigned char *base, void *world)\n"
NEW_REG = "static void register_dom_capture(const unsigned char *base)\n"

OLD_REG_CALL = "    void *L = resolve_lua_state(base, world);\n"
NEW_REG_CALL = "    void *L = resolve_lua_state(base);\n"

# 4) dispatch_get_state: Aufruf anpassen.
OLD_DISPATCH_CALL = "    register_dom_capture(base, (void *)(uintptr_t)world);\n"
NEW_DISPATCH_CALL = "    register_dom_capture(base);\n"


def main():
    with io.open(PATH, "r", encoding="utf-8") as f:
        src = f.read()

    edits = [
        (OLD_DEF, NEW_DEF),
        (OLD_RESOLVE, NEW_RESOLVE),
        (OLD_REG, NEW_REG),
        (OLD_REG_CALL, NEW_REG_CALL),
        (OLD_DISPATCH_CALL, NEW_DISPATCH_CALL),
    ]
    for i, (old, new) in enumerate(edits):
        if old not in src:
            print("FEHLER: Edit %d nicht gefunden: %r" % (i, old[:60]), file=sys.stderr)
            return 1
        src = src.replace(old, new, 1)

    with io.open(PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("OK: resolve_lua_state -> Memory-Read (kein GetSystem).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
