/*
 * pipe_bridge_incoming_send_hosttest.c - Host-Test fuer die Ingress-Logik
 * von POST /incoming_send (Issue #996, US3/G5).
 *
 * Bindet ausschliesslich incoming_send.h ein (Windows-frei) und prueft die
 * reine Logik: Request-Validierung (Pflichtfeld level >= 1), Feld-Extraktion
 * (from, delay_s inkl. Default/String-Form) und den Bau des Ziel-Kommandos
 * `incoming_wave {level,from,delay_s}` (inkl. Escaping + Puffer-Grenzfall).
 * Kein Windows, kein Spielprozess, kein Wine, kein Netz.
 *
 * Ausgabe: HOSTTEST_PASS=<n> HOSTTEST_FAIL=<m>
 * Exit:    0 wenn m == 0, sonst 1.
 *
 * Build (Host): cc -O1 -g -Wall -Wextra -I server/pipe-bridge -o <bin> <this>
 */
#include "incoming_send.h"

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

int main(void)
{
    incoming_send_t req;
    char line[256];

    /* --- Pflichtfeld level ----------------------------------------- */
    check(incoming_send_parse("{\"level\":3,\"from\":\"A\"}", &req) == 1,
          "parse: level=3 -> gueltig");
    check(req.level == 3, "parse: level uebernommen");
    check(strcmp(req.from, "A") == 0, "parse: from uebernommen");
    check(req.delay_s == INCOMING_SEND_DEFAULT_DELAY_S,
          "parse: delay_s Default = 5");
    check(req.has_delay == 0, "parse: has_delay Default = 0");

    /* Ungueltig: level fehlt / 0 / negativ / nicht-numerisch. */
    check(incoming_send_parse("{\"from\":\"A\"}", &req) == 0,
          "parse: level fehlt -> ungueltig");
    check(incoming_send_parse("{}", &req) == 0,
          "parse: leer -> ungueltig");
    check(incoming_send_parse("{\"level\":0}", &req) == 0,
          "parse: level=0 -> ungueltig");
    check(incoming_send_parse("{\"level\":-2}", &req) == 0,
          "parse: level<0 -> ungueltig");
    check(incoming_send_parse("{\"level\":\"x\"}", &req) == 0,
          "parse: level='x' -> ungueltig");
    check(incoming_send_parse(NULL, &req) == 0,
          "parse: NULL-Body -> ungueltig");

    /* level als String akzeptiert (json_get_number-Semantik). */
    check(incoming_send_parse("{\"level\":\"4\"}", &req) == 1,
          "parse: level='4' (String) -> gueltig");
    check(req.level == 4, "parse: String-level uebernommen");
    check(req.from[0] == '\0', "parse: from fehlt -> leer");
    check(req.has_delay == 0, "parse: delay fehlt -> Default");

    /* delay_s explizit (auch 0) und Float. */
    check(incoming_send_parse("{\"level\":2,\"delay_s\":0}", &req) == 1,
          "parse: delay_s=0 -> gueltig");
    check(req.delay_s == 0.0 && req.has_delay == 1,
          "parse: delay_s=0 uebernommen");
    check(incoming_send_parse("{\"level\":2,\"delay_s\":2.5}", &req) == 1,
          "parse: delay_s=2.5 -> gueltig");
    check(req.delay_s == 2.5, "parse: delay_s=2.5 uebernommen");
    /* negativer Delay bleibt Default (kein Spawn in der Vergangenheit). */
    check(incoming_send_parse("{\"level\":2,\"delay_s\":-1}", &req) == 1,
          "parse: delay_s<0 -> gueltig (Default)");
    check(req.delay_s == INCOMING_SEND_DEFAULT_DELAY_S && req.has_delay == 0,
          "parse: negativer delay -> Default, has_delay=0");

    /* --- Ziel-Kommando --------------------------------------------- */
    check(incoming_send_parse("{\"level\":3,\"from\":\"A\",\"delay_s\":5}", &req) == 1,
          "build: setup gueltig");
    check(incoming_send_pipe_line(&req, line, sizeof(line)) > 0,
          "build: pipe_line geschrieben");
    check_contains("build cmd", line, "\"cmd\":\"incoming_wave\"");
    check_contains("build level", line, "\"level\":3");
    check_contains("build from", line, "\"from\":\"A\"");
    check_contains("build delay", line, "\"delay_s\":5");
    check(line[strlen(line) - 1] == '\n', "build: Zeile endet mit \\n");

    /* from-Escaping: `"`/`\` werden escaped, kein Bruch. */
    check(incoming_send_parse("{\"level\":1,\"from\":\"A\\\\B\"}", &req) == 1,
          "build: from mit Backslash parst");
    check(strcmp(req.from, "A\\B") == 0, "build: from unescaped gelesen");
    check(incoming_send_pipe_line(&req, line, sizeof(line)) > 0,
          "build: pipe_line mit Backslash");
    check_contains("build esc", line, "\"from\":\"A\\\\B\"");

    /* Puffer-Grenzfall: zu kleiner Puffer -> -1. */
    check(incoming_send_pipe_line(&req, line, 8) == -1,
          "build: zu kleiner Puffer -> -1");

    printf("HOSTTEST_PASS=%d HOSTTEST_FAIL=%d\n", ht_pass, ht_fail);
    return ht_fail == 0 ? 0 : 1;
}
