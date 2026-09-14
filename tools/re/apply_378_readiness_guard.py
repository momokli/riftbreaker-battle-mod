#!/usr/bin/env python3
"""Issue #378 Fix: Read-Path-Readiness-Guard (World-null -> ganzer Frame no-op).

0x110-Crash: die Read-Path-Methoden (FindService::FindEntityByType,
HealthService::GetHealth/GetMaxHealth, MissionService::FinishCurrentMission,
Service-Getter) dereferenzieren intern World = this[+0x08] und dann Felder an
World+0x.. . Solange der World* nicht fertig ist (NULL) -> Page-Fault. Statt
weiterer Einzel-Guards: ein kanonisches "World bereit"-Signal am Frame-Anfang
und pro-Service-World-Checks. Ohne World* wird der ganze Frame zum No-Op.

Die .c-Datei wird NUR hier editiert (io.open(..., newline="\n")).
"""

import io
import sys

PATH = "bausteine/04-trainer-io/rbbridge/rbbridge.c"

WORLD_READY_FN = """\
/* World-Readiness-Signal: HealthService[+0x08] muss einen gueltigen World*
 * haben. Solange der NULL ist, duerfen weder lua_* noch Service-Methoden
 * laufen (sie dereferenzieren intern World+0x.. -> Page-Fault). */
static int world_ready(void)
{
    uint64_t world = 0;
    if (!g_health_service) {
        const unsigned char *base = NULL;
        size_t size = 0;
        const char *via = NULL;
        const unsigned char *execfn = NULL;
        if (!resolve_module(&base, &size, &via, &execfn))
            return 0;
        g_health_service = resolve_service_instance_by_rva(
            base, RBBRIDGE_HEALTH_SERVICE_VFTABLE_RVA);
    }
    if (!g_health_service ||
        !safe_read_u64((const unsigned char *)g_health_service + 0x08, &world) ||
        !world)
        return 0;
    return 1;
}

"""

EDITS = [
    # 1) Forward-Decl fuer world_ready.
    (
        "static int resolve_dom_instance_scan(void);\n"
        "static void *resolve_service_instance_by_rva(const unsigned char *base,\n"
        "                                             uint32_t vftable_rva);\n",
        "static int resolve_dom_instance_scan(void);\n"
        "static void *resolve_service_instance_by_rva(const unsigned char *base,\n"
        "                                             uint32_t vftable_rva);\n"
        "static int world_ready(void);\n",
    ),
    # 2) drain_pending_typed_commands: World-Readiness.
    (
        "    if (cmd == RBBRIDGE_TYPED_NONE)\n        return;\n\n    const unsigned char *base = NULL;\n",
        "    if (cmd == RBBRIDGE_TYPED_NONE)\n"
        "        return;\n"
        "\n"
        "    /* World-Readiness: ohne gueltigen World* keine nativen WRITEs. */\n"
        "    if (!world_ready())\n"
        "        return;\n"
        "\n"
        "    const unsigned char *base = NULL;\n",
    ),
    # 3) capture_dom_state_game_thread: World-Readiness vor allen lua_*/Services.
    (
        "    if (!resolve_dom_instance() || !g_dom_lua)\n        return;\n\n    void *L = g_dom_lua;\n",
        "    if (!resolve_dom_instance() || !g_dom_lua)\n"
        "        return;\n"
        "\n"
        "    /* World-Readiness: ohne gueltigen World* keine lua_* bzw. Service-Calls. */\n"
        "    if (!world_ready())\n"
        "        return;\n"
        "\n"
        "    void *L = g_dom_lua;\n",
    ),
    # 4) read_hq_health: FindService World*-Guard vor FindEntityByType.
    (
        "    uint64_t world = 0;\n"
        "    if (!safe_read_u64((const unsigned char *)g_health_service + 0x08,\n"
        "                       &world) ||\n"
        "        !world)\n"
        "        return 0;\n"
        "\n"
        "    find_entity_by_type_fn find_entity = (find_entity_by_type_fn)(uintptr_t)(\n"
        "        base + RBBRIDGE_FIND_ENTITY_BY_TYPE_RVA);\n",
        "    uint64_t world = 0;\n"
        "    if (!safe_read_u64((const unsigned char *)g_health_service + 0x08,\n"
        "                       &world) ||\n"
        "        !world)\n"
        "        return 0;\n"
        "\n"
        "    /* FindService[+0x08] ist ebenfalls der World*; FindEntityByType\n"
        "     * dereferenziert ihn intern. */\n"
        "    uint64_t find_world = 0;\n"
        "    if (!safe_read_u64((const unsigned char *)g_find_service + 0x08,\n"
        "                       &find_world) ||\n"
        "        !find_world)\n"
        "        return 0;\n"
        "\n"
        "    find_entity_by_type_fn find_entity = (find_entity_by_type_fn)(uintptr_t)(\n"
        "        base + RBBRIDGE_FIND_ENTITY_BY_TYPE_RVA);\n",
    ),
    # 5) world_ready() nach read_hq_health definieren.
    (
        "    *hp = get_health(g_health_service, entity);\n"
        "    *hpmax = get_max(g_health_service, entity);\n"
        "    return 1;\n"
        "}\n",
        "    *hp = get_health(g_health_service, entity);\n"
        "    *hpmax = get_max(g_health_service, entity);\n"
        "    return 1;\n"
        "}\n"
        "\n" + WORLD_READY_FN,
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
    print("OK: #378 Readiness-Guard angewendet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
