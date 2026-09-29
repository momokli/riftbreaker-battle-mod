/*
 * ready_gate.h - Windows-freie Ready-Gate-Logik fuer pipe_bridge (Issue #937).
 *
 * Warum header-only und ohne <windows.h>?
 *   `pipe_bridge.c` zieht winsock2/windows sowie `cockpit_html.inc` (Issue
 *   #265) und ist damit fuer den Host-Compiler uninteressant. Das eigentliche
 *   Gate (wer ist ready? sind alle da? ist der Start faellig? Timeout?) ist
 *   aber reine Logik ohne Win32 - sie liegt deshalb hier in einer eigenen,
 *   host-kompilierbaren Header-Datei, die `pipe_bridge.c` UND der Host-Test
 *   `tests/pipe-bridge-hosttest` einbinden (Muster `health_logic.h`).
 *
 * Vertrag (Issue #937, US3/US4):
 *   - ready_gate_add(g, id) traegt eine Verbindungs-ID idempotent ein
 *     (dieselbe ID zaehlt nur EINMAL - entscheidend fuer das Gate "distinct
 *     ready == players").
 *   - ready_gate_remove(g, id) entfernt sie wieder (idempotent).
 *   - ready_gate_count(g) == Anzahl der DISTINCT ready-Spieler.
 *   - ready_gate_all_ready(g, players) == (players >= 1 && count >= players).
 *   - ready_gate_should_fire(g, players) == genau EINMAL pro Runde (fired-Latch).
 *   - ready_gate_reset(g) loescht den Runden-Latch (fired/timed_out) samt Menge
 *     und Deadline; wird beim Runden-Reset der Bridge (POST /attack_reset bzw.
 *     /round_reset mit {"reset":1}) und implizit beim Prozessstart aufgerufen.
 *   - ready_gate_expired(g, now) == Grenzfall: now >= deadline gilt als
 *     abgelaufen; nur wenn ein Gate armed ist, noch jemand fehlt und der Start
 *     nicht schon gefeuert wurde.
 *
 * Keine Nebenwirkungen, keine I/O: jede Funktion ist deterministisch.
 */
#ifndef RBB_PIPE_READY_GATE_H
#define RBB_PIPE_READY_GATE_H

#include <stdio.h>
#include <string.h>

#define RBB_READY_MAX 16      /* max. gleichzeitig ready Spieler */
#define RBB_READY_ID_LEN 48   /* Platz fuer conn_id-Hex bzw. Fallback-Text */

typedef struct {
    char ids[RBB_READY_MAX][RBB_READY_ID_LEN];
    int count;       /* Anzahl DISTINCT ready-IDs */
    int players;     /* zuletzt aus get_state_result gelesen (>0 wenn bekannt) */
    int fired;       /* Start-Signal diese Runde schon gefeuert? (Latch) */
    int timed_out;   /* Timeout-Event diese Runde schon gemeldet? */
    double deadline; /* 0.0 = kein Gate armed; sonst Ablaufzeitpunkt (s) */
} ready_gate_t;

static inline void ready_gate_init(ready_gate_t *g)
{
    if (!g)
        return;
    memset(g, 0, sizeof(*g));
}

/* Index der ID oder -1. */
static inline int ready_gate_find(const ready_gate_t *g, const char *id)
{
    int i;
    if (!g || !id || !id[0])
        return -1;
    for (i = 0; i < g->count; i++) {
        if (strcmp(g->ids[i], id) == 0)
            return i;
    }
    return -1;
}

/* Traegt id ein. Rueckgabe 1 = neu eingetragen, 0 = schon da/ungueltig/voll. */
static inline int ready_gate_add(ready_gate_t *g, const char *id)
{
    if (!g || !id || !id[0])
        return 0;
    if (ready_gate_find(g, id) >= 0)
        return 0; /* idempotent */
    if (g->count >= RBB_READY_MAX)
        return 0;
    snprintf(g->ids[g->count], RBB_READY_ID_LEN, "%s", id);
    g->count++;
    return 1;
}

/* Entfernt id. Rueckgabe 1 = entfernt, 0 = nicht vorhanden. */
static inline int ready_gate_remove(ready_gate_t *g, const char *id)
{
    int i, j;
    if (!g)
        return 0;
    i = ready_gate_find(g, id);
    if (i < 0)
        return 0;
    for (j = i; j < g->count - 1; j++)
        memcpy(g->ids[j], g->ids[j + 1], RBB_READY_ID_LEN);
    g->count--;
    return 1;
}

static inline int ready_gate_count(const ready_gate_t *g)
{
    return g ? g->count : 0;
}

/* Alle da? players muss bekannt (>=1) und erreicht sein. */
static inline int ready_gate_all_ready(const ready_gate_t *g, int players)
{
    if (!g || players < 1)
        return 0;
    return g->count >= players;
}

/* Latch: genau EIN Start pro Runde (auch bei wiederholtem Poll). */
static inline int ready_gate_should_fire(const ready_gate_t *g, int players)
{
    if (!g || g->fired)
        return 0;
    return ready_gate_all_ready(g, players);
}

/* Leert die Ready-Menge samt Deadline. fired/timed_out bleiben als Runden-
 * Latch stehen; fuer einen echten Runden-Reset ready_gate_reset() nutzen. */
static inline void ready_gate_clear(ready_gate_t *g)
{
    if (!g)
        return;
    g->count = 0;
    g->deadline = 0.0;
}

/* Runden-Reset: leert Menge + Deadline UND den Runden-Latch (fired/timed_out),
 * damit das Gate in der naechsten Runde wieder genau einmal feuern kann.
 * `players` bleibt erhalten (wird je Poll aus get_state_result aktualisiert). */
static inline void ready_gate_reset(ready_gate_t *g)
{
    if (!g)
        return;
    g->count = 0;
    g->deadline = 0.0;
    g->fired = 0;
    g->timed_out = 0;
}

/* #937/US4: Timeout-Grenzfall. Rueckgabe 1, wenn ein Gate armed ist
 * (deadline > 0), noch jemand fehlt (count > 0), der Start nicht schon gefeuert
 * wurde und now >= deadline. now == deadline gilt bewusst als abgelaufen
 * (>=), damit der Grenzfall deterministisch/testbar ist. */
static inline int ready_gate_expired(const ready_gate_t *g, double now)
{
    if (!g)
        return 0;
    if (g->deadline <= 0.0)
        return 0;
    if (g->count <= 0)
        return 0;
    if (g->fired)
        return 0;
    return now >= g->deadline;
}

/* Armt das Gate beim ersten ready (deadline = now + timeout_s), falls noetig. */
static inline void ready_gate_arm(ready_gate_t *g, double now, double timeout_s)
{
    if (!g)
        return;
    if (g->deadline <= 0.0 && g->count > 0 && timeout_s > 0.0)
        g->deadline = now + timeout_s;
}

/* JSON-Escaping fuer IDs (Fallback-IDs koennen beliebigen Text tragen). */
static inline void rbb_ready_escape(const char *in, char *out, size_t n)
{
    size_t o = 0;
    if (!out || n == 0)
        return;
    for (size_t i = 0; in && in[i] && o + 7 < n; i++) {
        unsigned char c = (unsigned char)in[i];
        if (c == '"' || c == '\\') {
            out[o++] = '\\';
            out[o++] = (char)c;
        } else if (c < 0x20) {
            o += (size_t)snprintf(out + o, n - o, "\\u%04x", c);
        } else {
            out[o++] = (char)c;
        }
    }
    out[o] = '\0';
}

/* Schreibt die Statusfelder des Gates (NUL-terminiert) nach out/n:
 *   "players":N,"ready_count":M,"ready_players":["..",".."],"ready_deadline":D
 * Rueckgabe: Laenge ohne NUL (0 = Puffer zu klein). */
static inline size_t ready_gate_status_json(const ready_gate_t *g, char *out,
                                            size_t n)
{
    size_t o = 0;
    int i, count = g ? g->count : 0;
    char tmp[RBB_READY_ID_LEN * 2];

    if (!out || n < 32)
        return 0; /* kein halbes JSON */
    out[0] = '\0';

    if (snprintf(out + o, n - o, "\"players\":%d,\"ready_count\":%d,",
                 g ? g->players : 0, count) < 0)
        return 0;
    o = strlen(out);

    if (snprintf(out + o, n - o, "\"ready_players\":[") < 0)
        return 0;
    o += strlen(out + o);
    for (i = 0; i < count; i++) {
        rbb_ready_escape(g->ids[i], tmp, sizeof(tmp));
        if (snprintf(out + o, n - o, "%s\"%s\"", i ? "," : "", tmp) < 0)
            return 0;
        o += strlen(out + o);
    }
    if (snprintf(out + o, n - o, "],\"ready_deadline\":%.1f",
                 g ? g->deadline : 0.0) < 0)
        return 0;
    o = strlen(out);
    return o;
}

#endif /* RBB_PIPE_READY_GATE_H */
