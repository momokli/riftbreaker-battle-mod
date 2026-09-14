/*
 * rbbridge_hosttest.c - deterministischer HOST-Test fuer die reinen
 * Scan-/RTTI-Funktionen aus rbbridge.c (Issue #243 / PR #251).
 *
 * Kein Windows, kein Spielprozess, kein Netz: rbbridge.c wird mit
 * -DRBBRIDGE_HOSTTEST als Teil DIESER Uebersetzungseinheit compiliert und
 * laeuft gegen einen SYNTHETISCHEN, PE-artigen Puffer. Geprueft werden:
 *   - scan_bytes / scan_bytes_mask (Byte-Suche, Wildcard-Maske)
 *   - resolve_console_vftable (RTTI-Walk: RTTI-Name -> TD -> COL -> vftable)
 *   - resolve_console_service (Signatur + vftable + Instanz + Cache)
 *   - Stufe (d) der Modul-Resolution POSITIV (Loader-Sicht aus, Region
 *     MEM_IMAGE, (b)/(c)/(e) inert -> via=sigbase) + Negativfaelle
 *     (eigenes Image / Signatur ohne vftable -> verworfen) (Review B1)
 *   - Cache-Re-Validierung ohne GetModuleHandleA (Wine-Fall, Review R1)
 *   - pe_image_size defensiv (e_lfanew-Schranke, SizeOfImage > 0, R2)
 *   - Fehlerpfade: Modul fehlt / RTTI-Name fehlt / COL invalide /
 *     Signatur fehlt / Instanz fehlt -> Resolver liefert 0/NULL (kein Crash).
 *
 * Build (Host-Compiler, NICHT mingw!):
 *   cc -O1 -g -Wall -Wextra -I <.../rbbridge> -o rbbridge_hosttest \
 *      rbbridge_hosttest.c
 * Rueckgabe: 0 = alle Checks gruen, 1 = mindestens ein FAIL.
 */

#define RBBRIDGE_HOSTTEST 1
#include "rbbridge.c"

#include <stdlib.h>

static int g_pass = 0;
static int g_fail = 0;

static void check(int cond, const char *msg)
{
    if (cond) {
        g_pass++;
        printf("PASS: %s\n", msg);
    } else {
        g_fail++;
        printf("FAIL: %s\n", msg);
    }
}

/* ------------------------------------------------------------------ */
/* Synthetisches PE-artiges Image                                      */
/* ------------------------------------------------------------------ */

#define IMG_SIZE    0x3000
#define NT_OFF      0x80
#define TEXT_RVA    0x1000
#define TEXT_VSIZE  0x800

#define SIG_OFF     0x1100 /* ExecuteCommand-Signatur in .text        */
#define ACT_SIG_OFF 0x1300 /* ActivateMissionFlow-Signatur in .text   */
#define NAME_OFF    0x1400 /* RTTI-Namensstring                       */
#define COL_OFF     0x1500 /* CompleteObjectLocator                   */
#define VFT_REF_OFF 0x15F8 /* QWORD == base+COL_OFF (vftable-8)       */
#define VFT_OFF     0x1600 /* vftable-Start (= VFT_REF_OFF + 8)       */
#define INST_OFF    0x1700 /* QWORD == base+VFT_OFF (Instanz)         */
#define PAT_OFF     0x1800 /* freies Byte-Muster fuer scan_bytes      */

/* #386: Mission-Flow-Payload (eigene Offsets, kollisionsfrei zu #243). */
#define MNAME_OFF   0x1480 /* RTTI-Name MissionService                */
#define AMF_OFF     0x1200 /* ActivateMissionFlow(...,Database*)-Sig  */
#define CTOR_OFF    0x1300 /* Database::Database()-Sig                */
#define SETSTR_OFF  0x1340 /* Database::SetString-Sig                */
#define GETSTR_OFF  0x1380 /* Database::GetString-Sig                */
#define GETKEYS_OFF 0x13C0 /* Database::GetStringKeys-Sig            */

static void wr32(unsigned char *p, uint32_t v) { memcpy(p, &v, 4); }
static void wr64(unsigned char *p, uint64_t v) { memcpy(p, &v, 8); }

/*
 * Baut ein synthetisches Modul-Image.
 *   with_sig          : ExecuteCommand-Signatur in .text ablegen
 *   with_rtti         : RTTI-Name + TD ablegen
 *   valid_col         : COL.signature == 1 (sonst absichtlich 0)
 *   with_vftable_ref  : QWORD (base+COL_OFF) bei VFT_REF_OFF + plausible
 *                       vftable (erster Slot zeigt auf img+SIG_OFF)
 *   with_instance     : QWORD (base+VFT_OFF) bei INST_OFF
 */
static unsigned char *build_image(int with_sig, int with_rtti, int valid_col,
                                  int with_vftable_ref, int with_instance)
{
    unsigned char *img = (unsigned char *)calloc(1, IMG_SIZE);
    if (!img) {
        fprintf(stderr, "FAIL: calloc(%d) fehlgeschlagen\n", IMG_SIZE);
        exit(2);
    }

    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)img;
    dos->e_magic = IMAGE_DOS_SIGNATURE;
    dos->e_lfanew = NT_OFF;

    IMAGE_NT_HEADERS *nt = (IMAGE_NT_HEADERS *)(img + NT_OFF);
    nt->Signature = IMAGE_NT_SIGNATURE;
    nt->FileHeader.Machine = IMAGE_FILE_MACHINE_AMD64; /* pe_image_size prueft x64 */
    nt->FileHeader.NumberOfSections = 1;
    nt->FileHeader.SizeOfOptionalHeader =
        (uint16_t)sizeof(IMAGE_OPTIONAL_HEADER);
    nt->OptionalHeader.SizeOfImage = IMG_SIZE;

    IMAGE_SECTION_HEADER *sec = IMAGE_FIRST_SECTION(nt);
    memcpy(sec->Name, ".text", 5);
    sec->Misc.VirtualSize = TEXT_VSIZE;
    sec->VirtualAddress = TEXT_RVA;

    if (with_sig) {
        memcpy(img + SIG_OFF, RBBRIDGE_EXEC_SIG, sizeof(RBBRIDGE_EXEC_SIG));
        memcpy(img + ACT_SIG_OFF, RBBRIDGE_ACTIVATE_SIG,
               sizeof(RBBRIDGE_ACTIVATE_SIG));
    }

    if (with_rtti) {
        memcpy(img + NAME_OFF, RBBRIDGE_RTTI_NAME, sizeof(RBBRIDGE_RTTI_NAME));
        uint32_t td_rva = (uint32_t)(NAME_OFF - 0x10);

        if (with_vftable_ref) {
            wr32(img + COL_OFF, valid_col ? 1u : 0u); /* COL.signature    */
            wr32(img + COL_OFF + 0xC, td_rva);        /* pTypeDescriptor  */
            wr32(img + COL_OFF + 0x14, COL_OFF);      /* COL.pSelf == RVA */
            wr64(img + VFT_REF_OFF,                   /* vftable-8        */
                 (uint64_t)(uintptr_t)(img + COL_OFF));
            wr64(img + VFT_OFF,                       /* plausibler Slot  */
                 (uint64_t)(uintptr_t)(img + SIG_OFF));
        }
    }

    if (with_instance)
        wr64(img + INST_OFF, (uint64_t)(uintptr_t)(img + VFT_OFF));

    return img;
}

/*
 * #386: synthetisches Image fuer den Mission-Flow-Payload-Pfad. Wie
 * build_image(), aber mit MissionService-RTTI + den AOB-Signaturen des
 * Database-Pfads im .text (siehe Offsets oben).
 */
static unsigned char *build_mission_image(int with_mission_rtti, int valid_col,
                                          int with_vftable_ref, int with_instance,
                                          int with_amf, int with_ctor,
                                          int with_setstr, int with_getstr)
{
    unsigned char *img = build_image(0, 0, 0, 0, 0); /* PE-Geruest ohne Console */
    if (!img)
        return NULL;

    if (with_amf)
        memcpy(img + AMF_OFF, RBBRIDGE_AMF_SIG, sizeof(RBBRIDGE_AMF_SIG));
    if (with_ctor)
        memcpy(img + CTOR_OFF, RBBRIDGE_DB_CTOR_SIG,
               sizeof(RBBRIDGE_DB_CTOR_SIG));
    if (with_setstr)
        memcpy(img + SETSTR_OFF, RBBRIDGE_DB_SETSTRING_SIG,
               sizeof(RBBRIDGE_DB_SETSTRING_SIG));
    if (with_getstr)
        memcpy(img + GETSTR_OFF, RBBRIDGE_DB_GETSTRING_SIG,
               sizeof(RBBRIDGE_DB_GETSTRING_SIG));

    if (with_mission_rtti) {
        memcpy(img + MNAME_OFF, RBBRIDGE_MISSION_RTTI_NAME,
               sizeof(RBBRIDGE_MISSION_RTTI_NAME));
        uint32_t td_rva = (uint32_t)(MNAME_OFF - 0x10);
        if (with_vftable_ref) {
            wr32(img + COL_OFF, valid_col ? 1u : 0u);
            wr32(img + COL_OFF + 0xC, td_rva);
            wr32(img + COL_OFF + 0x14, COL_OFF);
            wr64(img + VFT_REF_OFF, (uint64_t)(uintptr_t)(img + COL_OFF));
            wr64(img + VFT_OFF, (uint64_t)(uintptr_t)(img + AMF_OFF));
        }
    }
    if (with_instance)
        wr64(img + INST_OFF, (uint64_t)(uintptr_t)(img + VFT_OFF));

    return img;
}

/* Stub-Funktionen fuer den Database-Builder (Aufruf-Mitschnitt). */
static int g_stub_ctor_calls = 0;
static int g_stub_set_calls = 0;
static char g_stub_key[64];
static char g_stub_val[64];

static void stub_ctor(void *db)
{
    g_stub_ctor_calls++;
    (void)db;
}

static void stub_setstring(void *db, const void *k, const void *v)
{
    g_stub_set_calls++;
    (void)db;
    if (!utfstring_read(k, g_stub_key, sizeof(g_stub_key)))
        g_stub_key[0] = '\0';
    if (!utfstring_read(v, g_stub_val, sizeof(g_stub_val)))
        g_stub_val[0] = '\0';
}

int main(void)
{
    /* Byte-Muster fuer die reinen Scan-Tests (ausserhalb .text). */
    static const unsigned char PAT[] = { 0xDE, 0xAD, 0xBE, 0xEF };
    static const unsigned char PAT_WILD[]    = { 0xAA, 0xBB, 0xCC, 0xDD };
    static const unsigned char PAT_WILD_MSK[] = { 0xFF, 0x00, 0xFF, 0xFF };

    printf("== rbbridge host-test (synthetischer PE-Puffer) ==\n");

    /* -------------------------------------------------------------- */
    /* scan_bytes                                                      */
    /* -------------------------------------------------------------- */
    unsigned char *img = build_image(1, 1, 1, 1, 1);
    ht_set_module(img, IMG_SIZE);

    memcpy(img + PAT_OFF, PAT, sizeof(PAT));
    const unsigned char *hit = scan_bytes(img, IMG_SIZE, PAT, sizeof(PAT));
    check(hit == img + PAT_OFF, "scan_bytes findet platziertes Muster");

    static const unsigned char NOPE[] = { 0x11, 0x22, 0x33, 0x44, 0x55 };
    check(scan_bytes(img, IMG_SIZE, NOPE, sizeof(NOPE)) == NULL,
          "scan_bytes: nicht vorhandenes Muster -> NULL");
    check(scan_bytes(img, 2, PAT, sizeof(PAT)) == NULL,
          "scan_bytes: len < pat_len -> NULL");
    check(scan_bytes(img, IMG_SIZE, PAT, 0) == NULL,
          "scan_bytes: pat_len == 0 -> NULL");

    /* Maskierter Scan: Wildcard-Bytes (mask==0) duerfen abweichen. */
    memcpy(img + PAT_OFF + 0x10, PAT_WILD, sizeof(PAT_WILD));
    img[PAT_OFF + 0x10 + 1] = 0x7E; /* weicht an maskierter Position ab */
    check(scan_bytes_mask(img, IMG_SIZE, PAT_WILD, PAT_WILD_MSK,
                          sizeof(PAT_WILD)) == img + PAT_OFF + 0x10,
          "scan_bytes_mask: maskiertes Byte ist don't-care");
    check(scan_bytes(img, IMG_SIZE, PAT_WILD, sizeof(PAT_WILD)) == NULL,
          "scan_bytes (exakt): abweichendes Byte -> kein Treffer");

    /* Signatur mit abweichenden rel32-Bytes: maskiert Treffer, exakt nicht. */
    unsigned char *img2 = build_image(1, 1, 1, 1, 1);
    img2[SIG_OFF + 14] ^= 0xFF; /* rel32-Displacement 1 kaputt */
    img2[SIG_OFF + 22] ^= 0xFF; /* rel32-Displacement 2 kaputt */
    ht_set_module(img2, IMG_SIZE);
    check(scan_bytes_mask(img2 + TEXT_RVA, TEXT_VSIZE, RBBRIDGE_EXEC_SIG,
                          RBBRIDGE_EXEC_SIG_MASK,
                          sizeof(RBBRIDGE_EXEC_SIG)) == img2 + SIG_OFF,
          "Signatur+Maske: rel32-Abweichung toleriert");
    check(scan_bytes(img2 + TEXT_RVA, TEXT_VSIZE, RBBRIDGE_EXEC_SIG,
                     sizeof(RBBRIDGE_EXEC_SIG)) == NULL,
          "Signatur exakt: rel32-Abweichung -> kein Treffer");
    /* E8-Opcode bleibt Pflicht (maskiert nur die Operanden). */
    img2[SIG_OFF + 13] ^= 0xFF;
    check(scan_bytes_mask(img2 + TEXT_RVA, TEXT_VSIZE, RBBRIDGE_EXEC_SIG,
                          RBBRIDGE_EXEC_SIG_MASK,
                          sizeof(RBBRIDGE_EXEC_SIG)) == NULL,
          "Signatur+Maske: E8-Opcode bleibt Pflicht");
    free(img2);

    /* -------------------------------------------------------------- */
    /* ActivateMissionFlow-AOB (Issue #385)                            */
    /* -------------------------------------------------------------- */
    ht_set_module(img, IMG_SIZE);
    check(scan_bytes(img + TEXT_RVA, TEXT_VSIZE, RBBRIDGE_ACTIVATE_SIG,
                     sizeof(RBBRIDGE_ACTIVATE_SIG)) == img + ACT_SIG_OFF,
          "ActivateMissionFlow-AOB im .text gefunden (#385)");
    {
        unsigned char *img3 = build_image(1, 1, 1, 1, 1);
        img3[ACT_SIG_OFF + 5] ^= 0xFF; /* Prolog-Byte abweichend */
        ht_set_module(img3, IMG_SIZE);
        check(scan_bytes(img3 + TEXT_RVA, TEXT_VSIZE, RBBRIDGE_ACTIVATE_SIG,
                         sizeof(RBBRIDGE_ACTIVATE_SIG)) == NULL,
              "ActivateMissionFlow-AOB: abweichendes Byte -> kein Treffer");
        free(img3);
    }

    /* -------------------------------------------------------------- */
    /* resolve_console_vftable                                         */
    /* -------------------------------------------------------------- */
    ht_set_module(img, IMG_SIZE);
    check(resolve_console_vftable(img, IMG_SIZE) == img + VFT_OFF,
          "resolve_console_vftable: RTTI->TD->COL->vftable");

    unsigned char *no_rtti = build_image(1, 0, 1, 1, 1);
    ht_set_module(no_rtti, IMG_SIZE);
    check(resolve_console_vftable(no_rtti, IMG_SIZE) == NULL,
          "vftable: RTTI-Name fehlt -> NULL");
    free(no_rtti);

    unsigned char *bad_col = build_image(1, 1, 0, 1, 1);
    ht_set_module(bad_col, IMG_SIZE);
    check(resolve_console_vftable(bad_col, IMG_SIZE) == NULL,
          "vftable: COL.signature != 1 -> NULL");
    free(bad_col);

    /* -------------------------------------------------------------- */
    /* resolve_console_service (Erfolg + Cache)                        */
    /* -------------------------------------------------------------- */
    ht_set_module(img, IMG_SIZE);
    g_console_cache.valid = 0;
    console_exec_fn fn = NULL;
    void *inst = NULL;
    check(resolve_console_service(&fn, &inst) == 1,
          "resolve_console_service: Erfolg (Signatur+vftable+Instanz)");
    check((const unsigned char *)(uintptr_t)fn == img + SIG_OFF,
          "resolve_console_service: fn == Signatur-Fundstelle");
    check(inst == (void *)(img + INST_OFF),
          "resolve_console_service: instance == vftable-Referenz");
    check(g_console_cache.valid == 1 &&
          g_console_cache.instance == inst &&
          g_console_cache.vftable == img + VFT_OFF,
          "resolve_console_service: Cache gefuellt");

    fn = NULL; inst = NULL;
    check(resolve_console_service(&fn, &inst) == 1 &&
          (const unsigned char *)(uintptr_t)fn == img + SIG_OFF,
          "resolve_console_service: Cache-Treffer (2. Aufruf)");

    /* Cache ungueltig machen: instance zeigt nicht mehr auf die vftable. */
    wr64(img + INST_OFF, (uint64_t)(uintptr_t)(img + COL_OFF));
    fn = NULL; inst = NULL;
    check(resolve_console_service(&fn, &inst) == 0,
          "resolve_console_service: Cache-Revalidierung greift "
          "(Instanz kaputt -> Re-Scan -> kein Fund)");
    check(g_console_cache.valid == 0,
          "resolve_console_service: Cache nach Fehlschlag ungueltig");
    wr64(img + INST_OFF, (uint64_t)(uintptr_t)(img + VFT_OFF)); /* heilen */
    check(resolve_console_service(&fn, &inst) == 1,
          "resolve_console_service: nach Heilung wieder Erfolg");

    /* -------------------------------------------------------------- */
    /* Fehlerpfade (deterministisch, ohne Crash)                       */
    /* -------------------------------------------------------------- */
    g_console_cache.valid = 0;
    ht_set_module(NULL, 0); /* Modul nicht geladen -> module_range == 0 */
    check(resolve_console_service(&fn, &inst) == 0,
          "resolve_console_service: Modul fehlt -> 0");

    unsigned char *no_sig = build_image(0, 1, 1, 1, 1);
    ht_set_module(no_sig, IMG_SIZE);
    g_console_cache.valid = 0;
    check(resolve_console_service(&fn, &inst) == 0,
          "resolve_console_service: Signatur fehlt -> 0");
    free(no_sig);

    unsigned char *no_vft = build_image(1, 1, 1, 0, 1);
    ht_set_module(no_vft, IMG_SIZE);
    g_console_cache.valid = 0;
    check(resolve_console_service(&fn, &inst) == 0,
          "resolve_console_service: kein vftable-Zeiger -> 0");
    free(no_vft);

    unsigned char *no_inst = build_image(1, 1, 1, 1, 0);
    ht_set_module(no_inst, IMG_SIZE);
    g_console_cache.valid = 0;
    check(resolve_console_service(&fn, &inst) == 0,
          "resolve_console_service: Instanz fehlt -> 0");
    free(no_inst);

    /* -------------------------------------------------------------- */
    /* B1 (Review): Stufe (d) POSITIV - Loader-Sicht aus, Region         */
    /* MEM_IMAGE, (b)/(c)/(e) inert.                                     */
    /* -------------------------------------------------------------- */
    unsigned char *imgd = build_image(1, 1, 1, 1, 1);
    ht_set_module(imgd, IMG_SIZE);
    ht_set_loader_visible(0);      /* (a) GetModuleHandleA/W -> NULL     */
    ht_set_region_type(MEM_IMAGE); /* (d) VirtualQuery-Region MEM_IMAGE  */
    ht_set_own_base(NULL);
    g_console_cache.valid = 0;
    {
        const unsigned char *rb = NULL, *refn = NULL;
        size_t rs = 0;
        const char *rvia = NULL;
        check(resolve_module(&rb, &rs, &rvia, &refn) == 1 &&
              rb == imgd && rs == IMG_SIZE && rvia != NULL &&
              strcmp(rvia, "sigbase") == 0 && refn == imgd + SIG_OFF,
              "B1: resolve_module via=sigbase (a/b/c/e neutralisiert)");
    }
    check(resolve_console_service(&fn, &inst) == 1 &&
          (const unsigned char *)(uintptr_t)fn == imgd + SIG_OFF &&
          inst == (void *)(imgd + INST_OFF),
          "B1: Stufe (d) positiv (Scan -> base/vftable/Instanz)");

    /* R1 (Review/Wine): Cache-Treffer OHNE Loader-Sicht. Nach dem (d)-Erfolg
     * ist der Cache gefuellt; (d) wird jetzt unaufloesbar (Region-Type 0)
     * und die Loader-Sicht bleibt versteckt -> ein Erfolg kann NUR aus der
     * Cache-Re-Validierung stammen (kein Voll-Scan). */
    ht_set_region_type(0);
    fn = NULL; inst = NULL;
    check(resolve_console_service(&fn, &inst) == 1 &&
          inst == (void *)(imgd + INST_OFF),
          "R1: Cache-Treffer ohne GetModuleHandleA (kein Voll-Scan)");
    check(g_console_cache.valid == 1, "R1: Cache bleibt gueltig (Wine-Fall)");

    /* B1-Negativ: "eigenes Image" (own == Kandidat) -> verworfen. */
    ht_set_region_type(MEM_IMAGE);
    ht_set_own_base(imgd);
    g_console_cache.valid = 0;
    fn = NULL; inst = NULL;
    check(resolve_console_service(&fn, &inst) == 0,
          "B1-Negativ: eigenes Image (own==Kandidat) -> verworfen");
    check(g_console_cache.valid == 0, "B1-Negativ: kein Cache-Fill");

    /* B1-Negativ: Modul traegt nur die Signatur, aber keine ConsoleService-
     * vftable (RTTI) - genau der Fall des eigenen Images -> verworfen. */
    ht_set_own_base(NULL);
    unsigned char *sig_only = build_image(1, 0, 0, 0, 0);
    ht_set_module(sig_only, IMG_SIZE);
    ht_set_loader_visible(0);
    ht_set_region_type(MEM_IMAGE);
    g_console_cache.valid = 0;
    fn = NULL; inst = NULL;
    check(resolve_console_service(&fn, &inst) == 0,
          "B1-Negativ: Sig ohne vftable -> Stufe (d) verwirft -> 0");
    free(sig_only);

    /* R2: pe_image_size defensiv (e_lfanew-Schranke, SizeOfImage > 0). */
    unsigned char *pit = build_image(0, 0, 0, 0, 0);
    size_t psz = 123;
    check(pe_image_size(pit, &psz), "R2: gueltiges PE -> ok");
    ((IMAGE_DOS_HEADER *)pit)->e_lfanew = 0; /* < sizeof(DOS-Header) */
    check(!pe_image_size(pit, &psz), "R2: e_lfanew == 0 -> verworfen");
    ((IMAGE_DOS_HEADER *)pit)->e_lfanew = 0x4000; /* zu gross */
    check(!pe_image_size(pit, &psz), "R2: e_lfanew zu gross -> verworfen");
    ((IMAGE_DOS_HEADER *)pit)->e_lfanew = NT_OFF;
    ((IMAGE_NT_HEADERS *)(pit + NT_OFF))->OptionalHeader.SizeOfImage = 0;
    check(!pe_image_size(pit, &psz), "R2: SizeOfImage == 0 -> verworfen");
    free(pit);

    /* Test-Knobs fuer alles Nachfolgende zuruecksetzen. */
    ht_set_loader_visible(1);
    ht_set_region_type(0);
    ht_set_own_base(NULL);
    g_console_cache.valid = 0;
    free(imgd);

    /* -------------------------------------------------------------- */
    /* basket_lookup_value (#401): reine Account-Basket-Lookup-Logik    */
    /* -------------------------------------------------------------- */
    /* Synthetischer Basket: 16-B-Entries {u32 StringHash, i64 Value}. */
    {
        static unsigned char basket[3 * 16];
        memset(basket, 0, sizeof(basket));
        uint32_t h0 = 0x659cc791u; /* carbonium */
        uint32_t h1 = 0x0d01a504u; /* ironium (intern "steel") */
        uint32_t h2 = 0x1b9f8256u; /* titanium */
        uint64_t v0 = 300000000ull, v1 = 50000000ull, v2 = 0ull;
        memcpy(basket + 0,  &h0, 4); memcpy(basket + 8,  &v0, 8);
        memcpy(basket + 16, &h1, 4); memcpy(basket + 24, &v1, 8);
        memcpy(basket + 32, &h2, 4); memcpy(basket + 40, &v2, 8);

        uint64_t out = 0;
        check(basket_lookup_value(basket, 3, h0, &out) == 1 && out == v0,
              "basket_lookup: carbonium-Wert gefunden (v0)");
        out = 0;
        check(basket_lookup_value(basket, 3, h1, &out) == 1 && out == v1,
              "basket_lookup: ironium-Wert gefunden (v1 = steel)");
        out = 123;
        check(basket_lookup_value(basket, 3, h2, &out) == 1 && out == v2,
              "basket_lookup: titanium == 0 -> gefunden (v2)");

        /* Nicht-Fund ist graceful: 0 zurueck, out unangetastet. */
        out = 424242;
        check(basket_lookup_value(basket, 3, 0xdeadbeefu, &out) == 0
              && out == 424242,
              "basket_lookup: unbekannter Hash -> 0, out unangetastet");
        /* Randfaelle: NULL/leer/zu gross -> 0, kein Crash. */
        check(basket_lookup_value(NULL, 3, h1, &out) == 0,
              "basket_lookup: arr == NULL -> 0");
        check(basket_lookup_value(basket, 0, h1, &out) == 0,
              "basket_lookup: count == 0 -> 0");
        check(basket_lookup_value(basket, 1025, h1, &out) == 0,
              "basket_lookup: count > 1024 -> 0 (defensiv)");
        /* out == NULL ist erlaubt (nur Existenz-Check). */
        check(basket_lookup_value(basket, 3, h1, NULL) == 1,
              "basket_lookup: out == NULL -> nur Existenz-Check");
    }

    /* -------------------------------------------------------------- */
    /* resource_internal_name (#421): Anzeigename -> interner Name      */
    /* -------------------------------------------------------------- */
    check(strcmp(resource_internal_name("ironium"), "steel") == 0,
          "resource_internal_name: ironium -> steel");
    check(strcmp(resource_internal_name("carbonium"), "carbonium") == 0,
          "resource_internal_name: carbonium -> carbonium");
    check(strcmp(resource_internal_name("mythium"), "carbonium") == 0,
          "resource_internal_name: unbekannt -> carbonium");
    check(strcmp(resource_internal_name(""), "carbonium") == 0,
          "resource_internal_name: leer -> carbonium");
    check(strcmp(resource_internal_name(NULL), "carbonium") == 0,
          "resource_internal_name: NULL -> carbonium");

    /* ============================================================== */
    /* #386: Mission-Flow-Payload (Database*)                          */
    /* ============================================================== */

    /* RTTI-Walk MissionService: positiv + Negativfaelle. */
    ht_set_module(NULL, 0);
    {
        unsigned char *mi = build_mission_image(1, 1, 1, 1, 1, 1, 1, 1);
        ht_set_module(mi, IMG_SIZE);
        check(resolve_mission_vftable(mi, IMG_SIZE) == mi + VFT_OFF,
              "#386 vftable: Mission-RTTI->TD->COL->vftable");

        const unsigned char *mv = NULL;
        void *minst = NULL;
        check(resolve_mission_service(&mv, &minst) == 1 &&
              minst == (void *)(mi + INST_OFF),
              "#386 resolve_mission_service: vftable+Instanz");

        /* AOB-Resolver (positiv). */
        check(resolve_amf_fn(mi, IMG_SIZE) == mi + AMF_OFF,
              "#386 AOB: ActivateMissionFlow(Database*) gefunden");
        check(resolve_db_ctor_fn(mi, IMG_SIZE) == mi + CTOR_OFF,
              "#386 AOB: Database::Database() gefunden");
        check(resolve_db_setstring_fn(mi, IMG_SIZE) == mi + SETSTR_OFF,
              "#386 AOB: Database::SetString gefunden");
        check(resolve_db_getstring_fn(mi, IMG_SIZE) == mi + GETSTR_OFF,
              "#386 AOB: Database::GetString gefunden");
        memcpy(mi + GETKEYS_OFF, RBBRIDGE_DB_GETKEYS_SIG,
               sizeof(RBBRIDGE_DB_GETKEYS_SIG));
        check(resolve_db_getkeys_fn(mi, IMG_SIZE) == mi + GETKEYS_OFF,
              "#386 AOB: Database::GetStringKeys gefunden");

        /* Wildcard: rel32 des AMF-CALLs darf abweichen. */
        mi[AMF_OFF + 34] ^= 0xFF;
        mi[AMF_OFF + 35] ^= 0xFF;
        check(resolve_amf_fn(mi, IMG_SIZE) == mi + AMF_OFF,
              "#386 AOB: AMF rel32-Wildcard toleriert");
        free(mi);
    }

    /* Negativ: RTTI-Name fehlt / COL ungueltig / Instanz fehlt. */
    {
        unsigned char *mi = build_mission_image(0, 1, 1, 1, 1, 1, 1, 1);
        ht_set_module(mi, IMG_SIZE);
        check(resolve_mission_vftable(mi, IMG_SIZE) == NULL,
              "#386 vftable-Negativ: Mission-RTTI fehlt -> NULL");
        free(mi);
    }
    {
        unsigned char *mi = build_mission_image(1, 0, 1, 1, 1, 1, 1, 1);
        ht_set_module(mi, IMG_SIZE);
        check(resolve_mission_vftable(mi, IMG_SIZE) == NULL,
              "#386 vftable-Negativ: COL.signature != 1 -> NULL");
        free(mi);
    }
    {
        unsigned char *mi = build_mission_image(1, 1, 1, 0, 1, 1, 1, 1);
        ht_set_module(mi, IMG_SIZE);
        const unsigned char *mv = NULL;
        void *minst = NULL;
        check(resolve_mission_service(&mv, &minst) == 0,
              "#386 resolve_mission_service: Instanz fehlt -> 0 (kein Aufruf)");
        free(mi);
    }
    {
        unsigned char *mi = build_mission_image(0, 0, 0, 0, 0, 0, 0, 0);
        ht_set_module(mi, IMG_SIZE);
        check(resolve_amf_fn(mi, IMG_SIZE) == NULL &&
              resolve_db_ctor_fn(mi, IMG_SIZE) == NULL &&
              resolve_db_setstring_fn(mi, IMG_SIZE) == NULL,
              "#386 AOB-Negativ: keine Signatur -> NULL");
        free(mi);
    }
    ht_set_module(NULL, 0);
    check(resolve_mission_service(NULL, NULL) == 0 ||
          resolve_mission_service(&(const unsigned char *){0}, NULL) == 0,
          "#386 resolve_mission_service: kein Modul -> 0");

    /* UtfString SSO: fill/read-Roundtrip + Kuerzung > 15 Zeichen. */
    {
        unsigned char u[40];
        char got[64];
        utfstring_fill(u, sizeof(u), "spawn_point");
        check(utfstring_read(u, got, sizeof(got)) == 1 &&
              strcmp(got, "spawn_point") == 0,
              "#386 utfstring: fill/read-Roundtrip");
        utfstring_fill(u, sizeof(u), "");
        check(utfstring_read(u, got, sizeof(got)) == 1 && got[0] == '\0',
              "#386 utfstring: leerer String");
        utfstring_fill(u, sizeof(u), "12345678901234567890");
        check(utfstring_read(u, got, sizeof(got)) == 1 && strlen(got) == 15,
              "#386 utfstring: >15 Zeichen defensiv gekuerzt");
        check(utfstring_read(NULL, got, sizeof(got)) == 0,
              "#386 utfstring: NULL -> 0 (kein Crash)");
    }

    /* Database-Builder mit Stub-Fn-Ptrs. */
    {
        unsigned char db[0x60];
        memset(db, 0, sizeof(db));
        g_stub_ctor_calls = 0;
        g_stub_set_calls = 0;
        database_init(db, stub_ctor);
        database_set_string(db, stub_setstring, "spawn_point", "42");
        database_set_string(db, stub_setstring, "name", "m1");
        check(g_stub_ctor_calls == 1, "#386 builder: Ctor genau 1x gerufen");
        check(g_stub_set_calls == 2, "#386 builder: SetString 2x gerufen");
        check(strcmp(g_stub_key, "name") == 0 &&
              strcmp(g_stub_val, "m1") == 0,
              "#386 builder: letzter SetString(key,value) korrekt");
        database_init(NULL, stub_ctor);
        database_set_string(db, NULL, "k", "v");
        check(g_stub_ctor_calls == 1 && g_stub_set_calls == 2,
              "#386 builder: NULL-Fn wird uebersprungen (kein Crash)");
    }

    /* copy_cstr: Kopie + Truncation. */
    {
        char cbuf[8];
        copy_cstr(cbuf, sizeof(cbuf), "abc");
        check(strcmp(cbuf, "abc") == 0, "#386 copy_cstr: Kopie");
        copy_cstr(cbuf, sizeof(cbuf), "0123456789");
        check(strlen(cbuf) == 7, "#386 copy_cstr: Truncation auf dst_sz-1");
        copy_cstr(cbuf, sizeof(cbuf), NULL);
        check(cbuf[0] == '\0', "#386 copy_cstr: NULL -> leer");
    }

    /* activate_result-JSON (Pipe-Roundtrip-Form ok:true/ok:false). */
    {
        char j[1024];
        activate_result_json(j, sizeof(j), 1, NULL, "flowA", "spawn-7");
        check(strstr(j, "\"event\":\"activate_result\"") != NULL &&
              strstr(j, "\"ok\":true") != NULL &&
              strstr(j, "\"name\":\"flowA\"") != NULL &&
              strstr(j, "\"spawn_point\":\"spawn-7\"") != NULL,
              "#386 activate_result: ok:true mit name/spawn_point");
        activate_result_json(j, sizeof(j), 0, "no_mission_service", NULL, NULL);
        check(strstr(j, "\"ok\":false") != NULL &&
              strstr(j, "\"reason\":\"no_mission_service\"") != NULL,
              "#386 activate_result: ok:false mit reason");
        /* Escaping: Anfuehrungszeichen im Namen wird escaped. */
        activate_result_json(j, sizeof(j), 1, NULL, "a\"b", NULL);
        check(strstr(j, "\\\"") != NULL,
              "#386 activate_result: JSON-Escape im Namen");
    }

    /* -------------------------------------------------------------- */
    /* #386: JSON-Ingress-Parser json_get_string (handle_line-Routing). */
    /* -------------------------------------------------------------- */
    {
        char v[64];
        check(json_get_string("{\"cmd\":\"get_state\"}", "cmd", v,
                              sizeof(v)) == 1 && strcmp(v, "get_state") == 0,
              "#386 json_get_string: cmd extrahiert");
        check(json_get_string(
                  "{\"cmd\":\"activate_mission_flow\",\"name\":\"m1\","
                  "\"spawn_point\":\"s2\"}",
                  "spawn_point", v, sizeof(v)) == 1 && strcmp(v, "s2") == 0,
              "#386 json_get_string: spawn_point extrahiert");
        check(json_get_string("{\"foo\":\"bar\"}", "cmd", v,
                              sizeof(v)) == 0,
              "#386 json_get_string: fehlender Key -> 0");
        check(json_get_string("{\"cmd\":123}", "cmd", v, sizeof(v)) == 0,
              "#386 json_get_string: Nicht-String-Wert -> 0");
        check(json_get_string(NULL, "cmd", v, sizeof(v)) == 0,
              "#386 json_get_string: NULL -> 0 (kein Crash)");
        check(json_get_string("{\"cmd\":\"a\\\\b\"}", "cmd", v,
                              sizeof(v)) == 1 && strcmp(v, "a\\b") == 0,
              "#386 json_get_string: Backslash-Escape aufgeloest");
    }

    /* -------------------------------------------------------------- */
    /* #386 S5 Read-Leg-Form: mission_flow_json (null / Fallback / Escape) */
    /* -------------------------------------------------------------- */
    {
        char mf[1024];

        /* 1) kein Payload geparkt -> ehrliches "null", kein Crash. */
        ht_set_module(NULL, 0);
        g_mission_flow_valid = 0;
        g_mission_payload_db = NULL;
        mission_flow_json(mf, sizeof(mf));
        check(strcmp(mf, "null") == 0,
              "#386 mission_flow_json: kein Payload -> null");

        /* 2) Payload geparkt, aber GetString-AOB fehlt -> Fallback auf die
         *    zuletzt gesetzten Felder (Accessor nicht aufloesbar). */
        unsigned char *mfmi = build_mission_image(1, 1, 1, 1, 1, 1, 1, 0);
        ht_set_module(mfmi, IMG_SIZE);
        g_mission_payload_db = (void *)mfmi; /* wird ohne AOB nicht gerufen */
        g_mission_flow_valid = 1;
        copy_cstr(g_mission_name, sizeof(g_mission_name), "flowB");
        copy_cstr(g_mission_spawn, sizeof(g_mission_spawn), "spawn-9");
        mission_flow_json(mf, sizeof(mf));
        check(strstr(mf, "\"name\":\"flowB\"") != NULL &&
              strstr(mf, "\"spawn_point\":\"spawn-9\"") != NULL,
              "#386 mission_flow_json: Fallback-Felder (AOB fehlt)");

        /* 3) Einbettung: Sonderzeichen in den Feldern werden JSON-escaped. */
        copy_cstr(g_mission_name, sizeof(g_mission_name), "a\"b\\c");
        mission_flow_json(mf, sizeof(mf));
        check(strstr(mf, "\\\"") != NULL && strstr(mf, "\\\\") != NULL,
              "#386 mission_flow_json: JSON-Escape der Felder");

        free(mfmi);
        g_mission_flow_valid = 0;
        g_mission_payload_db = NULL;
        ht_set_module(NULL, 0);
    }

    /* -------------------------------------------------------------- */
    /* #386 S4 Dispatch ok:false-Pfade (echter dispatch_activate_...).  */
    /* -------------------------------------------------------------- */
    {
        unsigned char *dmi;
        g_ht_last_reply[0] = '\0';
        g_ht_send_calls = 0;

        /* 1) kein Modul -> ok:false/no_module, kein Aufruf. */
        ht_set_module(NULL, 0);
        dispatch_activate_mission_flow((HANDLE)0, "n", "s");
        check(g_ht_send_calls == 1 &&
              strstr(g_ht_last_reply, "\"ok\":false") != NULL &&
              strstr(g_ht_last_reply, "\"reason\":\"no_module\"") != NULL,
              "#386 dispatch: kein Modul -> ok:false/no_module");

        /* 2) Modul ohne ActivateMissionFlow-AOB -> no_signature. */
        dmi = build_mission_image(1, 1, 1, 1, 0, 0, 0, 0);
        ht_set_module(dmi, IMG_SIZE);
        dispatch_activate_mission_flow((HANDLE)0, "n", "s");
        check(strstr(g_ht_last_reply, "\"reason\":\"no_signature\"") != NULL,
              "#386 dispatch: AOB fehlt -> ok:false/no_signature");
        free(dmi);

        /* 3) Signatur+Builder da, aber kein MissionService -> graceful. */
        dmi = build_mission_image(0, 0, 0, 0, 1, 1, 1, 0);
        ht_set_module(dmi, IMG_SIZE);
        dispatch_activate_mission_flow((HANDLE)0, "n", "s");
        check(strstr(g_ht_last_reply,
                     "\"reason\":\"no_mission_service\"") != NULL,
              "#386 dispatch: kein MissionService -> ok:false/no_service");
        free(dmi);

        /* Kein ok:false-Pfad darf den Payload parken. */
        check(g_mission_flow_valid == 0,
              "#386 dispatch: ok:false parkt keinen Payload (kein Aufruf)");
        ht_set_module(NULL, 0);
    }

    free(img);

    printf("HOSTTEST_PASS=%d HOSTTEST_FAIL=%d\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
