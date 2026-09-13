#!/usr/bin/env python3
"""Issue #376 (v3, thread-sicher): DOM-Capture via Game-Thread-Hook.

Der Mod (game thread) ruft pro Frame _G.rbbridge_capture_dom(wave, time_to_next)
auf; die hier registrierte C-Funktion cached die Werte atomar. dispatch_get_state
(pipe thread) liest NUR die Globals - kein Lua-Zugriff vom pipe thread (der
direkte Read in v1/v2 crashte den Server, s. SKILL riftbreaker-re).
"""

import io
import sys

PATH = "bausteine/04-trainer-io/rbbridge/rbbridge.c"

HELPER = r"""/* ====================================================================== */
/* DOM-Wellen-Counter + time-to-next (Issue #376): thread-sichere Variante  */
/*                                                                          */
/* Erkenntnis aus dem ersten Entwurf: Lua NICHT vom pipe thread anfassen -   */
/* das crasht den Server (LUA CRASH "attempt to call a nil value" in         */
/* survival_jungle.lua, winedbg --auto; s. .agents/skills/riftbreaker-re).   */
/* Die Werte (currentDifficultyLevel + state-abhaengiger Countdown) liegen   */
/* Lua-seitig in der Klasse dom_mananger (ein LuaGraphNode).                 */
/*                                                                          */
/* Deshalb: der Mod (rbbattle_autoexec.lua) wrappt dom_mananger:Update auf   */
/* dem GAME thread und ruft pro Frame die hier registrierte C-Funktion       */
/* _G.rbbridge_capture_dom(wave, time_to_next) auf. Diese schreibt die Werte */
/* atomar in Globals; dispatch_get_state (pipe thread) liest NUR die Globals */
/* - kein Lua-Zugriff mehr vom pipe thread.                                  */
/*                                                                          */
/* RE (Build 2.0.58485, verifiziert, s. .agents/skills/riftbreaker-re):      */
/*   World::GetSystem<LuaSystem>()   RVA 0x194EDA0  (this=World*)            */
/*   LuaSystem + 0x200 = Exor::Lua* ; Lua + 0x10 = lua_State*                 */
/*   lua_pushcclosure 0x290CBE0   lua_setfield  0x290D260                      */
/*   lua_tointeger    0x290D670   lua_gettop    0x290C710                      */
/*   LUA_GLOBALSINDEX = -10002 (0xFFFFD8EE)                                   */
/* ====================================================================== */

typedef int (__fastcall *rbbridge_lua_cfunction)(void *L);
typedef void (__fastcall *rbbridge_lua_pushcclosure_fn)(void *L,
                                                        rbbridge_lua_cfunction fn,
                                                        int n);
typedef void (__fastcall *rbbridge_lua_setfield_fn)(void *L, int idx,
                                                    const char *k);
typedef long long (__fastcall *rbbridge_lua_tointeger_fn)(void *L, int idx);
typedef int (__fastcall *rbbridge_lua_gettop_fn)(void *L);

#define RBBRIDGE_LUA_PUSHCCLOSURE_RVA 0x290CBE0u
#define RBBRIDGE_LUA_SETFIELD_RVA     0x290D260u
#define RBBRIDGE_LUA_TOINTEGER_RVA    0x290D670u
#define RBBRIDGE_LUA_GETTOP_RVA       0x290C710u
#define RBBRIDGE_LUA_GLOBALSINDEX     (-10002)

/* Atomarer DOM-Snapshot: game thread schreibt (capture), pipe thread liest. */
static const unsigned char *g_dom_base = NULL;
static volatile LONG g_dom_wave = -1;
static volatile LONG g_dom_time_to_next = -1;
static volatile LONG g_dom_capture_registered = 0;

/* Vom Mod (game thread) pro Frame aufgerufen: rbbridge_capture_dom(wave, ttn). */
static int rbbridge_capture_dom(void *L)
{
    if (!g_dom_base)
        return 0;
    rbbridge_lua_tointeger_fn tointeger =
        (rbbridge_lua_tointeger_fn)(uintptr_t)(g_dom_base +
                                               RBBRIDGE_LUA_TOINTEGER_RVA);
    rbbridge_lua_gettop_fn gettop =
        (rbbridge_lua_gettop_fn)(uintptr_t)(g_dom_base +
                                            RBBRIDGE_LUA_GETTOP_RVA);
    if (gettop(L) >= 2) {
        InterlockedExchange(&g_dom_wave, (LONG)tointeger(L, 1));
        InterlockedExchange(&g_dom_time_to_next, (LONG)tointeger(L, 2));
    }
    return 0;
}

/* lua_State* aus World -> LuaSystem -> Lua (RE #376). */
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

/* Registriert _G.rbbridge_capture_dom einmalig (lazy, retry bis ok). */
static void register_dom_capture(const unsigned char *base, void *world)
{
    if (g_dom_capture_registered)
        return;
    void *L = resolve_lua_state(base, world);
    if (!L)
        return;
    rbbridge_lua_pushcclosure_fn pushcclosure =
        (rbbridge_lua_pushcclosure_fn)(uintptr_t)(base +
                                                  RBBRIDGE_LUA_PUSHCCLOSURE_RVA);
    rbbridge_lua_setfield_fn setfield =
        (rbbridge_lua_setfield_fn)(uintptr_t)(base +
                                              RBBRIDGE_LUA_SETFIELD_RVA);
    g_dom_base = base;
    pushcclosure(L, rbbridge_capture_dom, 0);
    setfield(L, RBBRIDGE_LUA_GLOBALSINDEX, "rbbridge_capture_dom");
    g_dom_capture_registered = 1;
    dbg("register_dom_capture: _G.rbbridge_capture_dom registriert (L=%p)", L);
}

"""

ANCHOR_FN = "static void dispatch_get_state(HANDLE hPipe)\n"

# world ist bereits aufgeloest: register_dom_capture danach einhaengen.
OLD_WORLD = r"""    void *(*gpa)(void *, unsigned int) =
        (void *(*)(void *, unsigned int))(uintptr_t)(base + 0xC60050);
    void *account = gpa((void *)(uintptr_t)world, 0);"""

NEW_WORLD = r"""    /* #376: DOM-Capture (game thread -> Globals) einmalig registrieren. */
    register_dom_capture(base, (void *)(uintptr_t)world);

    void *(*gpa)(void *, unsigned int) =
        (void *(*)(void *, unsigned int))(uintptr_t)(base + 0xC60050);
    void *account = gpa((void *)(uintptr_t)world, 0);"""

OLD_TAIL = r"""    int64_t carbonium_max = read_resource_max(base, account, 0x659cc791);

    send_line(hPipe,
              "{\"event\":\"get_state_result\",\"ok\":true,"
              "\"carbonium\":%llu,\"carbonium_max\":%lld,\"resources\":%s}",
              (unsigned long long)carbonium, (long long)carbonium_max,
              resources);
}"""

NEW_TAIL = r"""    int64_t carbonium_max = read_resource_max(base, account, 0x659cc791);

    /* DOM-Snapshot aus den atomaren Globals (game thread schreibt). */
    int wave = (int)g_dom_wave;
    int time_to_next = (int)g_dom_time_to_next;

    send_line(hPipe,
              "{\"event\":\"get_state_result\",\"ok\":true,"
              "\"carbonium\":%llu,\"carbonium_max\":%lld,\"resources\":%s,"
              "\"wave\":%d,\"time_to_next\":%d}",
              (unsigned long long)carbonium, (long long)carbonium_max,
              resources, wave, time_to_next);
}"""


def main():
    with io.open(PATH, "r", encoding="utf-8") as f:
        src = f.read()

    if "rbbridge_capture_dom" in src:
        print("rbbridge_capture_dom bereits vorhanden - nichts zu tun.")
        return 0
    for name, anchor in (("dispatch_get_state", ANCHOR_FN), ("world-block", OLD_WORLD), ("get_state_result", OLD_TAIL)):
        if anchor not in src:
            print("FEHLER: Anker '%s' nicht gefunden." % name, file=sys.stderr)
            return 1

    src = src.replace(ANCHOR_FN, HELPER + ANCHOR_FN, 1)
    src = src.replace(OLD_WORLD, NEW_WORLD, 1)
    src = src.replace(OLD_TAIL, NEW_TAIL, 1)

    with io.open(PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("OK: thread-sichere DOM-Capture in rbbridge.c.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
