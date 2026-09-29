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

    /* --- #937/US4: Timeout-Grenzfall (deadline) ---------------------- */
    ready_gate_init(&g);
    check(ready_gate_expired(&g, 9999.0) == 0,
          "expired: kein Gate armed -> 0");
    ready_gate_add(&g, "aa11");
    ready_gate_arm(&g, 100.0, 180.0);
    check(ready_gate_expired(&g, 279.999) == 0, "expired: vor Deadline -> 0");
    check(ready_gate_expired(&g, 280.0) == 1,
          "expired: GENAU an Deadline -> 1 (Grenzfall >=)");
    check(ready_gate_expired(&g, 280.001) == 1, "expired: nach Deadline -> 1");
    g.fired = 1;
    check(ready_gate_expired(&g, 300.0) == 0,
          "expired: nach gefeuertem Start -> 0 (kein Timeout)");
    g.fired = 0;
    ready_gate_clear(&g);
    check(ready_gate_expired(&g, 300.0) == 0,
          "expired: leere Menge -> 0 (nichts zu timeouten)");
    check(ready_gate_expired(NULL, 300.0) == 0, "expired: NULL -> 0");

    /* --- #937/US3 (Fix): Runden-Latch zuruecksetzen ------------------ */
    /* Ein „genau einmal pro Runde"-Latch muss beim Runden-Reset geloescht
     * werden, sonst startet nach GAME_OVER->reset() ohne Bridge-Neustart keine
     * zweite Runde per /ready. clear() laesst den Latch stehen, reset() nicht. */
    ready_gate_init(&g);
    g.players = 2;
    ready_gate_add(&g, "aa11");
    ready_gate_add(&g, "bb22");
    ready_gate_arm(&g, 100.0, 180.0);
    check(ready_gate_should_fire(&g, 2) == 1, "reset: alle da -> fire");
    g.fired = 1;      /* Start gefeuert (Runde laeuft) */
    g.timed_out = 1;  /* Timeout-Event gemeldet */
    ready_gate_clear(&g);
    check(g.fired == 1 && g.timed_out == 1,
          "clear: fired/timed_out bleiben stehen (kein Runden-Reset)");
    check(ready_gate_should_fire(&g, 2) == 0,
          "clear: nach fire bleibt der Latch zu (genau einmal)");
    ready_gate_reset(&g);
    check(ready_gate_count(&g) == 0 && g.deadline == 0.0,
          "reset: Menge + Deadline zurueck");
    check(g.fired == 0 && g.timed_out == 0,
          "reset: fired/timed_out geloescht -> neue Runde");
    check(g.players == 2,
          "reset: players bleibt (kommt je Poll aus get_state)");
    ready_gate_add(&g, "cc33");
    ready_gate_add(&g, "dd44");
    check(ready_gate_should_fire(&g, 2) == 1,
          "reset: nach neuer Runde feuert das Gate ERNEUT");
    ready_gate_reset(NULL); /* defensiv: NULL darf nicht crashen */

    /* --- #937/US4 (Fix): Timeout-Obergrenze ------------------------- */
    /* env_int klemmt nur n > 0; unplausibel hohe RBB_READY_TIMEOUT_S
     * (z. B. 999999999) muessen auf eine sinnvolle Obergrenze begrenzt werden. */
    check(ready_timeout_cap(180, 180) == 180, "cap: Default bleibt");
    check(ready_timeout_cap(1, 180) == 1, "cap: 1 s bleibt");
    check(ready_timeout_cap(0, 180) == 180, "cap: 0 -> Default");
    check(ready_timeout_cap(-5, 180) == 180, "cap: negativ -> Default");
    check(ready_timeout_cap(999999999, 180) == RBB_READY_TIMEOUT_MAX,
          "cap: INT_MAX-nah -> Obergrenze");
    check(ready_timeout_cap(RBB_READY_TIMEOUT_MAX + 1, 180) ==
              RBB_READY_TIMEOUT_MAX,
          "cap: knapp ueber Grenze -> Obergrenze");
    check(ready_timeout_cap(RBB_READY_TIMEOUT_MAX, 180) ==
              RBB_READY_TIMEOUT_MAX,
          "cap: genau an der Grenze bleibt");

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
