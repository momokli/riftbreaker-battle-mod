#!/usr/bin/env python3
"""Issue #378: C++ state egress via ConsoleService::Update detour.

Ersetzt den Mod-Lua-Hook (_G.rbbridge_capture_state) durch einen reinen
C++-Read auf dem Game-Thread: der Detour auf ConsoleService::Update loest die
dom_mananger-Instanz (LuaGraphNode) auf, liest das Lua-self-table via lua C API
und baut den JSON-Snapshot (Spiegel von BuildStateJson) selbst. Der String wird
spinlock-guarded in g_dom_state_buf gecached; dispatch_get_state liest nur den
Cache. KEIN Lua-Zugriff vom pipe thread.

Die .c-Datei wird NUR hier editiert (io.open(..., newline="\n")), nie per
Editor/write_file (clang-format-Kollaps).
"""

import io
import sys

PATH = "server/dll/rbbridge.c"

# ---------------------------------------------------------------------------
# 1) Detour: READ-Pfad ergaenzen (Forward-Decl + Aufruf).
# ---------------------------------------------------------------------------
OLD_DETOUR = """/* Detour fuer ConsoleService::Update(float): laeuft auf dem Game-Thread. */
static void __fastcall detour_console_update(void *self, float dt)
{
    drain_pending_commands();
    if (g_original_update)
        g_original_update(self, dt);
}
"""

NEW_DETOUR = """/* Detour fuer ConsoleService::Update(float): laeuft auf dem Game-Thread.
 * Neben dem WRITE-Pfad (pending commands) wird hier auch der DOM-State
 * gelesen und gecached (READ-Pfad, Issue #378). */
static void capture_dom_state_game_thread(void);

static void __fastcall detour_console_update(void *self, float dt)
{
    drain_pending_commands();
    capture_dom_state_game_thread();
    if (g_original_update)
        g_original_update(self, dt);
}
"""

# ---------------------------------------------------------------------------
# 2) dispatch_get_state: statt Mod-Hook-Registrierung den Update-Detour
#    installieren (damit der Game-Thread den C++-Read ausfuehrt).
# ---------------------------------------------------------------------------
OLD_DISPATCH = """    /* #376: DOM-Capture (game thread -> Cache) einmalig registrieren. */
    register_dom_capture(base);
"""

NEW_DISPATCH = """    /* #378: C++-Egress — Update-Detour installieren, damit der Game-Thread
     * den DOM-State liest und in den Cache schreibt. */
    {
        console_exec_fn cfn = NULL;
        void *cinst = NULL;
        if (resolve_console_service(&cfn, &cinst))
            install_update_hook();
    }
"""

# ---------------------------------------------------------------------------
# 3) Neuer C++-Read-Block (ersetzt den kompletten Mod-Hook-Block).
# ---------------------------------------------------------------------------
NEW_BLOCK = """/* ====================================================================== */
/* DOM/Voll-State-Egress (Issue #378): C++-Direkt-Read auf dem Game-Thread.    */
/*                                                                             */
/* Der Detour auf ConsoleService::Update (install_update_hook) laeuft jede     */
/* Frame auf dem GAME thread. Hier wird die dom_mananger-Instanz               */
/* (LuaGraphNode, vftable RVA 0x2F46D70) aufgeloest, das Lua-self-table via    */
/* lua C API gelesen und ein JSON-Snapshot (Spiegel von BuildStateJson)        */
/* gebaut. Der String wird spinlock-guarded in g_dom_state_buf gecached;       */
/* dispatch_get_state (pipe thread) liest nur den Cache.                       */
/*                                                                             */
/* KEIN Lua-Zugriff vom pipe thread (crasht, s. SKILL riftbreaker-re).         */
/*                                                                             */
/* RE (Build 2.0.58485, verifiziert):                                          */
/*   LuaGraphNode vftable 0x2F46D70, +0x20 = luabind::object{lua_State*, ref} */
/*   lua_rawgeti 0x290CFA0  lua_getfield 0x290C550  lua_tonumber 0x290D790     */
/*   lua_settop 0x290D4D0  lua_gettop 0x290C710  lua_pushvalue 0x290CF20       */
/*   lua_tolstring 0x290D6D0  lua_toboolean 0x290D600  lua_objlen 0x290CA10    */
/*   lua_pcall 0x290CAC0  lua_type 0x290D880  lua_pushstring 0x290CE40         */
/*   LUA_REGISTRYINDEX = -10000; LUA_GLOBALSINDEX = -10002                     */
/* ====================================================================== */

typedef void (__fastcall *rbbridge_lua_rawgeti_fn)(void *L, int idx, int n);
typedef void (__fastcall *rbbridge_lua_getfield_fn)(void *L, int idx,
                                                    const char *k);
typedef double (__fastcall *rbbridge_lua_tonumber_fn)(void *L, int idx);
typedef void (__fastcall *rbbridge_lua_settop_fn)(void *L, int idx);
typedef int (__fastcall *rbbridge_lua_gettop_fn)(void *L);
typedef void (__fastcall *rbbridge_lua_pushvalue_fn)(void *L, int idx);
typedef const char *(__fastcall *rbbridge_lua_tolstring_fn)(void *L, int idx,
                                                            size_t *len);
typedef int (__fastcall *rbbridge_lua_toboolean_fn)(void *L, int idx);
typedef size_t (__fastcall *rbbridge_lua_objlen_fn)(void *L, int idx);
typedef int (__fastcall *rbbridge_lua_pcall_fn)(void *L, int nargs, int nresults,
                                                int errfunc);
typedef int (__fastcall *rbbridge_lua_type_fn)(void *L, int idx);
typedef void (__fastcall *rbbridge_lua_pushstring_fn)(void *L, const char *s);

#define RBBRIDGE_LUA_RAWGETI_RVA    0x290CFA0u
#define RBBRIDGE_LUA_GETFIELD_RVA   0x290C550u
#define RBBRIDGE_LUA_TONUMBER_RVA   0x290D790u
#define RBBRIDGE_LUA_SETTOP_RVA     0x290D4D0u
#define RBBRIDGE_LUA_GETTOP_RVA     0x290C710u
#define RBBRIDGE_LUA_PUSHVALUE_RVA  0x290CF20u
#define RBBRIDGE_LUA_TOLSTRING_RVA  0x290D6D0u
#define RBBRIDGE_LUA_TOBOOLEAN_RVA  0x290D600u
#define RBBRIDGE_LUA_OBJLEN_RVA     0x290CA10u
#define RBBRIDGE_LUA_PCALL_RVA      0x290CAC0u
#define RBBRIDGE_LUA_TYPE_RVA       0x290D880u
#define RBBRIDGE_LUA_PUSHSTRING_RVA 0x290CE40u
#define RBBRIDGE_LUA_REGISTRYINDEX  (-10000)
#define RBBRIDGE_LUA_GLOBALSINDEX   (-10002)
#define RBBRIDGE_LUA_TNIL           0
#define RBBRIDGE_LUA_TBOOLEAN       1
#define RBBRIDGE_LUA_TNUMBER        3
#define RBBRIDGE_LUA_TSTRING        4
#define RBBRIDGE_LUA_TTABLE         5
#define RBBRIDGE_LUA_TUSERDATA      7
#define RBBRIDGE_LUAGRAPHNODE_VFTABLE_RVA 0x2F46D70u
#define RBBRIDGE_LUAGRAPHNODE_OBJECT_OFF  0x20u
#define RBBRIDGE_LUAGRAPHNODE_REF_OFF     0x28u

/* Aufgeloeste lua_* C-API-Funktionszeiger (einmalig, game thread). */
typedef struct {
    rbbridge_lua_rawgeti_fn    rawgeti;
    rbbridge_lua_getfield_fn   getfield;
    rbbridge_lua_tonumber_fn   tonumber;
    rbbridge_lua_settop_fn     settop;
    rbbridge_lua_gettop_fn     gettop;
    rbbridge_lua_pushvalue_fn  pushvalue;
    rbbridge_lua_tolstring_fn  tolstring;
    rbbridge_lua_toboolean_fn  toboolean;
    rbbridge_lua_objlen_fn     objlen;
    rbbridge_lua_pcall_fn      call;
    rbbridge_lua_type_fn       type;
    rbbridge_lua_pushstring_fn pushstring;
} dom_lua_api_t;

static dom_lua_api_t g_lua;

/* DOM-Instanz-Cache + Snapshot (game thread schreibt, pipe thread liest). */
static const unsigned char *g_dom_base = NULL;
static void *g_dom_lua = NULL;
static int g_dom_ref = -1;
static volatile LONG g_dom_cache_valid = 0;
static uint64_t g_dom_next_resolve_tick = 0;

static char g_dom_state_buf[RESP_BUF_SIZE];
static volatile LONG g_dom_state_lock = 0;

/* Loest die dom_mananger-Instanz (LuaGraphNode) auf: vftable-Scan + [0x20]
 * luabind-object {lua_State*, int registry-ref}. Reine Memory-Reads, einmal
 * gecached. Rueckgabe 1 = ok, 0 = noch nicht aufloesbar. */
static int resolve_dom_instance(void)
{
    if (g_dom_cache_valid)
        return 1;

    uint64_t now = GetTickCount64();
    if (now < g_dom_next_resolve_tick)
        return 0;
    g_dom_next_resolve_tick = now + 1000; /* hoechstens 1x/Sekunde scannen */

    const unsigned char *base = NULL;
    size_t size = 0;
    const char *via = NULL;
    const unsigned char *execfn = NULL;
    if (!resolve_module(&base, &size, &via, &execfn))
        return 0;

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
            uint32_t ref32 = 0;
            if (safe_read_u64(inst + RBBRIDGE_LUAGRAPHNODE_OBJECT_OFF, &L) &&
                L &&
                safe_read_u32(inst + RBBRIDGE_LUAGRAPHNODE_REF_OFF, &ref32) &&
                (int)ref32 >= 0) {
                g_dom_base = base;
                g_dom_lua = (void *)(uintptr_t)L;
                g_dom_ref = (int)ref32;
                g_lua.rawgeti = (rbbridge_lua_rawgeti_fn)(uintptr_t)(
                    base + RBBRIDGE_LUA_RAWGETI_RVA);
                g_lua.getfield = (rbbridge_lua_getfield_fn)(uintptr_t)(
                    base + RBBRIDGE_LUA_GETFIELD_RVA);
                g_lua.tonumber = (rbbridge_lua_tonumber_fn)(uintptr_t)(
                    base + RBBRIDGE_LUA_TONUMBER_RVA);
                g_lua.settop = (rbbridge_lua_settop_fn)(uintptr_t)(
                    base + RBBRIDGE_LUA_SETTOP_RVA);
                g_lua.gettop = (rbbridge_lua_gettop_fn)(uintptr_t)(
                    base + RBBRIDGE_LUA_GETTOP_RVA);
                g_lua.pushvalue = (rbbridge_lua_pushvalue_fn)(uintptr_t)(
                    base + RBBRIDGE_LUA_PUSHVALUE_RVA);
                g_lua.tolstring = (rbbridge_lua_tolstring_fn)(uintptr_t)(
                    base + RBBRIDGE_LUA_TOLSTRING_RVA);
                g_lua.toboolean = (rbbridge_lua_toboolean_fn)(uintptr_t)(
                    base + RBBRIDGE_LUA_TOBOOLEAN_RVA);
                g_lua.objlen = (rbbridge_lua_objlen_fn)(uintptr_t)(
                    base + RBBRIDGE_LUA_OBJLEN_RVA);
                g_lua.call = (rbbridge_lua_pcall_fn)(uintptr_t)(
                    base + RBBRIDGE_LUA_PCALL_RVA);
                g_lua.type = (rbbridge_lua_type_fn)(uintptr_t)(
                    base + RBBRIDGE_LUA_TYPE_RVA);
                g_lua.pushstring = (rbbridge_lua_pushstring_fn)(uintptr_t)(
                    base + RBBRIDGE_LUA_PUSHSTRING_RVA);
                g_dom_cache_valid = 1;
                dbg("resolve_dom_instance: L=%p ref=%d", (void *)(uintptr_t)L,
                    (int)ref32);
                return 1;
            }
        }
    }
    return 0;
}

/* ---------- Minimaler JSON-Builder (flache Felder, wie BuildStateJson) ---- */

typedef struct {
    char *buf;
    size_t cap;
    size_t len;
    int first;
} json_buf_t;

static void jb_add(json_buf_t *jb, const char *s)
{
    size_t sl = strlen(s);
    if (jb->len + sl >= jb->cap)
        return;
    memcpy(jb->buf + jb->len, s, sl);
    jb->len += sl;
}

static void jb_pair(json_buf_t *jb, const char *pair)
{
    if (!jb->first)
        jb_add(jb, ",");
    jb->first = 0;
    jb_add(jb, pair);
}

static void jb_num(json_buf_t *jb, const char *key, double v)
{
    char pair[96];
    snprintf(pair, sizeof(pair), "\\"%s\\":%.14g", key, v);
    jb_pair(jb, pair);
}

static void jb_bool(json_buf_t *jb, const char *key, int v)
{
    char pair[96];
    snprintf(pair, sizeof(pair), "\\"%s\\":%s", key, v ? "true" : "false");
    jb_pair(jb, pair);
}

static void jb_str(json_buf_t *jb, const char *key, const char *v)
{
    char pair[160];
    snprintf(pair, sizeof(pair), "\\"%s\\":\\"%s\\"", key, v);
    jb_pair(jb, pair);
}

/* ---------- Lua-Feld-Reader (nur auf dem Game-Thread!) -------------------- */

static void dom_push_self(void *L, int ref)
{
    g_lua.rawgeti(L, RBBRIDGE_LUA_REGISTRYINDEX, ref);
}

static int dom_read_number(void *L, int ref, const char *name, double *out)
{
    int top = g_lua.gettop(L);
    dom_push_self(L, ref);
    g_lua.getfield(L, -1, name);
    int ok = (g_lua.type(L, -1) == RBBRIDGE_LUA_TNUMBER);
    if (ok)
        *out = g_lua.tonumber(L, -1);
    g_lua.settop(L, top);
    return ok;
}

static int dom_read_bool(void *L, int ref, const char *name, int *out)
{
    int top = g_lua.gettop(L);
    dom_push_self(L, ref);
    g_lua.getfield(L, -1, name);
    int ok = (g_lua.type(L, -1) == RBBRIDGE_LUA_TBOOLEAN);
    if (ok)
        *out = g_lua.toboolean(L, -1);
    g_lua.settop(L, top);
    return ok;
}

static int dom_read_table_len(void *L, int ref, const char *name, size_t *out)
{
    int top = g_lua.gettop(L);
    dom_push_self(L, ref);
    g_lua.getfield(L, -1, name);
    int ok = (g_lua.type(L, -1) == RBBRIDGE_LUA_TTABLE);
    if (ok)
        *out = g_lua.objlen(L, -1);
    g_lua.settop(L, top);
    return ok;
}

/* self[name]:method() -> String (State-Name). Rueckgabe 1 = String gelesen. */
static int dom_call_method_str(void *L, int ref, const char *name,
                               const char *method, char *out, size_t out_sz)
{
    int top = g_lua.gettop(L);
    int ok = 0;
    dom_push_self(L, ref);
    g_lua.getfield(L, -1, name);
    int obj = g_lua.gettop(L);
    g_lua.getfield(L, obj, method);
    g_lua.pushvalue(L, obj);
    if (g_lua.call(L, 1, 1, 0) == 0 &&
        g_lua.type(L, -1) == RBBRIDGE_LUA_TSTRING) {
        size_t len = 0;
        const char *s = g_lua.tolstring(L, -1, &len);
        if (s && len > 0) {
            if (len >= out_sz)
                len = out_sz - 1;
            memcpy(out, s, len);
            out[len] = '\\0';
            ok = 1;
        }
    }
    g_lua.settop(L, top);
    return ok;
}

/* rbStateRemaining: self[field]:GetState(name) -> GetDurationLimit-GetDuration. */
static int dom_state_remaining(void *L, int ref, const char *field,
                               const char *state_name, double *out)
{
    int top = g_lua.gettop(L);
    dom_push_self(L, ref);
    g_lua.getfield(L, -1, field);
    int sm = g_lua.gettop(L);

    /* s = sm:GetState(state_name) */
    g_lua.getfield(L, sm, "GetState");
    g_lua.pushvalue(L, sm);
    g_lua.pushstring(L, state_name);
    if (g_lua.call(L, 2, 1, 0) != 0) {
        g_lua.settop(L, top);
        return 0;
    }
    int st = g_lua.gettop(L);
    int t = g_lua.type(L, st);
    if (t != RBBRIDGE_LUA_TTABLE && t != RBBRIDGE_LUA_TUSERDATA) {
        g_lua.settop(L, top);
        return 0;
    }

    /* lim = s:GetDurationLimit() */
    g_lua.getfield(L, st, "GetDurationLimit");
    g_lua.pushvalue(L, st);
    if (g_lua.call(L, 1, 1, 0) != 0 ||
        g_lua.type(L, -1) != RBBRIDGE_LUA_TNUMBER) {
        g_lua.settop(L, top);
        return 0;
    }
    double lim = g_lua.tonumber(L, -1);
    g_lua.settop(L, st);

    /* dur = s:GetDuration() */
    g_lua.getfield(L, st, "GetDuration");
    g_lua.pushvalue(L, st);
    if (g_lua.call(L, 1, 1, 0) != 0 ||
        g_lua.type(L, -1) != RBBRIDGE_LUA_TNUMBER) {
        g_lua.settop(L, top);
        return 0;
    }
    double dur = g_lua.tonumber(L, -1);
    *out = lim - dur;
    g_lua.settop(L, top);
    return 1;
}

static double dom_ceil(double x)
{
    double i = (double)(long long)x;
    if (x > i)
        return i + 1.0;
    return i;
}

/* DomTimeToNext: state-abhaengiger Countdown (Spiegel der Mod-Logik). */
static double dom_time_to_next(void *L, int ref)
{
    char state[64];
    state[0] = '\\0';
    if (!dom_call_method_str(L, ref, "spawner", "GetCurrentState", state,
                             sizeof(state)))
        return 0.0;

    double t = 0.0;
    if (strcmp(state, "cooldown_after_spawn") == 0) {
        if (!dom_read_number(L, ref, "cooldownTimer", &t))
            t = 0.0;
    } else if (strcmp(state, "prepare_spawn") == 0) {
        if (!dom_read_number(L, ref, "waitForSpawnTimer", &t))
            t = 0.0;
    } else if (strcmp(state, "idle") == 0) {
        if (!dom_read_number(L, ref, "idleTimer", &t))
            t = 0.0;
    } else if (strcmp(state, "sleep") == 0) {
        if (!dom_read_number(L, ref, "sleepSafeTimer", &t))
            t = 0.0;
    } else if (strcmp(state, "wait") == 0) {
        if (!dom_state_remaining(L, ref, "spawner", "wait", &t))
            t = 0.0;
    }
    return t;
}

/* Game-Thread (im ConsoleService::Update-Detour): DOM-State lesen + cachen. */
static void capture_dom_state_game_thread(void)
{
    if (!resolve_dom_instance())
        return;

    void *L = g_dom_lua;
    int ref = g_dom_ref;

    char buf[RESP_BUF_SIZE];
    json_buf_t jb;
    jb.buf = buf;
    jb.cap = sizeof(buf);
    jb.len = 0;
    jb.first = 1;
    jb_add(&jb, "{");

    double d = 0.0;
    int b = 0;
    size_t n = 0;
    char s[128];

    /* DOM-Schwierigkeit */
    if (dom_read_number(L, ref, "currentDifficultyLevel", &d))
        jb_num(&jb, "wave", d);
    if (dom_read_number(L, ref, "maxDifficultyLevel", &d))
        jb_num(&jb, "max_wave", d);
    if (dom_read_number(L, ref, "freezedDifficultyLevel", &d))
        jb_num(&jb, "frozen_wave", d);

    /* Spawner (Wellen-Zyklus) */
    s[0] = '\\0';
    if (dom_call_method_str(L, ref, "spawner", "GetCurrentState", s, sizeof(s)))
        jb_str(&jb, "dom_state", s);
    jb_num(&jb, "time_to_next", dom_ceil(dom_time_to_next(L, ref)));
    if (dom_read_number(L, ref, "cooldownTimer", &d))
        jb_num(&jb, "cooldown_timer", d);
    if (dom_read_number(L, ref, "idleTimer", &d))
        jb_num(&jb, "idle_timer", d);
    if (dom_read_number(L, ref, "waitForSpawnTimer", &d))
        jb_num(&jb, "prepare_timer", d);
    if (dom_read_number(L, ref, "sleepSafeTimer", &d))
        jb_num(&jb, "sleep_timer", d);

    /* HQ */
    s[0] = '\\0';
    if (dom_call_method_str(L, ref, "upgradeHQ", "GetCurrentState", s, sizeof(s)))
        jb_str(&jb, "hq_state", s);
    if (dom_read_number(L, ref, "hqAttackSafeTimer", &d))
        jb_num(&jb, "hq_attack_timer", d);

    /* Schwierigkeits-Progression */
    s[0] = '\\0';
    if (dom_call_method_str(L, ref, "difficultyIncrease", "GetCurrentState", s,
                            sizeof(s)))
        jb_str(&jb, "difficulty_state", s);
    if (dom_state_remaining(L, ref, "difficultyIncrease", "difficulty_increase",
                            &d))
        jb_num(&jb, "time_to_next_difficulty", d);

    /* Flags */
    if (dom_read_bool(L, ref, "pauseAttacks", &b))
        jb_bool(&jb, "pause_attacks", b);
    if (dom_read_bool(L, ref, "cancelTheAttack", &b))
        jb_bool(&jb, "cancel_attack", b);
    if (dom_read_bool(L, ref, "spawnBoss", &b))
        jb_bool(&jb, "spawn_boss", b);
    if (dom_read_number(L, ref, "extraAttacks", &d))
        jb_num(&jb, "extra_attacks", d);

    /* Zaehler */
    if (dom_read_table_len(L, ref, "spawnedAttacks", &n))
        jb_num(&jb, "spawned_attacks", (double)n);
    if (dom_read_table_len(L, ref, "preparedAttacks", &n))
        jb_num(&jb, "prepared_attacks", (double)n);

    /* Event/Objective (event_manager) */
    if (dom_read_number(L, ref, "currentEventLevel", &d))
        jb_num(&jb, "event_level", d);
    if (dom_read_number(L, ref, "eventManagerTimer", &d))
        jb_num(&jb, "event_timer", d);
    if (dom_read_table_len(L, ref, "objectiveActiveList", &n))
        jb_num(&jb, "active_objectives", (double)n);
    {
        double obj_last = 0.0, obj_between = 0.0, ev_timer = 0.0;
        if (dom_read_number(L, ref, "objectiveLastSpawnTime", &obj_last) &&
            dom_read_number(L, ref, "objectiveCurrentTimeBetweenNext",
                            &obj_between) &&
            dom_read_number(L, ref, "eventManagerTimer", &ev_timer))
            jb_num(&jb, "time_to_next_objective",
                   obj_last + obj_between - ev_timer);
    }

    jb_add(&jb, "}");
    buf[jb.len] = '\\0';

    while (InterlockedExchange(&g_dom_state_lock, 1) != 0)
        ;
    memcpy(g_dom_state_buf, buf, jb.len + 1);
    InterlockedExchange(&g_dom_state_lock, 0);
}
"""


def main():
    with io.open(PATH, "r", encoding="utf-8") as f:
        src = f.read()

    # 1) Detour.
    if OLD_DETOUR not in src:
        print("FEHLER: Detour-Block nicht gefunden.", file=sys.stderr)
        return 1
    src = src.replace(OLD_DETOUR, NEW_DETOUR, 1)

    # 2) dispatch_get_state.
    if OLD_DISPATCH not in src:
        print("FEHLER: dispatch_get_state-Block nicht gefunden.", file=sys.stderr)
        return 1
    src = src.replace(OLD_DISPATCH, NEW_DISPATCH, 1)

    # 3) Großer Mod-Hook-Block (Banner bis Ende register_dom_capture).
    start_marker = "/* DOM/Voll-State-Egress (Issue #376):"
    end_marker = 'dbg("register_dom_capture: _G.rbbridge_capture_state registriert (L=%p)", L);\n}'
    i = src.find(start_marker)
    if i < 0:
        print("FEHLER: Start-Marker fuer Mod-Hook-Block nicht gefunden.", file=sys.stderr)
        return 1
    line_start = src.rfind("\n", 0, i) + 1
    banner_start = src.rfind("\n", 0, line_start - 1) + 1
    j = src.find(end_marker, i)
    if j < 0:
        print("FEHLER: Ende-Marker fuer Mod-Hook-Block nicht gefunden.", file=sys.stderr)
        return 1
    j += len(end_marker)
    src = src[:banner_start] + NEW_BLOCK + src[j:]

    with io.open(PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("OK: C++ state egress (#378) angewendet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
