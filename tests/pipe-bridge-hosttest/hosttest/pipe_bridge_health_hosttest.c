/*
 * pipe_bridge_health_hosttest.c - Host-Test fuer die /health-Logik
 * (Issue #902, US2).
 *
 * Bindet ausschliesslich health_logic.h ein (Windows-frei) und prueft
 * Statuscode + JSON-Body fuer alle Kombinationen aus pipe_ok, ping_ok und
 * deep. "Pipe kuenstlich trennen" ist hier pipe_ok=0 - damit ist Abnahme
 * (a) (`pipe:false` -> non-2xx) ohne Spielsession, ohne Windows, ohne Netz
 * und ohne Wine bewiesen.
 *
 * Ausgabe: HOSTTEST_PASS=<n> HOSTTEST_FAIL=<m>
 * Exit:    0 wenn m == 0, sonst 1.
 *
 * Build (Host): cc -O1 -g -Wall -Wextra -I server/pipe-bridge -o <bin> <this>
 */
#include "health_logic.h"

#include <stdio.h>
#include <string.h>

static int ht_pass = 0;
static int ht_fail = 0;

static void check_int(const char *label, int got, int want)
{
    if (got == want) {
        ht_pass++;
    } else {
        ht_fail++;
        printf("FAIL %s: got=%d want=%d\n", label, got, want);
    }
}

static void check_contains(const char *label, const char *hay, const char *needle)
{
    if (hay && strstr(hay, needle)) {
        ht_pass++;
    } else {
        ht_fail++;
        printf("FAIL %s: \"%s\" nicht in \"%s\"\n", label, needle,
               hay ? hay : "(null)");
    }
}

static void check_not_contains(const char *label, const char *hay,
                               const char *needle)
{
    if (hay && !strstr(hay, needle)) {
        ht_pass++;
    } else {
        ht_fail++;
        printf("FAIL %s: \"%s\" unerwartet in \"%s\"\n", label, needle,
               hay ? hay : "(null)");
    }
}

/* Prueft Body <needle>-Praesenz fuer die gegebene Kombination. */
static void body_case(int pipe_ok, int ping_ok, int deep)
{
    char body[256];
    memset(body, 0, sizeof(body));
    bridge_health_body(pipe_ok, ping_ok, deep, body, sizeof(body));
    printf("body(p=%d,ping=%d,deep=%d) = %s\n", pipe_ok, ping_ok, deep, body);
}

int main(void)
{
    char body[256];

    /* --- connect-probe (deep=0), Pipe steht: 200 + Bestandsformat ------ */
    check_int("status(1)", bridge_health_status(1), 200);
    bridge_health_body(1, 0, 0, body, sizeof(body));
    check_contains("body(p=1,ping=0,deep=0) ok:true", body, "\"ok\":true");
    check_contains("body(p=1,ping=0,deep=0) pipe:true", body, "\"pipe\":true");
    check_not_contains("body(p=1,ping=0,deep=0) kein ping-Feld", body,
                       "\"ping\"");
    check_not_contains("body(p=1,ping=0,deep=0) keine reason", body,
                       "\"reason\"");

    /* --- connect-probe (deep=0), Pipe tot: 503 + pipe_unavailable ----- */
    check_int("status(0)", bridge_health_status(0), 503);
    bridge_health_body(0, 0, 0, body, sizeof(body));
    check_contains("body(p=0,ping=0,deep=0) ok:false", body, "\"ok\":false");
    check_contains("body(p=0,ping=0,deep=0) pipe:false", body, "\"pipe\":false");
    check_contains("body(p=0,ping=0,deep=0) reason", body,
                   "\"reason\":\"pipe_unavailable\"");

    /* --- deep, Pipe tot: 503 + pipe_unavailable (kein Ping-Ziel) ------ */
    check_int("status_deep(0,0)", bridge_health_status_deep(0, 0), 503);
    check_int("status_deep(0,1)", bridge_health_status_deep(0, 1), 503);
    bridge_health_body(0, 1, 1, body, sizeof(body));
    check_contains("body(p=0,ping=1,deep=1) reason pipe_unavailable", body,
                   "\"reason\":\"pipe_unavailable\"");
    check_contains("body(p=0,ping=1,deep=1) pipe:false", body, "\"pipe\":false");

    /* --- deep, Pipe steht aber Ping faellt: 503 + ping_failed --------- */
    check_int("status_deep(1,0)", bridge_health_status_deep(1, 0), 503);
    bridge_health_body(1, 0, 1, body, sizeof(body));
    check_contains("body(p=1,ping=0,deep=1) ok:false", body, "\"ok\":false");
    check_contains("body(p=1,ping=0,deep=1) pipe:true", body, "\"pipe\":true");
    check_contains("body(p=1,ping=0,deep=1) ping:false", body, "\"ping\":false");
    check_contains("body(p=1,ping=0,deep=1) reason ping_failed", body,
                   "\"reason\":\"ping_failed\"");

    /* --- deep, Pipe steht und Ping ok: 200 + ok:true ------------------ */
    check_int("status_deep(1,1)", bridge_health_status_deep(1, 1), 200);
    bridge_health_body(1, 1, 1, body, sizeof(body));
    check_contains("body(p=1,ping=1,deep=1) ok:true", body, "\"ok\":true");
    check_contains("body(p=1,ping=1,deep=1) pipe:true", body, "\"pipe\":true");
    check_not_contains("body(p=1,ping=1,deep=1) kein reason", body,
                       "\"reason\"");

    /* --- Robustheit: kleiner Puffer bleibt NUL-terminiert, kein Crash - */
    {
        char tiny[16];
        memset(tiny, 'X', sizeof(tiny));
        bridge_health_body(0, 0, 1, tiny, sizeof(tiny));
        check_int("tiny NUL-terminiert", tiny[sizeof(tiny) - 1] == '\0', 1);
        check_int("tiny nicht ueberlaufen", tiny[0] == '{', 1);
    }
    /* n==0 / out==NULL duerfen nicht schreiben. */
    bridge_health_body(1, 1, 1, NULL, 0);
    check_int("NULL-out ueberlebt", 1, 1);

    body_case(1, 1, 1);

    printf("HOSTTEST_PASS=%d HOSTTEST_FAIL=%d\n", ht_pass, ht_fail);
    return ht_fail == 0 ? 0 : 1;
}
