#!/usr/bin/env python3
"""Issue #378 Fix: nil-sichere lua_*-Helper.

Boot-Crash (err_msgv / SubmitSystemTasks): fehlende Services/Methoden sind
waehrend des Boots nil; die C++-Helper riefen lua_pcall auf einen nil-Wert
(oder eine nil-Methode). Die Lua-Mod-Variante war pcall-gesichert, die
C++-Variante nicht. Fix: vor jedem lua_pcall pruefen, dass das Service-Objekt
nicht nil und die Methode eine Funktion ist (lua_type == LUA_TFUNCTION); bei
Fehler sauber mit lua_settop unwinden und Default (0/false/"") liefern.

Die .c-Datei wird NUR hier editiert (io.open(..., newline="\n")).
"""

import io
import sys

PATH = "bausteine/rbbridge/dll/rbbridge.c"

# (old, new) — jedes old muss exakt einmal vorkommen.
EDITS = [
    # 0) LUA_TFUNCTION-Konstante.
    (
        "#define RBBRIDGE_LUA_TUSERDATA      7\n",
        "#define RBBRIDGE_LUA_TUSERDATA      7\n#define RBBRIDGE_LUA_TFUNCTION      6\n",
    ),
    # 1) dom_call_method_str: self[name] darf nicht nil, Methode muss Funktion sein.
    (
        "/* self[name]:method() -> String (State-Name). Rueckgabe 1 = String gelesen. */\n"
        "static int dom_call_method_str(void *L, int ref, const char *name,\n"
        "                               const char *method, char *out, size_t out_sz)\n"
        "{\n"
        "    int top = g_lua.gettop(L);\n"
        "    int ok = 0;\n"
        "    dom_push_self(L, ref);\n"
        "    g_lua.getfield(L, -1, name);\n"
        "    int obj = g_lua.gettop(L);\n"
        "    g_lua.getfield(L, obj, method);\n"
        "    g_lua.pushvalue(L, obj);\n"
        "    if (g_lua.call(L, 1, 1, 0) == 0 &&\n"
        "        g_lua.type(L, -1) == RBBRIDGE_LUA_TSTRING) {\n"
        "        size_t len = 0;\n"
        "        const char *s = g_lua.tolstring(L, -1, &len);\n"
        "        if (s && len > 0) {\n"
        "            if (len >= out_sz)\n"
        "                len = out_sz - 1;\n"
        "            memcpy(out, s, len);\n"
        "            out[len] = '\\0';\n"
        "            ok = 1;\n"
        "        }\n"
        "    }\n"
        "    g_lua.settop(L, top);\n"
        "    return ok;\n"
        "}\n",
        "/* self[name]:method() -> String (State-Name). Rueckgabe 1 = String gelesen. */\n"
        "static int dom_call_method_str(void *L, int ref, const char *name,\n"
        "                               const char *method, char *out, size_t out_sz)\n"
        "{\n"
        "    int top = g_lua.gettop(L);\n"
        "    int ok = 0;\n"
        "    dom_push_self(L, ref);\n"
        "    g_lua.getfield(L, -1, name);\n"
        "    int obj = g_lua.gettop(L);\n"
        "    if (g_lua.type(L, obj) != RBBRIDGE_LUA_TNIL) {\n"
        "        g_lua.getfield(L, obj, method);\n"
        "        if (g_lua.type(L, -1) == RBBRIDGE_LUA_TFUNCTION) {\n"
        "            g_lua.pushvalue(L, obj);\n"
        "            if (g_lua.call(L, 1, 1, 0) == 0 &&\n"
        "                g_lua.type(L, -1) == RBBRIDGE_LUA_TSTRING) {\n"
        "                size_t len = 0;\n"
        "                const char *s = g_lua.tolstring(L, -1, &len);\n"
        "                if (s && len > 0) {\n"
        "                    if (len >= out_sz)\n"
        "                        len = out_sz - 1;\n"
        "                    memcpy(out, s, len);\n"
        "                    out[len] = '\\0';\n"
        "                    ok = 1;\n"
        "                }\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "    g_lua.settop(L, top);\n"
        "    return ok;\n"
        "}\n",
    ),
    # 2) dom_state_remaining: self[field] + jede Methode (GetState/GetDurationLimit/GetDuration).
    (
        "/* rbStateRemaining: self[field]:GetState(name) -> GetDurationLimit-GetDuration. */\n"
        "static int dom_state_remaining(void *L, int ref, const char *field,\n"
        "                               const char *state_name, double *out)\n"
        "{\n"
        "    int top = g_lua.gettop(L);\n"
        "    dom_push_self(L, ref);\n"
        "    g_lua.getfield(L, -1, field);\n"
        "    int sm = g_lua.gettop(L);\n"
        "\n"
        "    /* s = sm:GetState(state_name) */\n"
        '    g_lua.getfield(L, sm, "GetState");\n'
        "    g_lua.pushvalue(L, sm);\n"
        "    g_lua.pushstring(L, state_name);\n"
        "    if (g_lua.call(L, 2, 1, 0) != 0) {\n"
        "        g_lua.settop(L, top);\n"
        "        return 0;\n"
        "    }\n"
        "    int st = g_lua.gettop(L);\n"
        "    int t = g_lua.type(L, st);\n"
        "    if (t != RBBRIDGE_LUA_TTABLE && t != RBBRIDGE_LUA_TUSERDATA) {\n"
        "        g_lua.settop(L, top);\n"
        "        return 0;\n"
        "    }\n"
        "\n"
        "    /* lim = s:GetDurationLimit() */\n"
        '    g_lua.getfield(L, st, "GetDurationLimit");\n'
        "    g_lua.pushvalue(L, st);\n"
        "    if (g_lua.call(L, 1, 1, 0) != 0 ||\n"
        "        g_lua.type(L, -1) != RBBRIDGE_LUA_TNUMBER) {\n"
        "        g_lua.settop(L, top);\n"
        "        return 0;\n"
        "    }\n"
        "    double lim = g_lua.tonumber(L, -1);\n"
        "    g_lua.settop(L, st);\n"
        "\n"
        "    /* dur = s:GetDuration() */\n"
        '    g_lua.getfield(L, st, "GetDuration");\n'
        "    g_lua.pushvalue(L, st);\n"
        "    if (g_lua.call(L, 1, 1, 0) != 0 ||\n"
        "        g_lua.type(L, -1) != RBBRIDGE_LUA_TNUMBER) {\n"
        "        g_lua.settop(L, top);\n"
        "        return 0;\n"
        "    }\n"
        "    double dur = g_lua.tonumber(L, -1);\n"
        "    *out = lim - dur;\n"
        "    g_lua.settop(L, top);\n"
        "    return 1;\n"
        "}\n",
        "/* rbStateRemaining: self[field]:GetState(name) -> GetDurationLimit-GetDuration. */\n"
        "static int dom_state_remaining(void *L, int ref, const char *field,\n"
        "                               const char *state_name, double *out)\n"
        "{\n"
        "    int top = g_lua.gettop(L);\n"
        "    int sm;\n"
        "    int st;\n"
        "    double lim = 0.0, dur = 0.0;\n"
        "\n"
        "    dom_push_self(L, ref);\n"
        "    g_lua.getfield(L, -1, field);\n"
        "    sm = g_lua.gettop(L);\n"
        "    if (g_lua.type(L, sm) == RBBRIDGE_LUA_TNIL)\n"
        "        goto done;\n"
        "\n"
        "    /* s = sm:GetState(state_name) */\n"
        '    g_lua.getfield(L, sm, "GetState");\n'
        "    if (g_lua.type(L, -1) != RBBRIDGE_LUA_TFUNCTION)\n"
        "        goto done;\n"
        "    g_lua.pushvalue(L, sm);\n"
        "    g_lua.pushstring(L, state_name);\n"
        "    if (g_lua.call(L, 2, 1, 0) != 0)\n"
        "        goto done;\n"
        "    st = g_lua.gettop(L);\n"
        "    if (g_lua.type(L, st) != RBBRIDGE_LUA_TTABLE &&\n"
        "        g_lua.type(L, st) != RBBRIDGE_LUA_TUSERDATA)\n"
        "        goto done;\n"
        "\n"
        "    /* lim = s:GetDurationLimit() */\n"
        '    g_lua.getfield(L, st, "GetDurationLimit");\n'
        "    if (g_lua.type(L, -1) != RBBRIDGE_LUA_TFUNCTION)\n"
        "        goto done;\n"
        "    g_lua.pushvalue(L, st);\n"
        "    if (g_lua.call(L, 1, 1, 0) != 0 ||\n"
        "        g_lua.type(L, -1) != RBBRIDGE_LUA_TNUMBER)\n"
        "        goto done;\n"
        "    lim = g_lua.tonumber(L, -1);\n"
        "    g_lua.settop(L, st);\n"
        "\n"
        "    /* dur = s:GetDuration() */\n"
        '    g_lua.getfield(L, st, "GetDuration");\n'
        "    if (g_lua.type(L, -1) != RBBRIDGE_LUA_TFUNCTION)\n"
        "        goto done;\n"
        "    g_lua.pushvalue(L, st);\n"
        "    if (g_lua.call(L, 1, 1, 0) != 0 ||\n"
        "        g_lua.type(L, -1) != RBBRIDGE_LUA_TNUMBER)\n"
        "        goto done;\n"
        "    dur = g_lua.tonumber(L, -1);\n"
        "    *out = lim - dur;\n"
        "    g_lua.settop(L, top);\n"
        "    return 1;\n"
        "\n"
        "done:\n"
        "    g_lua.settop(L, top);\n"
        "    return 0;\n"
        "}\n",
    ),
    # 3) dom_self_method_number: self + Methode pruefen.
    (
        "/* self:method() -> Number (z.B. players = self:GetPlayersCounter()). */\n"
        "static int dom_self_method_number(void *L, int ref, const char *method,\n"
        "                                  double *out)\n"
        "{\n"
        "    int top = g_lua.gettop(L);\n"
        "    int ok = 0;\n"
        "    dom_push_self(L, ref);\n"
        "    int s = g_lua.gettop(L);\n"
        "    g_lua.getfield(L, s, method);\n"
        "    g_lua.pushvalue(L, s);\n"
        "    if (g_lua.call(L, 1, 1, 0) == 0 &&\n"
        "        g_lua.type(L, -1) == RBBRIDGE_LUA_TNUMBER) {\n"
        "        *out = g_lua.tonumber(L, -1);\n"
        "        ok = 1;\n"
        "    }\n"
        "    g_lua.settop(L, top);\n"
        "    return ok;\n"
        "}\n",
        "/* self:method() -> Number (z.B. players = self:GetPlayersCounter()). */\n"
        "static int dom_self_method_number(void *L, int ref, const char *method,\n"
        "                                  double *out)\n"
        "{\n"
        "    int top = g_lua.gettop(L);\n"
        "    int ok = 0;\n"
        "    dom_push_self(L, ref);\n"
        "    int s = g_lua.gettop(L);\n"
        "    if (g_lua.type(L, s) != RBBRIDGE_LUA_TNIL) {\n"
        "        g_lua.getfield(L, s, method);\n"
        "        if (g_lua.type(L, -1) == RBBRIDGE_LUA_TFUNCTION) {\n"
        "            g_lua.pushvalue(L, s);\n"
        "            if (g_lua.call(L, 1, 1, 0) == 0 &&\n"
        "                g_lua.type(L, -1) == RBBRIDGE_LUA_TNUMBER) {\n"
        "                *out = g_lua.tonumber(L, -1);\n"
        "                ok = 1;\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "    g_lua.settop(L, top);\n"
        "    return ok;\n"
        "}\n",
    ),
    # 4) dom_global_call: Service nil + Methode Funktion pruefen.
    (
        "/* _G[svc]:method() aufrufen (0 Argumente); Ergebnis-Typ zurueckgeben.\n"
        " * Laesst das Ergebnis auf dem Stack (Aufrufer raeumt per settop ab). */\n"
        "static int dom_global_call(void *L, const char *svc, const char *method)\n"
        "{\n"
        "    g_lua.getfield(L, RBBRIDGE_LUA_GLOBALSINDEX, svc);\n"
        "    int s = g_lua.gettop(L);\n"
        "    g_lua.getfield(L, s, method);\n"
        "    g_lua.pushvalue(L, s);\n"
        "    if (g_lua.call(L, 1, 1, 0) != 0)\n"
        "        return -1;\n"
        "    return g_lua.type(L, -1);\n"
        "}\n",
        "/* _G[svc]:method() aufrufen (0 Argumente); Ergebnis-Typ zurueckgeben.\n"
        " * Laesst das Ergebnis auf dem Stack (Aufrufer raeumt per settop ab).\n"
        " * nil-sicher: fehlendes Service-Objekt oder fehlende Methode -> -1. */\n"
        "static int dom_global_call(void *L, const char *svc, const char *method)\n"
        "{\n"
        "    g_lua.getfield(L, RBBRIDGE_LUA_GLOBALSINDEX, svc);\n"
        "    int s = g_lua.gettop(L);\n"
        "    if (g_lua.type(L, s) == RBBRIDGE_LUA_TNIL)\n"
        "        return -1;\n"
        "    g_lua.getfield(L, s, method);\n"
        "    if (g_lua.type(L, -1) != RBBRIDGE_LUA_TFUNCTION)\n"
        "        return -1;\n"
        "    g_lua.pushvalue(L, s);\n"
        "    if (g_lua.call(L, 1, 1, 0) != 0)\n"
        "        return -1;\n"
        "    return g_lua.type(L, -1);\n"
        "}\n",
    ),
    # 5) dom_global_number_arg_str: Service + Methode pruefen.
    (
        "/* _G[svc]:method(string_arg) -> Number (FindService:FindEntityByType). */\n"
        "static int dom_global_number_arg_str(void *L, const char *svc,\n"
        "                                     const char *method, const char *arg,\n"
        "                                     double *out)\n"
        "{\n"
        "    int top = g_lua.gettop(L);\n"
        "    int ok = 0;\n"
        "    g_lua.getfield(L, RBBRIDGE_LUA_GLOBALSINDEX, svc);\n"
        "    int s = g_lua.gettop(L);\n"
        "    g_lua.getfield(L, s, method);\n"
        "    g_lua.pushvalue(L, s);\n"
        "    g_lua.pushstring(L, arg);\n"
        "    if (g_lua.call(L, 2, 1, 0) == 0 &&\n"
        "        g_lua.type(L, -1) == RBBRIDGE_LUA_TNUMBER) {\n"
        "        *out = g_lua.tonumber(L, -1);\n"
        "        ok = 1;\n"
        "    }\n"
        "    g_lua.settop(L, top);\n"
        "    return ok;\n"
        "}\n",
        "/* _G[svc]:method(string_arg) -> Number (FindService:FindEntityByType). */\n"
        "static int dom_global_number_arg_str(void *L, const char *svc,\n"
        "                                     const char *method, const char *arg,\n"
        "                                     double *out)\n"
        "{\n"
        "    int top = g_lua.gettop(L);\n"
        "    int ok = 0;\n"
        "    g_lua.getfield(L, RBBRIDGE_LUA_GLOBALSINDEX, svc);\n"
        "    int s = g_lua.gettop(L);\n"
        "    if (g_lua.type(L, s) == RBBRIDGE_LUA_TNIL)\n"
        "        goto done;\n"
        "    g_lua.getfield(L, s, method);\n"
        "    if (g_lua.type(L, -1) != RBBRIDGE_LUA_TFUNCTION)\n"
        "        goto done;\n"
        "    g_lua.pushvalue(L, s);\n"
        "    g_lua.pushstring(L, arg);\n"
        "    if (g_lua.call(L, 2, 1, 0) == 0 &&\n"
        "        g_lua.type(L, -1) == RBBRIDGE_LUA_TNUMBER) {\n"
        "        *out = g_lua.tonumber(L, -1);\n"
        "        ok = 1;\n"
        "    }\n"
        "done:\n"
        "    g_lua.settop(L, top);\n"
        "    return ok;\n"
        "}\n",
    ),
    # 6) dom_global_number_arg: Service + Methode pruefen.
    (
        "/* _G[svc]:method(number_arg) -> Number (HealthService:GetHealth/GetMaxHealth). */\n"
        "static int dom_global_number_arg(void *L, const char *svc, const char *method,\n"
        "                                 double arg, double *out)\n"
        "{\n"
        "    int top = g_lua.gettop(L);\n"
        "    int ok = 0;\n"
        "    g_lua.getfield(L, RBBRIDGE_LUA_GLOBALSINDEX, svc);\n"
        "    int s = g_lua.gettop(L);\n"
        "    g_lua.getfield(L, s, method);\n"
        "    g_lua.pushvalue(L, s);\n"
        "    g_lua.pushnumber(L, arg);\n"
        "    if (g_lua.call(L, 2, 1, 0) == 0 &&\n"
        "        g_lua.type(L, -1) == RBBRIDGE_LUA_TNUMBER) {\n"
        "        *out = g_lua.tonumber(L, -1);\n"
        "        ok = 1;\n"
        "    }\n"
        "    g_lua.settop(L, top);\n"
        "    return ok;\n"
        "}\n",
        "/* _G[svc]:method(number_arg) -> Number (HealthService:GetHealth/GetMaxHealth). */\n"
        "static int dom_global_number_arg(void *L, const char *svc, const char *method,\n"
        "                                 double arg, double *out)\n"
        "{\n"
        "    int top = g_lua.gettop(L);\n"
        "    int ok = 0;\n"
        "    g_lua.getfield(L, RBBRIDGE_LUA_GLOBALSINDEX, svc);\n"
        "    int s = g_lua.gettop(L);\n"
        "    if (g_lua.type(L, s) == RBBRIDGE_LUA_TNIL)\n"
        "        goto done;\n"
        "    g_lua.getfield(L, s, method);\n"
        "    if (g_lua.type(L, -1) != RBBRIDGE_LUA_TFUNCTION)\n"
        "        goto done;\n"
        "    g_lua.pushvalue(L, s);\n"
        "    g_lua.pushnumber(L, arg);\n"
        "    if (g_lua.call(L, 2, 1, 0) == 0 &&\n"
        "        g_lua.type(L, -1) == RBBRIDGE_LUA_TNUMBER) {\n"
        "        *out = g_lua.tonumber(L, -1);\n"
        "        ok = 1;\n"
        "    }\n"
        "done:\n"
        "    g_lua.settop(L, top);\n"
        "    return ok;\n"
        "}\n",
    ),
    # 7) dom_global_bool_arg: Service + Methode pruefen.
    (
        "/* _G[svc]:method(number_arg) -> Bool (HealthService:IsAlive). */\n"
        "static int dom_global_bool_arg(void *L, const char *svc, const char *method,\n"
        "                               double arg, int *out)\n"
        "{\n"
        "    int top = g_lua.gettop(L);\n"
        "    int ok = 0;\n"
        "    g_lua.getfield(L, RBBRIDGE_LUA_GLOBALSINDEX, svc);\n"
        "    int s = g_lua.gettop(L);\n"
        "    g_lua.getfield(L, s, method);\n"
        "    g_lua.pushvalue(L, s);\n"
        "    g_lua.pushnumber(L, arg);\n"
        "    if (g_lua.call(L, 2, 1, 0) == 0 &&\n"
        "        g_lua.type(L, -1) == RBBRIDGE_LUA_TBOOLEAN) {\n"
        "        *out = g_lua.toboolean(L, -1);\n"
        "        ok = 1;\n"
        "    }\n"
        "    g_lua.settop(L, top);\n"
        "    return ok;\n"
        "}\n",
        "/* _G[svc]:method(number_arg) -> Bool (HealthService:IsAlive). */\n"
        "static int dom_global_bool_arg(void *L, const char *svc, const char *method,\n"
        "                               double arg, int *out)\n"
        "{\n"
        "    int top = g_lua.gettop(L);\n"
        "    int ok = 0;\n"
        "    g_lua.getfield(L, RBBRIDGE_LUA_GLOBALSINDEX, svc);\n"
        "    int s = g_lua.gettop(L);\n"
        "    if (g_lua.type(L, s) == RBBRIDGE_LUA_TNIL)\n"
        "        goto done;\n"
        "    g_lua.getfield(L, s, method);\n"
        "    if (g_lua.type(L, -1) != RBBRIDGE_LUA_TFUNCTION)\n"
        "        goto done;\n"
        "    g_lua.pushvalue(L, s);\n"
        "    g_lua.pushnumber(L, arg);\n"
        "    if (g_lua.call(L, 2, 1, 0) == 0 &&\n"
        "        g_lua.type(L, -1) == RBBRIDGE_LUA_TBOOLEAN) {\n"
        "        *out = g_lua.toboolean(L, -1);\n"
        "        ok = 1;\n"
        "    }\n"
        "done:\n"
        "    g_lua.settop(L, top);\n"
        "    return ok;\n"
        "}\n",
    ),
    # 8) capture_dom_state_game_thread: expliziter NULL-Guard.
    (
        "    if (!resolve_dom_instance())\n        return;\n",
        "    if (!resolve_dom_instance() || !g_dom_lua)\n        return;\n",
    ),
]


def main():
    with io.open(PATH, "r", encoding="utf-8") as f:
        src = f.read()

    for i, (old, new) in enumerate(EDITS):
        if src.count(old) != 1:
            print(
                "FEHLER: Edit %d nicht genau einmal gefunden (count=%d): %r" % (i, src.count(old), old[:60]),
                file=sys.stderr,
            )
            return 1
        src = src.replace(old, new, 1)

    with io.open(PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("OK: #378 nil-sichere lua_*-Helper angewendet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
