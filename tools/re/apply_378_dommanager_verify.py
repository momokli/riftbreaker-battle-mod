#!/usr/bin/env python3
"""Issue #378 Fix: resolve_dom_instance_scan -> dom_mananger statt erster
LuaGraphNode.

Es gibt mehrere LuaGraphNode-Instanzen (dom_mananger -> event_manager ->
LuaGraphNode, gleiche vftable). Der erste Treffer war event_manager, dessen
self-table die DOM-Felder nicht hat. Fix: nach einem Kandidaten (gueltiges L +
ref) das self-table via lua_rawgeti/lua_getfield auf currentDifficultyLevel
(number) pruefen; nur wenn vorhanden ist es dom_mananger, sonst naechster
Kandidat. Laeuft auf dem Game-Thread (Detour), also lua_* sicher.

Die .c-Datei wird NUR hier editiert (io.open(..., newline="\n")).
"""

import io
import sys

PATH = "server/dll/rbbridge.c"

OLD = "                (int)ref32 >= 0) {\n                g_dom_base = base;\n"

NEW = (
    "                (int)ref32 >= 0) {\n"
    "                /* Verifizieren: es gibt mehrere LuaGraphNode-Instanzen\n"
    "                 * (dom_mananger -> event_manager -> LuaGraphNode, gleiche\n"
    "                 * vftable). Nur dom_mananger hat currentDifficultyLevel als\n"
    "                 * number auf dem self-table. */\n"
    "                {\n"
    "                    rbbridge_lua_gettop_fn v_gettop = (rbbridge_lua_gettop_fn)(uintptr_t)(\n"
    "                        base + RBBRIDGE_LUA_GETTOP_RVA);\n"
    "                    rbbridge_lua_rawgeti_fn v_rawgeti = (rbbridge_lua_rawgeti_fn)(uintptr_t)(\n"
    "                        base + RBBRIDGE_LUA_RAWGETI_RVA);\n"
    "                    rbbridge_lua_getfield_fn v_getfield = (rbbridge_lua_getfield_fn)(uintptr_t)(\n"
    "                        base + RBBRIDGE_LUA_GETFIELD_RVA);\n"
    "                    rbbridge_lua_type_fn v_type = (rbbridge_lua_type_fn)(uintptr_t)(\n"
    "                        base + RBBRIDGE_LUA_TYPE_RVA);\n"
    "                    rbbridge_lua_settop_fn v_settop = (rbbridge_lua_settop_fn)(uintptr_t)(\n"
    "                        base + RBBRIDGE_LUA_SETTOP_RVA);\n"
    "                    void *Lcand = (void *)(uintptr_t)L;\n"
    "                    int top = v_gettop(Lcand);\n"
    "                    v_rawgeti(Lcand, RBBRIDGE_LUA_REGISTRYINDEX, (int)ref32);\n"
    '                    v_getfield(Lcand, -1, "currentDifficultyLevel");\n'
    "                    int is_dom = (v_type(Lcand, -1) == RBBRIDGE_LUA_TNUMBER);\n"
    "                    v_settop(Lcand, top);\n"
    "                    if (!is_dom)\n"
    "                        continue; /* naechster LuaGraphNode-Kandidat */\n"
    "                }\n"
    "\n"
    "                g_dom_base = base;\n"
)


def main():
    with io.open(PATH, "r", encoding="utf-8") as f:
        src = f.read()

    if src.count(OLD) != 1:
        print("FEHLER: Anker nicht genau einmal gefunden (count=%d)" % src.count(OLD), file=sys.stderr)
        return 1

    src = src.replace(OLD, NEW, 1)

    with io.open(PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("OK: #378 dom_mananger-Verifikation angewendet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
