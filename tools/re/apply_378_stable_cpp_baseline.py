#!/usr/bin/env python3
"""Issue #378: WRITE-only Baseline (READ-Pfad deaktiviert).

Entfernt den (fehlerhaften) LuaGraphNode::Update-READ-Detour vollstaendig und
laesst den ConsoleService::Update-Detour NUR den WRITE-Pfad ausfuehren.
Der Lua/DOM-READ-Pfad (capture_dom_state_game_thread) wird damit tot (nicht
mehr aufgerufen) und erst in einem Folgeschritt ueber den LUA/Game-Thread
sauber verdrahtet.

Die .c-Datei wird NUR hier editiert (io.open(..., newline="\n")).
"""

import io
import sys

PATH = "server/dll/rbbridge.c"

LGN_BLOCK = """\
/* ----------------------------------------------------------------------------
 * READ-Pfad auf dem LUA/Game-Thread (#378).
 *
 * ConsoleService::Update laeuft auf einem Worker-Thread (TaskWorldExecutor::
 * SubmitSystemTasks); lua_*-Calls dort korrumpieren den Lua-Stack und fuehren
 * zum 0x30/0x110-NULL-Deref-Crash. LuaGraphNode::Update laeuft dagegen auf dem
 * Lua/Game-Thread (wie dom_mananger:Update im Mod-Hook) — der sichere Ort.
 * Wir patchen den LuaGraphNode-vftable-Update-Slot und lesen nur fuer die
 * dom_mananger-Instanz (this == g_dom_instance), gethrottlet auf 2 Hz.
 * ------------------------------------------------------------------------- */
static void __fastcall detour_lgn_update(void *self, float dt)
{
    if (!g_dom_cache_valid)
        resolve_dom_instance(); /* throttled scan; lua_* nur hier (Lua-Thread) */

    if (g_dom_cache_valid && self == g_dom_instance) {
        uint64_t now = GetTickCount64();
        if (now >= g_lgn_next_read_tick) {
            g_lgn_next_read_tick = now + 500; /* 2 Hz reicht fuer 2s-Poll */
            capture_dom_state_game_thread();
        }
    }

    if (g_original_lgn_update)
        g_original_lgn_update(self, dt);
}

static int install_lgn_update_hook(void)
{
    if (g_lgn_update_hooked)
        return 1;

    const unsigned char *base = NULL;
    size_t size = 0;
    const char *via = NULL;
    const unsigned char *execfn = NULL;
    if (!resolve_module(&base, &size, &via, &execfn))
        return 0;

    const unsigned char *vftable = base + RBBRIDGE_LUAGRAPHNODE_VFTABLE_RVA;
    uint64_t target =
        (uint64_t)(uintptr_t)(base + RBBRIDGE_LUAGRAPHNODE_UPDATE_RVA);

    for (int i = 0; i < 128; i++) {
        uint64_t entry = 0;
        if (!safe_read_u64(vftable + (size_t)i * 8, &entry))
            break;
        if (entry != target)
            continue;
        DWORD old = 0;
        if (!VirtualProtect((void *)(vftable + (size_t)i * 8), 8,
                            PAGE_READWRITE, &old))
            return 0;
        g_original_lgn_update = (console_update_fn)(uintptr_t)entry;
        *(uint64_t *)(vftable + (size_t)i * 8) =
            (uint64_t)(uintptr_t)&detour_lgn_update;
        VirtualProtect((void *)(vftable + (size_t)i * 8), 8, old, &old);
        g_lgn_update_hooked = 1;
        dbg("install_lgn_update_hook: Slot %d gepatcht (orig=%p)", i,
            (void *)g_original_lgn_update);
        return 1;
    }
    dbg("install_lgn_update_hook: Update-Slot nicht gefunden (target=%p)",
        (void *)(uintptr_t)target);
    return 0;
}
"""

EDITS = [
    # A) lgn-Globals entfernen.
    (
        "static console_update_fn g_original_update = NULL;\n"
        "static volatile LONG g_update_hooked = 0;\n"
        "\n"
        "/* LuaGraphNode::Update (Lua/Game-Thread) — EINZIG sicherer Ort fuer\n"
        " * lua_*-Reads. ConsoleService::Update laeuft auf einem Worker-Thread\n"
        " * (TaskWorldExecutor) und ist fuer lua_* NICHT sicher (#378). */\n"
        "static console_update_fn g_original_lgn_update = NULL;\n"
        "static volatile LONG g_lgn_update_hooked = 0;\n"
        "static uint64_t g_lgn_next_read_tick = 0;\n"
        "\n"
        "#define RBBRIDGE_PENDING_CMD_MAX 512\n",
        "static console_update_fn g_original_update = NULL;\n"
        "static volatile LONG g_update_hooked = 0;\n"
        "\n"
        "#define RBBRIDGE_PENDING_CMD_MAX 512\n",
    ),
    # B) RVA-Define entfernen.
    (
        "#define RBBRIDGE_LUAGRAPHNODE_SET_SUSPENDED_RVA 0x1BA6CB0u\n"
        "#define RBBRIDGE_LUAGRAPHNODE_UPDATE_RVA        0x1BAA140u\n",
        "#define RBBRIDGE_LUAGRAPHNODE_SET_SUSPENDED_RVA 0x1BA6CB0u\n",
    ),
    # C) detour_console_update: kein lgn-Hook, kein READ.
    (
        "static void capture_dom_state_game_thread(void);\n"
        "static void install_lgn_update_hook(void);\n"
        "static void __fastcall detour_lgn_update(void *self, float dt);\n"
        "\n"
        "static void __fastcall detour_console_update(void *self, float dt)\n"
        "{\n"
        "    /* READ laeuft jetzt im LuaGraphNode::Update-Detour (Lua-Thread). */\n"
        "    install_lgn_update_hook();\n"
        "    drain_pending_typed_commands();\n"
        "    drain_pending_commands();\n"
        "    if (g_original_update)\n"
        "        g_original_update(self, dt);\n"
        "}\n",
        "static void capture_dom_state_game_thread(void);\n"
        "\n"
        "static void __fastcall detour_console_update(void *self, float dt)\n"
        "{\n"
        "    drain_pending_typed_commands();\n"
        "    drain_pending_commands();\n"
        "    if (g_original_update)\n"
        "        g_original_update(self, dt);\n"
        "}\n",
    ),
    # E) LGN_BLOCK entfernen (nach capture_dom_state_game_thread).
    (
        "    while (InterlockedExchange(&g_dom_state_lock, 1) != 0)\n"
        "        ;\n"
        "    memcpy(g_dom_state_buf, buf, jb.len + 1);\n"
        "    InterlockedExchange(&g_dom_state_lock, 0);\n"
        "}\n"
        "\n" + LGN_BLOCK + "\n"
        "static void dispatch_get_state(HANDLE hPipe)\n",
        "    while (InterlockedExchange(&g_dom_state_lock, 1) != 0)\n"
        "        ;\n"
        "    memcpy(g_dom_state_buf, buf, jb.len + 1);\n"
        "    InterlockedExchange(&g_dom_state_lock, 0);\n"
        "}\n"
        "\n"
        "\n"
        "static void dispatch_get_state(HANDLE hPipe)\n",
    ),
    # F) dispatch_get_state: install_lgn_update_hook entfernen.
    (
        "        if (resolve_console_service(&cfn, &cinst))\n"
        "            install_update_hook();\n"
        "        install_lgn_update_hook();\n"
        "    }\n",
        "        if (resolve_console_service(&cfn, &cinst))\n            install_update_hook();\n    }\n",
    ),
]


def main():
    with io.open(PATH, "r", encoding="utf-8") as f:
        src = f.read()

    for i, (old, new) in enumerate(EDITS):
        n = src.count(old)
        if n != 1:
            print(
                "FEHLER: Edit %d nicht genau einmal gefunden (count=%d): %r" % (i, n, old[:70]),
                file=sys.stderr,
            )
            return 1
        src = src.replace(old, new, 1)

    with io.open(PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("OK: #378 stable C++ baseline (WRITE-only) angewendet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
