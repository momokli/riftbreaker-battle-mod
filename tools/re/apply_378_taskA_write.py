#!/usr/bin/env python3
"""Issue #378 Task A: typed native WRITE commands (pause_dom/resume_dom/end_game).

Erweitert den WRITE-Pfad um einen typisierten Puffer (enum + native fn) parallel
zum String-Pfad. Der Detour auf ConsoleService::Update fuehrt die typisierten
Kommandos auf dem Game-Thread aus (SetSuspended / FinishCurrentMission). Der
String-Pfad (ExecuteCommand) bleibt als Fallback fuer untypisierte Kommandos.

Die .c-Datei wird NUR hier editiert (io.open(..., newline="\n")).
"""

import io
import sys

PATH = "bausteine/rbbridge/dll/rbbridge.c"

TYPED_BLOCK = """\
/* Typed native WRITE commands (#378): Puffer + Drain. Der Detour fuehrt sie
 * auf dem Game-Thread aus (kein ExecuteCommand->Lua). */
typedef enum {
    RBBRIDGE_TYPED_NONE = 0,
    RBBRIDGE_TYPED_PAUSE_DOM,
    RBBRIDGE_TYPED_RESUME_DOM,
    RBBRIDGE_TYPED_END_GAME,
} rbbridge_typed_cmd_t;

#define RBBRIDGE_LUAGRAPHNODE_SET_SUSPENDED_RVA 0x1BA6CB0u
#define RBBRIDGE_MISSION_SERVICE_VFTABLE_RVA    0x2E962A0u
#define RBBRIDGE_MISSION_FINISH_RVA             0xF9A190u
#define RBBRIDGE_MISSION_STATUS_WIN             0

typedef void (__fastcall *lua_graphnode_set_suspended_fn)(void *self,
                                                          char suspended);
typedef void (__fastcall *mission_finish_fn)(void *self, int status);

static volatile LONG g_pending_typed = RBBRIDGE_TYPED_NONE;

/* Game-Thread-only Caches fuer native WRITE (kein Lock noetig). */
static void *g_dom_instance = NULL;
static void *g_mission_service = NULL;

static int resolve_dom_instance_scan(void);
static void *resolve_service_instance_by_rva(const unsigned char *base,
                                             uint32_t vftable_rva);

/* Vftable-Scan nach einem Service-Singleton (instance[0] == base + rva). */
static void *resolve_service_instance_by_rva(const unsigned char *base,
                                             uint32_t vftable_rva)
{
    uint64_t needle = (uint64_t)(uintptr_t)(base + vftable_rva);
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
            if (q[i] == needle)
                return (void *)&q[i];
        }
    }
    return NULL;
}

/* Game-Thread: typisierte native Kommandos ausfuehren. */
static void drain_pending_typed_commands(void)
{
    int cmd = 0;
    while (InterlockedExchange(&g_pending_cmd_lock, 1) != 0)
        ;
    cmd = g_pending_typed;
    if (cmd != RBBRIDGE_TYPED_NONE)
        g_pending_typed = RBBRIDGE_TYPED_NONE;
    InterlockedExchange(&g_pending_cmd_lock, 0);

    if (cmd == RBBRIDGE_TYPED_NONE)
        return;

    const unsigned char *base = NULL;
    size_t size = 0;
    const char *via = NULL;
    const unsigned char *execfn = NULL;
    if (!resolve_module(&base, &size, &via, &execfn))
        return;

    switch (cmd) {
    case RBBRIDGE_TYPED_PAUSE_DOM:
    case RBBRIDGE_TYPED_RESUME_DOM: {
        if (!resolve_dom_instance_scan() || !g_dom_instance)
            return;
        lua_graphnode_set_suspended_fn fn =
            (lua_graphnode_set_suspended_fn)(uintptr_t)(
                base + RBBRIDGE_LUAGRAPHNODE_SET_SUSPENDED_RVA);
        fn(g_dom_instance, (cmd == RBBRIDGE_TYPED_PAUSE_DOM) ? 1 : 0);
        dbg("drain_pending_typed: %s (inst=%p)",
            (cmd == RBBRIDGE_TYPED_PAUSE_DOM) ? "pause_dom" : "resume_dom",
            g_dom_instance);
        break;
    }
    case RBBRIDGE_TYPED_END_GAME: {
        if (!g_mission_service)
            g_mission_service = resolve_service_instance_by_rva(
                base, RBBRIDGE_MISSION_SERVICE_VFTABLE_RVA);
        if (!g_mission_service)
            return;
        mission_finish_fn fn =
            (mission_finish_fn)(uintptr_t)(base + RBBRIDGE_MISSION_FINISH_RVA);
        fn(g_mission_service, RBBRIDGE_MISSION_STATUS_WIN);
        dbg("drain_pending_typed: end_game (MissionService=%p)",
            g_mission_service);
        break;
    }
    default:
        break;
    }
}

"""

EDITS = [
    # 1) Typed-Maschinerie vor drain_pending_commands.
    (
        "/* Game-Thread: ein ausstehendes Kommando ausfuehren. */\nstatic void drain_pending_commands(void)\n",
        TYPED_BLOCK + "/* Game-Thread: ein ausstehendes Kommando ausfuehren. */\n"
        "static void drain_pending_commands(void)\n",
    ),
    # 2) Detour ruft typed-Drain auf.
    (
        "static void __fastcall detour_console_update(void *self, float dt)\n"
        "{\n"
        "    drain_pending_commands();\n"
        "    capture_dom_state_game_thread();\n",
        "static void __fastcall detour_console_update(void *self, float dt)\n"
        "{\n"
        "    drain_pending_typed_commands();\n"
        "    drain_pending_commands();\n"
        "    capture_dom_state_game_thread();\n",
    ),
    # 3) dispatch_exec: typed-Kommandos erkennen + enqueuen.
    (
        "    /* Kommando nur in den Puffer; Game-Thread fuehrt es im Update-Hook aus\n"
        "     * (Lua darf nicht vom Pipe-Thread laufen, #376). */\n",
        "    int typed = RBBRIDGE_TYPED_NONE;\n"
        '    if (strcmp(command, "pause_dom") == 0)\n'
        "        typed = RBBRIDGE_TYPED_PAUSE_DOM;\n"
        '    else if (strcmp(command, "resume_dom") == 0)\n'
        "        typed = RBBRIDGE_TYPED_RESUME_DOM;\n"
        '    else if (strcmp(command, "end_game") == 0)\n'
        "        typed = RBBRIDGE_TYPED_END_GAME;\n"
        "\n"
        "    if (typed != RBBRIDGE_TYPED_NONE) {\n"
        "        while (InterlockedExchange(&g_pending_cmd_lock, 1) != 0)\n"
        "            ;\n"
        "        g_pending_typed = typed;\n"
        "        InterlockedExchange(&g_pending_cmd_lock, 0);\n"
        '        dbg("dispatch_exec: typed=%d -> Pending (async, Game-Thread)", typed);\n'
        "        send_line(hPipe,\n"
        '                  "{\\"event\\":\\"exec_result\\",\\"ok\\":true,"\n'
        '                  "\\"command\\":\\"%s\\",\\"async\\":true}",\n'
        "                  escaped);\n"
        "        return;\n"
        "    }\n"
        "\n"
        "    /* Kommando nur in den Puffer; Game-Thread fuehrt es im Update-Hook aus\n"
        "     * (Lua darf nicht vom Pipe-Thread laufen, #376). */\n",
    ),
    # 4a) resolve_dom_instance -> resolve_dom_instance_scan (ohne Throttle).
    (
        "static int resolve_dom_instance(void)\n"
        "{\n"
        "    if (g_dom_cache_valid)\n"
        "        return 1;\n"
        "\n"
        "    uint64_t now = GetTickCount64();\n"
        "    if (now < g_dom_next_resolve_tick)\n"
        "        return 0;\n"
        "    g_dom_next_resolve_tick = now + 1000; /* hoechstens 1x/Sekunde scannen */\n"
        "\n"
        "    const unsigned char *base = NULL;\n",
        "static int resolve_dom_instance_scan(void)\n"
        "{\n"
        "    if (g_dom_cache_valid)\n"
        "        return 1;\n"
        "\n"
        "    const unsigned char *base = NULL;\n",
    ),
    # 4b) Instanz-Pointer cachen.
    (
        "                g_dom_base = base;\n"
        "                g_dom_lua = (void *)(uintptr_t)L;\n"
        "                g_dom_ref = (int)ref32;\n",
        "                g_dom_base = base;\n"
        "                g_dom_instance = (void *)(uintptr_t)inst;\n"
        "                g_dom_lua = (void *)(uintptr_t)L;\n"
        "                g_dom_ref = (int)ref32;\n",
    ),
    # 4c) Throttleder Wrapper nach der Scan-Funktion.
    (
        "    return 0;\n}\n\n/* ---------- Minimaler JSON-Builder (flache Felder, wie BuildStateJson) ---- */\n",
        "    return 0;\n"
        "}\n"
        "\n"
        "/* Throttleder Wrapper fuer den READ-Pfad (1x/Sekunde scannen waehrend Boot). */\n"
        "static int resolve_dom_instance(void)\n"
        "{\n"
        "    if (g_dom_cache_valid)\n"
        "        return 1;\n"
        "    uint64_t now = GetTickCount64();\n"
        "    if (now < g_dom_next_resolve_tick)\n"
        "        return 0;\n"
        "    g_dom_next_resolve_tick = now + 1000; /* hoechstens 1x/Sekunde scannen */\n"
        "    return resolve_dom_instance_scan();\n"
        "}\n"
        "\n"
        "/* ---------- Minimaler JSON-Builder (flache Felder, wie BuildStateJson) ---- */\n",
    ),
]


def main():
    with io.open(PATH, "r", encoding="utf-8") as f:
        src = f.read()

    for i, (old, new) in enumerate(EDITS):
        if src.count(old) != 1:
            print(
                "FEHLER: Edit %d nicht genau einmal gefunden (count=%d): %r" % (i, src.count(old), old[:70]),
                file=sys.stderr,
            )
            return 1
        src = src.replace(old, new, 1)

    with io.open(PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("OK: #378 Task A (typed native WRITE) angewendet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
