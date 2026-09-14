#!/usr/bin/env python3
"""Issue #378 (Teil 2): fehlende State-Felder nachziehen.

Ergaenzt auf den C++-Read (Baustein aus apply_378_cpp_egress.py):
  - players             = self:GetPlayersCounter()
  - Service-Getter      = CampaignService/DifficultyService/MissionService
  - HQ-Felder           = FindService:FindEntityByType("headquarters") +
                          HealthService:GetHealth/GetMaxHealth/IsAlive

Alles bleibt auf dem Game-Thread (im ConsoleService::Update-Detour). Die
.c-Datei wird NUR hier editiert (io.open(..., newline="\n")).
"""

import io
import sys

PATH = "bausteine/04-trainer-io/rbbridge/rbbridge.c"

HELPER_BLOCK = """\
/* self:method() -> Number (z.B. players = self:GetPlayersCounter()). */
static int dom_self_method_number(void *L, int ref, const char *method,
                                  double *out)
{
    int top = g_lua.gettop(L);
    int ok = 0;
    dom_push_self(L, ref);
    int s = g_lua.gettop(L);
    g_lua.getfield(L, s, method);
    g_lua.pushvalue(L, s);
    if (g_lua.call(L, 1, 1, 0) == 0 &&
        g_lua.type(L, -1) == RBBRIDGE_LUA_TNUMBER) {
        *out = g_lua.tonumber(L, -1);
        ok = 1;
    }
    g_lua.settop(L, top);
    return ok;
}

/* _G[svc]:method() aufrufen (0 Argumente); Ergebnis-Typ zurueckgeben.
 * Laesst das Ergebnis auf dem Stack (Aufrufer raeumt per settop ab). */
static int dom_global_call(void *L, const char *svc, const char *method)
{
    g_lua.getfield(L, RBBRIDGE_LUA_GLOBALSINDEX, svc);
    int s = g_lua.gettop(L);
    g_lua.getfield(L, s, method);
    g_lua.pushvalue(L, s);
    if (g_lua.call(L, 1, 1, 0) != 0)
        return -1;
    return g_lua.type(L, -1);
}

static int dom_global_number(void *L, const char *svc, const char *method,
                             double *out)
{
    int top = g_lua.gettop(L);
    int t = dom_global_call(L, svc, method);
    int ok = (t == RBBRIDGE_LUA_TNUMBER);
    if (ok)
        *out = g_lua.tonumber(L, -1);
    g_lua.settop(L, top);
    return ok;
}

static int dom_global_bool(void *L, const char *svc, const char *method,
                           int *out)
{
    int top = g_lua.gettop(L);
    int t = dom_global_call(L, svc, method);
    int ok = (t == RBBRIDGE_LUA_TBOOLEAN);
    if (ok)
        *out = g_lua.toboolean(L, -1);
    g_lua.settop(L, top);
    return ok;
}

static int dom_global_string(void *L, const char *svc, const char *method,
                             char *out, size_t out_sz)
{
    int top = g_lua.gettop(L);
    int t = dom_global_call(L, svc, method);
    int ok = 0;
    if (t == RBBRIDGE_LUA_TSTRING) {
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

/* Globale Variable (kein Methodenaufruf) als Number lesen (INVALID_ID). */
static int dom_global_var_number(void *L, const char *name, double *out)
{
    int top = g_lua.gettop(L);
    g_lua.getfield(L, RBBRIDGE_LUA_GLOBALSINDEX, name);
    int ok = (g_lua.type(L, -1) == RBBRIDGE_LUA_TNUMBER);
    if (ok)
        *out = g_lua.tonumber(L, -1);
    g_lua.settop(L, top);
    return ok;
}

/* _G[svc]:method(string_arg) -> Number (FindService:FindEntityByType). */
static int dom_global_number_arg_str(void *L, const char *svc,
                                     const char *method, const char *arg,
                                     double *out)
{
    int top = g_lua.gettop(L);
    int ok = 0;
    g_lua.getfield(L, RBBRIDGE_LUA_GLOBALSINDEX, svc);
    int s = g_lua.gettop(L);
    g_lua.getfield(L, s, method);
    g_lua.pushvalue(L, s);
    g_lua.pushstring(L, arg);
    if (g_lua.call(L, 2, 1, 0) == 0 &&
        g_lua.type(L, -1) == RBBRIDGE_LUA_TNUMBER) {
        *out = g_lua.tonumber(L, -1);
        ok = 1;
    }
    g_lua.settop(L, top);
    return ok;
}

/* _G[svc]:method(number_arg) -> Number (HealthService:GetHealth/GetMaxHealth). */
static int dom_global_number_arg(void *L, const char *svc, const char *method,
                                 double arg, double *out)
{
    int top = g_lua.gettop(L);
    int ok = 0;
    g_lua.getfield(L, RBBRIDGE_LUA_GLOBALSINDEX, svc);
    int s = g_lua.gettop(L);
    g_lua.getfield(L, s, method);
    g_lua.pushvalue(L, s);
    g_lua.pushnumber(L, arg);
    if (g_lua.call(L, 2, 1, 0) == 0 &&
        g_lua.type(L, -1) == RBBRIDGE_LUA_TNUMBER) {
        *out = g_lua.tonumber(L, -1);
        ok = 1;
    }
    g_lua.settop(L, top);
    return ok;
}

/* _G[svc]:method(number_arg) -> Bool (HealthService:IsAlive). */
static int dom_global_bool_arg(void *L, const char *svc, const char *method,
                               double arg, int *out)
{
    int top = g_lua.gettop(L);
    int ok = 0;
    g_lua.getfield(L, RBBRIDGE_LUA_GLOBALSINDEX, svc);
    int s = g_lua.gettop(L);
    g_lua.getfield(L, s, method);
    g_lua.pushvalue(L, s);
    g_lua.pushnumber(L, arg);
    if (g_lua.call(L, 2, 1, 0) == 0 &&
        g_lua.type(L, -1) == RBBRIDGE_LUA_TBOOLEAN) {
        *out = g_lua.toboolean(L, -1);
        ok = 1;
    }
    g_lua.settop(L, top);
    return ok;
}

"""

FIELDS_BLOCK = """\
    /* Services (Globale Service-Objekte via _G). */
    if (dom_self_method_number(L, ref, "GetPlayersCounter", &d))
        jb_num(&jb, "players", d);
    if (dom_global_number(L, "CampaignService", "GetCreaturesBaseDifficulty", &d))
        jb_num(&jb, "creatures_difficulty", d);
    s[0] = '\\0';
    if (dom_global_string(L, "DifficultyService", "GetCurrentDifficultyName", s,
                          sizeof(s)))
        jb_str(&jb, "difficulty_name", s);
    s[0] = '\\0';
    if (dom_global_string(L, "DifficultyService", "GetWaveStrength", s, sizeof(s)))
        jb_str(&jb, "wave_strength", s);
    if (dom_global_bool(L, "DifficultyService", "AreWavesDisabled", &b))
        jb_bool(&jb, "waves_disabled", b);
    s[0] = '\\0';
    if (dom_global_string(L, "MissionService", "GetCurrentMissionName", s,
                          sizeof(s)))
        jb_str(&jb, "mission_name", s);
    s[0] = '\\0';
    if (dom_global_string(L, "MissionService", "GetCurrentBiomeName", s,
                          sizeof(s)))
        jb_str(&jb, "biome", s);
    if (dom_global_number(L, "DifficultyService", "GetMissionDuration", &d))
        jb_num(&jb, "mission_duration", d);
    if (dom_global_number(L, "DifficultyService", "GetWarmupDuration", &d))
        jb_num(&jb, "warmup_duration", d);
    if (dom_global_bool(L, "DifficultyService", "IsMissionInfinite", &b))
        jb_bool(&jb, "mission_infinite", b);

    /* HQ (FindService -> Entity, dann HealthService). */
    {
        double invalid_id = 0.0;
        int has_invalid = dom_global_var_number(L, "INVALID_ID", &invalid_id);
        double hq = 0.0;
        if (dom_global_number_arg_str(L, "FindService", "FindEntityByType",
                                      "headquarters", &hq) &&
            (!has_invalid || hq != invalid_id)) {
            double hp = 0.0;
            int alive = 0;
            if (dom_global_number_arg(L, "HealthService", "GetHealth", hq, &hp))
                jb_num(&jb, "hq_hp", hp);
            if (dom_global_number_arg(L, "HealthService", "GetMaxHealth", hq,
                                      &hp))
                jb_num(&jb, "hq_hp_max", hp);
            if (dom_global_bool_arg(L, "HealthService", "IsAlive", hq, &alive))
                jb_bool(&jb, "hq_dead", !alive);
        }
    }

"""

EDITS = [
    # 1) lua_pushnumber typedef.
    (
        "typedef void (__fastcall *rbbridge_lua_pushstring_fn)(void *L, const char *s);\n",
        "typedef void (__fastcall *rbbridge_lua_pushstring_fn)(void *L, const char *s);\n"
        "typedef void (__fastcall *rbbridge_lua_pushnumber_fn)(void *L, double n);\n",
    ),
    # 2) RVA define.
    (
        "#define RBBRIDGE_LUA_PUSHSTRING_RVA 0x290CE40u\n",
        "#define RBBRIDGE_LUA_PUSHSTRING_RVA 0x290CE40u\n#define RBBRIDGE_LUA_PUSHNUMBER_RVA 0x290CE20u\n",
    ),
    # 3) Struct-Member.
    (
        "    rbbridge_lua_pushstring_fn pushstring;\n} dom_lua_api_t;\n",
        "    rbbridge_lua_pushstring_fn pushstring;\n    rbbridge_lua_pushnumber_fn pushnumber;\n} dom_lua_api_t;\n",
    ),
    # 4) Aufloesung.
    (
        "                g_lua.pushstring = (rbbridge_lua_pushstring_fn)(uintptr_t)(\n"
        "                    base + RBBRIDGE_LUA_PUSHSTRING_RVA);\n"
        "                g_dom_cache_valid = 1;\n",
        "                g_lua.pushstring = (rbbridge_lua_pushstring_fn)(uintptr_t)(\n"
        "                    base + RBBRIDGE_LUA_PUSHSTRING_RVA);\n"
        "                g_lua.pushnumber = (rbbridge_lua_pushnumber_fn)(uintptr_t)(\n"
        "                    base + RBBRIDGE_LUA_PUSHNUMBER_RVA);\n"
        "                g_dom_cache_valid = 1;\n",
    ),
    # 5) Neue Helper vor capture_dom_state_game_thread.
    (
        "/* Game-Thread (im ConsoleService::Update-Detour): DOM-State lesen + cachen. */\n",
        HELPER_BLOCK + "/* Game-Thread (im ConsoleService::Update-Detour): DOM-State lesen + cachen. */\n",
    ),
    # 6) Neue Felder vor dem JSON-Abschluss.
    (
        "    jb_add(&jb, \"}\");\n    buf[jb.len] = '\\0';\n",
        FIELDS_BLOCK + "    jb_add(&jb, \"}\");\n    buf[jb.len] = '\\0';\n",
    ),
]


def main():
    with io.open(PATH, "r", encoding="utf-8") as f:
        src = f.read()

    for i, (old, new) in enumerate(EDITS):
        if old not in src:
            print("FEHLER: Edit %d nicht gefunden: %r" % (i, old[:70]), file=sys.stderr)
            return 1
        src = src.replace(old, new, 1)

    with io.open(PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("OK: #378 Teil 2 (players/services/hq) angewendet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
