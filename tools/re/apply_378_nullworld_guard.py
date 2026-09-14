#!/usr/bin/env python3
"""Issue #378 Fix: NULL-World-Guard fuer HQ-Read + native-WRITE end_game.

Crash: HealthService::GetHealth/GetMaxHealth dereferenzieren intern
World = this[+0x08] und dann World[+0x30]. Waehrend des Boots ist der World*
noch NULL -> Read von NULL+0x30 = 0x30 -> Page-Fault. Gleiches gilt fuer
MissionService::FinishCurrentMission. Fix: vor dem Aufruf den World* via
safe_read_u64 pruefen und bei NULL frueh zurueck (kein HQ / kein end_game).

Die .c-Datei wird NUR hier editiert (io.open(..., newline="\n")).
"""

import io
import sys

PATH = "bausteine/04-trainer-io/rbbridge/rbbridge.c"

EDITS = [
    # 1) read_hq_health: World* an HealthService[+0x08] pruefen.
    (
        "    if (!g_find_service || !g_health_service)\n"
        "        return 0;\n"
        "\n"
        "    find_entity_by_type_fn find_entity = (find_entity_by_type_fn)(uintptr_t)(\n"
        "        base + RBBRIDGE_FIND_ENTITY_BY_TYPE_RVA);\n",
        "    if (!g_find_service || !g_health_service)\n"
        "        return 0;\n"
        "\n"
        "    /* Boot-Guard: HealthService[+0x08] ist der World*; solange der NULL\n"
        "     * ist, wuerde GetHealth/GetMaxHealth intern auf NULL+0x30 lesen. */\n"
        "    uint64_t world = 0;\n"
        "    if (!safe_read_u64((const unsigned char *)g_health_service + 0x08,\n"
        "                       &world) ||\n"
        "        !world)\n"
        "        return 0;\n"
        "\n"
        "    find_entity_by_type_fn find_entity = (find_entity_by_type_fn)(uintptr_t)(\n"
        "        base + RBBRIDGE_FIND_ENTITY_BY_TYPE_RVA);\n",
    ),
    # 2) drain_pending_typed_commands: end_game World*-Guard.
    (
        "    case RBBRIDGE_TYPED_END_GAME: {\n"
        "        if (!g_mission_service)\n"
        "            g_mission_service = resolve_service_instance_by_rva(\n"
        "                base, RBBRIDGE_MISSION_SERVICE_VFTABLE_RVA);\n"
        "        if (!g_mission_service)\n"
        "            return;\n"
        "        mission_finish_fn fn =\n"
        "            (mission_finish_fn)(uintptr_t)(base + RBBRIDGE_MISSION_FINISH_RVA);\n",
        "    case RBBRIDGE_TYPED_END_GAME: {\n"
        "        uint64_t world = 0;\n"
        "        if (!g_mission_service)\n"
        "            g_mission_service = resolve_service_instance_by_rva(\n"
        "                base, RBBRIDGE_MISSION_SERVICE_VFTABLE_RVA);\n"
        "        if (!g_mission_service)\n"
        "            return;\n"
        "        /* Boot-Guard: MissionService[+0x08] = World*; NULL ->\n"
        "         * FinishCurrentMission wuerde intern NULL dereferenzieren. */\n"
        "        if (!safe_read_u64((const unsigned char *)g_mission_service + 0x08,\n"
        "                           &world) ||\n"
        "            !world)\n"
        "            return;\n"
        "        mission_finish_fn fn =\n"
        "            (mission_finish_fn)(uintptr_t)(base + RBBRIDGE_MISSION_FINISH_RVA);\n",
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
    print("OK: #378 NULL-World-Guard angewendet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
