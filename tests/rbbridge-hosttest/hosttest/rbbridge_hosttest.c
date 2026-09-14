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

    if (with_sig)
        memcpy(img + SIG_OFF, RBBRIDGE_EXEC_SIG, sizeof(RBBRIDGE_EXEC_SIG));

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

    free(img);

    printf("HOSTTEST_PASS=%d HOSTTEST_FAIL=%d\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
