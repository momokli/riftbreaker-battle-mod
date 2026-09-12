/*
 * rbbridge.c - In-Game-Bridge fuer den Rift-Breaker-Trainer (Harness),
 *              dual: Trainer-DLL ODER Standalone-EXE.
 *
 * Rolle (Architektur, siehe trainer/README.md):
 *   Die Trainer-DLL ist das EINZIGE I/O-Gateway zwischen Spielprozess und
 *   Aussenwelt (Tournament-Server). Der Lua-Mod bleibt reine Spiellogik.
 *   Diese DLL wird per Injector zur Laufzeit geladen (keine Datei-Engine-
 *   Eingriffe, Steam-kompatibel) und stellt einen Named-Pipe-Server bereit.
 *
 * Dual-Mode: Derselbe Quelltext baut zwei Varianten - die Pipe-Server-
 *   Logik liegt in rbbridge_start() und wird von beiden gerufen:
 *     - rbbridge.dll (Default, per Injector in den Spielprozess laden)
 *     - rbbridge_standalone.exe (#define RBBRIDGE_STANDALONE): dieselbe
 *       Server-Logik als normales Programm, damit ist die Trainer-IO auf
 *       jedem Windows-Rechner OHNE Injection testbar (Baustein 04, Test 0).
 *   Protokollverhalten ist in beiden Varianten IDENTISCH; die Standalone-
 *   Variante druckt nur eine Hinweiszeile beim Start.
 *
 * Was der Harness schon kann:
 *   - Named-Pipe-Server "\\.\pipe\rbbattle" (ein Client zur Zeit, v0)
 *   - Line-delimited-JSON-Protokoll v0 (siehe trainer/protocol.md):
 *       Ingress: {"cmd":"ping"}                      -> {"event":"pong"}
 *                {"cmd":"exec","command":"rb_wave 3"}-> dispatch_exec()
 *                (dispatch_exec ruft seit RE-Stand 2.0.58485 die echte
 *                 ConsoleService::ExecuteCommand() im Spielprozess auf,
 *                 siehe Abschnitt "ConsoleService-Anbindung" unten)
 *       Egress : {"event":"score_update","score":...,"resources":{...},
 *                "wave":...} (periodischer State-Snapshot, Issue #13,
 *                alle 5 s solange ein Client verbunden ist)
 *   - Robustheit: Fehler im Pipe-Dienst duerfen das Spiel NIEMALS
 *     abstuerzen; kein Client/kein Connect = ruhiger Wartethread; Client-
 *     disconnect = automatischer Reconnect ins naechste Connect.
 *   - Logging: OutputDebugString (DebugView) + %TEMP%\rbbridge.log.
 *
 * Was RE-abhaengig noch offen ist (TODO/FIXME im Code; Phase 2 des Projekts):
 *   - read_game_state(): echte Spiel-State-Werte (Score, Ressourcen, Wave)
 *     aus dem Prozess lesen statt Defaults (alles 0). Der score_update-
 *     Egress (send_state) ist damit strukturell schon verdrahtet
 *     (dispatch_exec selbst ist seit dem RE-Stand unten implementiert).
 *
 * Wichtig:
 *   - Kein Datei-I/O ueber die Lua-API noetig - alles laeuft hier in der DLL.
 *   - DllMain macht NICHTS Schweres (Loader-Lock): nur Thread starten.
 *   - Ein FreeLibrary zur Laufzeit (waerend der Pipe-Thread blockiert) wird
 *     best effort behandelt (Wake-up-Connect + kurzes Join), ist aber kein
 *     unterstuetzter Fall: Ueblich ist Inject-once / unload beim Prozessende.
 *
 * Build (x64):
 *   1) Trainer-DLL (Injection):
 *      MinGW-w64 : x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -shared -o rbbridge.dll rbbridge.c
 *      MSVC      : cl /nologo /O2 /W3 /LD rbbridge.c /Fe:rbbridge.dll
 *   2) Standalone-EXE (kein Injection noetig, Testmodus):
 *      MinGW-w64 : x86_64-w64-mingw32-gcc -O2 -Wall -Wextra -DRBBRIDGE_STANDALONE -o rbbridge_standalone.exe rbbridge.c
 *      MSVC      : cl /nologo /O2 /W3 /DRBBRIDGE_STANDALONE rbbridge.c /Fe:rbbridge_standalone.exe
 *      (-lws2_32 ist nicht noetig: die Named Pipe nutzt nur Win32-API.)
 */

#ifdef RBBRIDGE_HOSTTEST
/*
 * Host-Test-Build (tests/e2e-vollkette, KEIN Windows noetig):
 *   -DRBBRIDGE_HOSTTEST kompiliert AUSSCHLIESSLICH die reinen Scan-/RTTI-
 *   Funktionen (scan_bytes/scan_u32/scan_u64/resolve_console_vftable/
 *   resolve_console_service) gegen einen SYNTHETISCHEN PE-artigen Puffer.
 *   Der Shim stellt die minimalen Win32-Typen bereit und lenkt
 *   VirtualQuery/GetModuleHandleA auf den Testpuffer um - kein Spielprozess,
 *   kein Windows, kein Netz. Der echte Windows-Build (MinGW/MSVC, siehe
 *   #else) ist davon unberuehrt.
 */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef int BOOL;
typedef void *HANDLE;
typedef void *HMODULE;
typedef void *LPVOID;
typedef unsigned long DWORD;
typedef int32_t LONG;
typedef size_t SIZE_T;
typedef uint16_t WORD;

#define WINAPI

#define MEM_COMMIT   0x1000
#define MEM_FREE     0x10000
#define PAGE_GUARD   0x100
#define PAGE_READONLY 0x02
#define PAGE_READWRITE 0x04
#define PAGE_WRITECOPY 0x08
#define PAGE_EXECUTE_READ 0x20
#define PAGE_EXECUTE_READWRITE 0x40
#define PAGE_EXECUTE_WRITECOPY 0x80

#define IMAGE_DOS_SIGNATURE 0x5A4D     /* 'MZ'   */
#define IMAGE_NT_SIGNATURE  0x00004550 /* 'PE\0\0' */

typedef struct {
    void   *BaseAddress;
    void   *AllocationBase;
    DWORD   AllocationProtect;
    DWORD   __pad0;
    SIZE_T  RegionSize;
    DWORD   State;
    DWORD   Protect;
    DWORD   Type;
    DWORD   __pad1;
} MEMORY_BASIC_INFORMATION;

typedef struct {
    uint16_t e_magic;      /* 0x00 */
    uint8_t  _pad0[0x3A];  /* 0x02..0x3B */
    int32_t  e_lfanew;     /* 0x3C */
} IMAGE_DOS_HEADER;

typedef struct {
    uint16_t Machine;
    uint16_t NumberOfSections;
    uint32_t TimeDateStamp;
    uint32_t PointerToSymbolTable;
    uint32_t NumberOfSymbols;
    uint16_t SizeOfOptionalHeader;
    uint16_t Characteristics;
} IMAGE_FILE_HEADER;

typedef struct {
    uint8_t  _pad0[0x38];  /* Felder bis SizeOfImage */
    uint32_t SizeOfImage;  /* 0x38 */
    uint8_t  _pad1[0xB4];  /* Rest; sizeof == 0xF0 (wie x64-PE) */
} IMAGE_OPTIONAL_HEADER;

typedef struct {
    uint32_t Signature;
    IMAGE_FILE_HEADER FileHeader;
    IMAGE_OPTIONAL_HEADER OptionalHeader;
} IMAGE_NT_HEADERS;

typedef union {
    uint32_t PhysicalAddress;
    uint32_t VirtualSize;
} IMAGE_SECTION_MISC;

typedef struct {
    uint8_t Name[8];
    IMAGE_SECTION_MISC Misc;
    uint32_t VirtualAddress;
    uint32_t SizeOfRawData;
    uint32_t PointerToRawData;
    uint32_t PointerToRelocations;
    uint32_t PointerToLinenumbers;
    uint16_t NumberOfRelocations;
    uint16_t NumberOfLinenumbers;
    uint32_t Characteristics;
} IMAGE_SECTION_HEADER;

#define IMAGE_FIRST_SECTION(nthead) \
    ((IMAGE_SECTION_HEADER *)((uintptr_t)(nthead) + \
      offsetof(IMAGE_NT_HEADERS, OptionalHeader) + \
      (nthead)->FileHeader.SizeOfOptionalHeader))

/* Synthetisches Modul/Adressraum - die Tests setzen es per ht_set_module().
 *
 * g_ht_module_base ist die LOADER-Sicht (GetModuleHandleA/W), g_ht_region_*
 * die VirtualQuery-Sicht. ht_set_module() setzt beide gemeinsam;
 * ht_set_loader_visible(0) versteckt NUR die Loader-Sicht (Wine-Zielszenario:
 * Stufe (a) liefert NULL, obwohl die Region per VirtualQuery sichtbar bleibt)
 * und macht damit Stufe (d) (Signatur -> VirtualQuery->AllocationBase) positiv
 * testbar (Review B1). g_ht_region_type liefert den Type der synthetischen
 * Region (0 wie zuvor, oder MEM_IMAGE fuer Stufe (d)). */
static unsigned char *g_ht_module_base = NULL;
static unsigned char *g_ht_region_base = NULL;
static size_t         g_ht_region_size = 0;
static int            g_ht_region_type = 0;  /* 0 | MEM_IMAGE (Stufe d) */
static const void    *g_ht_own_base = NULL;  /* Override "eigenes Image"  */

static void ht_set_module(void *base, size_t size)
{
    g_ht_module_base = (unsigned char *)base;
    g_ht_region_base = (unsigned char *)base;
    g_ht_region_size = size;
}

/* Loader-Sicht (GetModuleHandleA/W) ein-/ausblenden, Region bleibt bestehen. */
static void ht_set_loader_visible(int visible)
{
    g_ht_module_base = visible ? g_ht_region_base : NULL;
}

static void ht_set_region_type(int type) { g_ht_region_type = type; }

/* Erzwingt die fuer den "eigenes Image"-Ausschluss massgebliche Basis. */
static void ht_set_own_base(const void *base) { g_ht_own_base = base; }

static void *ht_GetModuleHandleA(const char *name)
{
    (void)name;
    return (void *)g_ht_module_base; /* NULL == Modul nicht geladen */
}
#define GetModuleHandleA ht_GetModuleHandleA

/*
 * Minimal-VirtualQuery auf genau EINER synthetischen Region:
 *   addr <  base            -> freie Region von addr bis base (haelt die
 *                              Scanschleifen laufen, wie echte MEM_FREE-
 *                              Regionen unter Windows)
 *   base <= addr < base+size -> MEM_COMMIT/PAGE_READWRITE (lesbar)
 *   addr >= base+size        -> 0 (Ende, Scan bricht ab)
 */
static SIZE_T ht_VirtualQuery(const void *addr, MEMORY_BASIC_INFORMATION *mi,
                              SIZE_T mi_len)
{
    (void)mi_len;
    uintptr_t a, b, e;
    if (!g_ht_region_base)
        return 0;
    a = (uintptr_t)addr;
    b = (uintptr_t)g_ht_region_base;
    e = b + g_ht_region_size;
    if (a >= e)
        return 0;
    if (a < b) {
        mi->BaseAddress = (void *)a;
        mi->AllocationBase = (void *)a;
        mi->AllocationProtect = 0;
        mi->RegionSize = (SIZE_T)(b - a);
        mi->State = MEM_FREE;
        mi->Protect = 0;
        mi->Type = 0;
        return sizeof(*mi);
    }
    mi->BaseAddress = (void *)b;
    mi->AllocationBase = (void *)b;
    mi->AllocationProtect = PAGE_READWRITE;
    mi->RegionSize = (SIZE_T)(e - b);
    mi->State = MEM_COMMIT;
    mi->Protect = PAGE_READWRITE;
    mi->Type = (DWORD)g_ht_region_type; /* Default 0; B1: MEM_IMAGE */
    return sizeof(*mi);
}
#define VirtualQuery ht_VirtualQuery

/*
 * Shim fuer die #252-Modul-Resolution im Host-Test: die zusaetzlichen
 * Win32-Primitive (GetModuleHandleW, psapi-Enumeration, Toolhelp32,
 * LoadLibraryA) existieren hier als INERTE Stubs, damit rbbridge.c ohne
 * Windows kompiliert und deterministisch bleibt. Der synthetische Puffer
 * wird ausschliesslich ueber ht_set_module() (Stufe getmodulehandle)
 * bereitgestellt; alle weiteren Stufen liefern im Host-Test 0/NULL.
 */
#define MEM_IMAGE             0x1000000
#define IMAGE_FILE_MACHINE_AMD64 0x8664
#define MAX_PATH              260
#define INVALID_HANDLE_VALUE  ((HANDLE)(intptr_t)-1)

#define TH32CS_SNAPMODULE   0x00000008
#define TH32CS_SNAPMODULE32 0x00000010

typedef struct {
    DWORD    dwSize;
    DWORD    th32ModuleID;
    DWORD    th32ProcessID;
    DWORD    GlblcntUsage;
    DWORD    ProccntUsage;
    void    *modBaseAddr;
    DWORD    modBaseSize;
    HMODULE  hModule;
    wchar_t  szModule[MAX_PATH];
    wchar_t  szExePath[MAX_PATH];
} MODULEENTRY32W;

static HMODULE ht_GetModuleHandleW(const wchar_t *name)
{
    (void)name;
    return (HMODULE)g_ht_module_base; /* NULL == Modul nicht geladen */
}
#define GetModuleHandleW ht_GetModuleHandleW

static int ht_wcsicmp(const wchar_t *a, const wchar_t *b)
{
    for (;; a++, b++) {
        wchar_t x = *a, y = *b;
        if (x >= L'A' && x <= L'Z') x = (wchar_t)(x + 32);
        if (y >= L'A' && y <= L'Z') y = (wchar_t)(y + 32);
        if (x != y) return (int)x - (int)y;
        if (x == 0) return 0;
    }
}
#define _wcsicmp ht_wcsicmp

static HANDLE ht_GetCurrentProcess(void) { return (HANDLE)(intptr_t)-1; }
#define GetCurrentProcess ht_GetCurrentProcess

static DWORD ht_GetCurrentProcessId(void) { return 4242u; }
#define GetCurrentProcessId ht_GetCurrentProcessId

/* psapi-Modul-Enumeration: im Host-Test inaktiv. */
static BOOL ht_EnumProcessModules(HANDLE h, void *mods, SIZE_T cb, DWORD *need)
{
    (void)h; (void)mods; (void)cb; (void)need;
    return 0;
}
#define EnumProcessModules ht_EnumProcessModules

static DWORD ht_GetModuleBaseNameW(HANDLE h, HMODULE m, wchar_t *buf, DWORD n)
{
    (void)h; (void)m; (void)buf; (void)n;
    return 0;
}
#define GetModuleBaseNameW ht_GetModuleBaseNameW

static DWORD ht_GetModuleFileNameExW(HANDLE h, HMODULE m, wchar_t *buf, DWORD n)
{
    (void)h; (void)m; (void)buf; (void)n;
    return 0;
}
#define GetModuleFileNameExW ht_GetModuleFileNameExW

/* Toolhelp32-Modul-Snapshot: im Host-Test inaktiv. */
static HANDLE ht_CreateToolhelp32Snapshot(DWORD flags, DWORD pid)
{
    (void)flags; (void)pid;
    return INVALID_HANDLE_VALUE;
}
#define CreateToolhelp32Snapshot ht_CreateToolhelp32Snapshot

static BOOL ht_Module32FirstW(HANDLE snap, MODULEENTRY32W *me)
{
    (void)snap; (void)me;
    return 0;
}
#define Module32FirstW ht_Module32FirstW

static BOOL ht_Module32NextW(HANDLE snap, MODULEENTRY32W *me)
{
    (void)snap; (void)me;
    return 0;
}
#define Module32NextW ht_Module32NextW

static BOOL ht_CloseHandle(HANDLE h) { (void)h; return 1; }
#define CloseHandle ht_CloseHandle

/* LoadLibraryA-Fallback: im Host-Test inaktiv (kein Fund). */
static HMODULE ht_LoadLibraryA(const char *name) { (void)name; return NULL; }
#define LoadLibraryA ht_LoadLibraryA

#else /* !RBBRIDGE_HOSTTEST: echter Windows-Build */

#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601 /* GetTickCount64, Win7+ */
#endif

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <psapi.h>    /* Issue #252: EnumProcessModules/GetModuleBaseNameW ... */
#include <tlhelp32.h> /* Issue #252: Toolhelp32 Module32FirstW/NextW           */

#endif /* RBBRIDGE_HOSTTEST */

/* Host-Test-Hook fuer die "eigene Imagebasis" in module_via_sigbase().
 * Produktion: IMMER NULL (die Basis wird real per VirtualQuery bestimmt).
 * Host-Test: per ht_set_own_base() erzwingbar (deterministischer Negativfall). */
#ifdef RBBRIDGE_HOSTTEST
#define RBBRIDGE_OWN_IMAGE_BASE() ((const unsigned char *)g_ht_own_base)
#else
#define RBBRIDGE_OWN_IMAGE_BASE() ((const unsigned char *)NULL)
#endif

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* ------------------------------------------------------------------ */
/* Konstanten                                                          */
/* ------------------------------------------------------------------ */

#define PIPE_NAME_A "\\\\.\\pipe\\rbbattle"
#define PIPE_NAME_W L"\\\\.\\pipe\\rbbattle"

#define PIPE_INBUF_SIZE   4096 /* Lesechunk vom Pipe-Client              */
#define PIPE_LINE_MAX     8192 /* max. Laenge einer Protokollzeile       */
#define RESP_BUF_SIZE     4096 /* max. Laenge einer Antwortzeile         */
#define HEARTBEAT_MS      5000 /* Intervall des State-Platzhalter-Events */
#define POLL_MS           100  /* Serviceloop-Takt (nur bei Client)      */

#ifndef RBBRIDGE_HOSTTEST
/* Datei-Log: 1 = %TEMP%\rbbridge.log mitschreiben (Default an; abschalten
 * mit Umgebungsvariable RBBRIDGE_LOG=0). DebugView geht immer. */
static int g_file_log = 1;

/* ------------------------------------------------------------------ */
/* Globaler Zustand                                                    */
/* ------------------------------------------------------------------ */

static volatile LONG g_stop = 0;   /* 1 = Thread soll sich beenden       */
static HANDLE g_thread = NULL;     /* Handle des Pipe-Server-Threads     */
static LONG g_thread_started = 0;  /* verhindert doppelte Attach-Threads */
static CRITICAL_SECTION g_log_cs;  /* schuetzt das Datei-Log             */
#endif /* !RBBRIDGE_HOSTTEST */

/* ------------------------------------------------------------------ */
/* Logging (OutputDebugString + optionale Datei)                       */
/* ------------------------------------------------------------------ */

static void dbg(const char *fmt, ...)
{
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);

#ifdef RBBRIDGE_HOSTTEST
    (void)buf; /* Host-Test: kein Debug-/Datei-Log */
#else
    OutputDebugStringA(buf);

    if (!g_file_log)
        return;

    /* Anhaengen an %TEMP%\rbbridge.log - bewusst pro Zeile oeffnen/
     * schliessen: einfach, robust, kein Handle-Lebenszyklus-Problem. */
    EnterCriticalSection(&g_log_cs);
    wchar_t path[MAX_PATH];
    if (GetTempPathW(MAX_PATH, path) != 0) {
        size_t plen = wcslen(path);
        if (plen + 14 < MAX_PATH) { /* + "rbbridge.log" */
            wcscpy(path + plen, L"rbbridge.log");
            HANDLE h = CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ,
                                   NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
            if (h != INVALID_HANDLE_VALUE) {
                char out[1152];
                int n = snprintf(out, sizeof(out), "[pid=%lu tid=%lu] %s\r\n",
                                 (unsigned long)GetCurrentProcessId(),
                                 (unsigned long)GetCurrentThreadId(), buf);
                if (n > 0) {
                    DWORD written = 0;
                    WriteFile(h, out, (DWORD)n, &written, NULL);
                }
                CloseHandle(h);
            }
        }
    }
    LeaveCriticalSection(&g_log_cs);
#endif /* !RBBRIDGE_HOSTTEST */
}

#ifndef RBBRIDGE_HOSTTEST

/* ------------------------------------------------------------------ */
/* Minimal-JSON-Helfer (nur fuer das flache v0-Format noetig)          */
/* ------------------------------------------------------------------ */

/*
 * Sucht in einem flachen JSON-Objekt den Wert des Keys "key".
 * Erwartet das Muster:  ... "key" : "wert" ...
 * Kopiert den String-Wert (ohne Anfuehrungszeichen, einfache Backslash-
 * Escapes wie \" \\ werden aufgeloest) nach out. Rueckgabe 1 = gefunden.
 *
 * Kein vollwertiger Parser - bewusst klein; das v0-Protokoll ist flach und
 * wird von uns selbst erzeugt. FIXME(spaeter): echte JSON-Lib, falls das
 * Protokoll verschachtelt wird.
 */
static int json_get_string(const char *json, const char *key,
                           char *out, size_t out_sz)
{
    if (!json || !key || !out || out_sz == 0)
        return 0;

    size_t key_len = strlen(key);
    const char *p = json;

    while ((p = strstr(p, key)) != NULL) {
        /* Key muss in Anfuehrungszeichen stehen: vorher schliessendes ",
         * danach oeffnendes " */
        if (p != json && p[-1] == '"' && p[key_len] == '"') {
            const char *q = p + key_len + 1; /* hinter schliessendem " */
            while (*q == ' ' || *q == '\t')
                q++;
            if (*q != ':')
                return 0;
            q++;
            while (*q == ' ' || *q == '\t')
                q++;
            if (*q != '"')
                return 0; /* wir unterstuetzen nur String-Werte */
            q++;
            size_t n = 0;
            while (*q && *q != '"' && n + 1 < out_sz) {
                if (*q == '\\' && q[1]) { /* Minimal-Escapes */
                    q++;
                    if (*q == 'n')
                        out[n++] = '\n';
                    else if (*q == 't')
                        out[n++] = '\t';
                    else
                        out[n++] = *q; /* \\ \" \/ ... -> Zeichen selbst */
                } else {
                    out[n++] = *q;
                }
                q++;
            }
            out[n] = '\0';
            return *q == '"'; /* sauber geschlossen? */
        }
        p += key_len;
    }
    return 0;
}

/*
 * Kopiert einen String JSON-sicher (escaped \ und ") nach out.
 * Minimale Variante; reicht fuer Kommando-Echos im v0-Protokoll.
 */
static void json_escape(const char *in, char *out, size_t out_sz)
{
    size_t n = 0;
    for (const char *p = in; *p && n + 1 < out_sz; p++) {
        if (*p == '\\' || *p == '"') {
            if (n + 2 < out_sz) {
                out[n++] = '\\';
                out[n++] = *p;
            }
        } else {
            out[n++] = *p;
        }
    }
    out[n] = '\0';
}

/* ------------------------------------------------------------------ */
/* Antworten auf die Pipe schreiben                                    */
/* ------------------------------------------------------------------ */

/*
 * Sendet eine fertige Zeile an den Client. Protokoll v0 ist strikt
 * zeilenbasiert: JEDE outbound JSON-Nachricht wird mit '\n' terminiert
 * (Fix fuer den Newline-Quirk - frueher fehlte der Terminator und Clients
 * mussten roh/ohne Zeilenstruktur lesen).
 * Rueckgabe 0 = ok, -1 = Fehler (Client weg o.ae.).
 */
static int send_line(HANDLE hPipe, const char *fmt, ...)
{
    char buf[RESP_BUF_SIZE];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n < 0)
        return -1;
    if ((size_t)n >= sizeof(buf) - 1) /* Platz fuer '\n' + NUL lassen */
        n = (int)sizeof(buf) - 2;
    buf[n++] = '\n';
    buf[n] = '\0';

    DWORD written = 0;
    if (!WriteFile(hPipe, buf, (DWORD)n, &written, NULL) || written != (DWORD)n) {
        dbg("send_line: WriteFile fehlgeschlagen (GLE=%lu)", GetLastError());
        return -1;
    }
    return 0;
}

/*
 * Spiel-State-Egress (Issue #13): periodischer State-Snapshot.
 *
 * send_state() emittiert score_update gemaeß trainer/protocol.md (Score,
 * Ressourcen, aktuelle Wave). Die Struktur ist stabil und wird vom Server
 * (POST /report event=score_update) und der Web-UI konsumiert; die Werte
 * kommen aus read_game_state().
 */
typedef struct {
    uint64_t score;
    uint64_t resources_iron;
    uint64_t resources_carbon;
    uint32_t wave;
} game_state_t;

/*
 * Liest den aktuellen Spiel-State aus dem Prozess.
 *
 * FIXME(RE): echte Werte (Score, Ressourcen, aktuelle Wave) ueber die per
 * scan/ ermittelten Adressen/Signaturen lesen. Bis dahin liefert diese
 * Funktion einen neutralen Snapshot (alles 0): Das Protokoll ist damit
 * end-to-end verdrahtet, die Werte folgen in der RE-Phase.
 */
static void read_game_state(game_state_t *st)
{
    memset(st, 0, sizeof(*st));
}

/*
 * send_state(): periodischer State-Snapshot (Egress, Issue #13).
 *
 * Emittiert score_update gemaeß trainer/protocol.md. Die Werte stammen aus
 * read_game_state() (bis zur RE-Phase Defaults, alles 0).
 */
static void send_state(HANDLE hPipe)
{
    game_state_t st;
    read_game_state(&st);
    send_line(hPipe,
              "{\"event\":\"score_update\",\"t\":%llu,\"score\":%llu,"
              "\"resources\":{\"iron\":%llu,\"carbon\":%llu},\"wave\":%u}",
              (unsigned long long)GetTickCount64(),
              (unsigned long long)st.score,
              (unsigned long long)st.resources_iron,
              (unsigned long long)st.resources_carbon,
              (unsigned)st.wave);
}

#endif /* !RBBRIDGE_HOSTTEST: JSON-Helfer + send_line/game_state */

/* ------------------------------------------------------------------ */
/* ConsoleService-Anbindung (RE, AOB-/Signatur-basiert)                */
/*                                                                    */
/* Ziel: dispatch_exec() soll ConsoleService::ExecuteCommand(char      */
/* const*) im laufenden Dedicated-Server ausfuehren - OHNE feste RVAs. */
/* Alle Adressen werden zur Laufzeit aus der geladenen                 */
/* riftbreaker_dll_win_release.dll aufgeloest (damit ASLR-/Update-     */
/* fest):                                                              */
/*                                                                    */
/*   a) ExecuteCommand per Byte-Signatur im .text der Modulabbildung:  */
/*      48 89 5C 24 08 57 48 83 EC 50 48 8B DA E8 4E C8 5B 00         */
/*      48 8B F8 E8 D6 1F 93 00 48 89 (28 Bytes, Build 2.0.58485 /     */
/*      0.34.3, im .text eindeutig; entspricht RVA 0x1C0BEF0, wird     */
/*      aber NICHT als RVA verwendet).                                */
/*                                                                    */
/*   b) ConsoleService-vftable per RTTI-Walk:                         */
/*      - MSVC-RTTI-String ".?AVConsoleService@Exor@@" im Modul ->     */
/*        nameRva.                                                    */
/*      - MSVC-TypeDescriptor: name liegt bei TD+0x10 (davor liegen    */
/*        pVFTable + spare), also TD = nameRva - 0x10.                */
/*      - pTypeDescriptor ist ein DWORD (image-relative RVA == TD-     */
/*        RVA) im CompleteObjectLocator (COL); die Fundstelle ist      */
/*        COL+0xC. Verifikation: COL.signature == 1 und COL.pSelf ==   */
/*        COL-RVA (pSelf liegt bei COL+0x14, image-relativ).           */
/*      - Der vftable-Zeiger (QWORD == Modulbasis + COL-RVA) liegt     */
/*        bei vftable-8 -> vftable = Fundstelle + 8.                  */
/*                                                                    */
/*   c) Instanz: Scan des eigenen Adressraums (VirtualQuery-Schleife,  */
/*      nur MEM_COMMIT + lesbar, kein PAGE_GUARD) nach einem           */
/*      8-Byte-alignierten QWORD == vftable. Erster Treffer = this.    */
/*                                                                    */
/*   d) NICHT-Fund an JEDER Stelle -> dispatch_exec liefert             */
/*      {"event":"exec_result","ok":false,...,"reason":"..."} + dbg()  */
/*      und ruft NIEMALS auf. Unter MinGW-x64 gibt es kein SEH         */
/*      (__try/__except ist MSVC-only) - die Absicherung ist der       */
/*      Instanz-/Signatur-Check VOR dem Aufruf.                        */
/*                                                                    */
/* Gegenprobe (read-only pefile+capstone, planet, 2026-09-11): Die    */
/* AOB/RTTI-Aufloesung liefert exakt die frueheren festen RVAs         */
/* (vftable 0x2F23C80, execfn 0x1C0BEF0) - die RVAs sind damit nur     */
/* noch Verifikations-Notiz, keine Laufzeitadresse.                    */
/* ------------------------------------------------------------------ */

#define RBBRIDGE_MODULE_NAME   "riftbreaker_dll_win_release.dll"
#define RBBRIDGE_MODULE_BASE   "riftbreaker_dll_win_release"
#define RBBRIDGE_MODULE_NAME_W L"riftbreaker_dll_win_release.dll"
#define RBBRIDGE_MODULE_BASE_W L"riftbreaker_dll_win_release"

/* MSVC-RTTI-Name der ConsoleService-Klasse (mit NUL-Terminator). */
static const char RBBRIDGE_RTTI_NAME[] = ".?AVConsoleService@Exor@@";

/*
 * Byte-Signatur des ExecuteCommand-Prologs (28 Bytes, Build 2.0.58485 /
 * 0.34.3, siehe Kopfkommentar).
 *
 * ACHTUNG Build-Bindung: die Bytes 14..17 und 22..25 sind die
 * rel32-Displacements der beiden CALL-Anweisungen (E8). Sie aendern sich
 * mit JEDEM Rebuild der Engine, weil die relativen Call-Ziele wandern.
 * Sie werden daher per Byte-Maske als Wildcards behandelt (0x00 ==
 * don't care), waehrend die E8-Opcodes (Index 13 und 21) erhalten bleiben.
 * Damit haengt die Signatur nicht mehr an zwei konkreten rel32-Werten;
 * sie ist an den Build 2.0.58485 kalibriert (beide Displacements damals
 * E8 4E C8 5B 00 / E8 D6 1F 93 00) und muss bei einem Engine-Update
 * gegen die neue .text-Gegenprobe nachgezogen werden.
 */
static const unsigned char RBBRIDGE_EXEC_SIG[] = {
    0x48, 0x89, 0x5C, 0x24, 0x08, 0x57, 0x48, 0x83, 0xEC, 0x50,
    0x48, 0x8B, 0xDA, 0xE8, 0x4E, 0xC8, 0x5B, 0x00, 0x48, 0x8B,
    0xF8, 0xE8, 0xD6, 0x1F, 0x93, 0x00, 0x48, 0x89
};

/* Byte-Maske zur Signatur: 0x00 = Wildcard (don't care). Nur die
 * rel32-Operanden der beiden E8-CALLs sind maskiert, die Opcodes selbst
 * (Index 13/21) bleiben fest. */
static const unsigned char RBBRIDGE_EXEC_SIG_MASK[] = {
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF,
    0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF
};

/* x64-Aufrufkonvention: this=RCX, cmd=RDX - __fastcall ist auf x64 der
 * Standard (das Schluesselwort dokumentiert die Konvention nur). */
#ifdef RBBRIDGE_HOSTTEST
typedef void (*console_exec_fn)(void *self, const char *command);
#else
typedef void (__fastcall *console_exec_fn)(void *self, const char *command);
#endif

/*
 * Lesbare, committete Region ohne PAGE_GUARD? (Lesen dort ist sicher.)
 */
static int is_readable_region(const MEMORY_BASIC_INFORMATION *mi)
{
    if (mi->State != MEM_COMMIT || (mi->Protect & PAGE_GUARD))
        return 0;
    switch (mi->Protect & 0xFF) {
    case PAGE_READONLY:
    case PAGE_READWRITE:
    case PAGE_WRITECOPY:
    case PAGE_EXECUTE_READ:
    case PAGE_EXECUTE_READWRITE:
    case PAGE_EXECUTE_WRITECOPY:
        return 1;
    default:
        return 0;
    }
}

/*
 * Vergleicht n Bytes an p mit dem Muster pat unter der Byte-Maske mask
 * (mask == NULL bedeutet: alle Bytes muessen exakt passen). Rueckgabe 1 =
 * Treffer. Reiner Speichervergleich, keine Win32-Abhaengigkeit.
 */
static int sig_matches(const unsigned char *p, const unsigned char *pat,
                       const unsigned char *mask, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        unsigned char m = mask ? mask[i] : 0xFF;
        if (((p[i] ^ pat[i]) & m) != 0)
            return 0;
    }
    return 1;
}

/*
 * Sucht [start, start+len) nach einem Byte-Muster (mit optionaler
 * Byte-Maske, mask==NULL == exakt) und respektiert dabei den Seitenschutz
 * per VirtualQuery (nur MEM_COMMIT + lesbar, kein PAGE_GUARD).
 * Rueckgabe: erster Treffer oder NULL.
 */
static const unsigned char *scan_bytes_mask(const unsigned char *start,
                                            size_t len,
                                            const unsigned char *pat,
                                            const unsigned char *mask,
                                            size_t pat_len)
{
    if (!start || pat_len == 0 || len < pat_len)
        return NULL;

    uintptr_t addr = (uintptr_t)start;
    uintptr_t end  = (uintptr_t)start + len; /* SizeOfImage, kein Overflow */

    while (addr < end) {
        MEMORY_BASIC_INFORMATION mi;
        if (!VirtualQuery((const void *)addr, &mi, sizeof(mi)))
            break;
        uintptr_t next = (uintptr_t)mi.BaseAddress + mi.RegionSize;
        if (!is_readable_region(&mi)) {
            if (next <= addr)
                break;
            addr = next;
            continue;
        }
        uintptr_t rend = next < end ? next : end;
        if (rend > addr && (size_t)(rend - addr) >= pat_len) {
            const unsigned char *p = (const unsigned char *)addr;
            size_t n = (size_t)(rend - addr);
            for (size_t i = 0; i + pat_len <= n; i++) {
                if (sig_matches(p + i, pat, mask, pat_len))
                    return p + i;
            }
        }
        if (next <= addr)
            break;
        addr = next;
    }
    return NULL;
}

/* Exakte Suche (keine Wildcards) - Wrapper um scan_bytes_mask(). */
static const unsigned char *scan_bytes(const unsigned char *start, size_t len,
                                       const unsigned char *pat, size_t pat_len)
{
    return scan_bytes_mask(start, len, pat, NULL, pat_len);
}

static const unsigned char *scan_u32(const unsigned char *start, size_t len,
                                     uint32_t val)
{
    unsigned char pat[4];
    memcpy(pat, &val, sizeof(pat));
    return scan_bytes(start, len, pat, sizeof(pat));
}

static const unsigned char *scan_u64(const unsigned char *start, size_t len,
                                     uint64_t val)
{
    unsigned char pat[8];
    memcpy(pat, &val, sizeof(pat));
    return scan_bytes(start, len, pat, sizeof(pat));
}

/* ------------------------------------------------------------------ */
/* Modulbasis-Aufloesung (Wine-robust, loader-unabhaengig)             */
/*                                                                    */
/* GetModuleHandleA(name) liefert unter Wine GLE=126                  */
/* (ERROR_MOD_NOT_FOUND): Wine fuehrt das Spiel-DLL nicht zuverlaessig */
/* unter seinem Basisnamen in der Loader-Liste, obwohl es geladen ist. */
/* Deshalb wird die Modulbasis in mehreren Stufen bestimmt - die       */
/* erste erfolgreiche gewinnt, jede Stufe loggt per dbg() welcher Weg  */
/* griff (module_range: via=...):                                     */
/*   a) getmodulehandle: GetModuleHandleA/W mit/ohne ".dll"            */
/*   b) enum:            EnumProcessModules + GetModuleBaseNameW /     */
/*                       GetModuleFileNameExW (psapi)                  */
/*   c) toolhelp:        CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|   */
/*                       TH32CS_SNAPMODULE32) + Module32FirstW/NextW   */
/*   d) sigbase:         ExecuteCommand-Signatur (RBBRIDGE_EXEC_SIG)   */
/*                       im GESAMTEN eigenen Adressraum suchen         */
/*                       (VirtualQuery, nur lesbar/committet) und die  */
/*                       Modulbasis aus VirtualQuery(execfn)->         */
/*                       AllocationBase (MEM_IMAGE) ableiten + PE-Check*/
/*   e) loadlibrary:     LoadLibraryA als letzter Versuch (nur Basis)  */
/*                                                                    */
/* Nach Stufe (d) ist GetModuleHandleA irrelevant: der Signatur-Scan   */
/* findet die Basis unabhaengig vom Loader. Jede Stufe validiert MZ/PE  */
/* + SizeOfImage, bevor sie akzeptiert wird.                          */
/* ------------------------------------------------------------------ */

/* Vorwaerts-Deklaration: der Signatur-Scan validiert einen Kandidaten
 * damit, dass sich in dessen Modul die ConsoleService-vftable wirklich
 * aufloesen laesst (siehe module_via_sigbase). */
static const unsigned char *resolve_console_vftable(const unsigned char *base,
                                                    size_t size);

/* Ist [base] ein x64-PE-Image? Setzt *out_size = SizeOfImage.
 * Defensiv: MZ-Check, e_lfanew-Schranke (hinter dem DOS-Header und in
 * plausiblen Grenzen, schuetzt vor absurden Kandidaten aus Stufe (d)),
 * PE-Signatur, x64-Machine und SizeOfImage > 0. */
static int pe_image_size(const unsigned char *base, size_t *out_size)
{
    if (!base)
        return 0;
    const IMAGE_DOS_HEADER *dos = (const IMAGE_DOS_HEADER *)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE)
        return 0;
    uint32_t e_lfanew = (uint32_t)dos->e_lfanew;
    if (e_lfanew < (uint32_t)sizeof(IMAGE_DOS_HEADER) || e_lfanew > 0x1000u)
        return 0;
    const IMAGE_NT_HEADERS *nt =
        (const IMAGE_NT_HEADERS *)(base + e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE)
        return 0;
    if (nt->FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64)
        return 0;
    if (nt->OptionalHeader.SizeOfImage == 0)
        return 0;
    *out_size = (size_t)nt->OptionalHeader.SizeOfImage;
    return 1;
}

/* Vergleicht den Basenamen (nach letztem '/' bzw. '\') case-insensitiv. */
static int wname_is_module_icase(const wchar_t *path)
{
    const wchar_t *b = path;
    for (const wchar_t *p = path; *p; p++) {
        if (*p == L'\\' || *p == L'/')
            b = p + 1;
    }
    return _wcsicmp(b, RBBRIDGE_MODULE_NAME_W) == 0 ||
           _wcsicmp(b, RBBRIDGE_MODULE_BASE_W) == 0;
}

/* a) GetModuleHandleA/W mit/ohne ".dll" (und W-Variante). */
static int module_via_getmodulehandle(const unsigned char **out_base,
                                      size_t *out_size)
{
    HMODULE h;
    if ((h = GetModuleHandleA(RBBRIDGE_MODULE_NAME)) != NULL &&
        pe_image_size((const unsigned char *)h, out_size)) {
        *out_base = (const unsigned char *)h;
        return 1;
    }
    if ((h = GetModuleHandleA(RBBRIDGE_MODULE_BASE)) != NULL &&
        pe_image_size((const unsigned char *)h, out_size)) {
        *out_base = (const unsigned char *)h;
        return 1;
    }
    if ((h = GetModuleHandleW(RBBRIDGE_MODULE_NAME_W)) != NULL &&
        pe_image_size((const unsigned char *)h, out_size)) {
        *out_base = (const unsigned char *)h;
        return 1;
    }
    if ((h = GetModuleHandleW(RBBRIDGE_MODULE_BASE_W)) != NULL &&
        pe_image_size((const unsigned char *)h, out_size)) {
        *out_base = (const unsigned char *)h;
        return 1;
    }
    return 0;
}

/* b) EnumProcessModules + GetModuleBaseNameW/GetModuleFileNameExW (psapi). */
static int module_via_enum(const unsigned char **out_base, size_t *out_size)
{
    HMODULE mods[1024];
    DWORD needed = 0;
    HANDLE self = GetCurrentProcess();
    if (!EnumProcessModules(self, mods, sizeof(mods), &needed))
        return 0;
    size_t count = needed / sizeof(HMODULE);
    if (count > sizeof(mods) / sizeof(mods[0]))
        count = sizeof(mods) / sizeof(mods[0]);
    for (size_t i = 0; i < count; i++) {
        wchar_t name[MAX_PATH];
        if (GetModuleBaseNameW(self, mods[i], name, MAX_PATH) &&
            wname_is_module_icase(name) &&
            pe_image_size((const unsigned char *)mods[i], out_size)) {
            *out_base = (const unsigned char *)mods[i];
            return 1;
        }
        wchar_t path[MAX_PATH];
        if (GetModuleFileNameExW(self, mods[i], path, MAX_PATH) &&
            wname_is_module_icase(path) &&
            pe_image_size((const unsigned char *)mods[i], out_size)) {
            *out_base = (const unsigned char *)mods[i];
            return 1;
        }
    }
    return 0;
}

/* c) Toolhelp32-Modul-Snapshot (kernel32, kein psapi noetig). */
static int module_via_toolhelp(const unsigned char **out_base, size_t *out_size)
{
    HANDLE snap = CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, GetCurrentProcessId());
    if (snap == INVALID_HANDLE_VALUE)
        return 0;
    MODULEENTRY32W me;
    me.dwSize = sizeof(me);
    int found = 0;
    for (BOOL ok = Module32FirstW(snap, &me); ok; ok = Module32NextW(snap, &me)) {
        if ((wname_is_module_icase(me.szModule) ||
             wname_is_module_icase(me.szExePath)) &&
            pe_image_size((const unsigned char *)me.modBaseAddr, out_size)) {
            *out_base = (const unsigned char *)me.modBaseAddr;
            found = 1;
            break;
        }
    }
    CloseHandle(snap);
    return found;
}

/*
 * d) Loader-unabhaengig (wichtigster Fallback): ExecuteCommand-Signatur im
 * gesamten eigenen Adressraum suchen (VirtualQuery-Schleife, nur lesbare,
 * committete Regionen ohne PAGE_GUARD - wie scan_bytes) und die Modulbasis
 * aus VirtualQuery(execfn)->AllocationBase ableiten (bei MEM_IMAGE = Image-
 * base) + PE-Check. Liefert die gefundene execfn gleich mit zurueck (spart
 * den zweiten Scan in resolve_console_service).
 */
static int module_via_sigbase(const unsigned char **out_base, size_t *out_size,
                              const unsigned char **out_execfn)
{
    /* Eigenes Modul ausschliessen: RBBRIDGE_EXEC_SIG liegt als Konstante
     * auch im eigenen Image und wuerde sonst als "Treffer" erkannt.
     * Im Host-Test kann die "eigene" Basis per ht_set_own_base() erzwungen
     * werden (deterministischer Negativfall, Review B1). */
    const unsigned char *own = RBBRIDGE_OWN_IMAGE_BASE();
    if (!own) {
        MEMORY_BASIC_INFORMATION smi;
        if (VirtualQuery((const void *)&module_via_sigbase, &smi, sizeof(smi)))
            own = (const unsigned char *)smi.AllocationBase;
    }

    uintptr_t addr = 0;
    for (;;) {
        MEMORY_BASIC_INFORMATION mi;
        if (!VirtualQuery((const void *)addr, &mi, sizeof(mi)))
            break; /* Ende des Adressraums */
        uintptr_t next = (uintptr_t)mi.BaseAddress + mi.RegionSize;
        if (next <= addr) /* Overflow-Schutz */
            break;
        addr = next;
        if (!is_readable_region(&mi))
            continue;
        const unsigned char *p = (const unsigned char *)mi.BaseAddress;
        size_t n = (size_t)mi.RegionSize;
        for (size_t i = 0; i + sizeof(RBBRIDGE_EXEC_SIG) <= n; i++) {
            /* Maskierter Vergleich wie im .text-Scan (#251-Tip): die
             * rel32-Operanden der beiden E8-CALLs sind Wildcards. */
            if (!sig_matches(p + i, RBBRIDGE_EXEC_SIG,
                             RBBRIDGE_EXEC_SIG_MASK,
                             sizeof(RBBRIDGE_EXEC_SIG)))
                continue;
            const unsigned char *fn = p + i;
            MEMORY_BASIC_INFORMATION fmi;
            if (!VirtualQuery((const void *)fn, &fmi, sizeof(fmi)))
                continue;
            if (fmi.Type != MEM_IMAGE)
                continue;
            const unsigned char *b = (const unsigned char *)fmi.AllocationBase;
            if (b == own) /* unser eigenes Image -> kein Kandidat */
                continue;
            if (!pe_image_size(b, out_size))
                continue;
            /* Nur akzeptieren, wenn sich im Kandidatenmodul die
             * ConsoleService-vftable wirklich aufloesen laesst. Das ver-
             * wirft unser eigenes Image bzw. weitere rbbridge-Kopien, die
             * RBBRIDGE_EXEC_SIG/RTTI nur als Konstanten mitbringen. */
            if (!resolve_console_vftable(b, *out_size))
                continue;
            *out_base = b;
            *out_execfn = fn;
            return 1;
        }
    }
    return 0;
}

/*
 * e) Letzter Versuch: LoadLibraryA - das geladene Handle dient NUR als
 * Modulbasis. (Wine kann hier das bereits geladene Modul zurueckgeben;
 * andernfalls liefert der anschliessende Instanz-Scan keinen Treffer und
 * dispatch_exec faellt weiterhin graceful zurueck, KEIN Aufruf.)
 */
static int module_via_loadlibrary(const unsigned char **out_base,
                                  size_t *out_size)
{
    HMODULE h = LoadLibraryA(RBBRIDGE_MODULE_NAME);
    if (!h || !pe_image_size((const unsigned char *)h, out_size))
        return 0;
    *out_base = (const unsigned char *)h;
    return 1;
}

/*
 * Modulbasis + SizeOfImage von riftbreaker_dll_win_release.dll, Wine-robust.
 * Setzt *out_via auf die erfolgreiche Stufe (fuer das dbg()-Log) und
 * *out_execfn auf eine per Adressraum-Scan gefundene ExecuteCommand-Adresse
 * (oder NULL). Rueckgabe 1 = ok (base/size gesetzt), 0 = nirgends gefunden.
 */
static int resolve_module(const unsigned char **out_base, size_t *out_size,
                          const char **out_via,
                          const unsigned char **out_execfn)
{
    const unsigned char *base = NULL;
    size_t size = 0;

    *out_via = "none";
    *out_execfn = NULL;

    if (module_via_getmodulehandle(&base, &size)) {
        *out_via = "getmodulehandle";
    } else if (module_via_enum(&base, &size)) {
        *out_via = "enum";
    } else if (module_via_toolhelp(&base, &size)) {
        *out_via = "toolhelp";
    } else if (module_via_sigbase(&base, &size, out_execfn)) {
        *out_via = "sigbase";
    } else if (module_via_loadlibrary(&base, &size)) {
        *out_via = "loadlibrary";
    } else {
        dbg("module_range: '%s' nicht aufloesbar (GetModuleHandle A/W, "
            "EnumProcessModules, Toolhelp32, Adressraum-Signatur, "
            "LoadLibraryA alle fehlgeschlagen)",
            RBBRIDGE_MODULE_NAME);
        return 0;
    }

    dbg("module_range: via=%s base=%p size=%lu execfn=%p", *out_via,
        (void *)base, (unsigned long)size, (void *)*out_execfn);
    *out_base = base;
    *out_size = size;
    return 1;
}

/*
 * .text-Bereich (Code-Section) der Modulabbildung.
 * Rueckgabe 1 = ok, 0 = nicht gefunden.
 */
static int text_range(const unsigned char *base,
                      const unsigned char **out, size_t *out_len)
{
    const IMAGE_DOS_HEADER *dos = (const IMAGE_DOS_HEADER *)base;
    const IMAGE_NT_HEADERS *nt =
        (const IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    const IMAGE_SECTION_HEADER *sec = IMAGE_FIRST_SECTION(nt);
    for (unsigned i = 0; i < nt->FileHeader.NumberOfSections; i++, sec++) {
        if (memcmp(sec->Name, ".text", 5) == 0) {
            *out = base + sec->VirtualAddress;
            *out_len = sec->Misc.VirtualSize;
            return 1;
        }
    }
    return 0;
}

/*
 * Plausibilitaets-Check fuer einen vftable-Kandidaten (Risiko "First hit =
 * this"): Die vftable muss im Modul-Image liegen und als erste Referenz
 * einen weiteren Image-Zeiger enthalten (echte MSVC-vftables zeigen nur in
 * den Code des Moduls). execfn wird - falls er in den ersten 128 Slots
 * auftaucht - als starkes Zusatzsignal gemeldet; er ist NICHT Pflicht, weil
 * ExecuteCommand nicht virtuell sein muss. Reicht die Plausibilitaet nicht,
 * wird der Kandidat verworfen (kein Aufruf, Fehler-Event).
 * Rueckgabe 1 = plausibel, 0 = verwerfen.
 */
static int looks_like_vftable(const unsigned char *vftable,
                              const unsigned char *execfn,
                              const unsigned char *base, size_t size,
                              int *out_has_execfn)
{
    const unsigned char *img_end = base + size;
    if (out_has_execfn)
        *out_has_execfn = 0;
    if (!vftable || vftable < base || vftable + 8 > img_end)
        return 0;

    uint64_t first = 0;
    memcpy(&first, vftable, sizeof(first));
    const unsigned char *p0 = (const unsigned char *)(uintptr_t)first;
    if (p0 < base || p0 >= img_end)
        return 0; /* erste Referenz zeigt nicht ins Modul */

    for (size_t i = 0; i < 128; i++) {
        const unsigned char *slot = vftable + 8 * i;
        if (slot + 8 > img_end)
            break;
        uint64_t v = 0;
        memcpy(&v, slot, sizeof(v));
        if ((const unsigned char *)(uintptr_t)v == execfn) {
            if (out_has_execfn)
                *out_has_execfn = 1;
            break;
        }
    }
    return 1;
}

/*
 * Findet die ConsoleService-vftable per RTTI-Walk (siehe Kopfkommentar).
 * Rueckgabe: vftable-Adresse oder NULL.
 */
static const unsigned char *resolve_console_vftable(const unsigned char *base,
                                                    size_t size)
{
    /* sizeof incl. NUL-Terminator -> eindeutiger String-Treffer. */
    const unsigned char *name = scan_bytes(
        base, size, (const unsigned char *)RBBRIDGE_RTTI_NAME,
        sizeof(RBBRIDGE_RTTI_NAME));
    if (!name) {
        dbg("resolve_console_vftable: RTTI-Name '%s' nicht gefunden",
            RBBRIDGE_RTTI_NAME);
        return NULL;
    }

    const unsigned char *td = name - 0x10; /* TypeDescriptor-Beginn */
    uint32_t td_rva = (uint32_t)(uintptr_t)(td - base);

    /* Kandidaten fuer das pTypeDescriptor-Feld (COL+0xC) durchgehen:
     * erstes DWORD im Modul == td_rva; COL pruefen, sonst weiter. */
    const unsigned char *p = base;
    for (;;) {
        const unsigned char *hit = scan_u32(p, (size_t)((base + size) - p),
                                            td_rva);
        if (!hit)
            break;
        const unsigned char *col = hit - 0xC;
        uint32_t col_rva = (uint32_t)(uintptr_t)(col - base);
        uint32_t sig = 0, pself = 0;
        memcpy(&sig, col, sizeof(sig));            /* COL.signature   */
        memcpy(&pself, col + 0x14, sizeof(pself)); /* COL.pSelf (RVA) */
        if (sig == 1 && pself == col_rva) {
            /* COL-Zeiger liegt bei vftable-8 -> vftable = Fundstelle+8. */
            const unsigned char *ref = scan_u64(
                base, size, (uint64_t)(uintptr_t)(base + col_rva));
            if (!ref) {
                dbg("resolve_console_vftable: COL(rva=%08lx) gefunden, aber "
                    "kein vftable-Zeiger", (unsigned long)col_rva);
                return NULL;
            }
            const unsigned char *vftable = ref + 8;
            dbg("resolve_console_vftable: name=%p TD(rva=%08lx) COL(rva=%08lx) "
                "vftable(rva=%08lx)",
                (void *)name, (unsigned long)td_rva, (unsigned long)col_rva,
                (unsigned long)(uintptr_t)(vftable - base));
            return vftable;
        }
        p = hit + 1; /* falscher COL-Kandidat -> weiter suchen */
    }

    dbg("resolve_console_vftable: kein gueltiger COL fuer TD(rva=%08lx)",
        (unsigned long)td_rva);
    return NULL;
}

/*
 * Findet die ConsoleService-Instanz: 8-Byte-alignierter QWORD == vftable
 * im eigenen Adressraum (VirtualQuery-Schleife, nur lesbare Regionen).
 * Der Vergleich ist zugleich Self-check: die Fundstelle als this
 * interpretiert beginnt also mit ihrer vftable.
 * Rueckgabe: this oder NULL - der Aufrufer MUSS NULL als "nicht
 * verfuegbar" behandeln (Fehler-Event statt Crash).
 */
static void *resolve_console_instance(const unsigned char *vftable)
{
    uint64_t needle = (uint64_t)(uintptr_t)vftable;
    int hits = 0;
    void *instance = NULL;
    uintptr_t addr = 0;

    for (;;) {
        MEMORY_BASIC_INFORMATION mi;
        if (VirtualQuery((const void *)addr, &mi, sizeof(mi)) == 0)
            break; /* Ende des Adressraums */
        uintptr_t next = (uintptr_t)mi.BaseAddress + mi.RegionSize;
        if (next <= addr) /* Overflow-Schutz (kommt praktisch nie vor) */
            break;
        addr = next;

        if (!is_readable_region(&mi))
            continue;

        const uint64_t *q = (const uint64_t *)mi.BaseAddress;
        size_t nq = mi.RegionSize / sizeof(uint64_t); /* BaseAddress ist
                                                         seiten-, also auch
                                                         8-Byte-aligniert */
        for (size_t i = 0; i < nq; i++) {
            if (q[i] != needle)
                continue;
            hits++;
            if (instance == NULL)
                instance = (void *)&q[i]; /* erster plausibler Kandidat */
        }
    }
    dbg("resolve_console_instance: vftable=%p hits=%d instance=%p",
        (void *)vftable, hits, instance);
    return instance;
}

/*
 * Einmal aufgeloeste ConsoleService-Anbindung (Risiko: Voll-Scan des
 * Adressraums pro exec). Das Ergebnis wird gecacht und bei Folgeaufrufen
 * nur BILLIG re-validiert:
 *   - Modul-PE-Header an der gecachten Basis noch gueltig + gleiche Groesse?
 *     (pe_image_size; UNTER WINE liefert GetModuleHandleA NULL -> die
 *     Gueltigkeit darf nicht daran haengen, siehe Re-Validierung unten)
 *   - zeigt *(void**)instance noch auf die gecachte vftable?
 *   - stehen die Signatur-Bytes noch an fn? (sig_matches, maskiert)
 * Schlaegt eine Pruefung fehl, wird der Cache verworfen und voll neu
 * gescannt. Der Dispatch laeuft ausschliesslich im Pipe-Thread (eine
 * Verbindung zur Zeit) -> kein Lock noetig; waere der Dispatch
 * multithreaded, muesste der Cache synchronisiert werden (offen).
 */
typedef struct {
    int                  valid;
    const unsigned char *module_base;
    size_t               module_size;
    const unsigned char *fn;
    void                *instance;
    const unsigned char *vftable;
} console_cache_t;

static console_cache_t g_console_cache;

/*
 * Loest ExecuteCommand (Signatur) und ConsoleService (RTTI + Instanz)
 * auf und gibt beides zurueck. Nutzt einen statischen Cache (siehe oben).
 * Rueckgabe 1 = ok (fn/instance gesetzt), 0 = nicht gefunden.
 * Bei 0 sind fn/instance unbestimmt -> NICHT aufrufen.
 */
static int resolve_console_service(console_exec_fn *out_fn, void **out_inst)
{
    /* 1) Billige Re-Validierung eines evtl. vorhandenen Cache-Treffers.
     *
     * ACHTUNG Wine (#252): GetModuleHandleA(name) liefert dort NULL
     * (GLE=126), obwohl das Modul geladen ist. Die Re-Validierung darf
     * deshalb NICHT (allein) an GetModuleHandleA haengen - sonst wird der
     * Cache bei JEDEM exec verworfen und es folgt ein Voll-Scan-Stall.
     * Liefert GetModuleHandleA aber eine Basis, MUSS sie zur gecachten
     * passen (Modul entladen/neu geladen -> neuer Scan). Die eigentliche
     * Gueltigkeit tragen der PE-Header an der gecachten Basis + der
     * Instanz-/vftable- und Signatur-Check. */
    if (g_console_cache.valid) {
        void *mod = GetModuleHandleA(RBBRIDGE_MODULE_NAME);
        void *cur_vftable = NULL;
        size_t cur_size = 0;
        memcpy(&cur_vftable, g_console_cache.instance, sizeof(cur_vftable));
        int mod_ok = (mod == NULL) ||
                     ((const unsigned char *)mod == g_console_cache.module_base);
        if (mod_ok &&
            pe_image_size(g_console_cache.module_base, &cur_size) &&
            cur_size == g_console_cache.module_size &&
            cur_vftable == g_console_cache.vftable &&
            sig_matches(g_console_cache.fn, RBBRIDGE_EXEC_SIG,
                        RBBRIDGE_EXEC_SIG_MASK, sizeof(RBBRIDGE_EXEC_SIG))) {
            *out_fn = (console_exec_fn)(uintptr_t)g_console_cache.fn;
            *out_inst = g_console_cache.instance;
            return 1; /* Cache-Treffer, kein Voll-Scan */
        }
        g_console_cache.valid = 0; /* ungueltig -> voll neu scannen */
    }

    const unsigned char *base = NULL;
    size_t size = 0;
    const char *via = NULL;
    const unsigned char *execfn = NULL;

    if (!resolve_module(&base, &size, &via, &execfn))
        return 0;

    if (!execfn) {
        /* AOB bleibt Pflicht: bevorzugt im .text, sonst im gesamten Abbild.
         * Maskierter Scan (#251-Tip): die rel32-Operanden der beiden E8-CALLs
         * sind Wildcards (RBBRIDGE_EXEC_SIG_MASK), nur die E8-Opcodes sind
         * fest - so haengt die Signatur nicht an konkreten Call-Zielen. */
        const unsigned char *text = NULL;
        size_t text_len = 0;
        if (text_range(base, &text, &text_len))
            execfn = scan_bytes_mask(text, text_len, RBBRIDGE_EXEC_SIG,
                                     RBBRIDGE_EXEC_SIG_MASK,
                                     sizeof(RBBRIDGE_EXEC_SIG));
        if (!execfn)
            execfn = scan_bytes_mask(base, size, RBBRIDGE_EXEC_SIG,
                                     RBBRIDGE_EXEC_SIG_MASK,
                                     sizeof(RBBRIDGE_EXEC_SIG));
    }
    if (!execfn) {
        dbg("resolve_console_service: ExecuteCommand-Signatur nicht gefunden "
            "(base=%p size=%lu via=%s)", (void *)base,
            (unsigned long)size, via);
        return 0;
    }

    const unsigned char *vftable = resolve_console_vftable(base, size);
    if (!vftable)
        return 0;

    int has_execfn = 0;
    if (!looks_like_vftable(vftable, execfn, base, size, &has_execfn)) {
        dbg("resolve_console_service: vftable(rva=%08lx) verworfen "
            "(keine plausible vftable)",
            (unsigned long)(uintptr_t)(vftable - base));
        return 0;
    }

    void *instance = resolve_console_instance(vftable);
    if (!instance)
        return 0;

    dbg("resolve_console_service: base=%p vftable(rva=%08lx) execfn(rva=%08lx) "
        "instance=%p exec_in_vftable=%d",
        (void *)base, (unsigned long)(uintptr_t)(vftable - base),
        (unsigned long)(uintptr_t)(execfn - base), instance, has_execfn);

    /* Cache fuellen (Folgeaufrufe nur noch billig re-validieren). */
    g_console_cache.valid = 1;
    g_console_cache.module_base = base;
    g_console_cache.module_size = size;
    g_console_cache.fn = execfn;
    g_console_cache.instance = instance;
    g_console_cache.vftable = vftable;

    *out_fn = (console_exec_fn)(uintptr_t)execfn;
    *out_inst = instance;
    return 1;
}

#ifndef RBBRIDGE_HOSTTEST

/* ------------------------------------------------------------------ */
/* Dispatch: Ingress-Kommandos                                         */
/* ------------------------------------------------------------------ */

/*
 * {"cmd":"exec","command":"rb_wave 3"}
 *
 * Fuehrt das Kommando im Spiel aus - aequivalent zu
 *   ConsoleService::ExecuteCommand("rb_wave 3")
 * aus der Lua-Perspektive (der Lua-Mod registriert rb_wave, siehe
 * mod/lua/rbbattle_autoexec.lua im Spike-Branch).
 *
 * RE-Stand (Build 2.0.58485, GOG == Dedi, verifiziert 2026-09-09):
 *   - Adressen: resolve_console_service() loest ExecuteCommand per
 *     Byte-Signatur (Modul-.text) und die ConsoleService-Instanz per
 *     RTTI-Walk (vftable) + Adressraum-Scan auf - KEINE festen RVAs,
 *     damit ASLR-/Update-fest (Details im Kopfkommentar oben).
 *   - Aufruf: console_exec_fn(inst, command), direkt im Pipe-Thread.
 *     pcall-artige Absicherung gibt es unter MinGW-x64 in C nicht (kein
 *     __try/__except; nur MSVC kann das) - die Absicherung ist der
 *     Signatur-/Instanz-Check: ohne gueltige Aufloesung wird NICHT
 *     aufgerufen, sondern ein Fehler-Event gesendet.
 *
 * OFFENES RISIKO (Thread-Marshalling, nicht host-seitig entscheidbar):
 *   fn(instance, command) laeuft im Pipe-Thread, NICHT auf dem Main-/
 *   Spiel-Thread. Ob ConsoleService::ExecuteCommand thread-safe ist bzw.
 *   auf den Spiel-Thread gemarshalled werden MUSS, ist nicht belegt und
 *   wird erst der Live-Test (Spielprozess, #252) zeigen. Bis dahin gilt:
 *   kein Beweis fuer Threadsicherheit - nicht als erledigt betrachten.
 *   Der Aufruf selbst ist gegen Nicht-Fund abgesichert (Fehler-Event),
 *   gegen einen Fehl-Fund nur teilweise (siehe looks_like_vftable).
 *
 * Antwort bei Erfolg: {"event":"exec_result","ok":true,"command":"..."}
 */
static void dispatch_exec(HANDLE hPipe, const char *command)
{
    char escaped[RESP_BUF_SIZE];
    json_escape(command, escaped, sizeof(escaped));

    console_exec_fn fn = NULL;
    void *instance = NULL;
    if (!resolve_console_service(&fn, &instance)) {
        dbg("dispatch_exec: command='%s' -> ConsoleService/ExecuteCommand "
            "nicht aufloesbar, KEIN Aufruf", command);
        send_line(hPipe,
                  "{\"event\":\"exec_result\",\"ok\":false,"
                  "\"command\":\"%s\",\"reason\":"
                  "\"console_service_not_found\"}",
                  escaped);
        return;
    }

    dbg("dispatch_exec: command='%s' -> ExecuteCommand(inst=%p, fn=%p)",
        command, instance, (void *)fn);
    fn(instance, command); /* x64: this=RCX, cmd=RDX */

    dbg("dispatch_exec: command='%s' -> zurueckgekehrt (ok)", command);
    send_line(hPipe,
              "{\"event\":\"exec_result\",\"ok\":true,"
              "\"command\":\"%s\"}",
              escaped);
}

/*
 * Verteilt eine empfangene Protokollzeile (ohne \n).
 * Unbekanntes/Nicht-JSON wird geloggt und (nur bei JSON-artigen Zeilen)
 * mit einem error-Event beantwortet - der Client soll Feedback bekommen,
 * das Spiel darf sich nie daran stoeren.
 */
static void handle_line(HANDLE hPipe, const char *line)
{
    char cmd[64] = "";
    char command[512] = "";

    if (!json_get_string(line, "cmd", cmd, sizeof(cmd))) {
        /* kein "cmd"-Key: falls es wie JSON aussieht -> error-Event,
         * sonst still ignorieren (Moeglicherweise Fremdtext auf der Pipe). */
        dbg("handle_line: Zeile ohne 'cmd': %.160s", line);
        if (line[0] == '{') {
            send_line(hPipe, "{\"event\":\"error\",\"error\":\"missing_cmd\"}");
        }
        return;
    }

    if (strcmp(cmd, "ping") == 0) {
        /* TODO(spaeter): ggf. "t" mitgeben, damit der Client Latenz messen
         * kann - v0 bewusst minimal. */
        if (send_line(hPipe, "{\"event\":\"pong\",\"t\":%llu}",
                      (unsigned long long)GetTickCount64()) == 0) {
            dbg("handle_line: ping -> pong");
        }
        return;
    }

    if (strcmp(cmd, "exec") == 0) {
        if (!json_get_string(line, "command", command, sizeof(command))) {
            send_line(hPipe,
                      "{\"event\":\"error\",\"error\":\"exec_ohne_command\"}");
            return;
        }
        dispatch_exec(hPipe, command);
        return;
    }

    dbg("handle_line: unbekanntes cmd '%s'", cmd);
    send_line(hPipe, "{\"event\":\"error\",\"error\":\"unknown_cmd\"}");
}

/* ------------------------------------------------------------------ */
/* Pipe-Server: Verbindung bedienen                                    */
/* ------------------------------------------------------------------ */

/*
 * Bedient EINEN verbundenen Client, bis er sich trennt oder g_stop.
 * Polling mit PeekNamedPipe statt blockierendem ReadFile: so kann der
 * Thread nebenbei den State-Heartbeat senden und auf g_stop reagieren.
 * Rueckgabe 0 = normal beendet, -1 = Client weg / Fehler.
 */
static int serve_client(HANDLE hPipe)
{
    char line[PIPE_LINE_MAX];
    size_t nline = 0;
    DWORD last_beat = 0;

    dbg("serve_client: Client verbunden");

    for (;;) {
        if (g_stop)
            return 0;

        /* Client noch da? (PeekNamedPipe blockiert nicht) */
        DWORD avail = 0;
        if (!PeekNamedPipe(hPipe, NULL, 0, NULL, &avail, NULL)) {
            DWORD gle = GetLastError();
            if (gle != ERROR_BROKEN_PIPE && gle != ERROR_BAD_PIPE &&
                gle != ERROR_INVALID_HANDLE && gle != ERROR_PIPE_NOT_CONNECTED) {
                dbg("serve_client: PeekNamedPipe-Fehler GLE=%lu", gle);
            }
            dbg("serve_client: Client getrennt (GLE=%lu)", gle);
            return -1;
        }

        if (avail > 0) {
            char rbuf[PIPE_INBUF_SIZE];
            DWORD rd = 0;
            DWORD want = avail < sizeof(rbuf) ? avail : (DWORD)sizeof(rbuf);
            if (!ReadFile(hPipe, rbuf, want, &rd, NULL) || rd == 0) {
                dbg("serve_client: ReadFile fehlgeschlagen (GLE=%lu)",
                    GetLastError());
                return -1;
            }
            /* Bytes zeilenweise aufsammeln; '\r' ueberspringen */
            for (DWORD i = 0; i < rd; i++) {
                char c = rbuf[i];
                if (c == '\n') {
                    if (nline > 0) {
                        line[nline] = '\0';
                        handle_line(hPipe, line);
                    }
                    nline = 0;
                } else if (c != '\r') {
                    if (nline + 1 >= sizeof(line)) {
                        /* Zeile zu lang: verwerfen statt Puffer zu sprengen */
                        dbg("serve_client: Zeile >%u Bytes verworfen",
                            (unsigned)sizeof(line));
                        nline = 0;
                    } else {
                        line[nline++] = c;
                    }
                }
            }
        }

        /* State-Heartbeat (score_update, Egress Issue #13), nur bei Client */
        DWORD now = GetTickCount();
        if (last_beat == 0 || now - last_beat >= HEARTBEAT_MS) {
            last_beat = now;
            send_state(hPipe);
        }

        Sleep(POLL_MS);
    }
}

/*
 * Hauptschleife des Pipe-Servers. Laueft bis g_stop.
 * Kein Client = blockiert in ConnectNamedPipe (ruhig, keine CPU-Last);
 * Client weg = sofort naechste Connect-Runde (Reconnect).
 */
static DWORD WINAPI pipe_server_main(LPVOID unused)
{
    (void)unused;
    dbg("pipe_server_main: Start (Pipe %s)", PIPE_NAME_A);

    while (!g_stop) {
        HANDLE hPipe = CreateNamedPipeA(
            PIPE_NAME_A,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            1,                          /* nur 1 Instanz in v0             */
            RESP_BUF_SIZE,              /* Outbound-Puffergroesse          */
            PIPE_INBUF_SIZE,            /* Inbound-Puffergroesse           */
            0, NULL);

        if (hPipe == INVALID_HANDLE_VALUE) {
            dbg("pipe_server_main: CreateNamedPipeA fehlgeschlagen (GLE=%lu)",
                GetLastError());
            Sleep(1000);
            continue;
        }

        if (g_stop) {
            CloseHandle(hPipe);
            break;
        }

        BOOL connected = ConnectNamedPipe(hPipe, NULL);
        if (!connected && GetLastError() != ERROR_PIPE_CONNECTED) {
            /* z.B. kurz nach CreateNamedPipe schon verbunden (ERROR_PIPE_
             * CONNECTED = ok) - alles andere: aufraeumen und weitermachen */
            dbg("pipe_server_main: ConnectNamedPipe fehlgeschlagen (GLE=%lu)",
                GetLastError());
            CloseHandle(hPipe);
            Sleep(250);
            continue;
        }

        if (g_stop) {
            CloseHandle(hPipe);
            break;
        }

        serve_client(hPipe);

        DisconnectNamedPipe(hPipe);
        CloseHandle(hPipe);
        Sleep(100); /* kurze Pause vor dem naechsten Connect */
    }

    dbg("pipe_server_main: Ende");
    return 0;
}

/* ------------------------------------------------------------------ */
/* Gemeinsame Start-/Stopp-API (DLL-Attach UND Standalone-main)        */
/* ------------------------------------------------------------------ */

/*
 * Startet den Pipe-Server-Dienst (Pipe-Server-Thread + Init).
 * Wird gerufen von DllMain (DLL_PROCESS_ATTACH) und - im Standalone-
 * Modus - von main(). Doppelte Aufrufe sind dank g_thread_started ein
 * No-op (Rueckgabe 0 = ok/laeuft bereits, -1 = Fehler).
 */
int rbbridge_start(void)
{
    if (InterlockedCompareExchange(&g_thread_started, 1, 0) != 0)
        return 0; /* laeuft bereits (z.B. zweiter Attach) */

    /* Umgebungsvariable RBBRIDGE_LOG=0 schaltet das Datei-Log ab */
    char env[4] = "";
    if (GetEnvironmentVariableA("RBBRIDGE_LOG", env, sizeof(env)) > 0 &&
        strcmp(env, "0") == 0) {
        g_file_log = 0;
    }

    InitializeCriticalSection(&g_log_cs);
    g_stop = 0;

    /* WICHTIG (DLL-Fall): Hier laeuft das ggf. im DllMain-Kontext
     * (Loader-Lock) - nie blockieren/kein LoadLibrary, wir starten nur
     * einen unabhaengigen Thread. */
    g_thread = CreateThread(NULL, 0, pipe_server_main, NULL, 0, NULL);
    if (g_thread) {
        dbg("rbbridge_start: Pipe-Server-Thread laeuft");
        return 0;
    }
    dbg("rbbridge_start: CreateThread fehlgeschlagen (GLE=%lu)",
        GetLastError());
    InterlockedExchange(&g_thread_started, 0);
    DeleteCriticalSection(&g_log_cs);
    return -1;
}

/*
 * Stoppt den Pipe-Dienst (Wake-up + Join + Aufraeumen).
 * Wird gerufen von DllMain (DLL_PROCESS_DETACH) und von main() beim
 * Beenden (Ctrl+C im Standalone-Modus).
 */
void rbbridge_stop(void)
{
    if (!g_thread_started)
        return;

    g_stop = 1;

    /* Wake-up: Ein kurzer Eigen-Connect bringt den Thread aus einem
     * blockierenden ConnectNamedPipe (und danach sofort wieder raus,
     * weil g_stop gesetzt ist). Mehrmals versuchen, um die kleine
     * Race zwischen CreateNamedPipe/ConnectNamedPipe abzudecken. */
    for (int i = 0; i < 40 && g_thread; i++) {
        if (WaitForSingleObject(g_thread, 50) == WAIT_OBJECT_0)
            break;
        HANDLE w = CreateFileA(PIPE_NAME_A,
                               GENERIC_READ | GENERIC_WRITE,
                               0, NULL, OPEN_EXISTING, 0, NULL);
        if (w != INVALID_HANDLE_VALUE) {
            FlushFileBuffers(w);
            CloseHandle(w);
        }
    }
    if (g_thread) {
        WaitForSingleObject(g_thread, 500);
        CloseHandle(g_thread);
        g_thread = NULL;
    }
    DeleteCriticalSection(&g_log_cs);
    InterlockedExchange(&g_thread_started, 0);
}

/* ------------------------------------------------------------------ */
/* DllMain (nur im DLL-Build aktiv; im Standalone-Build ungenutzt)     */
/* ------------------------------------------------------------------ */

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved)
{
    (void)reserved;

    switch (reason) {
    case DLL_PROCESS_ATTACH: {
        /* Nur der allererste Attach startet den Thread (Guard liegt in
         * rbbridge_start; Doppel-Attach ist dort ein No-op). */
        rbbridge_start();

        /* Handle erst im Detach schliessen (brauchen es zum Join).
         * "DisableThreadLibraryCalls" spart die Load/Unload-Benach-
         * richtigungen fuer andere Threads. */
        DisableThreadLibraryCalls(hinst);
        break;
    }

    case DLL_PROCESS_DETACH: {
        rbbridge_stop();
        break;
    }

    default:
        break;
    }
    return TRUE;
}

/* ------------------------------------------------------------------ */
/* Standalone-Modus: main() statt DllMain                              */
/*                                                                    */
/* Aktiv nur bei #define RBBRIDGE_STANDALONE - dann ist dieser         */
/* Quelltext ein normales Konsolen-Programm (rbbridge_standalone.exe)  */
/* mit IDENTISCHEM Pipe-Protokoll, aber ohne Injection/Spielprozess.   */
/* ------------------------------------------------------------------ */

#ifdef RBBRIDGE_STANDALONE

static volatile LONG g_running = 1; /* 0 = main() soll den Dienst stoppen */

static BOOL WINAPI ctrl_handler(DWORD ctrl_type)
{
    (void)ctrl_type;
    if (g_running) {
        dbg("rbbridge_standalone: Ctrl+C/Ctrl+Break - beende Dienst");
        InterlockedExchange((volatile LONG *)&g_running, 0);
        g_stop = 1;
    }
    return TRUE; /* Ereignis behandelt -> kein Standard-Exit */
}

int main(void)
{
    /* Ctrl+C soll den Dienst sauber stoppen statt den Prozess sofort zu
     * beenden (sonst bliebe der Pipe-Server-Thread haengen). */
    SetConsoleCtrlHandler(ctrl_handler, TRUE);

    if (rbbridge_start() != 0) {
        fprintf(stderr, "rbbridge_standalone: Start fehlgeschlagen (GLE=%lu)\n",
                (unsigned long)GetLastError());
        return 1;
    }

    /* Hinweiszeile (einziger sichtbarer Unterschied zur DLL-Variante): */
    printf("rbbridge_standalone: Pipe-Server aktiv auf %s - Standalone-\n"
           "Modus ohne Injection. Test: python pipe_client.py "
           "(ping/pong). Ctrl+C beendet.\n",
           PIPE_NAME_A);
    fflush(stdout);

    /* Hauptschleife: warten, bis Ctrl+C g_stop/g_running setzt */
    while (g_running)
        Sleep(200);

    rbbridge_stop();
    printf("rbbridge_standalone: beendet\n");
    return 0;
}

#endif /* RBBRIDGE_STANDALONE */

#endif /* !RBBRIDGE_HOSTTEST (Dispatch + Pipe-Server + DllMain) */
