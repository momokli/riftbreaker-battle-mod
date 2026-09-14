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

/* ------------------------------------------------------------------ */
/* Issue #387: DOM (LuaGraphNode-Familie)                              */
/* ------------------------------------------------------------------ */

#define DOMSIG_OFF     0x1A00 /* RBBRIDGE_DOM_SIG (Update-Prolog)        */
#define SETSUSP_OFF    0x1B00 /* RBBRIDGE_SETSUSPEND_SIG                  */
#define DOM_VT_OFF     0x1C00 /* Familie-vftable (Slot0/1/2/7 belegt)     */
#define DOM_INST_OFF   0x1D00 /* Instanz 1 (QWORD == vtable)              */
#define DOM_GETTER_OFF 0x1E00 /* Slot1-Getter-Stub (lea rax,[rip];ret)    */
#define DOM_INST2_OFF  0x1F00 /* Instanz 2 (ambiguous_dom-Fall)           */

/* GetTypeHash-Ersatz: liefert den live bestaetigten C++-Klassen-Hash. In der
 * vtable auf Slot 2 abgelegt -> dom_type_hash() ruft ihn als echten Host-Code
 * (der Windows-Signaturstub waere unter der SysV-ABI nicht aufrufbar). */
static uint32_t ht_type_hash(const void *inst)
{
    (void)inst;
    return RBBRIDGE_DOM_HASH_LUAGRAPHNODE;
}

/* DOM-Image: AOB-Sigs, Familie-vftable und n Instanzen (0..2). */
static unsigned char *build_dom_image(int with_sig, int with_set, int n_inst)
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
    nt->FileHeader.Machine = IMAGE_FILE_MACHINE_AMD64;
    nt->FileHeader.NumberOfSections = 1;
    nt->FileHeader.SizeOfOptionalHeader = (uint16_t)sizeof(IMAGE_OPTIONAL_HEADER);
    nt->OptionalHeader.SizeOfImage = IMG_SIZE;

    IMAGE_SECTION_HEADER *sec = IMAGE_FIRST_SECTION(nt);
    memcpy(sec->Name, ".text", 5);
    sec->Misc.VirtualSize = TEXT_VSIZE;
    sec->VirtualAddress = TEXT_RVA;

    if (with_sig) {
        memcpy(img + DOMSIG_OFF, RBBRIDGE_DOM_SIG, sizeof(RBBRIDGE_DOM_SIG));
        /* Slot1-Getter-Stub: 48 8D 05 00 00 00 00 C3 (lea rax,[rip+0]; ret) */
        img[DOM_GETTER_OFF + 0] = 0x48;
        img[DOM_GETTER_OFF + 1] = 0x8D;
        img[DOM_GETTER_OFF + 2] = 0x05;
        img[DOM_GETTER_OFF + 7] = 0xC3;
        wr64(img + DOM_VT_OFF + 0 * 8, (uint64_t)(uintptr_t)(img + DOMSIG_OFF));
        wr64(img + DOM_VT_OFF + 1 * 8,
             (uint64_t)(uintptr_t)(img + DOM_GETTER_OFF));
        wr64(img + DOM_VT_OFF + 2 * 8, (uint64_t)(uintptr_t)&ht_type_hash);
        wr64(img + DOM_VT_OFF + 7 * 8, (uint64_t)(uintptr_t)(img + DOMSIG_OFF));
    }
    if (with_set)
        memcpy(img + SETSUSP_OFF, RBBRIDGE_SETSUSPEND_SIG,
               sizeof(RBBRIDGE_SETSUSPEND_SIG));
    if (n_inst >= 1)
        wr64(img + DOM_INST_OFF, (uint64_t)(uintptr_t)(img + DOM_VT_OFF));
    if (n_inst >= 2)
        wr64(img + DOM_INST2_OFF, (uint64_t)(uintptr_t)(img + DOM_VT_OFF));
    return img;
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

    /* -------------------------------------------------------------- */
    /* Issue #387: DOM-Familie — AOB-Sigs, vtable-Resolver,             */
    /* ambiguous_dom (ok:false, kein Crash), SetSuspended-Schreibpfad.   */
    /* -------------------------------------------------------------- */
    {
        uint64_t vts[RBBRIDGE_DOM_MAX_VT];
        uint64_t inst = 0, setfn = 0;
        int count = 0;
        const char *err;

        /* (a) beide AOB-Signaturen im synthetischen Image auffindbar */
        unsigned char *dimg = build_dom_image(1, 1, 1);
        ht_set_module(dimg, IMG_SIZE);
        check(resolve_dom_update(dimg, IMG_SIZE) == dimg + DOMSIG_OFF,
              "#387: RBBRIDGE_DOM_SIG im synthetischen Image gefunden");
        check(resolve_set_suspended(dimg, IMG_SIZE) == dimg + SETSUSP_OFF,
              "#387: RBBRIDGE_SETSUSPEND_SIG im synthetischen Image gefunden");
        check(resolve_dom_vtables(dimg, IMG_SIZE, dimg + DOMSIG_OFF, vts,
                                  RBBRIDGE_DOM_MAX_VT) == 1 &&
              vts[0] == (uint64_t)(uintptr_t)(dimg + DOM_VT_OFF),
              "#387: resolve_dom_vtables findet genau die Familie-vtable");

        /* (c) SetSuspended-Schreibpfad: eindeutige Instanz -> ok + Flag */
        g_ht_suspend_calls = 0;
        g_ht_suspend_on = -1;
        err = dom_apply_suspend(1, 0, &inst, &count, &setfn);
        check(err == NULL && count == 1 &&
              inst == (uint64_t)(uintptr_t)(dimg + DOM_INST_OFF),
              "#387: pause_dom eindeutig -> ok, Instanz gewaehlt");
        check(setfn == (uint64_t)(uintptr_t)(dimg + SETSUSP_OFF),
              "#387: SetSuspended-Zeiger == Signaturfund");
        check(g_ht_suspend_calls == 1 && g_ht_suspend_on == 1 &&
              dimg[DOM_INST_OFF + 0xF1] == 1,
              "#387: pause_dom schreibt suspended-Flag +0xF1 == 1");
        err = dom_apply_suspend(0, 0, &inst, &count, &setfn);
        check(err == NULL && dimg[DOM_INST_OFF + 0xF1] == 0,
              "#387: resume_dom schreibt suspended-Flag +0xF1 == 0");

        /* (b) Nicht-Fund-Pfade -> ok:false (Fehlercode), kein Crash */
        unsigned char *nosig = build_dom_image(0, 1, 1);
        ht_set_module(nosig, IMG_SIZE);
        check(strcmp(dom_apply_suspend(1, 0, &inst, &count, &setfn),
                     "no_dom_signature") == 0,
              "#387: ohne DOM-Sig -> no_dom_signature");
        free(nosig);

        unsigned char *noset = build_dom_image(1, 0, 1);
        ht_set_module(noset, IMG_SIZE);
        check(strcmp(dom_apply_suspend(1, 0, &inst, &count, &setfn),
                     "no_setsuspended_signature") == 0,
              "#387: ohne SetSuspended-Sig -> no_setsuspended_signature");
        free(noset);

        unsigned char *noinst = build_dom_image(1, 1, 0);
        ht_set_module(noinst, IMG_SIZE);
        check(strcmp(dom_apply_suspend(1, 0, &inst, &count, &setfn),
                     "no_dom_instance") == 0,
              "#387: ohne Instanz -> no_dom_instance");
        free(noinst);

        /* (b) zwei Kandidaten, kein ref -> ambiguous_dom (ok:false) */
        unsigned char *dimg2 = build_dom_image(1, 1, 2);
        ht_set_module(dimg2, IMG_SIZE);
        inst = 123; count = 0;
        g_ht_suspend_calls = 0;
        err = dom_apply_suspend(1, 0, &inst, &count, &setfn);
        check(err != NULL && strcmp(err, "ambiguous_dom") == 0 &&
              count == 2 && inst == 0,
              "#387: zwei Kandidaten -> ambiguous_dom (ok:false)");
        check(g_ht_suspend_calls == 0 && dimg2[DOM_INST_OFF + 0xF1] == 0 &&
              dimg2[DOM_INST2_OFF + 0xF1] == 0,
              "#387: ambiguous_dom schreibt KEIN Flag (kein Crash)");
        check(strcmp(dom_apply_suspend(0, 0, &inst, &count, &setfn),
                     "ambiguous_dom") == 0,
              "#387: ambiguous_dom auch bei resume");

        /* expliziter ref trennt die beiden Kandidaten (luabind-Registry-Ref) */
        {
            uint64_t r = 0x4321ull;
            memcpy(dimg2 + DOM_INST2_OFF + 0x28, &r, 8);
            g_ht_suspend_calls = 0;
            err = dom_apply_suspend(1, r, &inst, &count, &setfn);
            check(err == NULL &&
                  inst == (uint64_t)(uintptr_t)(dimg2 + DOM_INST2_OFF),
                  "#387: ref-Filter waehlt die richtige Instanz bei 2 Kandidaten");
            check(g_ht_suspend_calls == 1 && dimg2[DOM_INST2_OFF + 0xF1] == 1 &&
                  dimg2[DOM_INST_OFF + 0xF1] == 0,
                  "#387: ref-Filter schreibt nur das Flag der gewaehlten Instanz");
        }
        free(dimg2);

        /* Modul fehlt -> no_module, kein Crash */
        ht_set_module(NULL, 0);
        check(strcmp(dom_apply_suspend(1, 0, &inst, &count, &setfn),
                     "no_module") == 0,
              "#387: Modul fehlt -> no_module");
        free(dimg);
    }

    free(img);

    printf("HOSTTEST_PASS=%d HOSTTEST_FAIL=%d\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
