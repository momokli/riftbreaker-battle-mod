#!/usr/bin/env python3
"""Issue #376: fuegt den DOM-Wellen-Counter + time-to-next als C++-Direkt-Read
in rbbridge.c ein (dispatch_get_state). Reine str.replace-Edits - KEIN
clang-format (Regel aus AGENTS.md / io-re-session-handoff.md: .c nie per
editor schreiben). Idempotent: laeuft nur, wenn die Anker vorhanden sind.
"""

import io
import sys

PATH = "bausteine/04-trainer-io/rbbridge/rbbridge.c"

HELPER = r"""/* ====================================================================== */
/* DOM-Wellen-Counter + time-to-next (Issue #376): direkter C++-Read      */
/*                                                                        */
/* Die Werte liegen Lua-seitig in der Klasse dom_mananger (ein            */
/* Exor::LuaGraphNode; Quellkette dom_mananger -> event_manager ->        */
/* LuaGraphNode, dom_manager.lua / event_manager.lua). Statt Log-Tailing  */
/* wird die Instanz hier direkt ueber ihre vftable gefunden und die Lua-  */
/* self-Tabelle per luabind::object + lua-*-C-API gelesen.                */
/*                                                                        */
/* RE-Stand (Build 2.0.58485, byte-identisches PDB/DLL, verifiziert       */
/* 2026-09-13 via llvm-pdbutil -publics + tools/re/disasm.py):            */
/*   - LuaGraphNode-vftable RVA 0x2F46D70 (??_7LuaGraphNode@Exor@@6B@).   */
/*   - LuaGraphNode+0x20 = luabind::object { lua_State* @+0, int          */
/*     registry-ref @+8 } = die Lua-self-Tabelle. Beleg: Ctor 0x1B33630   */
/*     -> luabind-object-copy 0x1DA9F10; Ctor-Arg ist self aus            */
/*     "LuaGraphNode.__init(self, self)".                                  */
/*   - lua-*-C-API (Lua-5.1-Fork, statisch gelinkt, public Symbole):      */
/*       lua_rawgeti   0x290CFA0    lua_getfield  0x290C550               */
/*       lua_tonumber  0x290D790    lua_tointeger 0x290D670               */
/*       lua_settop    0x290D4D0    lua_gettop    0x290C710               */
/*       LUA_REGISTRYINDEX = -10000 (0xFFFFD8F0).                         */
/*                                                                        */
/* Identifikation dom_mananger: die self-Tabelle traegt das Zahlen-Feld   */
/* currentDifficultyLevel (1..9); andere LuaGraphNodes haben es nicht.     */
/*                                                                        */
/* Felder:                                                                */
/*   currentDifficultyLevel (Zahl) -> wave                                 */
/*   waitForSpawnTimer      (Zahl) -> time_to_next (Sekunden; nur im      */
/*     prepare_spawn-State; andere States haben eigene Timer, #376).       */
/*                                                                        */
/* Rueckgabe: 1 = gefunden (out_* gesetzt), 0 = nicht gefunden.            */
/* ====================================================================== */

typedef void (__fastcall *rbbridge_lua_rawgeti_fn)(void *L, int idx, int n);
typedef void (__fastcall *rbbridge_lua_getfield_fn)(void *L, int idx,
                                                    const char *k);
typedef double (__fastcall *rbbridge_lua_tonumber_fn)(void *L, int idx);
typedef long long (__fastcall *rbbridge_lua_tointeger_fn)(void *L, int idx);
typedef void (__fastcall *rbbridge_lua_settop_fn)(void *L, int idx);
typedef int (__fastcall *rbbridge_lua_gettop_fn)(void *L);

#define RBBRIDGE_LUAGRAPHNODE_VFTABLE_RVA 0x2F46D70u
#define RBBRIDGE_LUAGRAPHNODE_OBJECT_OFF  0x20u
#define RBBRIDGE_LUA_REGISTRYINDEX        (-10000)

static int read_dom_wave(const unsigned char *base, int *out_wave,
                         int *out_time_to_next)
{
    rbbridge_lua_rawgeti_fn rawgeti =
        (rbbridge_lua_rawgeti_fn)(uintptr_t)(base + 0x290CFA0);
    rbbridge_lua_getfield_fn getfield =
        (rbbridge_lua_getfield_fn)(uintptr_t)(base + 0x290C550);
    rbbridge_lua_tonumber_fn tonumber =
        (rbbridge_lua_tonumber_fn)(uintptr_t)(base + 0x290D790);
    rbbridge_lua_tointeger_fn tointeger =
        (rbbridge_lua_tointeger_fn)(uintptr_t)(base + 0x290D670);
    rbbridge_lua_settop_fn settop =
        (rbbridge_lua_settop_fn)(uintptr_t)(base + 0x290D4D0);
    rbbridge_lua_gettop_fn gettop =
        (rbbridge_lua_gettop_fn)(uintptr_t)(base + 0x290C710);

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
            uint32_t ref = 0;
            if (!safe_read_u64(inst + RBBRIDGE_LUAGRAPHNODE_OBJECT_OFF, &L))
                continue;
            if (!safe_read_u32(inst + RBBRIDGE_LUAGRAPHNODE_OBJECT_OFF + 8,
                               &ref))
                continue;
            /* luabind::object ist ungueltig, wenn kein Lua-State bzw. der
             * Registry-Ref das Sentinel (-2/-1) traegt -> False-Positive
             * bzw. noch nicht initialisierte Instanz ueberspringen. */
            if (!L || ref == 0xffffffffu || ref == 0xfffffffeu)
                continue;

            /* self-Tabelle aus dem Registry-Stack pushen und Felder lesen. */
            rawgeti((void *)(uintptr_t)L, RBBRIDGE_LUA_REGISTRYINDEX,
                    (int)ref);
            int tbl = gettop((void *)(uintptr_t)L); /* abs. Index d. Tabelle */

            getfield((void *)(uintptr_t)L, tbl, "currentDifficultyLevel");
            long long wave = tointeger((void *)(uintptr_t)L, tbl + 1);

            getfield((void *)(uintptr_t)L, tbl, "waitForSpawnTimer");
            double ttn = tonumber((void *)(uintptr_t)L, tbl + 2);

            settop((void *)(uintptr_t)L, tbl - 1); /* Stack zuruecksetzen */

            if (wave >= 1 && wave <= 9) {
                *out_wave = (int)wave;
                *out_time_to_next = (int)(ttn > 0.0 ? ttn + 0.999999 : 0.0);
                return 1;
            }
        }
    }
    return 0;
}

"""

ANCHOR_FN = "static void dispatch_get_state(HANDLE hPipe)\n"

OLD_TAIL = r"""    int64_t carbonium_max = read_resource_max(base, account, 0x659cc791);

    send_line(hPipe,
              "{\"event\":\"get_state_result\",\"ok\":true,"
              "\"carbonium\":%llu,\"carbonium_max\":%lld,\"resources\":%s}",
              (unsigned long long)carbonium, (long long)carbonium_max,
              resources);
}"""

NEW_TAIL = r"""    int64_t carbonium_max = read_resource_max(base, account, 0x659cc791);

    /* Wave-Counter + time-to-next (Issue #376): direkt aus der
     * dom_mananger-LuaGraphNode-Instanz lesen. -1 = nicht gefunden. */
    int wave = -1;
    int time_to_next = -1;
    read_dom_wave(base, &wave, &time_to_next);

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

    if "read_dom_wave" in src:
        print("read_dom_wave bereits vorhanden - nichts zu tun.")
        return 0

    if ANCHOR_FN not in src:
        print("FEHLER: Anker 'dispatch_get_state' nicht gefunden.", file=sys.stderr)
        return 1
    if OLD_TAIL not in src:
        print("FEHLER: get_state_result-send_line-Anker nicht gefunden.", file=sys.stderr)
        return 1

    # 1) Helper vor dispatch_get_state einfuegen (nach dem vorangehenden "}").
    src = src.replace(ANCHOR_FN, HELPER + ANCHOR_FN, 1)
    # 2) send_line um wave/time_to_next erweitern.
    src = src.replace(OLD_TAIL, NEW_TAIL, 1)

    with io.open(PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("OK: rbbridge.c patched (read_dom_wave + get_state_result).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
