/*
 * health_logic.h - Windows-freie Health-Entscheidungslogik fuer pipe_bridge.
 *
 * Warum header-only und ohne <windows.h>?
 *   `pipe_bridge.c` zieht winsock2/windows sowie `cockpit_html.inc` (nach
 *   Issue #265) und ist damit fuer den Host-Compiler uninteressant. Die
 *   Statuscode-/Body-Wahl des /health-Endpunkts (Issue #902) ist aber
 *   reine Logik ohne Win32 - sie liegt deshalb hier in einer eigenen,
 *   host-kompilierbaren Header-Datei, die `pipe_bridge.c` UND der
 *   Host-Test `tests/pipe-bridge-hosttest` einbinden.
 *
 * Vertrag (Issue #902, US1):
 *   pipe_ok == 1  -> 200 {"ok":true,"pipe":true}
 *   pipe_ok == 0  -> 503 {"ok":false,"pipe":false,"reason":"pipe_unavailable"}
 *   deep & pipe_ok & !ping_ok -> 503
 *                    {"ok":false,"pipe":true,"ping":false,"reason":"ping_failed"}
 *   deep & pipe_ok &  ping_ok -> 200 {"ok":true,"pipe":true}
 *
 *   Der reine Connect-Probe (Default /health) kennt KEINEN ping -> das
 *   Body-Feld "ping" wird dann bewusst weggelassen (Bestandsformat bleibt
 *   fuer pipe_ok=1 bitgleich zu vorher, nur der Statuscode ist ehrlich).
 *
 * Keine Nebenwirkungen, keine I/O: jede Funktion ist deterministisch und
 * kann beliebig oft aufgerufen werden.
 */
#ifndef RBB_PIPE_HEALTH_LOGIC_H
#define RBB_PIPE_HEALTH_LOGIC_H

#include <stdio.h>

/* HTTP-Status fuer den reinen Connect-Probe (kein DLL-Ping). */
static inline int bridge_health_status(int pipe_ok)
{
    return pipe_ok ? 200 : 503;
}

/* HTTP-Status fuer den Deep-Probe: 200 nur, wenn Pipe UND DLL-Ping stehen. */
static inline int bridge_health_status_deep(int pipe_ok, int ping_ok)
{
    if (!pipe_ok)
        return 503;
    return ping_ok ? 200 : 503;
}

/* Schreibt den JSON-Body (immer NUL-terminiert) nach out/n. */
static inline void bridge_health_body(int pipe_ok, int ping_ok, int deep,
                                      char *out, size_t n)
{
    if (!out || n == 0)
        return;

    if (!pipe_ok) {
        snprintf(out, n,
                 "{\"ok\":false,\"pipe\":false,\"reason\":\"pipe_unavailable\"}");
        return;
    }

    if (deep && !ping_ok) {
        snprintf(out, n,
                 "{\"ok\":false,\"pipe\":true,\"ping\":false,"
                 "\"reason\":\"ping_failed\"}");
        return;
    }

    snprintf(out, n, "{\"ok\":true,\"pipe\":true}");
}

#endif /* RBB_PIPE_HEALTH_LOGIC_H */
