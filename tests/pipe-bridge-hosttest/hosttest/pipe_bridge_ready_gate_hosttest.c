/*
 * pipe_bridge_ready_gate_hosttest.c - Host-Test fuer die Ready-Gate-Logik
 * (Issue #937, US3).
 *
 * Bindet ausschliesslich ready_gate.h ein (Windows-frei) und prueft die reine
 * Gate-Logik: idempotentes add/remove, distinct-count, all_ready,
 * should_fire-Latch, clear/arm und die Status-JSON-Felder. Kein Windows, kein
 * Spielprozess, kein Wine, kein Netz.
 *
 * Ausgabe: HOSTTEST_PASS=<n> HOSTTEST_FAIL=<m>
 * Exit:    0 wenn m == 0, sonst 1.
 *
 * Build (Host): cc -O1 -g -Wall -Wextra -I server/pipe-bridge -o <bin> <this>
 */
#include "ready_gate.h"

#include <stdio.h>
#include <string.h>

static int ht_pass = 0;
static int ht_fail = 0;

static void check(int cond, const char *msg)
{
    if (cond) {
        ht_pass++;
    } else {
        ht_fail++;
        printf("FAIL: %s\n", msg);
    }
}

int main(void)
{
    ready_gate_t g;
    char js[512];

    /* --- idempotentes add/count ------------------------------------- */
    ready_gate_init(&g);
    check(ready_gate_count(&g) == 0, "init: leer");
    check(ready_gate_add(&g, "aa11") == 1, "add: erste ID -> 1");
    check(ready_gate_add(&g, "aa11") == 0, "add: dieselbe ID -> 0 (idempotent)");
    check(ready_gate_count(&g) == 1, "count: distinct == 1");
    check(ready_gate_add(&g, "bb22") == 1, "add: zweite ID -> 1");
    check(ready_gate_count(&g) == 2, "count: distinct == 2");
    check(ready_gate_add(&g, "") == 0, "add: leere ID -> 0");
    check(ready_gate_add(&g, NULL) == 0, "add: NULL -> 0");
    check(ready_gate_find(&g, "bb22") == 1, "find: bb22 an Index 1");

    /* --- remove (idempotent) ---------------------------------------- */
    check(ready_gate_remove(&g, "aa11") == 1, "remove: vorhanden -> 1");
    check(ready_gate_remove(&g, "aa11") == 0, "remove: schon weg -> 0");
    check(ready_gate_count(&g) == 1 &&
              strcmp(g.ids[0], "bb22") == 0,
          "remove: Luecke geschlossen (bb22 rueckt nach)");

    /* --- all_ready --------------------------------------------------- */
    check(ready_gate_all_ready(&g, 0) == 0, "all_ready: players=0 -> 0");
    check(ready_gate_all_ready(&g, 2) == 0, "all_ready: 1/2 -> 0");
    ready_gate_add(&g, "cc33");
    check(ready_gate_all_ready(&g, 2) == 1, "all_ready: 2/2 -> 1");
    check(ready_gate_all_ready(&g, 3) == 0, "all_ready: 2/3 -> 0");

    /* --- should_fire-Latch (genau einmal) ---------------------------- */
    check(ready_gate_should_fire(&g, 2) == 1, "should_fire: alle da -> 1");
    g.fired = 1;
    check(ready_gate_should_fire(&g, 2) == 0,
          "should_fire: nach fire -> 0 (genau einmal)");
    g.fired = 0;

    /* --- clear / arm ------------------------------------------------- */
    ready_gate_arm(&g, 100.0, 180.0);
    check(g.deadline == 280.0, "arm: deadline = now + timeout");
    ready_gate_arm(&g, 200.0, 180.0);
    check(g.deadline == 280.0, "arm: idempotent (deadline bleibt)");
    ready_gate_clear(&g);
    check(ready_gate_count(&g) == 0 && g.deadline == 0.0,
          "clear: Menge + Deadline zurueck");

    /* --- Status-JSON ------------------------------------------------- */
    ready_gate_init(&g);
    g.players = 3;
    ready_gate_add(&g, "0x1a");
    ready_gate_add(&g, "0x2b");
    g.deadline = 1234.0;
    check(ready_gate_status_json(&g, js, sizeof(js)) > 0,
          "status_json: schreibt etwas");
    check(strstr(js, "\"players\":3") != NULL, "status_json: players");
    check(strstr(js, "\"ready_count\":2") != NULL, "status_json: ready_count");
    check(strstr(js, "\"ready_players\":[\"0x1a\",\"0x2b\"]") != NULL,
          "status_json: ready_players[]");
    check(strstr(js, "\"ready_deadline\":1234.0") != NULL,
          "status_json: ready_deadline");

    /* Escaping: Fallback-ID mit Quote bleibt gueltiges JSON. */
    ready_gate_init(&g);
    ready_gate_add(&g, "a\"b");
    check(ready_gate_status_json(&g, js, sizeof(js)) > 0 &&
              strstr(js, "a\\\"b") != NULL,
          "status_json: ID escaped");
    check(ready_gate_status_json(&g, js, 4) == 0,
          "status_json: Puffer zu klein -> 0");

    printf("HOSTTEST_PASS=%d HOSTTEST_FAIL=%d\n", ht_pass, ht_fail);
    return ht_fail == 0 ? 0 : 1;
}
