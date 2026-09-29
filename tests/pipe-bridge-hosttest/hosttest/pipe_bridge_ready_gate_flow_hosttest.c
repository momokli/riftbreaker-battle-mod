/*
 * pipe_bridge_ready_gate_flow_hosttest.c - Kern-Flow-Host-Test des Ready-Gates
 * (Issue #937, Integration host-seitig).
 *
 * Der eigentliche Orchestrierungs-Code (`ready_gate_on_chat`,
 * `ready_gate_tick`, `route_pipe_line`, `pipe_reader_main`) lebt in
 * `server/pipe-bridge/pipe_bridge.c` und zieht winsock2/windows sowie
 * cockpit_html.inc - nicht host-linkbar. Die dabei benutzte *reine* Logik
 * liegt aber vollstaendig in `ready_gate.h`. Dieser Harness faehrt exakt die
 * Aufrufsequenz der Bridge (1:1 aus pipe_bridge.c `ready_gate_on_chat` /
 * `ready_gate_tick` nachgebildet) ueber einen Mini-Treiber nach und prueft so
 * die End-to-End-Kette host-seitig:
 *
 *   player_chat("/ready", conn_id) -> Gate-Zaehler
 *     -> bei ready_count==players GENAU EIN Start-Signal (start_epoch++)
 *     -> Timeout an der Deadline -> ready_timeout, KEIN Start, Menge leer
 *        ( => attack_cycle faellt zurueck nach PAUSED, kein Kick)
 *     -> Runden-Reset -> das Gate feuert erneut (genau einmal pro Runde)
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

/* --- Mini-Treiber: bildet die Bridge-Orchestrierung nach ----------------- */

typedef struct {
    ready_gate_t gate;
    int start_epoch;      /* == g_start_epoch der Bridge */
    char last_ev[32];     /* zuletzt gebroadcastetes Event ("", ready_update,
                           * ready_all, ready_timeout) */
} sim_t;

static void sim_init(sim_t *s, int players)
{
    memset(s, 0, sizeof(*s));
    ready_gate_init(&s->gate);
    s->gate.players = players; /* aus get_state_result ("players":N) */
}

/* 1:1 der Ablauf aus ready_gate_on_chat() (pipe_bridge.c): erkennt genau
 * "/ready", traegt die conn_id idempotent ein, armt beim ersten ready und
 * feuert bei ready_count==players GENAU EINMAL das Start-Signal
 * (g_start_epoch++). Rueckgabe 1 = Start gefeuert, 0 = sonst. */
static int sim_on_chat(sim_t *s, const char *text, const char *conn_id,
                       double now)
{
    char id[RBB_READY_ID_LEN];
    int added = 0, fire = 0, first = 0, players = 0;

    s->last_ev[0] = '\0';
    if (strcmp(text, "/ready") != 0)
        return 0; /* is_ready_command: kein /ready -> ignoriert */

    if (conn_id && conn_id[0])
        snprintf(id, sizeof(id), "%s", conn_id);
    else
        snprintf(id, sizeof(id), "text:ready"); /* Fallback ohne conn_id */

    if (!s->gate.fired) {
        first = (s->gate.count == 0);
        added = ready_gate_add(&s->gate, id);
        if (added) {
            s->gate.timed_out = 0;
            ready_gate_arm(&s->gate, now, 180.0);
        }
        players = s->gate.players;
        if (added && ready_gate_should_fire(&s->gate, players)) {
            s->gate.fired = 1;
            s->gate.timed_out = 0;
            fire = 1;
        }
    }

    if (fire) {
        s->start_epoch++; /* == g_start_epoch++ */
        snprintf(s->last_ev, sizeof(s->last_ev), "ready_all");
        return 1;
    }
    if (added && first)
        snprintf(s->last_ev, sizeof(s->last_ev), "ready_update");
    return 0;
}

/* 1:1 aus ready_gate_tick(): bei Ablauf Menge leeren + ready_timeout melden,
 * NICHTS starten. Rueckgabe 1 = Timeout gefeuert. */
static int sim_tick(sim_t *s, double now)
{
    s->last_ev[0] = '\0';
    if (ready_gate_expired(&s->gate, now)) {
        ready_gate_clear(&s->gate);
        s->gate.timed_out = 1;
        snprintf(s->last_ev, sizeof(s->last_ev), "ready_timeout");
        return 1;
    }
    return 0;
}

/* Runden-Reset (POST /attack_reset|/round_reset {"reset":1}). */
static void sim_round_reset(sim_t *s)
{
    ready_gate_reset(&s->gate);
}

/* ------------------------------------------------------------------------ */

int main(void)
{
    sim_t s;

    /* === US3: Kern-Flow — mehrere /ready mit unterschiedlichen conn_ids === */
    sim_init(&s, 2);

    check(sim_on_chat(&s, "/ready", "conn-a", 100.0) == 0,
          "flow: 1. ready (1/2) startet NICHT");
    check(s.gate.count == 1, "flow: count nach 1. ready == 1");
    check(strcmp(s.last_ev, "ready_update") == 0,
          "flow: 1. ready -> ready_update-Event");
    check(s.start_epoch == 0, "flow: start_epoch bleibt 0 (1/2)");

    /* Gleiche conn_id zweimal -> count bleibt 1 (distinct-Gate). */
    check(sim_on_chat(&s, "/ready", "conn-a", 101.0) == 0,
          "flow: doppeltes ready (gleiche conn_id) startet NICHT");
    check(s.gate.count == 1, "flow: doppeltes ready -> count bleibt 1");

    /* Fremder/kein /ready-Text wird ignoriert. */
    check(sim_on_chat(&s, "/help", "conn-z", 102.0) == 0,
          "flow: Nicht-/ready-Text ignoriert");
    check(s.gate.count == 1, "flow: Nicht-/ready-Text aendert count nicht");

    /* Zweiter Spieler -> 2/2 -> GENAU EIN Start. */
    check(sim_on_chat(&s, "/ready", "conn-b", 103.0) == 1,
          "flow: 2. Spieler (2/2) -> Start gefeuert");
    check(s.gate.count == 2, "flow: count nach 2. Spieler == 2");
    check(s.start_epoch == 1, "flow: start_epoch == 1 nach 2/2");
    check(strcmp(s.last_ev, "ready_all") == 0,
          "flow: 2/2 -> ready_all-Event");

    /* Kein Doppel-Start: weiteres /ready nach gefeuertem Start. */
    check(sim_on_chat(&s, "/ready", "conn-c", 104.0) == 0,
          "flow: weiteres ready nach Start -> KEIN Doppel-Start");
    check(s.start_epoch == 1, "flow: start_epoch bleibt 1 (kein Doppel-Start)");
    check(s.gate.count == 2, "flow: count nach Doppel-Start-Versuch unveraendert");

    /* === Grenzfall: players=0 -> kein Start ========================== */
    sim_init(&s, 0);
    check(sim_on_chat(&s, "/ready", "conn-a", 100.0) == 0,
          "flow: players=0 -> kein Start");
    check(s.start_epoch == 0, "flow: players=0 -> start_epoch bleibt 0");
    check(s.gate.count == 1, "flow: players=0 -> ready trotzdem gezaehlt");

    /* === US4: Timeout exakt an der Deadline, kein Start, kein Kick ===== */
    sim_init(&s, 2);
    sim_on_chat(&s, "/ready", "conn-a", 100.0); /* armt: deadline = 280.0 */
    check(s.gate.deadline == 280.0, "flow: deadline == now + 180");
    check(sim_tick(&s, 279.999) == 0,
          "flow: vor Deadline -> kein Timeout");
    check(s.start_epoch == 0, "flow: vor Deadline kein Start");
    check(sim_tick(&s, 280.0) == 1,
          "flow: GENAU an Deadline -> Timeout (Grenzfall >=)");
    check(strcmp(s.last_ev, "ready_timeout") == 0,
          "flow: Timeout -> ready_timeout-Event");
    check(s.start_epoch == 0, "flow: Timeout -> KEIN Start (PAUSED, kein Kick)");
    check(s.gate.count == 0, "flow: Timeout -> Ready-Menge geleert");
    check(s.gate.timed_out == 1, "flow: Timeout -> timed_out-Flag gesetzt");
    check(sim_tick(&s, 400.0) == 0,
          "flow: Timeout feuert nicht erneut (Menge leer)");

    /* Neuer Versuch nach Timeout: frisches /ready darf wieder starten. */
    check(sim_on_chat(&s, "/ready", "conn-a", 400.0) == 0,
          "flow: nach Timeout 1. ready (1/2)");
    check(s.gate.timed_out == 0,
          "flow: neues ready loescht Timeout-Latch");
    check(sim_on_chat(&s, "/ready", "conn-b", 401.0) == 1,
          "flow: nach Timeout erneut 2/2 -> Start");
    check(s.start_epoch == 1, "flow: nach Timeout start_epoch == 1");

    /* === US3: Runden-Reset -> Gate feuert erneut (genau einmal/Runde) == */
    sim_round_reset(&s);
    check(s.gate.fired == 0 && s.gate.timed_out == 0 && s.gate.count == 0,
          "flow: Runden-Reset loescht Latch + Menge");
    check(s.gate.players == 2, "flow: Runden-Reset behaelt players");
    check(sim_on_chat(&s, "/ready", "conn-x", 500.0) == 0,
          "flow: neue Runde 1/2 -> kein Start");
    check(sim_on_chat(&s, "/ready", "conn-y", 501.0) == 1,
          "flow: neue Runde 2/2 -> Start erneut gefeuert");
    check(s.start_epoch == 2, "flow: start_epoch == 2 (zweite Runde)");

    /* Timeout kann in der neuen Runde ebenso wieder greifen. */
    sim_round_reset(&s);
    sim_on_chat(&s, "/ready", "conn-x", 600.0);
    check(sim_tick(&s, 780.0) == 1,
          "flow: Timeout greift auch in der neuen Runde");

    printf("HOSTTEST_PASS=%d HOSTTEST_FAIL=%d\n", ht_pass, ht_fail);
    return ht_fail == 0 ? 0 : 1;
}
