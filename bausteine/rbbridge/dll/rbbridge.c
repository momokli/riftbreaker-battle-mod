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
 *       Ingress: {"cmd":"ping"}          -> {"event":"pong"}
 *                {"cmd":"probe"}         -> Memory-Dump (PlayerService-Kette)
 *                {"cmd":"get_state"}     -> carbonium/ironium/max/resources (C++)
 *                {"cmd":"add_resource"}  -> carbonium direkt aendern (C++)
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
 * Host-Test-Build (tests/rbbridge-hosttest, KEIN Windows noetig):
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
#include <stdlib.h>
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

/* safe_read_* im Host-Test: der Testpuffer ist immer gueltig, also direktes
 * memcpy (kein VirtualQuery). So laesst sich die reine Basket-Lookup-Logik
 * (basket_lookup_value) ohne Spielprozess testen. */
static int safe_read_u64(const void *addr, uint64_t *out)
{
    memcpy(out, addr, sizeof(uint64_t));
    return 1;
}

static int safe_read_u32(const void *addr, uint32_t *out)
{
    memcpy(out, addr, sizeof(uint32_t));
    return 1;
}

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

/* Ressourcen-StringHashes (FNV-1a-32 des INTERNEN Ressourcennamens).
 * Ironium ist der Anzeigename der internen Ressource "steel"
 * (cheat.lua: On "ironium" -> "steel"; Asset-Namen ironium_*; siehe
 * docs/research/resource-hash-map.md #371). FNV-1a("ironium")=0x91de9d9c
 * ist KEIN Basket-Key. */
#define RBBRIDGE_HASH_CARBONIUM 0x659cc791u
#define RBBRIDGE_HASH_IRONIUM   0x0d01a504u /* intern: "steel" */

/* Forward-Decl: echte Definition im Windows-Build (weiter unten),
 * Host-Test-Shim im RBBRIDGE_HOSTTEST-Block oben. */
static int safe_read_u64(const void *addr, uint64_t *out);
static int safe_read_u32(const void *addr, uint32_t *out);

/* Liest den ResourceValue (int64-Fixed-Point x10^6) zu `hash` aus einem
 * Account-Basket-Array ({u32 StringHash, i64 ResourceValue} * count, 16 B
 * je Eintrag). Reine Lookup-Logik ohne Spielprozess -> host-testbar
 * (tests/rbbridge-hosttest). Rueckgabe 1 = gefunden (out gesetzt),
 * 0 = nicht gefunden / unlesbar. Kein Crash bei Nicht-Fund. */
static int basket_lookup_value(const unsigned char *arr, uint64_t count,
                               uint32_t hash, uint64_t *out)
{
    if (!arr || count == 0 || count > 1024)
        return 0;

    for (uint64_t i = 0; i < count; i++) {
        const unsigned char *e = arr + i * 16;
        uint32_t h = 0;
        uint64_t v = 0;
        if (!safe_read_u32(e, &h))
            continue;
        if (h != hash)
            continue;
        if (!safe_read_u64(e + 8, &v))
            return 0;
        if (out)
            *out = v;
        return 1;
    }
    return 0;
}

/* Mappt den ANZEIGEnamen einer Ressource auf ihren INTERNEN Namen, den
 * PlayerService::AddResourceAmount ueber den StringHash aufloest. Ironium
 * ist der Anzeigename der internen Ressource "steel" (cheat.lua: On
 * "ironium" -> "steel"; docs/research/resource-hash-map.md #371).
 * Unbekannt/leer -> "carbonium" (Backward-Compat: alte Clients senden kein
 * `resource`-Feld). Reine Funktion ohne Spielprozess -> host-testbar
 * (tests/rbbridge-hosttest, analog basket_lookup_value). */
static const char *resource_internal_name(const char *display)
{
    if (!display || !display[0])
        return "carbonium";
    if (strcmp(display, "ironium") == 0)
        return "steel";
    return "carbonium";
}

/* Prueft den `mode`-Parameter von activate_mission_flow. Das Spiel erwartet
 * als 3. Argument IMMER "default" (genau das uebergibt die spieleigene Lua);
 * ein anderer Wert (z. B. "hard") hat auf dem Dev-Server die DLL-Pipe
 * dauerhaft gekillt (#447). Leer/fehlend -> "default" (Backward-Compat: alte
 * Clients senden kein `mode`-Feld). Reine Funktion ohne Spielprozess ->
 * host-testbar (tests/rbbridge-hosttest, analog resource_internal_name).
 * Rueckgabe: 1 = erlaubt ("default" oder leer), 0 = ablehnen. */
static int mission_flow_mode_ok(const char *mode)
{
    if (!mode || !mode[0])
        return 1;
    return strcmp(mode, "default") == 0;
}

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

    /* Konsolen-/docker-Log (#392): gleicher Stream wie das Spiel (stderr ->
     * docker), mit Zeitstempel fuer Korrelation mit bridge + exor_logs. */
    {
        SYSTEMTIME st;
        GetLocalTime(&st);
        fprintf(stderr, "[%02d:%02d:%02d.%03d] [rbbridge] %s\n",
                st.wHour, st.wMinute, st.wSecond, st.wMilliseconds, buf);
        fflush(stderr);
    }

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

/*
 * Byte-Signatur des ActivateMissionFlow-Prologs (Issue #385, Build
 * 2.0.58485). 33 Bytes, im .text eindeutig (Gegenprobe planet 2026-09-15:
 * genau 1 Treffer). Sie entspricht dem Database*-Overload von
 * `Riftbreaker::MissionService::ActivateMissionFlow` (RVA 0xF93280) - die
 * RVA ist NUR Verifikations-Notiz, die Laufzeitadresse kommt ausschliesslich
 * aus diesem AOB-Scan (KEINE feste Adresse).
 *
 * Prolog-Disasm (tools/re/disasm.py, planet):
 *   48 89 5C 24 08   mov  [rsp+8], rbx
 *   48 89 6C 24 18   mov  [rsp+0x18], rbp
 *   48 89 74 24 20   mov  [rsp+0x20], rsi
 *   57               push rdi
 *   48 83 EC 50      sub  rsp,0x50
 *   49 8B E9         mov  rbp,r9        ; a2 (logicFile)
 *   49 8B F0         mov  rsi,r8        ; a1 (name)
 *   48 8B FA         mov  rdi,rdx       ; hidden ret (UtfString out)
 *   48 8B 59 08      mov  rbx,[rcx+8]   ; this -> World*
 * Kein rel32-Displacement im Prolog -> keine Wildcard-Maske noetig.
 */
static const unsigned char RBBRIDGE_ACTIVATE_SIG[] = {
    0x48, 0x89, 0x5C, 0x24, 0x08, 0x48, 0x89, 0x6C, 0x24, 0x18,
    0x48, 0x89, 0x74, 0x24, 0x20, 0x57, 0x48, 0x83, 0xEC, 0x50,
    0x49, 0x8B, 0xE9, 0x49, 0x8B, 0xF0, 0x48, 0x8B, 0xFA, 0x48,
    0x8B, 0x59, 0x08
};

/*
 * Byte-Signatur des DeactivateMissionFlow-Entrypoints (Issue #389, Build
 * 2.0.58485). 88 Bytes, im .text eindeutig (Gegenprobe planet 2026-09-15:
 * genau 1 Treffer - RVA 0xF960E0). Sie entspricht
 * `Riftbreaker::MissionService::DeactivateMissionFlow(UtfString const&)`
 * (RVA 0xF960E0 = reine Verifikations-Notiz; die Laufzeitadresse kommt
 * ausschliesslich aus diesem AOB-Scan, KEINE feste Adresse).
 *
 * WICHTIG: Der 16-Byte-Prolog ALLEIN ist NICHT eindeutig - er matcht auch
 * `MissionService::IsGraphActive(UtfString const&)` (RVA 0xF9E1F0); beide
 * teilen Prolog + Argument-Marshalling. Unterschied erst am Ende: IsGraph-
 * Active kehrt mit `cmp eax,1 / sete al` (bool) zurueck, DeactivateMission-
 * Flow mit `add rsp,0x30` (void). Deshalb laeuft die Signatur exakt 88 Bytes
 * bis zum letzten Byte 87 (`0x48` = `add rsp,0x30` vs `0x83` = `cmp eax,1`).
 * Die vier E8-CALL-rel32 sind NICHT gepinnt (buildabhaengig) -> Wildcard-
 * Maske (analog RBBRIDGE_EXEC_SIG_MASK); die E8-Opcodes bleiben Pflicht.
 *
 * Prolog-Disasm (tools/re/disasm.py, planet):
 *   48 89 5C 24 08   mov  [rsp+8], rbx
 *   57               push rdi
 *   48 83 EC 30      sub  rsp,0x30
 *   48 8B 59 08      mov  rbx,[rcx+8]     ; this -> World*
 *   48 8B FA         mov  rdi,rdx         ; a1 (UtfString const& name)
 *   E8 ..            call <getter>        (rel32 wildcard)
 *   48 8B C8         mov  rcx,rax
 *   E8 ..            call <system getter> (rel32 wildcard)
 *   48 8B CB         mov  rcx,rbx
 *   8B 50 30         mov  edx,[rax+0x30]
 *   E8 ..            call <deactivate>    (rel32 wildcard)
 *   48 8B 4F 18      mov  rcx,[rdi+0x18]  ; name.size
 *   48 83 C7 08      add  rdi,8
 *   48 83 7F 18 0F   cmp  qword [rdi+0x18],0xf  ; SSO?
 *   76 03            jbe  +3
 *   48 8B 3F         mov  rdi,[rdi]
 *   48 89 4C 24 28   mov  [rsp+0x28],rcx
 *   48 8D 54 24 20   lea  rdx,[rsp+0x20]
 *   48 8B C8         mov  rcx,rax
 *   48 89 7C 24 20   mov  [rsp+0x20],rdi
 *   E8 ..            call <core>          (rel32 wildcard)
 *   48 8B 5C 24 40   mov  rbx,[rsp+0x40]
 *   48               (Beginn `add rsp,0x30`; Diskriminator vs IsGraphActive)
 */
static const unsigned char RBBRIDGE_DEACTIVATE_SIG[] = {
    0x48, 0x89, 0x5C, 0x24, 0x08, 0x57, 0x48, 0x83, 0xEC, 0x30, 0x48, 0x8B,
    0x59, 0x08, 0x48, 0x8B, 0xFA, 0xE8, 0xCA, 0xDC, 0x5D, 0x01, 0x48, 0x8B,
    0xC8, 0xE8, 0x62, 0x40, 0x37, 0xFF, 0x48, 0x8B, 0xCB, 0x8B, 0x50, 0x30,
    0xE8, 0xA7, 0x52, 0xD4, 0x00, 0x48, 0x8B, 0x4F, 0x18, 0x48, 0x83, 0xC7,
    0x08, 0x48, 0x83, 0x7F, 0x18, 0x0F, 0x76, 0x03, 0x48, 0x8B, 0x3F, 0x48,
    0x89, 0x4C, 0x24, 0x28, 0x48, 0x8D, 0x54, 0x24, 0x20, 0x48, 0x8B, 0xC8,
    0x48, 0x89, 0x7C, 0x24, 0x20, 0xE8, 0x5E, 0x67, 0x3A, 0xFF, 0x48, 0x8B,
    0x5C, 0x24, 0x40, 0x48
};

/* Wildcard-Maske zu RBBRIDGE_DEACTIVATE_SIG: 0x00 = don't care. Nur die
 * rel32-Operanden der vier E8-CALLs sind maskiert; die E8-Opcodes selbst
 * bleiben Pflicht. */
static const unsigned char RBBRIDGE_DEACTIVATE_SIG_MASK[] = {
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF,
    0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF
};

/* ------------------------------------------------------------------ */
/* #386: Exor::Database-Payload (Mission-Flow-Datenobjekt)             */
/*                                                                    */
/* `dom_mananger.data` ist ein Exor::Database und wird als `data`-      */
/* Argument ([rsp+0x28], 4. Stack-Parameter) an                        */
/* MissionService::ActivateMissionFlow (RBBRIDGE_ACTIVATE_SIG)          */
/* durchgereicht. Database hat KEINE vftable (??_7Database@Exor@@6B@    */
/* fehlt im Binary) und ist 0x60 Byte gross (3x 0x20 Container).       */
/*                                                                    */
/* Alle Adressen werden zur Laufzeit per AOB im Modulabbild gefunden   */
/* (KEINE feste RVA im Aufrufpfad). rel32-Operanden sind per Maske     */
/* (0x00 = Wildcard) entschaerft; nur die E8-Opcodes bleiben Pflicht.  */
/*                                                                    */
/* RE-Nachweis + Klassen-Layout:                                      */
/*   docs/research/database-object-re-findings.md                     */
/* Gegenprobe planet (Build 2.0.58485, 2026-09-15, disasm.py+capstone):*/
/*   RBBRIDGE_DB_SETSTRING_SIG  1 Treffer @ RVA 0x25956B0              */
/*   RBBRIDGE_DB_GETSTRING_SIG  1 Treffer @ RVA 0x2591FD0              */
/*   RBBRIDGE_DB_CTOR_SIG       Body NICHT eindeutig (byte-identisch   */
/*                              mit ??0EntityStatComponent@...), daher */
/*                              Anker ueber `new 0x60`-Call-Site       */
/*                              (RBBRIDGE_NEWDB_SITE_SIG/-CALL_SIG).   */
/* ------------------------------------------------------------------ */

/* Exor::Database::SetString - Prolog, exakt (kein rel32 im Prefix).
 *   4C 89 44 24 18   mov  [rsp+0x18], r8
 *   53               push rbx
 *   48 83 EC 30      sub  rsp,0x30
 *   49 8B D8         mov  rbx,r8
 *   48 8B 42 18      mov  rax,[rdx+0x18]   ; key.size
 *   48 83 C2 08      add  rdx,8
 *   48 83 7A 18 0F   cmp  qword [rdx+0x18],0xf  ; SSO? */
static const unsigned char RBBRIDGE_DB_SETSTRING_SIG[] = {
    0x4C, 0x89, 0x44, 0x24, 0x18, 0x53, 0x48, 0x83, 0xEC, 0x30,
    0x49, 0x8B, 0xD8, 0x48, 0x8B, 0x42, 0x18, 0x48, 0x83, 0xC2,
    0x08, 0x48, 0x83, 0x7A, 0x18, 0x0F
};

/* Exor::Database::GetString - Prolog; rel32 der E8 @ Index 12 maskiert.
 *   40 53                    push rbx
 *   48 81 EC 80 00 00 00     sub  rsp,0x80
 *   48 8B DA                 mov  rbx,rdx
 *   E8 ..                    call <lookup>   (rel32 maskiert)
 *   48 85 C0                 test rax,rax
 *   74 09                    je   +9 */
static const unsigned char RBBRIDGE_DB_GETSTRING_SIG[] = {
    0x40, 0x53, 0x48, 0x81, 0xEC, 0x80, 0x00, 0x00, 0x00, 0x48,
    0x8B, 0xDA, 0xE8, 0xFF, 0xFF, 0xFF, 0xFF, 0x48, 0x85, 0xC0,
    0x74, 0x09, 0x48, 0x81, 0xC4, 0x80, 0x00, 0x00, 0x00, 0x5B,
    0xC3
};
static const unsigned char RBBRIDGE_DB_GETSTRING_SIG_MASK[] = {
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF
};

/* Exor::Database::Database() - Prolog (Gegenpruefung des Anker-Ziels);
 * rel32 der E8 @ Index 23 maskiert.
 *   48 89 5C 24 18   mov  [rsp+0x18], rbx
 *   48 89 74 24 20   mov  [rsp+0x20], rsi
 *   48 89 4C 24 08   mov  [rsp+8], rcx
 *   57               push rdi
 *   48 83 EC 20      sub  rsp,0x20
 *   48 8B F9         mov  rdi,rcx
 *   E8 ..            call <container ctor>   (rel32 maskiert) */
static const unsigned char RBBRIDGE_DB_CTOR_SIG[] = {
    0x48, 0x89, 0x5C, 0x24, 0x18, 0x48, 0x89, 0x74, 0x24, 0x20,
    0x48, 0x89, 0x4C, 0x24, 0x08, 0x57, 0x48, 0x83, 0xEC, 0x20,
    0x48, 0x8B, 0xF9, 0xE8, 0xFF, 0xFF, 0xFF, 0xFF
};
static const unsigned char RBBRIDGE_DB_CTOR_SIG_MASK[] = {
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00
};

/* Anker der `new Database(0x60)`-Call-Site (MS-x64):
 *   B9 60 00 00 00   mov  ecx,0x60
 *   E8 ..            call operator new
 *   48 8B C8         mov  rcx,rax
 *   E8 ..            call Database::Database()   <- Ziel = Ctor
 * Die rel32-Operanden sind build-gebunden und werden nie verglichen; das
 * Ctor-Ziel wird zur Laufzeit aus dem zweiten rel32 berechnet. */
static const unsigned char RBBRIDGE_NEWDB_SITE_SIG[] = {
    0xB9, 0x60, 0x00, 0x00, 0x00, 0xE8
};
static const unsigned char RBBRIDGE_NEWDB_CALL_SIG[] = {
    0x48, 0x8B, 0xC8, 0xE8
};

/* RE-Befunde #385 (Build 2.0.58485) - feste RVAs NUR fuer die kleinen
 * Helfer (analog zu den bestehenden PlayerService-RVAs in get_state);
 * ActivateMissionFlow selbst wird per AOB aufgeloest (RBBRIDGE_ACTIVATE_SIG).
 *
 * Aufrufkonvention des Database*-Overloads (MSVC x64, verifiziert am
 * Disasm von 0xF93280): this=RCX, hidden-ret-UtfString*=RDX, name=R8,
 * logicFile=R9, mode=[rsp+0x20], data=[rsp+0x28]. Der 3-Arg-Overload
 * (RVA 0xF93130) ist nur ein Shim, der den Workhorse mit data=NULL ruft -
 * NULL ist also ein vom Spiel selbst benutzter, gueltiger Database-Wert. */
#define RBBRIDGE_RVA_MISSIONSERVICE_VFTABLE 0x2e962a0u /* ??_7MissionService@Riftbreaker@@6B@ */
#define RBBRIDGE_RVA_UTFSTRING_CTOR         0x3ae1e0u  /* UtfString(char const*) */
#define RBBRIDGE_RVA_UTFSTRING_DTOR         0x26f1f0u  /* ~UtfString() */
#define RBBRIDGE_RVA_ISGRAPHACTIVE          0xf9e1f0u  /* bool MissionService::IsGraphActive(UtfString const&) */
/* #389: Riftbreaker::MissionService::DeactivateMissionFlow(UtfString const&)
 * RVA 0xF960E0 (planet 2026-09-15, publics addr=0001:16339168 -> 0x1000+).
 * NUR Verifikations-Notiz: aufgeloest wird per RBBRIDGE_DEACTIVATE_SIG. */

/* ------------------------------------------------------------------ */
/* CampaignService-Difficulty (Issue #388, Build 2.0.58485)            */
/*                                                                    */
/* `Riftbreaker::CampaignService` haelt die Kreaturen-Basis-Difficulty   */
/* dieses Runs. Die Methoden sind NICHT virtuell (PDB: QEAA/Public     */
/* non-virtual) - sie werden also direkt aufgerufen, nicht ueber die    */
/* vftable. Die vftable (RVA 0x2E9C340) dient nur der Instanz-Aufloesung */
/* (dieselbe QWORD-Scan-Technik wie bei Player-/MissionService).        */
/*                                                                    */
/* Layout (Disasm aller vier Funktionen, planet 2026-09-15):            */
/*   CampaignService + 0x10 -> ptr; dieses Ziel-Objekt haelt            */
/*   float creatures_base_difficulty bei + 0x584.                       */
/*                                                                    */
/* Aufrufkonvention (MSVC x64): this=RCX, float-Argument in XMM1        */
/* (Integer-Slot 0 = this belegt RCX; FP-Args zaehlen unabhaengig).      */
/* Rueckgabe float in XMM0. Reines C++ -> thread-agnostisch (#378).     */
/*                                                                    */
/* Hinweis zum Issue-Text: eine Methode `RevertCreaturesBaseDifficulty` */
/* existiert im Binary NICHT. Es gibt stattdessen                        */
/* `DecreaseCreaturesBaseDifficulty(float)` (Delta) und                   */
/* `SetCreaturesBaseDifficulty(float)` (absolut). Beide sind abgebildet. */
/* ------------------------------------------------------------------ */

/* Float-Konstante +0x584 steht in allen vier Funktionen; die Prologe sind
 * im .text eindeutig (Gegenprobe planet 2026-09-15: je genau 1 Treffer).
 * Die RVA ist NUR Verifikations-Notiz - die Laufzeitadresse kommt aus dem
 * AOB-Scan (KEINE feste Adresse). */

/* GetCreaturesBaseDifficulty() -> float  (RVA 0x100B9E0)
 *   48 8B 41 10              mov   rax,[rcx+0x10]
 *   F3 0F 10 80 84 05 00 00  movss xmm0,[rax+0x584]
 *   C3                       ret */
static const unsigned char RBBRIDGE_DIFF_GET_SIG[] = {
    0x48, 0x8B, 0x41, 0x10, 0xF3, 0x0F, 0x10, 0x80, 0x84, 0x05,
    0x00, 0x00, 0xC3
};

/* SetCreaturesBaseDifficulty(float)  (RVA 0x101F100)
 *   48 8B 41 10              mov   rax,[rcx+0x10]
 *   F3 0F 11 88 84 05 00 00  movss [rax+0x584],xmm1
 *   C3                       ret */
static const unsigned char RBBRIDGE_DIFF_SET_SIG[] = {
    0x48, 0x8B, 0x41, 0x10, 0xF3, 0x0F, 0x11, 0x88, 0x84, 0x05,
    0x00, 0x00, 0xC3
};

/* IncreaseCreaturesBaseDifficulty(float)  (RVA 0x10118B0)
 *   48 8B 41 10              mov   rax,[rcx+0x10]
 *   F3 0F 58 88 84 05 00 00  addss xmm1,[rax+0x584]
 *   F3 0F 11 88 84 05 00 00  movss [rax+0x584],xmm1
 *   C3                       ret */
static const unsigned char RBBRIDGE_DIFF_INC_SIG[] = {
    0x48, 0x8B, 0x41, 0x10, 0xF3, 0x0F, 0x58, 0x88, 0x84, 0x05,
    0x00, 0x00, 0xF3, 0x0F, 0x11, 0x88, 0x84, 0x05, 0x00, 0x00,
    0xC3
};

/* DecreaseCreaturesBaseDifficulty(float)  (RVA 0x1008180)
 *   48 8B 41 10              mov   rax,[rcx+0x10]
 *   F3 0F 10 80 84 05 00 00  movss xmm0,[rax+0x584]
 *   F3 0F 5C C1              subss xmm0,xmm1
 *   F3 0F 11 80 84 05 00 00  movss [rax+0x584],xmm0
 *   C3                       ret */
static const unsigned char RBBRIDGE_DIFF_DEC_SIG[] = {
    0x48, 0x8B, 0x41, 0x10, 0xF3, 0x0F, 0x10, 0x80, 0x84, 0x05,
    0x00, 0x00, 0xF3, 0x0F, 0x5C, 0xC1, 0xF3, 0x0F, 0x11, 0x80,
    0x84, 0x05, 0x00, 0x00, 0xC3
};

#define RBBRIDGE_RVA_CAMPAIGNSERVICE_VFTABLE 0x2e9c340u /* ??_7CampaignService@Riftbreaker@@6B@ */

/* ------------------------------------------------------------------ */
/* #476: Vanilla-Naturwellen "aus" — DifficultyService-Schalter        */
/*                                                                    */
/* Der Dedi startet im Vanilla-Survival-Preset; `dom_mananger` (Lua-Klasse,
 * so im Spiel benannt; Datei `dom_manager.lua`) zieht den Naturwellen-Takt
 * aus der Difficulty:                                                  */
/*   self.pauseAttacks = DifficultyService:AreWavesDisabled()         */
/*   + GetWaveStrength()=="sandbox" -> pauseAttacks = true            */
/* Bei pauseAttacks=true laeuft der Spawner nur idle<->dummy_state und */
/* ruft NIE PrepareWave/Streaming -> 0 Naturwellen (das ist "aus",     */
/* kein Freeze).                                                       */
/*                                                                    */
/* Kette (Disasm Build 2.0.58485, planet 2026-09-15):                  */
/*   DifficultyService (RTTI .?AVDifficultyService@Riftbreaker@@)      */
/*     +0x08 = Exor::World*                                            */
/*   World-System (TypeHash 0x221d7af2, Getter per AOB-Signatur) haelt:*/
/*     +0x08  = difficulty name UtfString                              */
/*     +0x118 = wave_strength UtfString                                */
/*     +0x1B9 = mission_infinite bool                                  */
/*     +0x1BA = waves_disabled bool  <- AreWavesDisabled() liefert das */
/*                                                                    */
/* Abgrenzung SetSuspended: LuaGraphNode::SetSuspended(bool) ist         */
/* `mov byte [rcx+0xF1],dl; ret` (RVA 0x1BA6CB0) und LuaGraphNode::     */
/* Update kehrt bei [this+0xF1]!=0 SOFORT zurueck -> das ist EINFRIEREN */
/* (Pause), nicht "aus". Deshalb steuert dieses Primitiv den echten      */
/* Difficulty-Schalter (AreWavesDisabled).                             */
/*                                                                    */
/* #476-RE: DifficultyDef "sandbox" in scripts/difficulty/             */
/* difficulties.difficulty setzt wave_strength "sandbox" +             */
/* mission_infinite 1 -> `set difficulty "sandbox"` ist der deklarative */
/* Boot-Schalter (deploy/roles/riftbreaker-server config.cfg.j2).       */
/*                                                                    */
/* Thread-Modell (#378): native Reads/Writes, KEIN lua_* — laeuft auf  */
/* dem Pipe-Thread. ANNAHME (#478): der Getter ist ein reiner Lookup   */
/* (kein lua_*, kein Lock, keine Seiteneffekte); bei parallelem         */
/* status+off im Live-Test auf Race/Crash achten.                      */
/* Alle Adressen per AOB/RTTI, KEINE feste Adresse.                     */
/* Steht AUSSERHALB des #ifndef-RBBRIDGE_HOSTTEST-Blocks, damit der     */
/* Host-Test Signatur + op-Parser direkt pruefen kann.                  */
/* ------------------------------------------------------------------ */

/* MSVC-RTTI-Name der DifficultyService-Klasse (mit NUL-Terminator). */
static const char RBBRIDGE_DIFFSVC_RTTI[] = ".?AVDifficultyService@Riftbreaker@@";

/* Feld-Offsets im World-System-Objekt (aus dem Disasm, Build 2.0.58485). */
#define RBBRIDGE_DIFFSYS_OFF_NAME          0x08u   /* UtfString          */
#define RBBRIDGE_DIFFSYS_OFF_WAVESTRENGTH  0x118u  /* UtfString          */
#define RBBRIDGE_DIFFSYS_OFF_INFINITE      0x1B9u  /* bool               */
#define RBBRIDGE_DIFFSYS_OFF_WAVESDISABLED 0x1BAu  /* bool (der Schalter)*/

/* Byte-Signatur des World-System-Getters (RVA 0xC5F4A0 = reine Notiz):
 *   48 83 EC 48            sub  rsp,0x48
 *   48 81 C1 C0 00 00 00   add  rcx,0xC0
 *   48 8D 54 24 20         lea  rdx,[rsp+0x20]
 *   41 B8 F2 7A 1D 22      mov  r8d,0x221d7af2    ; TypeHash (build-stabil)
 *   E8 ..                  call <lookup>           (rel32 maskiert)
 * Nur die rel32-Bytes des E8 sind Wildcards; der Opcode bleibt Pflicht. */
static const unsigned char RBBRIDGE_DIFFSYS_GET_SIG[] = {
    0x48, 0x83, 0xEC, 0x48,
    0x48, 0x81, 0xC1, 0xC0, 0x00, 0x00, 0x00,
    0x48, 0x8D, 0x54, 0x24, 0x20,
    0x41, 0xB8, 0xF2, 0x7A, 0x1D, 0x22,
    0xE8, 0xFF, 0xFF, 0xFF, 0xFF
};
static const unsigned char RBBRIDGE_DIFFSYS_GET_SIG_MASK[] = {
    0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0x00, 0x00, 0x00, 0x00
};

/* ops des natural_waves-Kommandos. 0=status, 1=off, 2=on, -1=ungueltig.
 * Steht ausserhalb des Hosttest-Guards -> direkt host-testbar. */
static int natural_waves_op(const char *op)
{
    if (!op || op[0] == '\0' || strcmp(op, "status") == 0)
        return 0; /* Default = status */
    if (strcmp(op, "off") == 0)
        return 1;
    if (strcmp(op, "on") == 0)
        return 2;
    return -1;
}

/* Selbstkonsistenz der Signatur (Laenge == Maske, Wildcard nur am E8-rel32),
 * damit ein versehentlicher Edit nicht still eine zu lockere Signatur baut. */
static int diffsys_sig_selfcheck(void)
{
    size_t n = sizeof(RBBRIDGE_DIFFSYS_GET_SIG);
    if (n != sizeof(RBBRIDGE_DIFFSYS_GET_SIG_MASK))
        return 0;
    for (size_t i = 0; i < n; i++) {
        unsigned char m = RBBRIDGE_DIFFSYS_GET_SIG_MASK[i];
        int rel32 = (i >= 23 && i <= 26);
        if (rel32) {
            if (m != 0x00)
                return 0;
        } else if (m != 0xFF) {
            return 0;
        }
    }
    /* E8-Opcode der CALL-Anweisung muss exakt bleiben. */
    return RBBRIDGE_DIFFSYS_GET_SIG[22] == 0xE8 &&
           RBBRIDGE_DIFFSYS_GET_SIG_MASK[22] == 0xFF;
}

/* Zerlegt den Funktionskoerper einer der vier Tiny-Difficulty-Funktionen in
 * die beiden Layout-Offsets:
 *   48 8B 41 <disp8>                mov   rax,[rcx+disp8]   ; this_deref
 *   F3 0F <op> <modrm> <disp32>     movss/addss [rax+disp32] / xmm..,[rax+disp32]
 *   C3                              ret
 * Rueckgabe 1 = erwartete Form. Damit sind die Layout-Offsets NICHT fest
 * verdrahtet, sondern stammen aus dem per AOB aufgeloesten Funktionskoerper
 * (build-robust: ein geaendertes Layout aendert die Bytes und wird erkannt).
 * Steht bewusst AUSSERHALB des #ifndef-RBBRIDGE_HOSTTEST-Blocks, damit der
 * Host-Test den Decoder direkt pruefen kann (Review PR #433). */
static int diff_decode(const unsigned char *fn, uint32_t *this_deref,
                       int32_t *field_off)
{
    if (!fn || !this_deref || !field_off)
        return 0;
    if (fn[0] != 0x48 || fn[1] != 0x8B || fn[2] != 0x41)
        return 0; /* mov rax,[rcx+disp8] */
    if (fn[4] != 0xF3 || fn[5] != 0x0F)
        return 0;
    /* 0x10 movss-load, 0x11 movss-store, 0x58 addss, 0x5C subss */
    if (fn[6] != 0x10 && fn[6] != 0x11 && fn[6] != 0x58 && fn[6] != 0x5C)
        return 0;
    if (fn[7] != 0x80 && fn[7] != 0x88)
        return 0;
    *this_deref = (uint32_t)fn[3];
    memcpy(field_off, fn + 8, sizeof(*field_off));
    return 1;
}

/* ------------------------------------------------------------------ */
/* #479: Readiness-Gate — Welt-Initialisierung abwarten                */
/*                                                                    */
/* Crash #436/#479: der Pipe-Thread ruft einen Game-Call (get_state),   */
/* obwohl der `world`-Pointer schon non-NULL ist, die Welt aber noch    */
/* NICHT fertig initialisiert wurde (ECS-/Team-Map im Aufbau). Folge:   */
/* GetPlayerAccount -> GetPlayerTeam -> EcsContext::FindIt faultet mit  */
/* einem Garbage-Pointer (RIP riftbreaker.dll+0x275895).                */
/*                                                                    */
/* Ready-Signal (log-belegt, Build 2.0.58485, planet): exor_logs.txt    */
/* enthaelt in JEDEM erfolgreichen Boot                                */
/*   MapGenerator.cpp:828  - InstantiateMap took: <n> ms               */
/*   NavigationGraph.cpp:462 - NavigationGraph::Generate - Graph       */
/*                             generated in <n> sec                    */
/* und in den gecrashten Boots (Crash VOR Map-Fertigstellung) NICHT.    */
/*                                                                    */
/* Der reine Marker-Test steht bewusst AUSSERHALB des                   */
/* #ifndef-RBBRIDGE_HOSTTEST-Blocks -> direkt host-testbar (#394).      */
/* ------------------------------------------------------------------ */

/* Fertig-Marker (Substring, ohne Zeilen-/Zeitstempel). Der NavigationGraph-
 * Marker ist der belastbare: er wird erst nach abgeschlossener Map-Instanz
 * und Navmesh-Aufbau geloggt. Belege siehe docs/research/
 * dedicated-io-re-findings.md, Abschnitt "#479". */
static const char *const RBBRIDGE_READY_MARKERS[] = {
    "NavigationGraph::Generate - Graph generated",
    "InstantiateMap took",
};
#define RBBRIDGE_READY_MARKER_COUNT \
    (sizeof(RBBRIDGE_READY_MARKERS) / sizeof(RBBRIDGE_READY_MARKERS[0]))

/* Substring-Suche in einem NICHT-NUL-terminierten Puffer (portabel; memmem
 * gibt es nicht ueberall). Rueckgabe 1 = gefunden. */
static int rbbridge_buf_contains(const char *hay, size_t hay_len,
                                 const char *needle)
{
    size_t nlen;
    if (!hay || !needle)
        return 0;
    nlen = strlen(needle);
    if (nlen == 0)
        return 1;
    if (hay_len < nlen)
        return 0;
    for (size_t i = 0; i + nlen <= hay_len; i++) {
        if (hay[i] == needle[0] && memcmp(hay + i, needle, nlen) == 0)
            return 1;
    }
    return 0;
}

/* Readiness-Entscheidung AUS dem Log-Inhalt (rein, host-testbar).
 * Rueckgabe 1 = Welt fertig, 0 = noch nicht bereit. Leerer/fehlender
 * Inhalt -> 0 (konservativ: NICHT aufrufen). */
static int rbbridge_log_is_ready(const char *buf, size_t len)
{
    if (!buf || len == 0)
        return 0;
    for (size_t i = 0; i < RBBRIDGE_READY_MARKER_COUNT; i++) {
        if (rbbridge_buf_contains(buf, len, RBBRIDGE_READY_MARKERS[i]))
            return 1;
    }
    return 0;
}

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
#ifdef RBBRIDGE_HOSTTEST
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
#endif /* RBBRIDGE_HOSTTEST */

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
#ifdef RBBRIDGE_HOSTTEST
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
#endif /* RBBRIDGE_HOSTTEST */

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
#ifdef RBBRIDGE_HOSTTEST
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
#endif /* RBBRIDGE_HOSTTEST */

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
#ifdef RBBRIDGE_HOSTTEST
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
#endif /* RBBRIDGE_HOSTTEST */

/* ------------------------------------------------------------------ */
/* #386: Database-Payload - Resolver + Builder                         */
/*                                                                    */
/* Alle Adressen werden aus dem Modulabbild aufgeloest (AOB), NIE als   */
/* feste RVA aufgerufen. Nicht-Fund an JEDER Stufe -> NULL + dbg(),     */
/* kein Aufruf (kein SEH unter MinGW-x64).                             */
/* ------------------------------------------------------------------ */

/* .text-Bereich des Modulabbilds; 1 = ok. Reine Header-Auswertung, kein
 * Zugriff auf den Spielprozess. */
static int rbbridge_text_range(const unsigned char *base, size_t size,
                               const unsigned char **out, size_t *out_len)
{
    const IMAGE_DOS_HEADER *dos;
    const IMAGE_NT_HEADERS *nt;
    const IMAGE_SECTION_HEADER *sec;

    if (!base || size < sizeof(IMAGE_DOS_HEADER))
        return 0;
    dos = (const IMAGE_DOS_HEADER *)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE)
        return 0;
    if ((size_t)dos->e_lfanew > size ||
        size - (size_t)dos->e_lfanew < sizeof(IMAGE_NT_HEADERS))
        return 0;
    nt = (const IMAGE_NT_HEADERS *)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE)
        return 0;
    sec = IMAGE_FIRST_SECTION(nt);
    for (unsigned i = 0; i < nt->FileHeader.NumberOfSections; i++, sec++) {
        if (memcmp(sec->Name, ".text", 5) == 0) {
            size_t va = (size_t)sec->VirtualAddress;
            size_t vs = (size_t)sec->Misc.VirtualSize;
            if (va > size || vs > size - va)
                return 0;
            *out = base + va;
            *out_len = vs;
            return 1;
        }
    }
    return 0;
}

/* Signatur-Scan: .text zuerst, sonst ganzes Abbild (maskiert; NULL = exakt).
 * .text-first entschaerft kurze Signaturen gegen .rdata-Zufallstreffer. */
static const unsigned char *scan_text_first(const unsigned char *base,
                                            size_t size,
                                            const unsigned char *sig,
                                            const unsigned char *mask,
                                            size_t sig_len)
{
    const unsigned char *text = NULL;
    size_t text_len = 0;
    const unsigned char *hit = NULL;

    if (rbbridge_text_range(base, size, &text, &text_len))
        hit = scan_bytes_mask(text, text_len, sig, mask, sig_len);
    if (!hit)
        hit = scan_bytes_mask(base, size, sig, mask, sig_len);
    return hit;
}

static const void *resolve_db_setstring_fn(const unsigned char *base,
                                           size_t size)
{
    return (const void *)scan_text_first(base, size,
                                         RBBRIDGE_DB_SETSTRING_SIG, NULL,
                                         sizeof(RBBRIDGE_DB_SETSTRING_SIG));
}

static const void *resolve_db_getstring_fn(const unsigned char *base,
                                           size_t size)
{
    return (const void *)scan_text_first(base, size,
                                         RBBRIDGE_DB_GETSTRING_SIG,
                                         RBBRIDGE_DB_GETSTRING_SIG_MASK,
                                         sizeof(RBBRIDGE_DB_GETSTRING_SIG));
}

/* Loest Exor::Database::Database() NICHT per Prolog-Scan auf (der Body ist
 * nicht eindeutig), sondern ueber die `new 0x60`-Call-Site: das rel32-Ziel
 * des zweiten E8 (nach `mov rcx,rax`) ist ein Ctor-Kandidat. Von den
 * Kandidaten bleibt nur, wer den Prolog (RBBRIDGE_DB_CTOR_SIG) erfuellt -
 * gleichgrosse Fremd-Ctors (z. B. `??0EntityStatComponent@Riftbreaker@@`)
 * weichen bereits bei Byte 4 ab. Genau 1 Treffer -> Ctor, sonst NULL
 * (kein Aufruf). Live-Gegenprobe planet 2026-09-15: Kandidaten
 * {0x26AF30, 0x26B0A0, 0x2C6550} -> Prolog-Filter -> 0x2C6550. */
static const void *resolve_db_ctor_fn(const unsigned char *base, size_t size)
{
    const unsigned char *end = base + size;
    const unsigned char *p = base;
    const void *cand[16];
    int ncand = 0;
    int sites = 0;
    const void *match = NULL;
    int nmatch = 0;

    while (p && p < end) {
        const unsigned char *site =
            scan_bytes(p, (size_t)(end - p), RBBRIDGE_NEWDB_SITE_SIG,
                       sizeof(RBBRIDGE_NEWDB_SITE_SIG));
        if (!site)
            break;
        sites++;
        const unsigned char *q = site + sizeof(RBBRIDGE_NEWDB_SITE_SIG);
        size_t win = (size_t)(end - q);
        if (win > 0x40)
            win = 0x40;
        const unsigned char *call =
            scan_bytes(q, win, RBBRIDGE_NEWDB_CALL_SIG,
                       sizeof(RBBRIDGE_NEWDB_CALL_SIG));
        if (call) {
            const unsigned char *e8 = call + 3; /* E8-Opcode */
            int32_t rel = 0;
            memcpy(&rel, e8 + 1, sizeof(rel));
            const unsigned char *tgt = e8 + 5 + rel; /* rel32 @ e8+1 */
            if (tgt >= base && tgt < end) {
                int seen = 0;
                for (int i = 0; i < ncand; i++) {
                    if (cand[i] == (const void *)tgt)
                        seen = 1;
                }
                if (!seen && ncand < (int)(sizeof(cand) / sizeof(cand[0])))
                    cand[ncand++] = (const void *)tgt;
            }
        }
        p = site + 1;
    }

    /* Nur Kandidaten mit passendem Prolog zaehlen. */
    for (int i = 0; i < ncand; i++) {
        const unsigned char *c = (const unsigned char *)cand[i];
        if (c + sizeof(RBBRIDGE_DB_CTOR_SIG) > end)
            continue;
        if (sig_matches(c, RBBRIDGE_DB_CTOR_SIG, RBBRIDGE_DB_CTOR_SIG_MASK,
                        sizeof(RBBRIDGE_DB_CTOR_SIG))) {
            match = cand[i];
            nmatch++;
        }
    }

    if (nmatch != 1) {
        dbg("resolve_db_ctor_fn: %d Site(s), %d Prolog-Kandidat(en) "
            "-> NULL (kein Aufruf)",
            sites, nmatch);
        return NULL;
    }
    dbg("resolve_db_ctor_fn: %d Site(s), Ctor=%p (rva=%08lx)", sites, match,
        (unsigned long)((const unsigned char *)match - base));
    return match;
}

/* Kopiert einen C-String mit harter Schranke (immer NUL-terminiert). */
static void copy_cstr(char *dst, size_t n, const char *src)
{
    size_t i = 0;
    if (!dst || n == 0)
        return;
    if (src) {
        for (; src[i] && i + 1 < n; i++)
            dst[i] = src[i];
    }
    dst[i] = '\0';
}
/* Vorwaerts-Deklarationen: die Windows-Build-Definitionen von        */
/* safe_read_u32/u64 liegen weiter unten (im Dispatch-Block); der     */
/* Hosttest-Shim stellt sie bereits bereit.                            */
static int safe_read_u32(const void *addr, uint32_t *out);
static int safe_read_u64(const void *addr, uint64_t *out);

/* #520: pause_dom / resume_dom — nativ via LuaGraphNode::SetSuspended  */
/*                                                                    */
/* Reines C++-Primitiv (kein Lua/Console, kein DOM-Lua-Pfad #446):     */
/*   Exor::LuaGraphNode::SetSuspended(bool) = `mov byte [rcx+0xF1],dl; */
/*   ret` (RVA 0x1BA6CB0, Build 2.0.58485) — setzt NUR das Flag;       */
/*   LuaGraphNode::Update (RVA 0x1BAA140) kehrt bei [this+0xF1]!=0     */
/*   SOFORT zurueck => der DOM-Node friert ein (Pause).                */
/*                                                                    */
/* Instanz-Navigation (NATIV, ohne lua_*):                             */
/*   Alle LuaGraphNode-Instanzen teilen EINE vftable                  */
/*   (RVA 0x2F46D70, PDB `??_7LuaGraphNode@Exor@@6B@`). Unterschieden */
/*   werden sie ueber das Feld +0x30: das ist der FNV-1a-32-Hash des   */
/*   Lua-Skriptpfads der Klasse (live verifiziert auf planet            */
/*   2026-09-15: +0x30 == fnv1a("lua/missions/v2/dom_manager.lua")     */
/*   = 0x76aad119 => dom_mananger; daneben: survival_jungle-Node,      */
/*   graph/logic/…-Pool …). Der DOM-Node ist damit deterministisch      */
/*   auffindbar: vftable-Scan + TypeHash == fnv1a(DOM-Skript).          */
/*                                                                    */
/* Klassen-Layout (PDB + Disasm, Build 2.0.58485):                     */
/*   +0x20 luabind::object { lua_State* }  +0x28 registry-ref (int)    */
/*   +0x30 u32 TypeHash (FNV-1a des Skriptpfads)                       */
/*   +0xB8/+0xC0 child-node-Vector (begin/size)                        */
/*   +0xF0/+0xF1 finished/suspended-Flags                             */
/*                                                                    */
/* Thread-Modell (#378): reiner C++-Flag-Write, KEIN lua_*, kein Lock  */
/* => thread-agnostisch; der Pipe-Thread ruft direkt. Kein Detour.     */
/* Lua-Semantik-Note: das Spiel-Lua macht in PauseDOM zusaetzlich      */
/* `CampaignService:OperateDOMPlanetaryJump(true)` (ResumeDOM nur      */
/* `SetSuspended(false)`) — der HUD-Mission-Flow bleibt davon ohnehin  */
/* unberuehrt (siehe Skill-Pitfall). Diese Bridge bildet exakt         */
/* SetSuspended ab (Issue-Scope); PlanetaryJump ist Folge-Issue.       */
/*                                                                    */
/* Steht AUSSERHALB des #ifndef-RBBRIDGE_HOSTTEST-Blocks, damit der    */
/* Host-Test FNV-1a, Signatur-Selfcheck und TypeHash-Konstante direkt  */
/* pruefen kann.                                                      */
/* ------------------------------------------------------------------ */

#define RBBRIDGE_LUAGRAPHNODE_VFTABLE_RVA   0x2F46D70u
#define RBBRIDGE_LUAGRAPHNODE_L_OFF         0x20u
#define RBBRIDGE_LUAGRAPHNODE_REF_OFF       0x28u
#define RBBRIDGE_LUAGRAPHNODE_TYPEHASH_OFF  0x30u
#define RBBRIDGE_LUAGRAPHNODE_SUSPENDED_OFF 0xF1u

/* Lua-Skriptpfad der DOM-Klasse (MissionService:AddGameRule-Pfad);
 * die TypeHash-Konstante ist NUR Verifikations-Notiz — der Resolver
 * hasht den Pfad zur Laufzeit. */
static const char RBBRIDGE_DOM_SCRIPT[] = "lua/missions/v2/dom_manager.lua";
#define RBBRIDGE_DOM_SCRIPT_HASH 0x76aad119u

/* FNV-1a-32 (Offset-Basis 0x811c9dc5, Primzahl 0x01000193) — identisch
 * zur Resource-StringHash-Implementierung (docs/research/resource-hash-map.md).
 * Die Engine keyed auch Lua-Klassen-TypeHashes so (live belegt #520). */
static uint32_t rbbridge_fnv1a32(const char *s)
{
    uint32_t h = 0x811c9dc5u;
    if (!s)
        return h;
    for (const unsigned char *p = (const unsigned char *)s; *p; p++)
        h = (h ^ (uint32_t)*p) * 0x01000193u;
    return h;
}

/* Byte-Signatur von LuaGraphNode::SetSuspended (die GANZE Funktion,
 * 7 Bytes, Build 2.0.58485): `mov byte ptr [rcx+0xF1], dl; ret`.
 * Kein rel32-Operand => keine Wildcard-Maske noetig. Die Signatur ist im
 * .text genau EINMAL vorhanden (Gegenprobe 2026-09-15, planet); zur
 * Laufzeit wird die Eindeutigkeit erneut geprueft (s. resolve unten). */
static const unsigned char RBBRIDGE_SET_SUSPENDED_SIG[] = {
    0x88, 0x91, 0xF1, 0x00, 0x00, 0x00, 0xC3
};

/* Selfcheck (host-testbar): Laenge, Ret-Opcode und der adressierte
 * Flag-Offset (+0xF1) stehen konsistent zur Layout-Annahme. */
static int set_suspended_sig_selfcheck(void)
{
    static const unsigned char expect[] = {0x88, 0x91, 0xF1, 0x00, 0x00, 0x00, 0xC3};
    if (sizeof(RBBRIDGE_SET_SUSPENDED_SIG) != sizeof(expect))
        return 0;
    if (memcmp(RBBRIDGE_SET_SUSPENDED_SIG, expect, sizeof(expect)) != 0)
        return 0;
    /* +0xF1 im Displacement der mov-Anweisung? */
    if (RBBRIDGE_SET_SUSPENDED_SIG[2] != (unsigned char)(RBBRIDGE_LUAGRAPHNODE_SUSPENDED_OFF & 0xFF))
        return 0;
    /* TypeHash der DOM-Klasse == FNV-1a ihres Skriptpfads? */
    if (rbbridge_fnv1a32(RBBRIDGE_DOM_SCRIPT) != RBBRIDGE_DOM_SCRIPT_HASH)
        return 0;
    return 1;
}

/* Zaehlt Treffer einer Signatur im Modul-Image (Eindeutigkeits-Guard). */
static int sig_count_in_image(const unsigned char *base, size_t size,
                              const unsigned char *sig, size_t n)
{
    const unsigned char *p = base;
    const unsigned char *end = base + size;
    int count = 0;

    if (!base || !sig || !n)
        return 0;
    while (p < end) {
        const unsigned char *hit = scan_bytes(p, (size_t)(end - p), sig, n);
        if (!hit)
            break;
        count++;
        p = hit + 1;
    }
    return count;
}

/* Loest LuaGraphNode::SetSuspended per AOB auf — NUR wenn die Signatur im
 * .text genau einmal vorkommt (sonst NULL, kein Aufruf). */
static const unsigned char *resolve_set_suspended_fn(const unsigned char *base,
                                                     size_t size)
{
    if (!set_suspended_sig_selfcheck())
        return NULL;
    if (sig_count_in_image(base, size, RBBRIDGE_SET_SUSPENDED_SIG,
                           sizeof(RBBRIDGE_SET_SUSPENDED_SIG)) != 1)
        return NULL;
    return scan_bytes(base, size, RBBRIDGE_SET_SUSPENDED_SIG,
                      sizeof(RBBRIDGE_SET_SUSPENDED_SIG));
}

/* Findet den DOM-Node (dom_mananger): vftable-Scan + TypeHash-Filter.
 * Reine Leseoperation, kein Aufruf — graceful NULL. */
static void *resolve_dom_node(const unsigned char *base)
{
    uint32_t want;
    uint64_t needle;
    uintptr_t addr = 0;

    if (!base)
        return NULL;
    want = rbbridge_fnv1a32(RBBRIDGE_DOM_SCRIPT);
    needle = (uint64_t)(uintptr_t)(base + RBBRIDGE_LUAGRAPHNODE_VFTABLE_RVA);

    for (;;) {
        MEMORY_BASIC_INFORMATION mi;
        if (VirtualQuery((const void *)addr, &mi, sizeof(mi)) == 0)
            break;
        uintptr_t next = (uintptr_t)mi.BaseAddress + mi.RegionSize;
        if (next <= addr)
            break;
        addr = next;
        if (!is_readable_region(&mi))
            continue;
        const uint64_t *q = (const uint64_t *)mi.BaseAddress;
        size_t nq = mi.RegionSize / sizeof(uint64_t);
        for (size_t i = 0; i < nq; i++) {
            unsigned char *inst;
            uint32_t th = 0, ref32 = 0;
            uint64_t L = 0;
            if (q[i] != needle)
                continue;
            inst = (unsigned char *)&q[i];
            if (!safe_read_u32(inst + RBBRIDGE_LUAGRAPHNODE_TYPEHASH_OFF, &th))
                continue;
            if (th != want)
                continue; /* anderer LuaGraphNode (Pool/Mission) */
            if (!safe_read_u64(inst + RBBRIDGE_LUAGRAPHNODE_L_OFF, &L) || !L)
                continue;
            if (!safe_read_u32(inst + RBBRIDGE_LUAGRAPHNODE_REF_OFF, &ref32))
                continue;
            if ((int32_t)ref32 < 0)
                continue;
            return inst;
        }
    }
    return NULL;
}

#ifndef RBBRIDGE_HOSTTEST

/* ------------------------------------------------------------------ */
/* #479: Readiness-Gate (produktionsseitig)                            */
/*                                                                    */
/* world_is_ready(): 1, sobald die Welt nachweislich fertig ist. Das    */
/* Signal kommt aus dem Spiel-Log (exor_logs.txt), NICHT aus einem      */
/* `world != NULL`-Test: der Pointer ist frueh non-NULL, die ECS-/       */
/* Team-Strukturen aber noch im Aufbau (Crash #436/#479).               */
/*                                                                    */
/* Einmal erkannt -> gelatcht. Die Welt wird im Betrieb nicht wieder     */
/* "unfertig"; ein Map-Neustart geht ohnehin mit Prozess-Neustart +      */
/* frischem exor_logs.txt einher.                                       */
/* ------------------------------------------------------------------ */

/* Log-Suffixe relativ zu %USERPROFILE% (Wine: C:\users\<user>).
 * Fall 1 = Community-Rezept (Documents), Fall 2 = -Dedicated-Server-Pfad. */
static const char *const RBBRIDGE_EXOR_LOG_CANDIDATES[] = {
    "\\Documents\\The Riftbreaker\\exor_logs.txt",
    "\\AppData\\LocalLow\\The Riftbreaker - Dedicated Server\\exor_logs.txt",
};

static int g_world_ready = 0; /* Latch: 1 = Welt fertig (einmalig gesetzt) */

/* Eine Logdatei oeffnen, (bis Cap) einlesen und auf Ready-Marker pruefen.
 * Rueckgabe 1 = Marker gefunden. Jeder Fehler -> 0 (konservativ). */
static int readiness_scan_file(const char *path)
{
    HANDLE h;
    char *buf;
    DWORD got = 0;
    int ready = 0;
    /* exor_logs.txt ist wenige 10 KB; 4 MB als harte Obergrenze genuegt
     * und verhindert, dass ein absichtlich/versehentlich riesiger Log
     * den Pipe-Thread blockiert. */
    const DWORD cap = 4u * 1024u * 1024u;

    if (!path || !path[0])
        return 0;
    h = CreateFileA(path, GENERIC_READ,
                    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE)
        return 0;
    buf = (char *)malloc(cap);
    if (!buf) {
        CloseHandle(h);
        return 0;
    }
    if (ReadFile(h, buf, cap, &got, NULL) && got > 0)
        ready = rbbridge_log_is_ready(buf, (size_t)got);
    free(buf);
    CloseHandle(h);
    return ready;
}

/* Welt fertig? Prueft zuerst den expliziten Override RBBRIDGE_EXOR_LOG,
 * dann die Standardpfade unter %USERPROFILE%. Rueckgabe 1 = bereit. */
static int world_is_ready(void)
{
    char env[1024];

    if (g_world_ready)
        return 1;

    if (GetEnvironmentVariableA("RBBRIDGE_EXOR_LOG", env, sizeof(env)) > 0 &&
        env[0]) {
        if (readiness_scan_file(env)) {
            g_world_ready = 1;
            return 1;
        }
    }

    if (GetEnvironmentVariableA("USERPROFILE", env, sizeof(env)) > 0 &&
        env[0]) {
        char path[1200];
        for (size_t i = 0;
             i < sizeof(RBBRIDGE_EXOR_LOG_CANDIDATES) /
                     sizeof(RBBRIDGE_EXOR_LOG_CANDIDATES[0]);
             i++) {
            snprintf(path, sizeof(path), "%s%s", env,
                     RBBRIDGE_EXOR_LOG_CANDIDATES[i]);
            if (readiness_scan_file(path)) {
                g_world_ready = 1;
                return 1;
            }
        }
    }
    return 0;
}

/* Gate fuer alle Game-Calls: liefert 1, wenn der Call NICHT laufen darf
 * (und hat dann bereits `{"event":<event>,"ok":false,
 * "reason":"world_not_ready"}` gesendet). 0 = Call darf laufen. */
static int readiness_block(HANDLE hPipe, const char *event)
{
    if (world_is_ready())
        return 0;
    dbg("readiness-gate: %s -> world_not_ready (kein Game-Call)",
        event ? event : "?");
    send_line(hPipe,
              "{\"event\":\"%s\",\"ok\":false,"
              "\"reason\":\"world_not_ready\"}",
              event ? event : "error");
    return 1;
}

/* Liest ein QWORD von addr, nur wenn die Region committet+lesbar ist.
 * Rueckgabe 1 = gelesen, 0 = nicht lesbar (out bleibt unveraendert). */
static int safe_read_u64(const void *addr, uint64_t *out) {
  MEMORY_BASIC_INFORMATION mi;
  if (!VirtualQuery(addr, &mi, sizeof(mi)))
    return 0;
  if (!is_readable_region(&mi))
    return 0;
  if ((uintptr_t)addr + sizeof(uint64_t) >
      (uintptr_t)mi.BaseAddress + mi.RegionSize)
    return 0;
  memcpy(out, addr, sizeof(uint64_t));
  return 1;
}

/* Liest ein DWORD von addr, nur wenn die Region committet+lesbar ist. */
static int safe_read_u32(const void *addr, uint32_t *out) {
  MEMORY_BASIC_INFORMATION mi;
  if (!VirtualQuery(addr, &mi, sizeof(mi)))
    return 0;
  if (!is_readable_region(&mi))
    return 0;
  if ((uintptr_t)addr + sizeof(uint32_t) >
      (uintptr_t)mi.BaseAddress + mi.RegionSize)
    return 0;
  memcpy(out, addr, sizeof(uint32_t));
  return 1;
}

/* Dumpft n QWORDS ab addr als Hex-Array (eine JSON-Zeile). Sichere Reads:
 * nicht-lesbare Woerter werden als null ausgegeben (kein Crash). */
static void dump_qwords(HANDLE hPipe, const char *label, const void *addr,
                        size_t n) {
  char buf[RESP_BUF_SIZE];
  size_t off = 0;
  int w = snprintf(buf + off, sizeof(buf) - off,
                   "{\"event\":\"probe_dump\",\"label\":\"%s\","
                   "\"addr\":\"0x%llx\",\"qwords\":[",
                   label, (unsigned long long)(uintptr_t)addr);
  if (w < 0)
    return;
  off += (size_t)w;

  for (size_t i = 0; i < n; i++) {
    uint64_t v = 0;
    safe_read_u64((const unsigned char *)addr + i * 8, &v);
    if (off + 32 >= sizeof(buf))
      break;
    w = snprintf(buf + off, sizeof(buf) - off, "%s\"0x%llx\"", i ? "," : "",
                 (unsigned long long)v);
    if (w < 0)
      return;
    off += (size_t)w;
  }
  snprintf(buf + off, sizeof(buf) - off, "]}");
  send_line(hPipe, "%s", buf);
}

/* Scannt lesbare Regionen nach einem 32-bit-Wert (z.B. carbonium-Hash
 * 0x659cc791) und dumpft Treffer + den 8-Byte-Wert bei +8 (ResourceValue). */
static void scan_hash(HANDLE hPipe, uint32_t needle, int max_hits) {
  uintptr_t addr = 0;
  int hits = 0;
  unsigned long long regions = 0, bytes = 0;

  for (;;) {
    MEMORY_BASIC_INFORMATION mi;
    if (VirtualQuery((const void *)addr, &mi, sizeof(mi)) == 0)
      break;
    uintptr_t next = (uintptr_t)mi.BaseAddress + mi.RegionSize;
    if (next <= addr)
      break;
    addr = next;
    if (!is_readable_region(&mi))
      continue;
    regions++;
    bytes += mi.RegionSize;

    const uint32_t *p = (const uint32_t *)mi.BaseAddress;
    size_t n = mi.RegionSize / sizeof(uint32_t);
    for (size_t i = 0; i < n; i++) {
      if (p[i] == needle) {
        const unsigned char *e = (const unsigned char *)&p[i];
        uint64_t val = 0;
        safe_read_u64(e + 8, &val);
        send_line(hPipe,
                  "{\"event\":\"scan_hit\",\"hash\":\"0x%08x\","
                  "\"addr\":\"0x%llx\",\"value\":%llu,\"value_hex\":\"0x%llx\"}",
                  needle, (unsigned long long)(uintptr_t)e,
                  (unsigned long long)val, (unsigned long long)val);
        if (++hits >= max_hits) {
          send_line(hPipe,
                    "{\"event\":\"scan_done\",\"hits\":%d,\"regions\":%llu,"
                    "\"bytes\":%llu}",
                    hits, regions, bytes);
          return;
        }
      }
    }
  }
  send_line(hPipe,
            "{\"event\":\"scan_done\",\"hits\":%d,\"regions\":%llu,"
            "\"bytes\":%llu}",
            hits, regions, bytes);
}

/* Probe (RE #363): PlayerService-Kette live dumpen, um die Account-Struktur
 * zu bestaetigen. Gibt Pointer + Speicher-Fenster als JSON aus. */
static void probe_resources(HANDLE hPipe) {
  const unsigned char *base = NULL;
  size_t size = 0;
  const char *via = NULL;
  const unsigned char *execfn = NULL;

  if (!resolve_module(&base, &size, &via, &execfn)) {
    send_line(hPipe, "{\"event\":\"probe\",\"error\":\"no_module\"}");
    return;
  }

  /* #479: Readiness-Gate — die Probe traversiert die PlayerService-Kette
   * (World-Nutzung) und darf erst nach fertiger Welt laufen. */
  if (!world_is_ready()) {
    send_line(hPipe, "{\"event\":\"probe\",\"error\":\"world_not_ready\"}");
    return;
  }

  /* PlayerService-vftable RVA 0x2e8e910 (RE #363, build-konsistent). */
  const unsigned char *vftable = base + 0x2e8e910;
  const uint64_t needle = (uint64_t)(uintptr_t)vftable;
  unsigned char *ps = NULL;
  uintptr_t addr = 0;

  for (;;) {
    MEMORY_BASIC_INFORMATION mi;
    if (VirtualQuery((const void *)addr, &mi, sizeof(mi)) == 0)
      break;
    uintptr_t next = (uintptr_t)mi.BaseAddress + mi.RegionSize;
    if (next <= addr)
      break;
    addr = next;
    if (!is_readable_region(&mi))
      continue;
    const uint64_t *q = (const uint64_t *)mi.BaseAddress;
    size_t nq = mi.RegionSize / sizeof(uint64_t);
    for (size_t i = 0; i < nq; i++) {
      if (q[i] == needle) {
        ps = (unsigned char *)&q[i];
        break;
      }
    }
    if (ps)
      break;
  }

  if (!ps) {
    send_line(hPipe, "{\"event\":\"probe\",\"error\":\"no_playerservice\"}");
    return;
  }

  uint64_t resource_system = 0;
  safe_read_u64(ps + 8, &resource_system);
  /* Container ist EMBEDDED bei resource_system+0x30 (lea, kein Deref) -
   * im Disasm 0x180c60050 / 0x180f1e700 belegt. */
  uint64_t container = resource_system ? resource_system + 0x30 : 0;

  send_line(hPipe,
            "{\"event\":\"probe\",\"playerservice\":\"0x%llx\","
            "\"resource_system\":\"0x%llx\",\"container\":\"0x%llx\"}",
            (unsigned long long)(uintptr_t)ps,
            (unsigned long long)resource_system, (unsigned long long)container);

  dump_qwords(hPipe, "playerservice", ps, 24);
  if (resource_system)
    dump_qwords(hPipe, "resource_system",
                (const void *)(uintptr_t)resource_system, 24);
  if (container)
    dump_qwords(hPipe, "container", (const void *)(uintptr_t)container, 24);

  /* carbonium-Hash scannen (Cross-Check). */
  scan_hash(hPipe, 0x659cc791, 24);

  /* Account-Basket deterministisch dumpen: GetPlayerAccount(World*, 0) ->
   * ResourceAccount*; account[+8] = sortiertes (StringHash, ResourceValue)-
   * Array, account[+0x10] = Count (Disasm 0x2E04A0). */
  {
    uint64_t world = 0;
    safe_read_u64(ps + 8, &world);
    if (world) {
      void *(*gpa)(void *, unsigned int) =
          (void *(*)(void *, unsigned int))(uintptr_t)(base + 0xC60050);
      void *account = gpa((void *)(uintptr_t)world, 0);
      if (account) {
        uint64_t arr = 0, count = 0;
        safe_read_u64((unsigned char *)account + 8, &arr);
        safe_read_u64((unsigned char *)account + 0x10, &count);
        send_line(hPipe,
                  "{\"event\":\"account\",\"account\":\"0x%llx\","
                  "\"array\":\"0x%llx\",\"count\":%llu}",
                  (unsigned long long)(uintptr_t)account,
                  (unsigned long long)arr, (unsigned long long)count);
        if (arr && count && count < 256) {
          for (unsigned long long i = 0; i < count; i++) {
            const unsigned char *e = (const unsigned char *)(uintptr_t)arr +
                                     i * 16;
            uint64_t hv = 0, v = 0;
            safe_read_u64(e, &hv);
            safe_read_u64(e + 8, &v);
            send_line(hPipe,
                      "{\"event\":\"basket_entry\",\"i\":%llu,"
                      "\"hash\":\"0x%08x\",\"value_hex\":\"0x%llx\","
                      "\"value\":%llu}",
                      i, (unsigned int)hv, (unsigned long long)v,
                      (unsigned long long)v);
          }
        }
      } else {
        send_line(hPipe, "{\"event\":\"account\",\"error\":\"no_account\"}");
      }
    } else {
      send_line(hPipe, "{\"event\":\"account\",\"error\":\"no_world\"}");
    }
  }
}


/* get_state (Issue #363/#365, Ironium #401): liest den Account-Basket und
 * liefert EINE get_state_result-Zeile (carbonium/ironium/max/resources,
 * pure C++). Ironium ist der Anzeigename der internen Ressource "steel"
 * (Hash 0x0d01a504, siehe RBBRIDGE_HASH_IRONIUM). */
/*
 * Liest die max/capacity einer Ressource (int64-Fixed-Point x10^6).
 * Quelle (RE #370): ResourceAccount+0x20 ist eine Hash-Map (StringHash ->
 * float max in Display-Einheiten). Lookup via 0x18028ac00(container, &out,
 * &hash); Treffer: node = out[0], float max bei node+0xc. Der Wert wird mit
 * der scale-Globale (RVA 0x4794210, zur Laufzeit 1e6) in Fixed-Point
 * umgerechnet: max = (int64)(scale * max_float).
 * Rueckgabe: max (>=0) oder -1 bei keinem Fund/Lesefehler.
 */
static int64_t read_resource_max(const unsigned char *base,
                                 const void *account, uint32_t hash)
{
    typedef void (__fastcall *account_lookup_fn)(void *, void *,
                                                 const uint32_t *);
    account_lookup_fn lookup =
        (account_lookup_fn)(uintptr_t)(base + 0x28ac00);

    uint64_t out[4] = {0, 0, 0, 0};
    lookup((void *)((const unsigned char *)account + 0x20), (void *)out,
           &hash);

    uint64_t node = out[0];
    if (!node)
        return -1;

    uint32_t bits = 0;
    if (!safe_read_u32((const unsigned char *)(uintptr_t)node + 0xc, &bits))
        return -1;

    uint64_t scale64 = 0;
    safe_read_u64(base + 0x4794210, &scale64);
    uint32_t scale = (uint32_t)scale64;
    if (scale == 0)
        scale = 1000000u;

    float f;
    memcpy(&f, &bits, sizeof(f));
    return (int64_t)((double)f * (double)scale);
}


/* ------------------------------------------------------------------ */
/* MissionService / ActivateMissionFlow (Issue #385)                   */
/*                                                                    */
/* Reines C++-Primitiv (kein Lua):                                     */
/*   MissionService::ActivateMissionFlow(UtfString const& name,        */
/*       UtfString const& logicFile, UtfString const& mode,            */
/*       Database* data)                                              */
/* startet einen Mission-Flow (Welle). Service-Instanz per vftable-     */
/* Scan (RVA 0x2E962A0), Funktion per AOB-Signatur (RBBRIDGE_ACTIVATE_ */
/* SIG - KEINE feste Adresse). Die UtfString-Argumente werden ueber den */
/* Spiel-eigenen Ctor (RVA 0x3AE1E0) gebaut - NICHT von Hand (SSO fasst */
/* nur 15 Zeichen; Logic-Pfade sind laenger -> Heap-Allokation durch    */
/* den Ctor). `data` bleibt NULL (#386 liefert das Database*-Objekt     */
/* nach); der 3-Arg-Overload des Spiels ruft den Workhorse selbst mit   */
/* NULL.                                                               */
/*                                                                    */
/* Thread-Modell (#378): native C++-Reads/Writes sind thread-agnostisch;*/
/* der Aufruf laeuft auf dem Pipe-Thread und enthaelt KEIN lua_*.       */
/* ------------------------------------------------------------------ */

/* Scannt den eigenen Adressraum (nur MEM_COMMIT + lesbar, kein PAGE_GUARD)
 * nach einem 8-Byte-alignierten QWORD == needle. Reine Leseoperation, kein
 * Aufruf; Rueckgabe = Fundstelle (erstes Vorkommen) oder NULL. */
static unsigned char *scan_qword_instance(uint64_t needle)
{
    uintptr_t addr = 0;
    for (;;) {
        MEMORY_BASIC_INFORMATION mi;
        if (VirtualQuery((const void *)addr, &mi, sizeof(mi)) == 0)
            break;
        uintptr_t next = (uintptr_t)mi.BaseAddress + mi.RegionSize;
        if (next <= addr)
            break;
        addr = next;
        if (!is_readable_region(&mi))
            continue;
        const uint64_t *q = (const uint64_t *)mi.BaseAddress;
        size_t nq = mi.RegionSize / sizeof(uint64_t);
        for (size_t i = 0; i < nq; i++) {
            if (q[i] == needle)
                return (unsigned char *)&q[i];
        }
    }
    return NULL;
}

/* Baut eine Exor::UtfString-Instanz (40 Byte) aus einem C-String ueber den
 * Spiel-eigenen Ctor (RVA 0x3AE1E0). Layout (Disasm): +0x00 = Allocator-
 * Proxy, +0x08 = SSO-Puffer/Heap-Ptr, +0x18 = size, +0x20 = capacity. */
typedef void *(__fastcall *utfstring_ctor_fn)(void *self, const char *s);
typedef void (__fastcall *utfstring_dtor_fn)(void *self);

static void build_utfstring(const unsigned char *base, const char *s,
                            unsigned char out[40])
{
    memset(out, 0, 40);
    utfstring_ctor_fn ctor =
        (utfstring_ctor_fn)(uintptr_t)(base + RBBRIDGE_RVA_UTFSTRING_CTOR);
    ctor((void *)out, s);
}

static void destroy_utfstring(const unsigned char *base, unsigned char out[40])
{
    utfstring_dtor_fn dtor =
        (utfstring_dtor_fn)(uintptr_t)(base + RBBRIDGE_RVA_UTFSTRING_DTOR);
    dtor((void *)out);
}

/* Kopiert den Inhalt einer UtfString-Instanz als C-String nach buf.
 * data@+8 (SSO) bzw. *(us+8) (Heap, capacity > 15), size@+0x18.
 * Rueckgabe 1 = ok. */
static int utfstring_to_cstr(const unsigned char *us, char *buf, size_t n)
{
    uint64_t size = 0, cap = 0, ptr = 0;
    MEMORY_BASIC_INFORMATION mi;
    const unsigned char *data;

    if (n == 0)
        return 0;
    buf[0] = '\0';
    if (!safe_read_u64(us + 0x18, &size) || !safe_read_u64(us + 0x20, &cap))
        return 0;
    data = us + 8;
    if (cap > 0xf) {
        if (!safe_read_u64(us + 8, &ptr) || !ptr)
            return 0;
        data = (const unsigned char *)(uintptr_t)ptr;
    }
    if (size >= n)
        size = n - 1;
    if (!VirtualQuery(data, &mi, sizeof(mi)) || !is_readable_region(&mi))
        return 0;
    if (data + size > (const unsigned char *)mi.BaseAddress + mi.RegionSize)
        return 0;
    if (size)
        memcpy(buf, data, (size_t)size);
    buf[size] = '\0';
    return 1;
}

/* Minimales JSON-Escaping fuer String-Werte (Quote/Backslash/Steuerzeichen);
 * der Flow-Name stammt aus dem Spiel. */
static void json_escape_into(const char *in, char *out, size_t n)
{
    size_t o = 0;
    if (n == 0)
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

/* Database-ABI (MSVC x64, aus dem Disasm):
 *   Database::Database()                       this=RCX
 *   Database::SetString(UtfString const& key,
 *                       UtfString const& value) this=RCX, key=RDX, val=R8
 *   Database::GetString(UtfString const& key)   this=RCX, key=RDX -> ut*/
typedef void (__fastcall *db_ctor_fn)(void *self);
typedef void (__fastcall *db_setstring_fn)(void *self, const void *key,
                                           const void *value);
typedef const void *(__fastcall *db_getstring_fn)(void *self,
                                                  const void *key);

/* Baut ein frisches 0x60-Byte-Database-Objekt (Default-Ctor + SetString).
 * Fehlt eine Adresse -> NULL (kein Aufruf). Das Objekt wird bewusst NICHT
 * freigegeben: der Mission-Flow-Kern reicht den Zeiger durch (Lifetime bis
 * Flow-Ende). Rueckgabe = Objekt oder NULL. */
static void *build_database_payload(const unsigned char *base,
                                    const void *ctor, const void *setstr,
                                    const char *key, const char *value)
{
    unsigned char *db;
    unsigned char k[40], v[40];

    if (!base || !ctor || !setstr)
        return NULL;
    db = (unsigned char *)malloc(0x60);
    if (!db)
        return NULL;
    memset(db, 0, 0x60);
    ((db_ctor_fn)(uintptr_t)ctor)((void *)db);
    build_utfstring(base, key, k);
    build_utfstring(base, value ? value : "", v);
    ((db_setstring_fn)(uintptr_t)setstr)((void *)db, (const void *)k,
                                         (const void *)v);
    destroy_utfstring(base, v);
    destroy_utfstring(base, k);
    return db;
}

/* Liest ein Feld aus dem geparkten Database-Objekt via Database::GetString.
 * Rueckgabe 1 = ok. Nie ein Crash (utfstring_to_cstr prueft die Region). */
static int database_get_string(const unsigned char *base, const void *db,
                               const void *getstr, const char *key,
                               char *out, size_t out_sz)
{
    unsigned char k[40];
    const void *v;

    if (!base || !db || !getstr || !out || out_sz == 0)
        return 0;
    out[0] = '\0';
    build_utfstring(base, key, k);
    v = ((db_getstring_fn)(uintptr_t)getstr)((void *)db, (const void *)k);
    destroy_utfstring(base, k);
    if (!v)
        return 0;
    return utfstring_to_cstr((const unsigned char *)v, out, out_sz);
}

/* Letzter per activate_mission_flow gestarteter Flow (Flow-ID des Spiels);
 * dient dem Read-Feld in get_state. Zugriff nur auf dem (einzigen)
 * Pipe-Server-Thread -> kein Lock noetig. */
static char g_last_flow[192];

/* #386: zuletzt gebautes Exor::Database-Payload (Mission-Flow `data`) und
 * der darin gesetzte spawn_point. Geparkt fuer die Read-Leg in get_state;
 * absichtlich NICHT freigegeben (Lifetime bis Flow-Ende). Zugriff nur auf
 * dem (einzigen) Pipe-Server-Thread -> kein Lock noetig. */
static void *g_mission_payload_db = NULL;
static char g_mission_spawn[128] = "";

/*
 * activate_mission_flow (Write #385/#386): startet einen Mission-Flow
 * direkt ueber den C++-Workhorse (AOB-aufgeloest). Ist `spawn_point`
 * gesetzt, wird zusaetzlich ein Exor::Database-Payload (#386) gebaut
 * (Default-Ctor + SetString("spawn_point", ...), beide AOB-aufgeloest)
 * und als `data`-Argument ([rsp+0x28]) durchgereicht. Events:
 *   {"event":"activate_mission_flow_result","ok":true,"flow":"<id>",
 *    "spawn_point":"<sp>"}
 *   {"event":"activate_mission_flow_result","ok":false,"reason":"..."}
 */
static void dispatch_activate_mission_flow(HANDLE hPipe, const char *logic,
                                           const char *mode,
                                           const char *spawn_point)
{
    const unsigned char *base = NULL;
    size_t size = 0;
    const char *via = NULL;
    const unsigned char *execfn = NULL;
    const unsigned char *fn = NULL;
    unsigned char *ms;
    unsigned char u_name[40], u_logic[40], u_mode[40], ret[40];
    char flow[192] = "";
    char esc[192 * 2];
    char esc_sp[128 * 2];
    void *payload = NULL;
    int want_payload = (spawn_point && spawn_point[0]) ? 1 : 0;

    typedef void *(__fastcall *activate_fn)(void *self, void *retbuf,
                                            const void *name,
                                            const void *logicFile,
                                            const void *mode, const void *data);
    activate_fn act;

    if (!logic || !logic[0]) {
        send_line(hPipe, "{\"event\":\"activate_mission_flow_result\","
                         "\"ok\":false,\"reason\":\"missing_logic\"}");
        return;
    }

    /* #447: nur "default" ans Spiel reichen — ein anderer Modus killt die
     * DLL-Pipe dauerhaft (siehe mission_flow_mode_ok). */
    if (!mission_flow_mode_ok(mode)) {
        dbg("activate_mission_flow: mode '%s' abgelehnt (nur 'default')",
            mode ? mode : "");
        send_line(hPipe, "{\"event\":\"activate_mission_flow_result\","
                         "\"ok\":false,\"reason\":\"bad_mode\"}");
        return;
    }

    /* #479: Readiness-Gate vor dem Game-Call (Welt muss fertig sein). */
    if (readiness_block(hPipe, "activate_mission_flow_result"))
        return;

    if (!resolve_module(&base, &size, &via, &execfn)) {
        send_line(hPipe, "{\"event\":\"activate_mission_flow_result\","
                         "\"ok\":false,\"reason\":\"no_module\"}");
        return;
    }

    /* Funktion per AOB-Signatur (kein festes RVA) im Modulabbild. */
    fn = scan_bytes(base, size, RBBRIDGE_ACTIVATE_SIG,
                    sizeof(RBBRIDGE_ACTIVATE_SIG));
    if (!fn) {
        dbg("activate_mission_flow: AOB-Signatur nicht gefunden");
        send_line(hPipe, "{\"event\":\"activate_mission_flow_result\","
                         "\"ok\":false,\"reason\":\"no_activate_signature\"}");
        return;
    }

    /* MissionService-Instanz per vftable-Scan (RVA 0x2E962A0). */
    ms = scan_qword_instance(
        (uint64_t)(uintptr_t)(base + RBBRIDGE_RVA_MISSIONSERVICE_VFTABLE));
    if (!ms) {
        send_line(hPipe, "{\"event\":\"activate_mission_flow_result\","
                         "\"ok\":false,\"reason\":\"no_missionservice\"}");
        return;
    }

    /* #386: Database-Payload nur bei gesetztem spawn_point bauen; die
     * Adressen kommen aus AOB-Signaturen (KEIN festes RVA). Nicht-Fund ->
     * ok:false, KEIN Aufruf. */
    if (want_payload) {
        const void *db_ctor = resolve_db_ctor_fn(base, size);
        const void *db_setstr = resolve_db_setstring_fn(base, size);
        if (!db_ctor || !db_setstr) {
            dbg("activate_mission_flow: DB-Signatur fehlt (ctor=%p setstr=%p)",
                db_ctor, db_setstr);
            send_line(hPipe,
                      "{\"event\":\"activate_mission_flow_result\","
                      "\"ok\":false,\"reason\":\"no_database_signature\"}");
            return;
        }
        payload = build_database_payload(base, db_ctor, db_setstr,
                                         "spawn_point", spawn_point);
        if (!payload) {
            send_line(hPipe,
                      "{\"event\":\"activate_mission_flow_result\","
                      "\"ok\":false,\"reason\":\"payload_alloc_failed\"}");
            return;
        }
    }

    /* Argumente: name="" (wie dom_mananger:SpawnWave), logicFile, mode. */
    build_utfstring(base, "", u_name);
    build_utfstring(base, logic, u_logic);
    build_utfstring(base, (mode && mode[0]) ? mode : "default", u_mode);
    memset(ret, 0, sizeof(ret));

    act = (activate_fn)(uintptr_t)fn;

    /* data = Database-Payload (#386) oder NULL (#385-Default). */
    act((void *)ms, ret, u_name, u_logic, u_mode, payload);

    utfstring_to_cstr(ret, flow, sizeof(flow));

    /* Ergebnis-UtfString + Argumente wieder freigeben (Ctor-Heap). */
    destroy_utfstring(base, ret);
    destroy_utfstring(base, u_mode);
    destroy_utfstring(base, u_logic);
    destroy_utfstring(base, u_name);

    /* Flow-ID fuer das Read-Feld in get_state merken. */
    {
        size_t i = 0;
        for (; flow[i] && i + 1 < sizeof(g_last_flow); i++)
            g_last_flow[i] = flow[i];
        g_last_flow[i] = '\0';
    }

    if (payload) {
        g_mission_payload_db = payload;
        copy_cstr(g_mission_spawn, sizeof(g_mission_spawn), spawn_point);
    }

    json_escape_into(flow, esc, sizeof(esc));
    json_escape_into(g_mission_spawn, esc_sp, sizeof(esc_sp));

    dbg("activate_mission_flow: logic='%s' mode='%s' spawn_point='%s' "
        "fn_rva=%08lx ms=%p payload=%p flow='%s'",
        logic, (mode && mode[0]) ? mode : "default", g_mission_spawn,
        (unsigned long)(uintptr_t)(fn - base), (void *)ms, payload, flow);

    send_line(hPipe,
              "{\"event\":\"activate_mission_flow_result\",\"ok\":true,"
              "\"flow\":\"%s\",\"spawn_point\":\"%s\"}",
              esc, esc_sp);
}

/* Mission-Flow-Read fuer get_state: 1 wenn `flow` laut
 * MissionService::IsGraphActive(UtfString const&) aktiv ist, sonst 0.
 * Kein Flow gemerkt / kein Service -> 0 (graceful). */
static int mission_flow_active(const unsigned char *base, const char *flow)
{
    unsigned char *ms;
    unsigned char u[40];
    typedef unsigned char (__fastcall *is_active_fn)(void *, const void *);
    is_active_fn is_active;
    unsigned char r;

    if (!flow || !flow[0])
        return 0;
    ms = scan_qword_instance(
        (uint64_t)(uintptr_t)(base + RBBRIDGE_RVA_MISSIONSERVICE_VFTABLE));
    if (!ms)
        return 0;

    build_utfstring(base, flow, u);
    is_active = (is_active_fn)(uintptr_t)(base + RBBRIDGE_RVA_ISGRAPHACTIVE);
    r = is_active((void *)ms, (const void *)u);
    destroy_utfstring(base, u);
    return r ? 1 : 0;
}

/* ------------------------------------------------------------------ */
/* CampaignService::*CreaturesBaseDifficulty (Issue #388)              */
/*                                                                    */
/* Vier native C++-Methoden (KEIN Lua, KEIN Console):                  */
/*   GetCreaturesBaseDifficulty()          -> float                    */
/*   SetCreaturesBaseDifficulty(float)     -> absolut setzen           */
/*   Increase-/DecreaseCreaturesBaseDifficulty(float) -> Delta         */
/* Alle vier werden per AOB-Signatur aufgeloest (RBBRIDGE_DIFF_*_SIG,  */
/* planet-verifiziert eindeutig) - KEINE feste Adresse. Die            */
/* CampaignService-Instanz kommt aus dem vftable-QWORD-Scan            */
/* (RVA 0x2E9C340). Die Layout-Offsets (this-deref + Feld-Offset)       */
/* werden aus dem AOB-aufgeloesten Funktionskoerper DEKODIERT, nicht    */
/* hart verdrahtet.                                                    */
/*                                                                    */
/* Aufruf-Strategie: die Methoden werden NICHT indirekt aufgerufen.    */
/* Live-Befund (#388, planet): ein `call` mit scan-abgeleitetem `this`  */
/* aus dem Pipe-Thread hat den Pipe-Thread gefaultet (Unhandled page    */
/* fault, Stack-Guard) und die Bridge bis zum Restart lahmgelegt. Der   */
/* Effekt der Methoden (movss-Load/Store auf [*[this+this_deref]+off])   */
/* wird deshalb als VirtualQuery-geprueter Speicher-Zugriff ausgefuehrt  */
/* - identische Semantik, kein Fremdspeicher-Zugriff.                   */
/*                                                                    */
/* Thread-Modell (#378): reine Speicher-Ops -> thread-agnostisch,       */
/* laeuft auf dem Pipe-Thread. Kein lua_* beteiligt.                    */
/* ------------------------------------------------------------------ */

#ifdef RBBRIDGE_HOSTTEST
#define RBBRIDGE_NOINLINE
#else
#define RBBRIDGE_NOINLINE __attribute__((noinline))
#endif

typedef struct {
    int valid;
    const unsigned char *module_base;
    size_t module_size;
    unsigned char *cs; /* CampaignService-Instanz */
    uint32_t this_deref;
    int32_t field_off;
    /* Fundstellen der vier Funktionen: erlaubt die Re-Validierung der
     * Funktionskoerper (Layout-Hotpatch bei gleicher Modulbasis) ohne
     * kompletten Neu-Scan (Review-Hinweis PR #433). */
    const unsigned char *fget, *fset, *finc, *fdec;
} campaign_diff_cache_t;

static campaign_diff_cache_t g_campdiff_cache;

/* Loest den kompletten Pfad auf: vier AOB-Signaturen -> Offsets aus dem
 * Funktionskoerper -> CampaignService-Instanz per vftable-Scan. Alles rein
 * lesend (VirtualQuery-geprueft). Rueckgabe 1 = auflösbar, 0 = graceful. */
static RBBRIDGE_NOINLINE int resolve_campaign_diff(const unsigned char *base,
                                                   size_t size,
                                                   unsigned char **out_cs,
                                                   uint32_t *out_this_deref,
                                                   int32_t *out_field_off)
{
    const unsigned char *fget, *fset, *finc, *fdec;
    uint32_t td_get = 0, td_set = 0, td_inc = 0, td_dec = 0;
    int32_t off_get = 0, off_set = 0, off_inc = 0, off_dec = 0;
    unsigned char *cs;
    uint64_t inner = 0;

    if (!base || size == 0 || !out_cs || !out_this_deref || !out_field_off)
        return 0;

    /* Cache-Treffer nur, wenn Modulbasis/-groesse, Instanz-vftable UND die
     * vier Funktionskoerper unveraendert sind. Letzteres faengt einen
     * Layout-Hotpatch (gleiche Modulbasis, geaenderte Prologe) ab, ohne den
     * teuren vollen AOB-Scan zu wiederholen. */
    if (g_campdiff_cache.valid && g_campdiff_cache.module_base == base &&
        g_campdiff_cache.module_size == size && g_campdiff_cache.cs &&
        g_campdiff_cache.fget && g_campdiff_cache.fset &&
        g_campdiff_cache.finc && g_campdiff_cache.fdec &&
        memcmp(g_campdiff_cache.fget, RBBRIDGE_DIFF_GET_SIG,
               sizeof(RBBRIDGE_DIFF_GET_SIG)) == 0 &&
        memcmp(g_campdiff_cache.fset, RBBRIDGE_DIFF_SET_SIG,
               sizeof(RBBRIDGE_DIFF_SET_SIG)) == 0 &&
        memcmp(g_campdiff_cache.finc, RBBRIDGE_DIFF_INC_SIG,
               sizeof(RBBRIDGE_DIFF_INC_SIG)) == 0 &&
        memcmp(g_campdiff_cache.fdec, RBBRIDGE_DIFF_DEC_SIG,
               sizeof(RBBRIDGE_DIFF_DEC_SIG)) == 0 &&
        safe_read_u64(g_campdiff_cache.cs, &inner) &&
        inner == (uint64_t)(uintptr_t)(base +
                                       RBBRIDGE_RVA_CAMPAIGNSERVICE_VFTABLE)) {
        *out_cs = g_campdiff_cache.cs;
        *out_this_deref = g_campdiff_cache.this_deref;
        *out_field_off = g_campdiff_cache.field_off;
        return 1;
    }
    g_campdiff_cache.valid = 0;

    fget = scan_bytes(base, size, RBBRIDGE_DIFF_GET_SIG,
                      sizeof(RBBRIDGE_DIFF_GET_SIG));
    fset = scan_bytes(base, size, RBBRIDGE_DIFF_SET_SIG,
                      sizeof(RBBRIDGE_DIFF_SET_SIG));
    finc = scan_bytes(base, size, RBBRIDGE_DIFF_INC_SIG,
                      sizeof(RBBRIDGE_DIFF_INC_SIG));
    fdec = scan_bytes(base, size, RBBRIDGE_DIFF_DEC_SIG,
                      sizeof(RBBRIDGE_DIFF_DEC_SIG));
    if (!fget || !fset || !finc || !fdec)
        return 0;

    if (!diff_decode(fget, &td_get, &off_get) ||
        !diff_decode(fset, &td_set, &off_set) ||
        !diff_decode(finc, &td_inc, &off_inc) ||
        !diff_decode(fdec, &td_dec, &off_dec))
        return 0;

    /* Alle vier Funktionen muessen dasselbe Ziel-Feld adressieren. */
    if (td_get != td_set || td_get != td_inc || td_get != td_dec)
        return 0;
    if (off_get != off_set || off_get != off_inc || off_get != off_dec)
        return 0;

    cs = scan_qword_instance((uint64_t)(uintptr_t)(
        base + RBBRIDGE_RVA_CAMPAIGNSERVICE_VFTABLE));
    if (!cs)
        return 0;
    if (!safe_read_u64(cs + td_get, &inner) || !inner)
        return 0;

    g_campdiff_cache.valid = 1;
    g_campdiff_cache.module_base = base;
    g_campdiff_cache.module_size = size;
    g_campdiff_cache.cs = cs;
    g_campdiff_cache.this_deref = td_get;
    g_campdiff_cache.field_off = off_get;
    g_campdiff_cache.fget = fget;
    g_campdiff_cache.fset = fset;
    g_campdiff_cache.finc = finc;
    g_campdiff_cache.fdec = fdec;

    *out_cs = cs;
    *out_this_deref = td_get;
    *out_field_off = off_get;
    return 1;
}

/* Liest float nur aus committed+lesbarer Region (kein Crash auf Fremdspeicher). */
static int safe_read_f32(const void *addr, float *out)
{
    MEMORY_BASIC_INFORMATION mi;
    if (!addr || !out)
        return 0;
    if (!VirtualQuery(addr, &mi, sizeof(mi)))
        return 0;
    if (!is_readable_region(&mi))
        return 0;
    if ((uintptr_t)addr + sizeof(float) >
        (uintptr_t)mi.BaseAddress + mi.RegionSize)
        return 0;
    memcpy(out, addr, sizeof(*out));
    return 1;
}

/* IEEE-Endlichkeitspruefung ohne libm/math.h (mingw exponiert `isfinite`
 * nicht unter der Default-std). NaN: v-v != 0; +/-Inf: v-v = NaN != 0. */
static int float_is_finite(float v)
{
    volatile float d = v - v;
    return d == 0.0f;
}

/* Schreibt float nur in committed+lesbare Region (Seite kurz RW schalten).
 * PAGE_READWRITE genuegt fuer einen reinen Datenschreibzugriff - PAGE_EXECUTE
 * waere unnoetig und wuerde die Angriffsflaeche fuer Anti-Cheat/AV vergroessern
 * (Review-Hinweis PR #433). Der alte Schutz wird in einer EIGENEN Variablen
 * festgehalten (nicht als Out-Param wiederverwendet). */
static int safe_write_f32(void *addr, float v)
{
    MEMORY_BASIC_INFORMATION mi;
    DWORD old_protect = 0;
    DWORD ignored = 0;
    if (!addr)
        return 0;
    if (!VirtualQuery(addr, &mi, sizeof(mi)))
        return 0;
    if (!is_readable_region(&mi))
        return 0;
    if ((uintptr_t)addr + sizeof(float) >
        (uintptr_t)mi.BaseAddress + mi.RegionSize)
        return 0;
    if (!VirtualProtect(addr, sizeof(float), PAGE_READWRITE, &old_protect))
        return 0;
    memcpy(addr, &v, sizeof(v));
    /* Restore mit separater Out-Variable (der alte Schutz bleibt erhalten). */
    if (!VirtualProtect(addr, sizeof(float), old_protect, &ignored))
        return 0;
    return 1;
}

/* Read (#388): creatures_base_difficulty ueber den AOB-aufgeloesten Pfad.
 * 1 = ok. Es wird NUR gelesen (kein Aufruf in Spielcode). */
static RBBRIDGE_NOINLINE int creatures_difficulty_read(
    const unsigned char *base, size_t size, float *out)
{
    unsigned char *cs;
    uint32_t this_deref = 0;
    int32_t field_off = 0;
    uint64_t inner = 0;

    if (!out)
        return 0;
    if (!resolve_campaign_diff(base, size, &cs, &this_deref, &field_off))
        return 0;
    if (!safe_read_u64(cs + this_deref, &inner) || !inner)
        return 0;
    return safe_read_f32((const unsigned char *)(uintptr_t)inner + field_off,
                         out);
}

/*
 * creatures_difficulty (Write/Read #388): op = set|increase|decrease,
 * value = float. Semantik identisch zu den RE-ten Methoden (Set/Increase/
 * Decrease schreiben alle dasselbe float-Feld), aber als geführter
 * Speicher-Zugriff - KEIN indirekter Aufruf in Spielcode aus dem
 * Pipe-Thread (live #388: der Aufruf mit scan-abgeleitetem `this` hat den
 * Pipe-Thread gefaultet). Events:
 *   {"event":"creatures_difficulty_result","ok":true,"op":"set",
 *    "value":2.5,"before":1.0,"after":2.5}
 *   {"event":"creatures_difficulty_result","ok":false,"reason":"..."}
 */
static RBBRIDGE_NOINLINE void dispatch_creatures_difficulty(HANDLE hPipe,
                                                            const char *op,
                                                            double value)
{
    const unsigned char *base = NULL;
    size_t size = 0;
    const char *via = NULL;
    const unsigned char *execfn = NULL;
    unsigned char *cs;
    uint32_t this_deref = 0;
    int32_t field_off = 0;
    uint64_t inner = 0;
    float before = 0.0f, after = 0.0f, target = (float)value;
    int have_before;

    if (!op || !op[0]) {
        send_line(hPipe, "{\"event\":\"creatures_difficulty_result\","
                         "\"ok\":false,\"reason\":\"missing_op\"}");
        return;
    }
    if (strcmp(op, "set") != 0 && strcmp(op, "increase") != 0 &&
        strcmp(op, "decrease") != 0) {
        send_line(hPipe, "{\"event\":\"creatures_difficulty_result\","
                         "\"ok\":false,\"reason\":\"unknown_op\"}");
        return;
    }

    /* #479: Readiness-Gate vor dem Game-Call (Welt muss fertig sein). */
    if (readiness_block(hPipe, "creatures_difficulty_result"))
        return;

    if (!resolve_module(&base, &size, &via, &execfn)) {
        send_line(hPipe, "{\"event\":\"creatures_difficulty_result\","
                         "\"ok\":false,\"reason\":\"no_module\"}");
        return;
    }

    if (!resolve_campaign_diff(base, size, &cs, &this_deref, &field_off)) {
        dbg("creatures_difficulty: Pfad nicht auflösbar (AOB/Instanz)");
        send_line(hPipe, "{\"event\":\"creatures_difficulty_result\","
                         "\"ok\":false,\"reason\":\"no_campaignservice\"}");
        return;
    }
    if (!safe_read_u64(cs + this_deref, &inner) || !inner) {
        send_line(hPipe, "{\"event\":\"creatures_difficulty_result\","
                         "\"ok\":false,\"reason\":\"no_target\"}");
        return;
    }

    have_before = safe_read_f32(
        (const unsigned char *)(uintptr_t)inner + field_off, &before);

    if (strcmp(op, "increase") == 0) {
        if (!have_before) {
            send_line(hPipe, "{\"event\":\"creatures_difficulty_result\","
                             "\"ok\":false,\"reason\":\"no_read\"}");
            return;
        }
        target = before + (float)value;
    } else if (strcmp(op, "decrease") == 0) {
        if (!have_before) {
            send_line(hPipe, "{\"event\":\"creatures_difficulty_result\","
                             "\"ok\":false,\"reason\":\"no_read\"}");
            return;
        }
        target = before - (float)value;
    }

    if (!safe_write_f32(
            (unsigned char *)(uintptr_t)inner + field_off, target)) {
        send_line(hPipe, "{\"event\":\"creatures_difficulty_result\","
                         "\"ok\":false,\"reason\":\"write_failed\"}");
        return;
    }

    if (!safe_read_f32((const unsigned char *)(uintptr_t)inner + field_off,
                       &after))
        after = target;

    dbg("creatures_difficulty: op='%s' value=%.4f cs=%p inner=%p off=0x%x "
        "before=%.4f after=%.4f",
        op, value, (void *)cs, (void *)(uintptr_t)inner,
        (unsigned)field_off, (double)(have_before ? before : 0.0f),
        (double)after);

    if (have_before) {
        send_line(hPipe,
                  "{\"event\":\"creatures_difficulty_result\",\"ok\":true,"
                  "\"op\":\"%s\",\"value\":%.4f,\"before\":%.4f,"
                  "\"after\":%.4f}",
                  op, value, (double)before, (double)after);
    } else {
        send_line(hPipe,
                  "{\"event\":\"creatures_difficulty_result\",\"ok\":true,"
                  "\"op\":\"%s\",\"value\":%.4f,\"after\":%.4f}",
                  op, value, (double)after);
    }
}

/*
 * deactivate_mission_flow (Write #389): beendet einen Mission-Flow direkt
 * ueber den C++-Workhorse (AOB-aufgeloest), KEIN Lua/Console:
 *   MissionService::DeactivateMissionFlow(UtfString const& name)
 * `name` = Flow-ID (aus activate_mission_flow) bzw. explizit uebergeben.
 * Events:
 *   {"event":"deactivate_mission_flow_result","ok":true,"flow":"<id>"}
 *   {"event":"deactivate_mission_flow_result","ok":false,"reason":"..."}
 * Graceful: fehlt Modul/Signatur/Instanz oder ist `flow` leer, wird NICHTS
 * aufgerufen (kein Crash).
 *
 * Thread-Modell (#378): reines C++ -> thread-agnostisch (Pipe-Thread), kein
 * lua_*. Der Effekt ist ueber das Read-Feld `mission_flow_active`
 * (MissionService::IsGraphActive) in get_state sichtbar (Full-Chain #394).
 */
static void dispatch_deactivate_mission_flow(HANDLE hPipe, const char *flow)
{
    const unsigned char *base = NULL;
    size_t size = 0;
    const char *via = NULL;
    const unsigned char *execfn = NULL;
    const unsigned char *fn = NULL;
    unsigned char *ms;
    unsigned char u_flow[40];
    char esc[192 * 2];

    typedef void (__fastcall * deactivate_fn)(void *self, const void *name);
    deactivate_fn deact;

    if (!flow || !flow[0]) {
        send_line(hPipe, "{\"event\":\"deactivate_mission_flow_result\","
                         "\"ok\":false,\"reason\":\"missing_flow\"}");
        return;
    }

    /* #479: Readiness-Gate vor dem Game-Call (Welt muss fertig sein). */
    if (readiness_block(hPipe, "deactivate_mission_flow_result"))
        return;

    if (!resolve_module(&base, &size, &via, &execfn)) {
        send_line(hPipe, "{\"event\":\"deactivate_mission_flow_result\","
                         "\"ok\":false,\"reason\":\"no_module\"}");
        return;
    }

    /* Funktion per AOB-Signatur + Maske im Modulabbild (kein festes RVA). */
    fn = scan_bytes_mask(base, size, RBBRIDGE_DEACTIVATE_SIG,
                         RBBRIDGE_DEACTIVATE_SIG_MASK,
                         sizeof(RBBRIDGE_DEACTIVATE_SIG));
    if (!fn) {
        dbg("deactivate_mission_flow: AOB-Signatur nicht gefunden");
        send_line(hPipe, "{\"event\":\"deactivate_mission_flow_result\","
                         "\"ok\":false,\"reason\":\"no_deactivate_signature\"}");
        return;
    }

    /* MissionService-Instanz per vftable-Scan (RVA 0x2E962A0). */
    ms = scan_qword_instance(
        (uint64_t)(uintptr_t)(base + RBBRIDGE_RVA_MISSIONSERVICE_VFTABLE));
    if (!ms) {
        send_line(hPipe, "{\"event\":\"deactivate_mission_flow_result\","
                         "\"ok\":false,\"reason\":\"no_missionservice\"}");
        return;
    }

    build_utfstring(base, flow, u_flow);
    deact = (deactivate_fn)(uintptr_t)fn;
    deact((void *)ms, (const void *)u_flow);
    destroy_utfstring(base, u_flow);

    json_escape_into(flow, esc, sizeof(esc));
    dbg("deactivate_mission_flow: flow='%s' fn_rva=%08lx ms=%p",
        flow, (unsigned long)(uintptr_t)(fn - base), (void *)ms);

    send_line(hPipe,
              "{\"event\":\"deactivate_mission_flow_result\",\"ok\":true,"
              "\"flow\":\"%s\"}",
              esc);
}

/* ------------------------------------------------------------------ */
/* #476: natural_waves (Read+Write) — DifficultyService-Schalter        */
/* ------------------------------------------------------------------ */

/* Byte lesen/schreiben, nur in committed+lesbarer Region (kein Crash auf
 * Fremdspeicher). Analog zu safe_read_u32/safe_write_f32. */
static int safe_read_u8(const void *addr, unsigned char *out)
{
    MEMORY_BASIC_INFORMATION mi;
    if (!addr || !out)
        return 0;
    if (!VirtualQuery(addr, &mi, sizeof(mi)))
        return 0;
    if (!is_readable_region(&mi))
        return 0;
    if ((uintptr_t)addr + 1 > (uintptr_t)mi.BaseAddress + mi.RegionSize)
        return 0;
    memcpy(out, addr, 1);
    return 1;
}

static int safe_write_u8(void *addr, unsigned char v)
{
    MEMORY_BASIC_INFORMATION mi;
    DWORD old_protect = 0;
    DWORD ignored = 0;
    if (!addr)
        return 0;
    if (!VirtualQuery(addr, &mi, sizeof(mi)))
        return 0;
    if (!is_readable_region(&mi))
        return 0;
    if ((uintptr_t)addr + 1 > (uintptr_t)mi.BaseAddress + mi.RegionSize)
        return 0;
    if (!VirtualProtect(addr, 1, PAGE_READWRITE, &old_protect))
        return 0;
    memcpy(addr, &v, 1);
    if (!VirtualProtect(addr, 1, old_protect, &ignored))
        return 0;
    return 1;
}

/* Findet eine Klasse-vftable per RTTI-Walk (MSVC RTTI). Identische Technik
 * wie resolve_console_vftable, aber mit uebergebenem Klassennamen. */
static const unsigned char *resolve_rtti_vftable(const unsigned char *base,
                                                 size_t size,
                                                 const char *rtti_name)
{
    const unsigned char *name;
    const unsigned char *td;
    const unsigned char *p;
    uint32_t td_rva;

    if (!base || !size || !rtti_name)
        return NULL;
    name = scan_bytes(base, size, (const unsigned char *)rtti_name,
                      strlen(rtti_name) + 1);
    if (!name)
        return NULL;
    td = name - 0x10; /* TypeDescriptor-Beginn */
    td_rva = (uint32_t)(uintptr_t)(td - base);

    p = base;
    for (;;) {
        const unsigned char *hit = scan_u32(p, (size_t)((base + size) - p),
                                             td_rva);
        if (!hit)
            break;
        const unsigned char *col = hit - 0xC;
        uint32_t col_rva = (uint32_t)(uintptr_t)(col - base);
        uint32_t sig = 0, pself = 0;
        memcpy(&sig, col, sizeof(sig));
        memcpy(&pself, col + 0x14, sizeof(pself));
        if (sig == 1 && pself == col_rva) {
            const unsigned char *ref = scan_u64(
                base, size, (uint64_t)(uintptr_t)(base + col_rva));
            if (!ref)
                return NULL;
            return ref + 8;
        }
        p = hit + 1;
    }
    return NULL;
}

typedef void *(__fastcall *diffsys_get_fn)(void *world);

/* Loest die Kette DifficultyService -> World -> System-Objekt auf. Alles
 * per RTTI/AOB; der Getter wird als reiner Lookup aufgerufen (native Read,
 * thread-agnostisch, KEIN lua_*, kein Lock — ANNAHME #478), Rueckgabe
 * 1 = auflösbar. */
static RBBRIDGE_NOINLINE int resolve_diffsys(const unsigned char *base,
                                             size_t size,
                                             void **out_inner)
{
    const unsigned char *vt;
    const unsigned char *fn;
    unsigned char *svc;
    uint64_t world = 0;
    void *inner;

    if (out_inner)
        *out_inner = NULL;
    if (!diffsys_sig_selfcheck())
        return 0; /* Signatur/Maske inkonsistent -> nicht scannen */
    vt = resolve_rtti_vftable(base, size, RBBRIDGE_DIFFSVC_RTTI);
    if (!vt)
        return 0;
    svc = scan_qword_instance((uint64_t)(uintptr_t)vt);
    if (!svc)
        return 0;
    if (!safe_read_u64(svc + 8, &world) || !world)
        return 0;
    fn = scan_bytes_mask(base, size, RBBRIDGE_DIFFSYS_GET_SIG,
                         RBBRIDGE_DIFFSYS_GET_SIG_MASK,
                         sizeof(RBBRIDGE_DIFFSYS_GET_SIG));
    if (!fn)
        return 0;
    inner = ((diffsys_get_fn)(uintptr_t)fn)((void *)(uintptr_t)world);
    if (!inner)
        return 0;
    if (out_inner)
        *out_inner = inner;
    return 1;
}

/* Liest den Wellen-Schalter-Zustand (graceful: 0 = nicht auflösbar). */
static int natural_waves_read(const unsigned char *base, size_t size,
                              int *out_disabled, int *out_infinite,
                              char *strength, size_t sn,
                              char *dname, size_t dn)
{
    void *inner = NULL;
    unsigned char b = 0;

    if (!resolve_diffsys(base, size, &inner) || !inner)
        return 0;
    if (out_disabled) {
        if (!safe_read_u8((unsigned char *)inner +
                          RBBRIDGE_DIFFSYS_OFF_WAVESDISABLED, &b))
            return 0;
        *out_disabled = b ? 1 : 0;
    }
    if (out_infinite) {
        if (!safe_read_u8((unsigned char *)inner +
                          RBBRIDGE_DIFFSYS_OFF_INFINITE, &b))
            return 0;
        *out_infinite = b ? 1 : 0;
    }
    if (strength && sn)
        utfstring_to_cstr((const unsigned char *)inner +
                          RBBRIDGE_DIFFSYS_OFF_WAVESTRENGTH, strength, sn);
    if (dname && dn)
        utfstring_to_cstr((const unsigned char *)inner +
                          RBBRIDGE_DIFFSYS_OFF_NAME, dname, dn);
    return 1;
}

/* Schreibt den Schalter, den AreWavesDisabled() liest (natives Flag).
 * 1 = geschrieben. */
static RBBRIDGE_NOINLINE int natural_waves_write(const unsigned char *base,
                                                 size_t size, int disabled)
{
    void *inner = NULL;
    if (!resolve_diffsys(base, size, &inner) || !inner)
        return 0;
    return safe_write_u8((unsigned char *)inner +
                         RBBRIDGE_DIFFSYS_OFF_WAVESDISABLED,
                         (unsigned char)(disabled ? 1 : 0));
}

/*
 * natural_waves: op = status|off|on. Native Reads/Writes ueber die
 * DifficultyService (KEIN Lua/Console); Thread-agnostisch (#378).
 * Events:
 *   {"event":"natural_waves_result","ok":true,"op":"status",
 *    "readback":"ok","waves_disabled":true,"wave_strength":"sandbox",
 *    "mission_infinite":true,"difficulty":"sandbox"}
 *   {"event":"natural_waves_result","ok":true,"op":"off",
 *    "written":true,"readback":"ok",...}
 *   {"event":"natural_waves_result","ok":true,"op":"off",
 *    "written":true,"readback":"failed"}  (Write ok, Readback-Fehler)
 *   {"event":"natural_waves_result","ok":false,
 *    "reason":"write_failed"|"not_resolvable"|"unknown_op"|"no_module"}
 * Graceful: fehlt Modul/RTTI/Getter/Instanz -> ok:false, es wird NICHTS
 * geschrieben. Write- und Read-Fehlerpfad getrennt (#478).
 */
static RBBRIDGE_NOINLINE void dispatch_natural_waves(HANDLE hPipe,
                                                     const char *op)
{
    const unsigned char *base = NULL;
    size_t size = 0;
    const char *via = NULL;
    const unsigned char *execfn = NULL;
    int o = natural_waves_op(op);
    int disabled = -1, infinite = -1, before = -1;
    int written = 0;
    char strength[64] = "";
    char dname[64] = "";
    char esc_s[128];
    char esc_d[128];

    if (o < 0) {
        send_line(hPipe, "{\"event\":\"natural_waves_result\","
                         "\"ok\":false,\"reason\":\"unknown_op\"}");
        return;
    }
    if (!resolve_module(&base, &size, &via, &execfn)) {
        send_line(hPipe, "{\"event\":\"natural_waves_result\","
                         "\"ok\":false,\"reason\":\"no_module\"}");
        return;
    }

    /* Vorher-Zustand lesen (fuer den Bericht; auch bei op=off/on). */
    natural_waves_read(base, size, &before, NULL, NULL, 0, NULL, 0);

    if (o == 1 || o == 2) { /* off / on */
        if (!natural_waves_write(base, size, o == 1)) {
            /* Write-Pfad getrennt vom Read-Pfad (#478): ein fehlgeschlagener
             * Write heisst, dass NICHTS geschrieben wurde. */
            dbg("natural_waves: Schalter nicht aufloesbar (RTTI/AOB/Instanz)");
            send_line(hPipe,
                      "{\"event\":\"natural_waves_result\","
                      "\"ok\":false,\"reason\":\"write_failed\"}");
            return;
        }
        written = 1;
    }

    if (!natural_waves_read(base, size, &disabled, &infinite,
                            strength, sizeof(strength),
                            dname, sizeof(dname))) {
        /* Der Write hat gegriffen, der Readback nicht: getrennt melden,
         * statt einen erfolgreichen Write als ok:false zu verkaufen (#478). */
        if (written) {
            send_line(hPipe,
                      "{\"event\":\"natural_waves_result\",\"ok\":true,"
                      "\"op\":\"%s\",\"written\":true,"
                      "\"readback\":\"failed\"}",
                      op && op[0] ? op : "status");
            return;
        }
        send_line(hPipe,
                  "{\"event\":\"natural_waves_result\","
                  "\"ok\":false,\"reason\":\"not_resolvable\"}");
        return;
    }

    json_escape_into(strength, esc_s, sizeof(esc_s));
    json_escape_into(dname, esc_d, sizeof(esc_d));
    dbg("natural_waves: op='%s' before=%d disabled=%d infinite=%d "
        "strength='%s' difficulty='%s'",
        op ? op : "status", before, disabled, infinite, strength, dname);

    if (written) {
        send_line(hPipe,
                  "{\"event\":\"natural_waves_result\",\"ok\":true,"
                  "\"op\":\"%s\",\"written\":true,\"readback\":\"ok\","
                  "\"waves_disabled\":%s,\"wave_strength\":\"%s\","
                  "\"mission_infinite\":%s,\"difficulty\":\"%s\"}",
                  op && op[0] ? op : "status",
                  disabled ? "true" : "false",
                  esc_s,
                  infinite ? "true" : "false",
                  esc_d);
        return;
    }
    send_line(hPipe,
              "{\"event\":\"natural_waves_result\",\"ok\":true,"
              "\"op\":\"status\",\"readback\":\"ok\","
              "\"waves_disabled\":%s,\"wave_strength\":\"%s\","
              "\"mission_infinite\":%s,\"difficulty\":\"%s\"}",
              disabled ? "true" : "false",
              esc_s,
              infinite ? "true" : "false",
              esc_d);
}

/* ------------------------------------------------------------------ */
/* ------------------------------------------------------------------ */
/* #520: pause_dom / resume_dom — nativ via LuaGraphNode::SetSuspended  */
/* Konstanten/Helper (fnv1a, AOB, Resolver) stehen AUSSERHALB des       */
/* Hosttest-Guards weiter oben; hier nur der Pipe-Dispatch.             */
/* ------------------------------------------------------------------ */


typedef void (__fastcall *lua_graphnode_set_suspended_fn)(void *self,
                                                          unsigned char suspended);

/*
 * pause_dom / resume_dom (Write #520): setzt LuaGraphNode::SetSuspended
 * des DOM-Nodes. Events:
 *   {"event":"pause_dom_result","ok":true,"paused":true,
 *    "readback":"ok","node":"0x..."}
 *   {"event":"resume_dom_result","ok":true,"paused":false,...}
 *   {"event":"..._result","ok":false,"reason":
 *    "no_module"|"signature_not_found"|"dom_node_not_found"}
 * Graceful: fehlt Modul/Signatur/Instanz -> ok:false, es wird NICHTS
 * geschrieben (kein Aufruf, kein Crash).
 */
static RBBRIDGE_NOINLINE void dispatch_pause_dom(HANDLE hPipe, int pause)
{
    const char *ev = pause ? "pause_dom_result" : "resume_dom_result";
    const unsigned char *base = NULL;
    size_t size = 0;
    const char *via = NULL;
    const unsigned char *execfn = NULL;
    const unsigned char *fn;
    void *inst;
    unsigned char rb = 0;
    int rb_ok;

    if (!resolve_module(&base, &size, &via, &execfn)) {
        dbg("pause_dom: Modul nicht aufloesbar");
        send_line(hPipe,
                  "{\"event\":\"%s\",\"ok\":false,\"reason\":\"no_module\"}",
                  ev);
        return;
    }
    fn = resolve_set_suspended_fn(base, size);
    if (!fn) {
        dbg("pause_dom: SetSuspended-Signatur fehlt/mehrdeutig");
        send_line(hPipe,
                  "{\"event\":\"%s\",\"ok\":false,"
                  "\"reason\":\"signature_not_found\"}",
                  ev);
        return;
    }
    inst = resolve_dom_node(base);
    if (!inst) {
        dbg("pause_dom: DOM-Node (dom_mananger) nicht gefunden");
        send_line(hPipe,
                  "{\"event\":\"%s\",\"ok\":false,"
                  "\"reason\":\"dom_node_not_found\"}",
                  ev);
        return;
    }

    ((lua_graphnode_set_suspended_fn)(uintptr_t)fn)(inst, (unsigned char)(pause ? 1 : 0));

    rb_ok = safe_read_u8((unsigned char *)inst +
                         RBBRIDGE_LUAGRAPHNODE_SUSPENDED_OFF, &rb);
    dbg("pause_dom: %s node=%p write=%d readback=%s(%d)",
        pause ? "pause" : "resume", inst, pause ? 1 : 0,
        rb_ok ? "ok" : "failed", (int)rb);
    send_line(hPipe,
              "{\"event\":\"%s\",\"ok\":true,\"paused\":%s,"
              "\"readback\":\"%s\",\"node\":\"0x%llx\"}",
              ev,
              rb_ok ? (rb ? "true" : "false") : (pause ? "true" : "false"),
              rb_ok ? "ok" : "failed",
              (unsigned long long)(uintptr_t)inst);
}

static void dispatch_get_state(HANDLE hPipe)
{
    const unsigned char *base = NULL;
    size_t size = 0;
    const char *via = NULL;
    const unsigned char *execfn = NULL;

    /* #479: Readiness-Gate zuerst — vor JEDEM Game-Call. */
    if (readiness_block(hPipe, "get_state_result"))
        return;

    if (!resolve_module(&base, &size, &via, &execfn)) {
        send_line(hPipe, "{\"event\":\"get_state_result\",\"ok\":false,"
                         "\"reason\":\"no_module\"}");
        return;
    }

    /* DOM-Suspend-Flag (Read #520): nativ aus dem dom_mananger-Node
     * (LuaGraphNode::+0xF1) — unabhaengig vom Spieler-Account. Nicht
     * aufloesbar -> null (graceful). */
    char dom_field[48];
    {
        void *dn = resolve_dom_node(base);
        unsigned char sb = 0;
        if (dn && safe_read_u8((unsigned char *)dn +
                               RBBRIDGE_LUAGRAPHNODE_SUSPENDED_OFF, &sb))
            snprintf(dom_field, sizeof(dom_field), "%s", sb ? "true" : "false");
        else
            snprintf(dom_field, sizeof(dom_field), "null");
    }

    /* Mission-Flow (Read #385): haengt NICHT am Spieler-Account, ist also
     * auch ohne geladene Welt lesbar (Flow-ID + IsGraphActive). */
    char flow_esc[192 * 2];
    int flow_active = mission_flow_active(base, g_last_flow);
    json_escape_into(g_last_flow, flow_esc, sizeof(flow_esc));

    /* Creatures-Base-Difficulty (Read #388): haengt an der
     * CampaignService-Instanz, NICHT am Spieler-Account - also auch ohne
     * geladene Welt lesbar. Nicht aufloesbar -> null (graceful).
     * NaN/Inf wuerden als `nan`/`inf` kein gueltiges JSON ergeben -> null
     * (Review-Hinweis PR #433). */
    char diff_field[48];
    {
        float cbd = 0.0f;
        if (creatures_difficulty_read(base, size, &cbd) &&
            float_is_finite(cbd))
            snprintf(diff_field, sizeof(diff_field), "%.4f", (double)cbd);
        else
            snprintf(diff_field, sizeof(diff_field), "null");
    }

    /* Mission-Flow-Payload (Read #386): zuletzt gebautes Exor::Database-
     * Objekt; spawn_point via Database::GetString (AOB-aufgeloest),
     * Fallback = zuletzt gesetzter Wert. Nie ein Crash; kein Payload und
     * kein gemerkter Wert -> null. */
    char payload_field[320];
    {
        char sp[128] = "";
        int sp_ok = 0;
        if (g_mission_payload_db) {
            const void *gs = resolve_db_getstring_fn(base, size);
            if (gs)
                sp_ok = database_get_string(base, g_mission_payload_db, gs,
                                            "spawn_point", sp, sizeof(sp));
        }
        if (!sp_ok)
            copy_cstr(sp, sizeof(sp), g_mission_spawn);
        if (!g_mission_payload_db && !sp[0]) {
            snprintf(payload_field, sizeof(payload_field), "null");
        } else {
            char esp[128 * 2];
            json_escape_into(sp, esp, sizeof(esp));
            snprintf(payload_field, sizeof(payload_field),
                     "{\"spawn_point\":\"%s\"}", esp);
        }
    }

    /* PlayerService-vftable RVA 0x2e8e910 (RE #363/#365). */
    const unsigned char *vftable = base + 0x2e8e910;
    const uint64_t needle = (uint64_t)(uintptr_t)vftable;
    unsigned char *ps = NULL;
    uintptr_t addr = 0;

    for (;;) {
        MEMORY_BASIC_INFORMATION mi;
        if (VirtualQuery((const void *)addr, &mi, sizeof(mi)) == 0)
            break;
        uintptr_t next = (uintptr_t)mi.BaseAddress + mi.RegionSize;
        if (next <= addr)
            break;
        addr = next;
        if (!is_readable_region(&mi))
            continue;
        const uint64_t *q = (const uint64_t *)mi.BaseAddress;
        size_t nq = mi.RegionSize / sizeof(uint64_t);
        for (size_t i = 0; i < nq; i++) {
            if (q[i] == needle) {
                ps = (unsigned char *)&q[i];
                break;
            }
        }
        if (ps)
            break;
    }

    if (!ps) {
        send_line(hPipe, "{\"event\":\"get_state_result\",\"ok\":false,"
                         "\"reason\":\"no_playerservice\","
                         "\"dom_paused\":%s,"
                         "\"mission_flow\":\"%s\","
                         "\"mission_flow_active\":%s,"
                         "\"mission_flow_payload\":%s,"
                         "\"creatures_base_difficulty\":%s}",
                  dom_field, flow_esc, flow_active ? "true" : "false",
                  payload_field, diff_field);
        return;
    }

    uint64_t world = 0;
    safe_read_u64(ps + 8, &world);
    if (!world) {
        send_line(hPipe, "{\"event\":\"get_state_result\",\"ok\":false,"
                         "\"reason\":\"no_world\","
                         "\"dom_paused\":%s,"
                         "\"mission_flow\":\"%s\","
                         "\"mission_flow_active\":%s,"
                         "\"mission_flow_payload\":%s,"
                         "\"creatures_base_difficulty\":%s}",
                  dom_field, flow_esc, flow_active ? "true" : "false",
                  payload_field, diff_field);
        return;
    }

    void *(*gpa)(void *, unsigned int) =
        (void *(*)(void *, unsigned int))(uintptr_t)(base + 0xC60050);
    void *account = gpa((void *)(uintptr_t)world, 0);
    if (!account) {
        send_line(hPipe, "{\"event\":\"get_state_result\",\"ok\":false,"
                         "\"reason\":\"no_account\","
                         "\"dom_paused\":%s,"
                         "\"mission_flow\":\"%s\","
                         "\"mission_flow_active\":%s,"
                         "\"mission_flow_payload\":%s,"
                         "\"creatures_base_difficulty\":%s}",
                  dom_field, flow_esc, flow_active ? "true" : "false",
                  payload_field, diff_field);
        return;
    }

    uint64_t arr = 0, count = 0;
    safe_read_u64((unsigned char *)account + 8, &arr);
    safe_read_u64((unsigned char *)account + 0x10, &count);

    /* Basket-Werte per reiner Lookup-Logik (host-getestet: basket_lookup_value).
     * Nicht gefunden -> 0, identisch zum bisherigen carbonium-Verhalten
     * (graceful, kein Crash). Ironium = interne Ressource "steel". */
    uint64_t carbonium = 0;
    uint64_t ironium = 0;
    basket_lookup_value((const unsigned char *)(uintptr_t)arr, count,
                        RBBRIDGE_HASH_CARBONIUM, &carbonium);
    basket_lookup_value((const unsigned char *)(uintptr_t)arr, count,
                        RBBRIDGE_HASH_IRONIUM, &ironium);

    char resources[RESP_BUF_SIZE];
    size_t roff = 0;
    int nres = 0;
    int w = snprintf(resources + roff, sizeof(resources) - roff, "[");
    if (w > 0)
        roff += (size_t)w;

    if (arr && count && count < 256) {
        for (uint64_t i = 0; i < count; i++) {
            const unsigned char *e = (const unsigned char *)(uintptr_t)arr +
                                     i * 16;
            uint64_t hv = 0, v = 0;
            safe_read_u64(e, &hv);
            safe_read_u64(e + 8, &v);
            uint32_t h = (uint32_t)hv;
            if (roff + 64 < sizeof(resources)) {
                w = snprintf(resources + roff, sizeof(resources) - roff,
                             "%s{\"hash\":\"0x%08x\",\"value\":%llu}",
                             nres ? "," : "", h, (unsigned long long)v);
                if (w > 0)
                    roff += (size_t)w;
                nres++;
            }
        }
    }
    snprintf(resources + roff, sizeof(resources) - roff, "]");

    int64_t carbonium_max = read_resource_max(base, account,
                                              RBBRIDGE_HASH_CARBONIUM);
    int64_t ironium_max = read_resource_max(base, account,
                                            RBBRIDGE_HASH_IRONIUM);

    send_line(hPipe,
              "{\"event\":\"get_state_result\",\"ok\":true,"
              "\"carbonium\":%llu,\"carbonium_max\":%lld,"
              "\"ironium\":%llu,\"ironium_max\":%lld,\"resources\":%s,"
              "\"dom_paused\":%s,"
              "\"mission_flow\":\"%s\",\"mission_flow_active\":%s,"
              "\"mission_flow_payload\":%s,"
              "\"creatures_base_difficulty\":%s}",
              (unsigned long long)carbonium, (long long)carbonium_max,
              (unsigned long long)ironium, (long long)ironium_max,
              resources, dom_field, flow_esc, flow_active ? "true" : "false",
              payload_field, diff_field);
}


/*
 * Verteilt eine empfangene Protokollzeile (ohne
).
 * Unbekanntes/Nicht-JSON wird geloggt und (nur bei JSON-artigen Zeilen)
 * mit einem error-Event beantwortet - der Client soll Feedback bekommen,
 * das Spiel darf sich nie daran stoeren.
 */
/*
 * add_resource (Write-PoC, Issue #373; Ressourcen-Auswahl Issue #421):
 * aendert eine Ressource DIREKT ueber
 * PlayerService::AddResourceAmount(unsigned int, UtfString const&, float,
 * bool) - RVA 0xF1E3D0 (Build 2.0.58485) - der C++-Write-Pfad OHNE
 * Lua/Console (ein Hop weniger als exec -> ConsoleService -> Lua -> C++).
 * Der Anzeigename (z. B. "ironium") wird per resource_internal_name() auf den
 * internen Namen ("steel") gemappt; Default carbonium.
 *
 * RE-Befunde (tools/re/disasm.py + llvm-pdbutil, siehe
 * docs/research/io-write-poc.md):
 *   B1  AddResourceAmount = RVA 0xF1E3D0 (Signatur/Disasm bestaetigt).
 *   B2  Negativ erlaubt: entry.value += amount; negatives Ergebnis wird
 *       auf 0 geklemmt (Disasm 0x1802d6890).
 *   B3  Einheiten: der float wird intern skaliert
 *         basket_delta = (int64)( (float)scale * amount )
 *       scale = .data-global bei RVA 0x4794210 (Laufzeitwert wird gelesen,
 *       statischer Initialwert 0x03bde828). Basket = int64-Fixed-Point
 *       x10^6 (READ-bewiesen). => raw = N * 1e6 ; amount = raw / scale.
 *   B4  UtfString-Layout: data@+8 (SSO wenn capacity@+0x20 <= 15),
 *       size@+0x18, capacity@+0x20; Offset 0 wird NICHT gelesen. Wir bauen
 *       eine SSO-Instanz des internen Ressourcennamens auf dem Stack.
 *   B5  playerId = 0 (Spieler 1); das bool (Stack-Arg) wird an den
 *       Broadcast/HUD-Sync durchgereicht (true = sichtbar machen).
 *   B6  Thread-Safety: Aufruf im Pipe-Thread (wie ExecuteCommand); offen,
 *       Live-Test steht noch aus (gleiche Risiko-Klasse wie exec).
 *
 * Aufrufkonvention (MS x64): this=RCX, playerId=RDX, name=R8, amount=XMM3,
 * flag=[rsp+0x28]. MinGW-x64 (ms_abi Default) deckt die Deklaration ab.
 */
static void dispatch_add_resource(HANDLE hPipe, const char *resource_str,
                                  const char *amount_str)
{
    const unsigned char *base = NULL;
    size_t size = 0;
    const char *via = NULL;
    const unsigned char *execfn = NULL;

    if (!resolve_module(&base, &size, &via, &execfn)) {
        send_line(hPipe, "{\"event\":\"add_resource_result\",\"ok\":false,"
                         "\"reason\":\"no_module\"}");
        return;
    }

    float amount = 0.0f;
    if (sscanf(amount_str, "%f", &amount) != 1) {
        send_line(hPipe, "{\"event\":\"add_resource_result\",\"ok\":false,"
                         "\"reason\":\"bad_amount\"}");
        return;
    }

    /* #479: Readiness-Gate — erst nach der Argument-Validierung, aber vor
     * jedem Game-Zugriff (vtable-Scan/Call). */
    if (readiness_block(hPipe, "add_resource_result"))
        return;

    /* PlayerService-Instanz per vftable-Scan (RVA 0x2e8e910, wie get_state). */
    const unsigned char *vftable = base + 0x2e8e910;
    const uint64_t needle = (uint64_t)(uintptr_t)vftable;
    unsigned char *ps = NULL;
    uintptr_t addr = 0;
    for (;;) {
        MEMORY_BASIC_INFORMATION mi;
        if (VirtualQuery((const void *)addr, &mi, sizeof(mi)) == 0)
            break;
        uintptr_t next = (uintptr_t)mi.BaseAddress + mi.RegionSize;
        if (next <= addr)
            break;
        addr = next;
        if (!is_readable_region(&mi))
            continue;
        const uint64_t *q = (const uint64_t *)mi.BaseAddress;
        size_t nq = mi.RegionSize / sizeof(uint64_t);
        for (size_t i = 0; i < nq; i++) {
            if (q[i] == needle) {
                ps = (unsigned char *)&q[i];
                break;
            }
        }
        if (ps)
            break;
    }

    if (!ps) {
        send_line(hPipe, "{\"event\":\"add_resource_result\",\"ok\":false,"
                         "\"reason\":\"no_playerservice\"}");
        return;
    }

    /* scale-Globale (RVA 0x4794210) lesen; 0 -> Fallback 1e6. */
    uint64_t scale64 = 0;
    safe_read_u64(base + 0x4794210, &scale64);
    uint32_t scale = (uint32_t)scale64;
    if (scale == 0)
        scale = 1000000u;

    /* Betrag -> int64-Fixed-Point x10^6 -> float fuer den Call. */
    double raw_dbl = (double)amount * 1000000.0;
    int64_t raw = (int64_t)raw_dbl;
    float amount_float = (float)raw / (float)scale;

    /* Interner Ressourcenname (Anzeigename -> intern, z. B. ironium->steel;
     * Default carbonium). UtfString als SSO-Instanz auf dem Stack (B4):
     * 40 Byte, [0]=unused, [8]=inline-buffer(16B), [0x18]=size,
     * [0x20]=capacity. SSO fasst <= 15 Zeichen; beide Namen passen. */
    const char *internal = resource_internal_name(resource_str);
    size_t nlen = strlen(internal);
    unsigned char name[40];
    memset(name, 0, sizeof(name));
    memcpy(name + 8, internal, nlen + 1); /* + NUL */
    {
        uint64_t sz = nlen, cap = 0xf; /* cap <= 15 -> SSO, data = name + 8 */
        memcpy(name + 0x18, &sz, sizeof(sz));
        memcpy(name + 0x20, &cap, sizeof(cap));
    }

    /* bool PlayerService::AddResourceAmount(this, playerId, name, amount,
     * flag) */
    typedef unsigned char (__fastcall *add_resource_fn)(void *, unsigned int,
                                                        const void *, float,
                                                        unsigned char);
    add_resource_fn fn = (add_resource_fn)(uintptr_t)(base + 0xF1E3D0);

    unsigned char ret = fn(ps, 0, (const void *)name, amount_float, 1);

    dbg("add_resource: resource='%s' internal='%s' amount='%s' raw=%lld "
        "scale=%u amount_float=%.6f ret=%u",
        resource_str ? resource_str : "", internal, amount_str, (long long)raw,
        (unsigned)scale, (double)amount_float, (unsigned)ret);

    send_line(hPipe,
              "{\"event\":\"add_resource_result\",\"ok\":true,"
              "\"amount\":\"%s\",\"raw\":%lld,\"scale\":%u,\"ret\":%s}",
              amount_str, (long long)raw, (unsigned)scale,
              ret ? "true" : "false");
}

static void handle_line(HANDLE hPipe, const char *line)
{
    char cmd[64] = "";

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

    if (strcmp(cmd, "probe") == 0) {
        probe_resources(hPipe);
        return;
    }

    if (strcmp(cmd, "get_state") == 0) {
        dispatch_get_state(hPipe);
        return;
    }

    if (strcmp(cmd, "add_resource") == 0) {
        char resource[64] = "";
        char amount[64] = "";
        if (!json_get_string(line, "amount", amount, sizeof(amount))) {
            send_line(hPipe,
                      "{\"event\":\"error\",\"error\":\"add_resource_ohne_amount\"}");
            return;
        }
        /* resource ist optional (Default carbonium, Backward-Compat). */
        json_get_string(line, "resource", resource, sizeof(resource));
        dispatch_add_resource(hPipe, resource, amount);
        return;
    }

    /* activate_mission_flow (Write #385): startet einen Mission-Flow (Welle)
     * direkt per C++ (kein Lua/Console). `logic` = Logic-File-Name (z. B.
     * "logic/dom/attack_level_1_entry.logic"), `mode` optional (Default
     * "default"); andere Modi werden abgelehnt (#447). */
    if (strcmp(cmd, "activate_mission_flow") == 0) {
        char logic[256] = "";
        char mode[64] = "default";
        char spawn[128] = "";
        if (!json_get_string(line, "logic", logic, sizeof(logic)) ||
            !logic[0]) {
            send_line(hPipe, "{\"event\":\"activate_mission_flow_result\","
                             "\"ok\":false,\"reason\":\"missing_logic\"}");
            return;
        }
        json_get_string(line, "mode", mode, sizeof(mode));
        /* #386: optionaler spawn_point -> Database-Payload. */
        json_get_string(line, "spawn_point", spawn, sizeof(spawn));
        dispatch_activate_mission_flow(hPipe, logic, mode, spawn);
        return;
    }

    /* creatures_difficulty (Read/Write #388): CampaignService-Kreaturen-
     * Basis-Difficulty per C++-Primitiv. `op` = set|increase|decrease,
     * `value` = float (als String, wie bei add_resource/amount). */
    if (strcmp(cmd, "creatures_difficulty") == 0) {
        char op[32] = "";
        char value[64] = "";
        if (!json_get_string(line, "op", op, sizeof(op)) || !op[0]) {
            send_line(hPipe, "{\"event\":\"creatures_difficulty_result\","
                             "\"ok\":false,\"reason\":\"missing_op\"}");
            return;
        }
        if (!json_get_string(line, "value", value, sizeof(value)) ||
            !value[0]) {
            send_line(hPipe, "{\"event\":\"creatures_difficulty_result\","
                             "\"ok\":false,\"reason\":\"missing_value\"}");
            return;
        }
        dispatch_creatures_difficulty(hPipe, op, strtod(value, NULL));
        return;
    }

    /* natural_waves (Read/Write #476): steuert den Vanilla-Naturwellen-
     * Schalter (DifficultyService::AreWavesDisabled, natives C++-Flag).
     * `op` = status|off|on (Default status). */
    if (strcmp(cmd, "natural_waves") == 0) {
        char op[32] = "status";
        json_get_string(line, "op", op, sizeof(op));
        dispatch_natural_waves(hPipe, op);
        return;
    }

    /* deactivate_mission_flow (Write #389): beendet einen Mission-Flow (Welle)
     * direkt per C++ (kein Lua/Console). `flow` optional; Default = zuletzt per
     * activate_mission_flow gestarteter Flow (g_last_flow). */
    if (strcmp(cmd, "deactivate_mission_flow") == 0) {
        char flow[192] = "";
        if (!json_get_string(line, "flow", flow, sizeof(flow)) || !flow[0]) {
            snprintf(flow, sizeof(flow), "%s", g_last_flow);
        }
        dispatch_deactivate_mission_flow(hPipe, flow);
        return;
    }

    /* pause_dom / resume_dom (Write #520): natives LuaGraphNode::
     * SetSuspended des DOM-Nodes (kein Lua-DOM, kein Console). */
    if (strcmp(cmd, "pause_dom") == 0) {
        dispatch_pause_dom(hPipe, 1);
        return;
    }

    if (strcmp(cmd, "resume_dom") == 0) {
        dispatch_pause_dom(hPipe, 0);
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
