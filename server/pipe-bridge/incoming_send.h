/*
 * incoming_send.h - Windows-freie Ingress-Logik fuer POST /incoming_send
 * (Issue #996, US3/G5).
 *
 * Warum header-only und ohne <windows.h>?
 *   `pipe_bridge.c` zieht winsock2/windows und ist damit fuer den
 *   Host-Compiler uninteressant. Die eigentliche Ingress-Logik (Request
 *   validieren, Ziel-Kommando fuer den Wave-Spawn in Welt B bauen) ist aber
 *   reine Logik ohne Win32 - sie liegt deshalb hier in einer eigenen,
 *   host-kompilierbaren Header-Datei, die `pipe_bridge.c` UND der Host-Test
 *   `tests/pipe-bridge-hosttest` einbinden (Muster `health_logic.h` /
 *   `ready_gate.h`).
 *
 * Vertrag (Issue #996, US3):
 *   Der Referee pusht einen gegnerischen Wellen-Send an die Bridge der
 *   Zielwelt. HTTP-Body: {"level":<int>=1>,"from":"<welt>","delay_s":<float?>}.
 *   Der Batch wird in das Ziel-Event des Protokolls uebersetzt und ueber den
 *   Pipe-/Exec-Kanal als Wave-Spawn in dieser Welt ausgeloest:
 *
 *     {"cmd":"incoming_wave","level":<n>,"from":"<welt>","delay_s":<f>}
 *
 *   (Ziel-Event `incoming_wave {level,from,delay_s}`, server/protocol.md.)
 *   Der exakte DLL-/Lua-Wire-Beweis braucht ein Live-Spiel (#252) und ist
 *   bewusst NICHT Teil dieses Host-Tests (er prueft Parse/Build/Response).
 *
 * Keine Nebenwirkungen, keine I/O: jede Funktion ist deterministisch.
 */
#ifndef RBB_PIPE_INCOMING_SEND_H
#define RBB_PIPE_INCOMING_SEND_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define INCOMING_SEND_FROM_LEN 64       /* Platz fuer Welt-Kennung/Prefix */
#define INCOMING_SEND_DEFAULT_DELAY_S 5.0 /* delay_s-Default (protocol.md) */

typedef struct {
    int level;      /* Difficulty-Level, >= 1 */
    char from[INCOMING_SEND_FROM_LEN]; /* sendende Welt (z. B. "A") */
    double delay_s; /* Spawn-Verzoegerung in Sekunden */
    int has_delay;  /* 1 = delay_s explizit im Body gesetzt */
} incoming_send_t;

/* Minimal-JSON-String-Leser: findet "key":"value" (nur String-Werte) und
 * schreibt den entpackten Wert nach out. 0 wenn nicht gefunden. Bewusst
 * dieselbe Minimal-Logik wie pipe_bridge.c/rbbridge.c (json_get_string),
 * nur mit eigenem Prefix, damit die Einbindung nicht kollidiert. */
static inline int inj_json_get_string(const char *json, const char *key,
                                      char *out, size_t out_sz)
{
    if (!json || !key || !out || out_sz == 0)
        return 0;

    size_t key_len = strlen(key);
    const char *p = json;

    while ((p = strstr(p, key)) != NULL) {
        if (p != json && p[-1] == '"' && p[key_len] == '"') {
            const char *q = p + key_len + 1;
            while (*q == ' ' || *q == '\t')
                q++;
            if (*q != ':')
                return 0;
            q++;
            while (*q == ' ' || *q == '\t')
                q++;
            if (*q != '"')
                return 0; /* nur String-Werte */
            q++;
            size_t n = 0;
            while (*q && *q != '"' && n + 1 < out_sz) {
                if (*q == '\\' && q[1]) {
                    q++;
                    if (*q == 'n')
                        out[n++] = '\n';
                    else if (*q == 't')
                        out[n++] = '\t';
                    else
                        out[n++] = *q;
                } else {
                    out[n++] = *q;
                }
                q++;
            }
            out[n] = '\0';
            return *q == '"';
        }
        p += key_len;
    }
    return 0;
}

/* Minimal-JSON-Zahl-Leser: findet "key":<zahl> ODER "key":"<zahl>". 0 wenn
 * nicht gefunden. Dieselbe Minimal-Logik wie pipe_bridge.c (json_get_number). */
static inline int inj_json_get_number(const char *json, const char *key,
                                      double *out)
{
    if (!json || !key || !out)
        return 0;

    size_t key_len = strlen(key);
    const char *p = json;

    while ((p = strstr(p, key)) != NULL) {
        if (p != json && p[-1] == '"' && p[key_len] == '"') {
            const char *q = p + key_len + 1;
            while (*q == ' ' || *q == '\t')
                q++;
            if (*q == ':') {
                q++;
                while (*q == ' ' || *q == '\t')
                    q++;
                if (*q == '"') /* auch als String geschickt: akzeptieren */
                    q++;
                {
                    char *end = NULL;
                    double v = strtod(q, &end);
                    if (end == q)
                        return 0; /* Zahlbeginn erwartet, hier keiner */
                    *out = v;
                    return 1;
                }
            }
        }
        p += key_len;
    }
    return 0;
}

/* JSON-Escape (nur `"` und `\`) fuer den `from`-Wert im Ziel-Kommando. */
static inline void inj_json_escape(const char *in, char *out, size_t out_sz)
{
    size_t n = 0;
    if (!out || out_sz == 0)
        return;
    for (const char *p = in ? in : ""; *p && n + 1 < out_sz; p++) {
        if ((*p == '\\' || *p == '"') && n + 2 < out_sz) {
            out[n++] = '\\';
            out[n++] = *p;
        } else {
            out[n++] = *p;
        }
    }
    out[n] = '\0';
}

/* Validiert den Request-Body. Rueckgabe: 1 = gueltig (level >= 1), 0 = 400.
 * `from` ist optional (Default ""), `delay_s` optional (Default 5.0 s). */
static inline int incoming_send_parse(const char *body, incoming_send_t *out)
{
    double lvl = 0.0;
    double d = 0.0;

    if (!out)
        return 0;
    memset(out, 0, sizeof(*out));
    out->delay_s = INCOMING_SEND_DEFAULT_DELAY_S;

    if (!inj_json_get_number(body, "level", &lvl))
        return 0;
    if (lvl < 1.0)
        return 0;
    out->level = (int)lvl;

    inj_json_get_string(body, "from", out->from, sizeof(out->from));

    if (inj_json_get_number(body, "delay_s", &d) && d >= 0.0) {
        out->delay_s = d;
        out->has_delay = 1;
    }
    return 1;
}

/* Baut die Pipe-Zeile (Ziel-Event `incoming_wave {level,from,delay_s}`) fuer
 * den Wave-Spawn. Rueckgabe: Laenge (>0) oder -1 bei Puffer-Ueberlauf. */
static inline int incoming_send_pipe_line(const incoming_send_t *req, char *out,
                                          size_t n)
{
    char esc_from[INCOMING_SEND_FROM_LEN * 2];
    int written;

    if (!req || !out || n == 0)
        return -1;
    inj_json_escape(req->from, esc_from, sizeof(esc_from));
    written = snprintf(out, n,
                       "{\"cmd\":\"incoming_wave\",\"level\":%d,"
                       "\"from\":\"%s\",\"delay_s\":%g}\n",
                       req->level, esc_from, req->delay_s);
    if (written < 0 || (size_t)written >= n)
        return -1;
    return written;
}

#endif /* RBB_PIPE_INCOMING_SEND_H */
