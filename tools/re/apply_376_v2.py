#!/usr/bin/env python3
"""Issue #376 (v2): read_dom_wave liest jetzt den state-abhaengigen Countdown.

Die drei DOM-Timer-Felder (cooldownTimer, idleTimer, waitForSpawnTimer) sind
wechselseitig exklusiv (nur der State der State-Machine ist aktiv). time_to_next
= max der drei (auf >= 0 geklemmt, ceil). So liefert get_state den echten
"time to next wave" unabhaengig davon, ob der DOM gerade in cooldown_after_spawn,
idle oder prepare_spawn haengt.
"""

import io
import sys

PATH = "bausteine/rbbridge/dll/rbbridge.c"

OLD = r"""            getfield((void *)(uintptr_t)L, tbl, "currentDifficultyLevel");
            long long wave = tointeger((void *)(uintptr_t)L, tbl + 1);

            getfield((void *)(uintptr_t)L, tbl, "waitForSpawnTimer");
            double ttn = tonumber((void *)(uintptr_t)L, tbl + 2);

            settop((void *)(uintptr_t)L, tbl - 1); /* Stack zuruecksetzen */

            if (wave >= 1 && wave <= 9) {
                *out_wave = (int)wave;
                *out_time_to_next = (int)(ttn > 0.0 ? ttn + 0.999999 : 0.0);
                return 1;
            }"""

NEW = r"""            getfield((void *)(uintptr_t)L, tbl, "currentDifficultyLevel");
            long long wave = tointeger((void *)(uintptr_t)L, tbl + 1);

            /* State-abhaengiger Countdown: cooldownTimer (cooldown_after_spawn),
             * idleTimer (idle) und waitForSpawnTimer (prepare_spawn) sind
             * wechselseitig exklusiv; nur der aktive State ist > 0. */
            getfield((void *)(uintptr_t)L, tbl, "cooldownTimer");
            double t_cooldown = tonumber((void *)(uintptr_t)L, tbl + 2);
            getfield((void *)(uintptr_t)L, tbl, "idleTimer");
            double t_idle = tonumber((void *)(uintptr_t)L, tbl + 3);
            getfield((void *)(uintptr_t)L, tbl, "waitForSpawnTimer");
            double t_prepare = tonumber((void *)(uintptr_t)L, tbl + 4);

            settop((void *)(uintptr_t)L, tbl - 1); /* Stack zuruecksetzen */

            double ttn = t_cooldown;
            if (t_idle > ttn)
                ttn = t_idle;
            if (t_prepare > ttn)
                ttn = t_prepare;

            if (wave >= 1 && wave <= 9) {
                *out_wave = (int)wave;
                *out_time_to_next = (int)(ttn > 0.0 ? ttn + 0.999999 : 0.0);
                return 1;
            }"""


def main():
    with io.open(PATH, "r", encoding="utf-8") as f:
        src = f.read()

    if OLD not in src:
        print("FEHLER: alter read_dom_wave-Block nicht gefunden.", file=sys.stderr)
        return 1

    src = src.replace(OLD, NEW, 1)

    with io.open(PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print("OK: read_dom_wave liest jetzt cooldown/idle/prepare-Timer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
