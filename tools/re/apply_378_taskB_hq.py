#!/usr/bin/env python3
"""Issue #378 Task B: hq_* via direkte C++ Reads (kein lua_*).

Ersetzt den lua-basierten HQ-Block in capture_dom_state_game_thread durch einen
reinen C++-Read: FindService::FindEntityByType("headquarters") -> entity id,
dann HealthService::GetHealth/GetMaxHealth (lesen HealthComponent[+0x00]/[+0x04]
als float). hq_dead = current <= 0.0f. Die vier damit ungenutzten lua-Helper
(dom_global_var_number / dom_global_number_arg_str / dom_global_number_arg /
dom_global_bool_arg) werden entfernt.

Die .c-Datei wird NUR hier editiert (io.open(..., newline="\n")).
"""

import io
import sys

PATH = "bausteine/04-trainer-io/rbbridge/rbbridge.c"

HQ_READ_BLOCK = """\
/* HQ health via pure C++ (no lua_*): FindService::FindEntityByType ->
 * HealthService::GetHealth/GetMaxHealth (lesen HealthComponent[+0x00]/[+0x04]).
 * Die HealthService-Methoden kapseln exakt dieselbe Offset-Kette (World+0x30
 * ECS-Store -> lookup -> HealthComponent), ohne das komplexe TypeAny-out-Arg
 * des rohen lookups nachbauen zu muessen. */
#define RBBRIDGE_FIND_SERVICE_VFTABLE_RVA    0x2E94C98u
#define RBBRIDGE_HEALTH_SERVICE_VFTABLE_RVA  0x2E95760u
#define RBBRIDGE_FIND_ENTITY_BY_TYPE_RVA     0x1C0E420u
#define RBBRIDGE_HEALTH_GET_HEALTH_RVA       0xF9BBB0u
#define RBBRIDGE_HEALTH_GET_MAX_HEALTH_RVA   0xF9C360u
#define RBBRIDGE_INVALID_ENTITY_ID           0xFFFFFFFFu

typedef uint32_t (__fastcall *find_entity_by_type_fn)(void *self,
                                                      const char *type);
typedef float (__fastcall *health_get_float_fn)(void *self, uint32_t entityId);

static void *g_find_service = NULL;
static void *g_health_service = NULL;

static int read_hq_health(const unsigned char *base, float *hp, float *hpmax)
{
    if (!g_find_service)
        g_find_service = resolve_service_instance_by_rva(
            base, RBBRIDGE_FIND_SERVICE_VFTABLE_RVA);
    if (!g_health_service)
        g_health_service = resolve_service_instance_by_rva(
            base, RBBRIDGE_HEALTH_SERVICE_VFTABLE_RVA);
    if (!g_find_service || !g_health_service)
        return 0;

    find_entity_by_type_fn find_entity = (find_entity_by_type_fn)(uintptr_t)(
        base + RBBRIDGE_FIND_ENTITY_BY_TYPE_RVA);
    uint32_t entity = find_entity(g_find_service, "headquarters");
    if (entity == RBBRIDGE_INVALID_ENTITY_ID)
        return 0;

    health_get_float_fn get_health = (health_get_float_fn)(uintptr_t)(
        base + RBBRIDGE_HEALTH_GET_HEALTH_RVA);
    health_get_float_fn get_max = (health_get_float_fn)(uintptr_t)(
        base + RBBRIDGE_HEALTH_GET_MAX_HEALTH_RVA);
    *hp = get_health(g_health_service, entity);
    *hpmax = get_max(g_health_service, entity);
    return 1;
}

"""

# Die 4 lua-Helper, die nach dem Umstieg ungenutzt waeren -> entfernen.
UNUSED_LUA_HELPERS = """\
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
    if (g_lua.type(L, s) == RBBRIDGE_LUA_TNIL)
        goto done;
    g_lua.getfield(L, s, method);
    if (g_lua.type(L, -1) != RBBRIDGE_LUA_TFUNCTION)
        goto done;
    g_lua.pushvalue(L, s);
    g_lua.pushstring(L, arg);
    if (g_lua.call(L, 2, 1, 0) == 0 &&
        g_lua.type(L, -1) == RBBRIDGE_LUA_TNUMBER) {
        *out = g_lua.tonumber(L, -1);
        ok = 1;
    }
done:
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
    if (g_lua.type(L, s) == RBBRIDGE_LUA_TNIL)
        goto done;
    g_lua.getfield(L, s, method);
    if (g_lua.type(L, -1) != RBBRIDGE_LUA_TFUNCTION)
        goto done;
    g_lua.pushvalue(L, s);
    g_lua.pushnumber(L, arg);
    if (g_lua.call(L, 2, 1, 0) == 0 &&
        g_lua.type(L, -1) == RBBRIDGE_LUA_TNUMBER) {
        *out = g_lua.tonumber(L, -1);
        ok = 1;
    }
done:
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
    if (g_lua.type(L, s) == RBBRIDGE_LUA_TNIL)
        goto done;
    g_lua.getfield(L, s, method);
    if (g_lua.type(L, -1) != RBBRIDGE_LUA_TFUNCTION)
        goto done;
    g_lua.pushvalue(L, s);
    g_lua.pushnumber(L, arg);
    if (g_lua.call(L, 2, 1, 0) == 0 &&
        g_lua.type(L, -1) == RBBRIDGE_LUA_TBOOLEAN) {
        *out = g_lua.toboolean(L, -1);
        ok = 1;
    }
done:
    g_lua.settop(L, top);
    return ok;
}

"""

OLD_HQ = """\
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

NEW_HQ = """\
    /* HQ (pure C++ offset: FindService -> Entity -> HealthComponent). */
    {
        float hp = 0.0f, hpmax = 0.0f;
        if (read_hq_health(g_dom_base, &hp, &hpmax)) {
            jb_num(&jb, "hq_hp", (double)hp);
            jb_num(&jb, "hq_hp_max", (double)hpmax);
            jb_bool(&jb, "hq_dead", hp <= 0.0f);
        }
    }
"""

EDITS = [
    # 1) Native HQ-Read-Helper nach read_resource_max.
    (
        "    float f;\n    memcpy(&f, &bits, sizeof(f));\n    return (int64_t)((double)f * (double)scale);\n}\n",
        "    float f;\n"
        "    memcpy(&f, &bits, sizeof(f));\n"
        "    return (int64_t)((double)f * (double)scale);\n"
        "}\n"
        "\n" + HQ_READ_BLOCK,
    ),
    # 2) Ungenutzte lua-Helper entfernen.
    (UNUSED_LUA_HELPERS, ""),
    # 3) HQ-Block auf C++-Read umstellen.
    (OLD_HQ, NEW_HQ),
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
    print("OK: #378 Task B (hq_* via C++ HealthComponent) angewendet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
