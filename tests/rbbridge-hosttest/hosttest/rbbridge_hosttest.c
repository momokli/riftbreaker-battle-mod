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
#define DGET_SIG_OFF 0x1180 /* CampaignService difficulty-Get-Signatur  */
#define DSET_SIG_OFF 0x11A0 /* CampaignService difficulty-Set-Signatur  */
#define DINC_SIG_OFF 0x11C0 /* CampaignService difficulty-Inc-Signatur  */
#define DDEC_SIG_OFF 0x11E0 /* CampaignService difficulty-Dec-Signatur  */
#define ACT_SIG_OFF 0x1300 /* ActivateMissionFlow-Signatur in .text   */
#define DEACT_SIG_OFF 0x1340 /* DeactivateMissionFlow-Signatur (#389)  */
#define DBSS_SIG_OFF 0x1240 /* Database::SetString-Signatur (#386)     */
#define DBGS_SIG_OFF 0x1260 /* Database::GetString-Signatur (#386)     */
#define DBCTOR_OFF   0x1280 /* Database::Database()-Prolog (#386)      */
#define NEWDB_OFF    0x12A0 /* `new Database`-Call-Site (#386)         */
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
        memcpy(img + DGET_SIG_OFF, RBBRIDGE_DIFF_GET_SIG,
               sizeof(RBBRIDGE_DIFF_GET_SIG));
        memcpy(img + DSET_SIG_OFF, RBBRIDGE_DIFF_SET_SIG,
               sizeof(RBBRIDGE_DIFF_SET_SIG));
        memcpy(img + DINC_SIG_OFF, RBBRIDGE_DIFF_INC_SIG,
               sizeof(RBBRIDGE_DIFF_INC_SIG));
        memcpy(img + DDEC_SIG_OFF, RBBRIDGE_DIFF_DEC_SIG,
               sizeof(RBBRIDGE_DIFF_DEC_SIG));
        memcpy(img + ACT_SIG_OFF, RBBRIDGE_ACTIVATE_SIG,
               sizeof(RBBRIDGE_ACTIVATE_SIG));
        memcpy(img + DEACT_SIG_OFF, RBBRIDGE_DEACTIVATE_SIG,
               sizeof(RBBRIDGE_DEACTIVATE_SIG));
        /* #386: Database-Payload-Anker (SetString/GetString-Prolog + die
         * `new 0x60`-Call-Site, deren zweiter E8 auf den Ctor-Prolog zeigt). */
        memcpy(img + DBSS_SIG_OFF, RBBRIDGE_DB_SETSTRING_SIG,
               sizeof(RBBRIDGE_DB_SETSTRING_SIG));
        memcpy(img + DBGS_SIG_OFF, RBBRIDGE_DB_GETSTRING_SIG,
               sizeof(RBBRIDGE_DB_GETSTRING_SIG));
        memcpy(img + DBCTOR_OFF, RBBRIDGE_DB_CTOR_SIG,
               sizeof(RBBRIDGE_DB_CTOR_SIG));
        memcpy(img + NEWDB_OFF, RBBRIDGE_NEWDB_SITE_SIG,
               sizeof(RBBRIDGE_NEWDB_SITE_SIG));
        {
            unsigned char *cs = img + NEWDB_OFF + 0x20;
            int32_t rel;
            memcpy(cs, RBBRIDGE_NEWDB_CALL_SIG,
                   sizeof(RBBRIDGE_NEWDB_CALL_SIG));
            /* Ziel = e8 + 5 + rel32; e8 = cs+3 -> Basis cs+8. */
            rel = (int32_t)((img + DBCTOR_OFF) - (cs + 8));
            memcpy(cs + 4, &rel, sizeof(rel));
        }
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
    /* DeactivateMissionFlow-AOB (Issue #389)                          */
    /* -------------------------------------------------------------- */
    ht_set_module(img, IMG_SIZE);
    check(scan_bytes_mask(img + TEXT_RVA, TEXT_VSIZE,
                          RBBRIDGE_DEACTIVATE_SIG,
                          RBBRIDGE_DEACTIVATE_SIG_MASK,
                          sizeof(RBBRIDGE_DEACTIVATE_SIG)) ==
              img + DEACT_SIG_OFF,
          "DeactivateMissionFlow-AOB (Maske) im .text gefunden (#389)");
    {
        /* Prolog-identische IsGraphActive-Form (cmp eax,1 / sete al statt
         * add rsp,0x30): darf NICHT treffen -> Diskriminator-Byte 87. */
        unsigned char *img4 = build_image(1, 1, 1, 1, 1);
        img4[DEACT_SIG_OFF + 87] = 0x83; /* cmp eax,1 */
        img4[DEACT_SIG_OFF + 88] = 0xF8;
        img4[DEACT_SIG_OFF + 89] = 0x01;
        ht_set_module(img4, IMG_SIZE);
        check(scan_bytes_mask(img4 + TEXT_RVA, TEXT_VSIZE,
                              RBBRIDGE_DEACTIVATE_SIG,
                              RBBRIDGE_DEACTIVATE_SIG_MASK,
                              sizeof(RBBRIDGE_DEACTIVATE_SIG)) == NULL,
              "Deactivate-AOB: IsGraphActive-Form (cmp eax,1) -> kein Treffer");
        free(img4);
    }
    {
        unsigned char *img5 = build_image(1, 1, 1, 1, 1);
        img5[DEACT_SIG_OFF + 5] ^= 0xFF; /* Prolog-Byte abweichend */
        ht_set_module(img5, IMG_SIZE);
        check(scan_bytes_mask(img5 + TEXT_RVA, TEXT_VSIZE,
                              RBBRIDGE_DEACTIVATE_SIG,
                              RBBRIDGE_DEACTIVATE_SIG_MASK,
                              sizeof(RBBRIDGE_DEACTIVATE_SIG)) == NULL,
              "Deactivate-AOB: abweichendes Prolog-Byte -> kein Treffer");
        free(img5);
    }
    {
        /* rel32-Wildcards: kaputte CALL-Operanden werden toleriert. */
        unsigned char *img6 = build_image(1, 1, 1, 1, 1);
        img6[DEACT_SIG_OFF + 18] ^= 0xFF; /* call-1 rel32 */
        img6[DEACT_SIG_OFF + 26] ^= 0xFF; /* call-2 rel32 */
        img6[DEACT_SIG_OFF + 37] ^= 0xFF; /* call-3 rel32 */
        img6[DEACT_SIG_OFF + 78] ^= 0xFF; /* call-4 rel32 */
        ht_set_module(img6, IMG_SIZE);
        check(scan_bytes_mask(img6 + TEXT_RVA, TEXT_VSIZE,
                              RBBRIDGE_DEACTIVATE_SIG,
                              RBBRIDGE_DEACTIVATE_SIG_MASK,
                              sizeof(RBBRIDGE_DEACTIVATE_SIG)) ==
                  img6 + DEACT_SIG_OFF,
              "Deactivate-AOB: rel32-Abweichung (Maske) toleriert");
        free(img6);
    }
    {
        /* E8-Opcode bleibt Pflicht (nur Operanden sind maskiert). */
        unsigned char *img7 = build_image(1, 1, 1, 1, 1);
        img7[DEACT_SIG_OFF + 17] ^= 0xFF; /* E8 -> AA */
        ht_set_module(img7, IMG_SIZE);
        check(scan_bytes_mask(img7 + TEXT_RVA, TEXT_VSIZE,
                              RBBRIDGE_DEACTIVATE_SIG,
                              RBBRIDGE_DEACTIVATE_SIG_MASK,
                              sizeof(RBBRIDGE_DEACTIVATE_SIG)) == NULL,
              "Deactivate-AOB: E8-Opcode bleibt Pflicht");
        free(img7);
    }

    /* -------------------------------------------------------------- */
    /* CampaignService-Difficulty-AOBs (Issue #388)                    */
    /* -------------------------------------------------------------- */
    ht_set_module(img, IMG_SIZE);
    check(scan_bytes(img + TEXT_RVA, TEXT_VSIZE, RBBRIDGE_DIFF_GET_SIG,
                     sizeof(RBBRIDGE_DIFF_GET_SIG)) == img + DGET_SIG_OFF,
          "difficulty-Get-AOB im .text gefunden (#388)");
    check(scan_bytes(img + TEXT_RVA, TEXT_VSIZE, RBBRIDGE_DIFF_SET_SIG,
                     sizeof(RBBRIDGE_DIFF_SET_SIG)) == img + DSET_SIG_OFF,
          "difficulty-Set-AOB im .text gefunden (#388)");
    check(scan_bytes(img + TEXT_RVA, TEXT_VSIZE, RBBRIDGE_DIFF_INC_SIG,
                     sizeof(RBBRIDGE_DIFF_INC_SIG)) == img + DINC_SIG_OFF,
          "difficulty-Increase-AOB im .text gefunden (#388)");
    check(scan_bytes(img + TEXT_RVA, TEXT_VSIZE, RBBRIDGE_DIFF_DEC_SIG,
                     sizeof(RBBRIDGE_DIFF_DEC_SIG)) == img + DDEC_SIG_OFF,
          "difficulty-Decrease-AOB im .text gefunden (#388)");
    /* Get (13 B, endet auf C3) darf NICHT in der Decrease-Funktion
     * aufschlagen: Decrease hat dieselben ersten 12 Bytes, aber statt C3
     * geht es mit F3 0F 5C C1 weiter. */
    {
        unsigned char *imgs = build_image(1, 1, 1, 1, 1);
        memcpy(imgs + DGET_SIG_OFF, RBBRIDGE_DIFF_DEC_SIG,
               sizeof(RBBRIDGE_DIFF_DEC_SIG));
        memset(imgs + DGET_SIG_OFF + sizeof(RBBRIDGE_DIFF_DEC_SIG), 0,
               sizeof(RBBRIDGE_DIFF_GET_SIG));
        ht_set_module(imgs, IMG_SIZE);
        check(scan_bytes(imgs + TEXT_RVA, TEXT_VSIZE, RBBRIDGE_DIFF_GET_SIG,
                         sizeof(RBBRIDGE_DIFF_GET_SIG)) == NULL,
              "difficulty-Get-AOB: kein Treffer im Decrease-Prefix");
        free(imgs);
    }
    {
        unsigned char *img4 = build_image(1, 1, 1, 1, 1);
        img4[DSET_SIG_OFF + 5] ^= 0xFF; /* Prolog-Byte abweichend */
        ht_set_module(img4, IMG_SIZE);
        check(scan_bytes(img4 + TEXT_RVA, TEXT_VSIZE, RBBRIDGE_DIFF_SET_SIG,
                         sizeof(RBBRIDGE_DIFF_SET_SIG)) == NULL,
              "difficulty-Set-AOB: abweichendes Byte -> kein Treffer");
        free(img4);
    }

    /* -------------------------------------------------------------- */
    /* diff_decode (Layout-Offsets aus dem Funktionskoerper)           */
    /* -------------------------------------------------------------- */
    /* Alle vier Prologe muessen denselben this-deref (0x10) und dasselbe
     * Feld-Offset (0x584) dekodieren. */
    {
        uint32_t td = 0;
        int32_t fo = 0;
        check(diff_decode(RBBRIDGE_DIFF_GET_SIG, &td, &fo) && td == 0x10 &&
                  fo == 0x584,
              "diff_decode: Get -> this_deref 0x10 / field 0x584");
        check(diff_decode(RBBRIDGE_DIFF_SET_SIG, &td, &fo) && td == 0x10 &&
                  fo == 0x584,
              "diff_decode: Set -> this_deref 0x10 / field 0x584");
        check(diff_decode(RBBRIDGE_DIFF_INC_SIG, &td, &fo) && td == 0x10 &&
                  fo == 0x584,
              "diff_decode: Increase -> this_deref 0x10 / field 0x584");
        check(diff_decode(RBBRIDGE_DIFF_DEC_SIG, &td, &fo) && td == 0x10 &&
                  fo == 0x584,
              "diff_decode: Decrease -> this_deref 0x10 / field 0x584");

        /* Ein geaendertes Layout aendert die Bytes -> Decoder lehnt ab
         * (statt ein falsches Offset zu liefern). */
        unsigned char body[sizeof(RBBRIDGE_DIFF_GET_SIG)];
        memcpy(body, RBBRIDGE_DIFF_GET_SIG, sizeof(body));
        body[2] = 0x42; /* nicht mehr `mov rax,[rcx+disp8]` */
        check(!diff_decode(body, &td, &fo),
              "diff_decode: fremder Prolog -> 0");
        memcpy(body, RBBRIDGE_DIFF_GET_SIG, sizeof(body));
        body[6] = 0x99; /* unbekannter SSE-Opcode */
        check(!diff_decode(body, &td, &fo),
              "diff_decode: unbekannter Opcode -> 0");
        memcpy(body, RBBRIDGE_DIFF_GET_SIG, sizeof(body));
        body[7] = 0x90; /* kein movss-ModRM */
        check(!diff_decode(body, &td, &fo),
              "diff_decode: unbekanntes ModRM -> 0");
        check(!diff_decode(NULL, &td, &fo) &&
                  !diff_decode(RBBRIDGE_DIFF_GET_SIG, NULL, &fo) &&
                  !diff_decode(RBBRIDGE_DIFF_GET_SIG, &td, NULL),
              "diff_decode: NULL-Argumente -> 0");
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
    /* mission_flow_mode_ok (#447): nur "default" ist erlaubt           */
    /* -------------------------------------------------------------- */
    /* Verhalten: "default" -> erlaubt; leer/NULL -> erlaubt (wird im
     * Dispatch als "default" behandelt, Backward-Compat fuer alte Clients
     * ohne `mode`-Feld); alles andere -> abgelehnt (bad_mode), weil ein
     * Nicht-default-Modus die DLL-Pipe dauerhaft killt. */
    check(mission_flow_mode_ok("default") == 1,
          "mission_flow_mode_ok: \"default\" -> erlaubt");
    check(mission_flow_mode_ok("") == 1,
          "mission_flow_mode_ok: leer -> erlaubt (Default)");
    check(mission_flow_mode_ok(NULL) == 1,
          "mission_flow_mode_ok: NULL -> erlaubt (Default)");
    check(mission_flow_mode_ok("hard") == 0,
          "mission_flow_mode_ok: \"hard\" -> abgelehnt (bad_mode)");
    check(mission_flow_mode_ok("Default") == 0,
          "mission_flow_mode_ok: \"Default\" -> abgelehnt (case-sensitiv)");
    check(mission_flow_mode_ok("default ") == 0,
          "mission_flow_mode_ok: \"default \" -> abgelehnt (kein Trim)");

    /* -------------------------------------------------------------- */
    /* #386: Database-Payload-Resolver + Builder (AOB, kein Lua)        */
    /* -------------------------------------------------------------- */
    /* Frisches Image: das Haupt-`img` ist an dieser Stelle nicht mehr
     * garantiert in einer lesbaren Region (die frueheren Tests haben den
     * Scan-Puffer weiterverwendet). */
    {
        unsigned char *imgdb = build_image(1, 1, 1, 1, 1);
        ht_set_module(imgdb, IMG_SIZE);
        check(resolve_db_setstring_fn(imgdb, IMG_SIZE) == imgdb + DBSS_SIG_OFF,
              "resolve_db_setstring_fn: findet SetString-Signatur");
        check(resolve_db_getstring_fn(imgdb, IMG_SIZE) == imgdb + DBGS_SIG_OFF,
              "resolve_db_getstring_fn: findet GetString-Signatur");
        check(resolve_db_ctor_fn(imgdb, IMG_SIZE) == imgdb + DBCTOR_OFF,
              "resolve_db_ctor_fn: new-0x60-Anker -> Ctor-Prolog");
        free(imgdb);
    }

    /* Negativfaelle: ohne Signatur/Site -> NULL (kein Aufruf). */
    {
        unsigned char *img2 = build_image(0, 0, 0, 0, 0);
        ht_set_module(img2, IMG_SIZE);
        check(resolve_db_setstring_fn(img2, IMG_SIZE) == NULL,
              "resolve_db_setstring_fn: ohne Signatur -> NULL");
        check(resolve_db_getstring_fn(img2, IMG_SIZE) == NULL,
              "resolve_db_getstring_fn: ohne Signatur -> NULL");
        check(resolve_db_ctor_fn(img2, IMG_SIZE) == NULL,
              "resolve_db_ctor_fn: ohne Site -> NULL");
        free(img2);
    }

    /* build_database_payload / database_get_string liegen bewusst im
     * Nicht-Host-Block (sie rufen Spiel-Ctor/SetString/GetString); ihre
     * NULL-Guards sind dort Compile-Zeit-konstant. Der Host-Test deckt den
     * AOB-Auflösungspfad ab (oben) - der Builder-Aufruf selbst braucht das
     * echte Modul (Live-Test planet). */

    /* copy_cstr: harte Schranke, immer NUL-terminiert. */
    {
        char buf[8];
        copy_cstr(buf, sizeof(buf), "abcdefghij");
        check(strcmp(buf, "abcdefg") == 0,
              "copy_cstr: kuerzt auf n-1 + NUL");
        copy_cstr(buf, sizeof(buf), NULL);
        check(buf[0] == '\0', "copy_cstr: NULL -> leerer String");
    }

    /* -------------------------------------------------------------- */
    /* natural_waves (#476): op-Parser + Signatur-Selbstkontrolle      */
    /* -------------------------------------------------------------- */
    {
        check(natural_waves_op("status") == 0 &&
                  natural_waves_op("") == 0 &&
                  natural_waves_op(NULL) == 0,
              "natural_waves_op: status/leer/NULL -> 0");
        check(natural_waves_op("off") == 1,
              "natural_waves_op: off -> 1");
        check(natural_waves_op("on") == 2,
              "natural_waves_op: on -> 2");
        check(natural_waves_op("pause") == -1 &&
                  natural_waves_op("suspend") == -1 &&
                  natural_waves_op("OFF") == -1,
              "natural_waves_op: unbekannt/Grossschreibung -> -1");

        check(diffsys_sig_selfcheck() == 1,
              "diffsys_sig_selfcheck: Laenge/Maske/Wildcards konsistent");

        /* Aenderung an einem FESTEN Byte -> kein Treffer mehr. */
        {
            unsigned char body[sizeof(RBBRIDGE_DIFFSYS_GET_SIG)];
            memcpy(body, RBBRIDGE_DIFFSYS_GET_SIG, sizeof(body));
            body[17] = 0x00; /* TypeHash-Byte veraendert */
            check(!sig_matches(body, RBBRIDGE_DIFFSYS_GET_SIG,
                               RBBRIDGE_DIFFSYS_GET_SIG_MASK,
                               sizeof(body)),
                  "natural_waves-Sig: festes Byte geaendert -> kein Treffer");

            /* Aenderung am E8-rel32-Wildcard -> Treffer bleibt. */
            memcpy(body, RBBRIDGE_DIFFSYS_GET_SIG, sizeof(body));
            body[24] = 0xAA;
            check(sig_matches(body, RBBRIDGE_DIFFSYS_GET_SIG,
                              RBBRIDGE_DIFFSYS_GET_SIG_MASK,
                              sizeof(body)),
                  "natural_waves-Sig: rel32-Wildcard toleriert");

            /* E8-Opcode selbst ist Pflicht. */
            memcpy(body, RBBRIDGE_DIFFSYS_GET_SIG, sizeof(body));
            body[22] = 0x90;
            check(!sig_matches(body, RBBRIDGE_DIFFSYS_GET_SIG,
                               RBBRIDGE_DIFFSYS_GET_SIG_MASK,
                               sizeof(body)),
                  "natural_waves-Sig: E8-Opcode Pflicht -> kein Treffer");
        }
    }

    /* #479: Readiness-Gate — reiner Log-Marker-Test (host-testbar)     */
    /* -------------------------------------------------------------- */
    {
        /* Erfolgreicher Boot: NavigationGraph-Marker vorhanden. */
        const char *ok_log =
            "[12:40:33.501] [info] MapGenerator.cpp:828 - InstantiateMap took: 4846 ms\n"
            "[12:40:34.154] [info] NavigationGraph.cpp:462 - "
            "NavigationGraph::Generate - Graph generated in 0.595806 sec.\n";
        check(rbbridge_log_is_ready(ok_log, strlen(ok_log)) == 1,
              "readiness: erfolgreicher Boot (Graph generated) -> bereit");

        /* Gecrashter Boot (#479): Crash VOR Map-Fertigstellung, kein Marker. */
        const char *crash_log =
            "[13:39:45.677] [info] MapGenerator.cpp:976 - ExecuteBuffers took: 48 ms\n"
            "[13:39:46.763] [critical] CrashHandlerWin32.cpp:103 - CRASH\n";
        check(rbbridge_log_is_ready(crash_log, strlen(crash_log)) == 0,
              "readiness: Crash-Boot ohne Marker -> NICHT bereit");

        /* Nur der MapGenerator-Marker (Fallback) genuegt ebenfalls. */
        const char *map_only =
            "[12:40:33.501] [info] MapGenerator.cpp:828 - InstantiateMap took: 4846 ms\n";
        check(rbbridge_log_is_ready(map_only, strlen(map_only)) == 1,
              "readiness: nur InstantiateMap-Marker -> bereit");

        /* Leer/fehlend -> konservativ NICHT bereit. */
        check(rbbridge_log_is_ready("", 0) == 0,
              "readiness: leerer Puffer -> NICHT bereit");
        check(rbbridge_log_is_ready(NULL, 100) == 0,
              "readiness: NULL -> NICHT bereit");

        /* Marker NICHT ueber die Puffergrenze hinaus suchen (kein Treffer
         * bei abgeschnittenem Marker) -> kein Read-Overrun, 0. */
        const char *partial =
            "NavigationGraph::Generate - Graph gene";
        check(rbbridge_log_is_ready(partial, strlen(partial)) == 0,
              "readiness: abgeschnittener Marker -> NICHT bereit");

        /* Substring-Helper: Ende exakt an der Puffergrenze. */
        const char *tail = "xx Graph generated";
        check(rbbridge_buf_contains(tail, strlen(tail),
                                    "Graph generated") == 1,
              "buf_contains: Treffer bis exakt Pufferende");
        check(rbbridge_buf_contains(tail, 9, "Graph generated") == 0,
              "buf_contains: Treffer hinter Pufferende ignoriert");
    }

    /* -------------------------------------------------------------- */
    /* #520: pause_dom/resume_dom (SetSuspended nativ)                 */
    /* -------------------------------------------------------------- */
    {
        /* FNV-1a-32: bekannte Vektoren + der auf planet live verifizierte
         * TypeHash des DOM-Skriptpfads (LuaGraphNode::+0x30). */
        check(rbbridge_fnv1a32("") == 0x811c9dc5u,
              "fnv1a32: leerer String -> Offset-Basis");
        check(rbbridge_fnv1a32("a") == 0xe40c292cu,
              "fnv1a32: 'a' -> 0xe40c292c");
        check(rbbridge_fnv1a32(RBBRIDGE_DOM_SCRIPT) == 0x76aad119u,
              "fnv1a32: DOM-Skriptpfad -> TypeHash 0x76aad119 (#520)");
        check(rbbridge_fnv1a32(RBBRIDGE_DOM_SCRIPT) == RBBRIDGE_DOM_SCRIPT_HASH,
              "fnv1a32: Konstante == Laufzeit-Hash (Resolver-Determinismus)");

        /* Signatur-Selfcheck: Laenge/Ret-Opcode/Flag-Offset konsistent. */
        check(set_suspended_sig_selfcheck() == 1,
              "SetSuspended-Sig: Selfcheck gruen (+0xF1, 0xC3, Hash)");
    }

    /* resolve_set_suspended_fn: genau ein Treffer -> Adresse; zwei Treffer
     * (mehrdeutig) -> NULL (kein Aufruf) — analog zum Live-Gegencheck. */
    {
        unsigned char *si = build_image(0, 0, 0, 0, 0);
        ht_set_module(si, IMG_SIZE);
        check(resolve_set_suspended_fn(si, IMG_SIZE) == NULL,
              "SetSuspended-AOB: ohne Muster -> NULL (graceful)");
        memcpy(si + 0x1380, RBBRIDGE_SET_SUSPENDED_SIG,
               sizeof(RBBRIDGE_SET_SUSPENDED_SIG));
        check(resolve_set_suspended_fn(si, IMG_SIZE) == si + 0x1380,
              "SetSuspended-AOB: genau 1 Treffer -> Adresse");
        memcpy(si + 0x1390, RBBRIDGE_SET_SUSPENDED_SIG,
               sizeof(RBBRIDGE_SET_SUSPENDED_SIG));
        check(resolve_set_suspended_fn(si, IMG_SIZE) == NULL,
              "SetSuspended-AOB: 2 Treffer -> NULL (mehrdeutig, kein Aufruf)");
        free(si);
    }

    /* resolve_dom_node: vftable-Scan + TypeHash-Filter. */
    {
        unsigned char *ni = build_image(0, 0, 0, 0, 0);
        const uint32_t dom_hash = rbbridge_fnv1a32(RBBRIDGE_DOM_SCRIPT);
        const uint64_t vf =
            (uint64_t)(uintptr_t)(ni + RBBRIDGE_LUAGRAPHNODE_VFTABLE_RVA);

        ht_set_module(ni, IMG_SIZE);
        check(resolve_dom_node(ni) == NULL,
              "dom_node: leeres Image -> NULL (graceful)");

        /* Fremder LuaGraphNode (falscher TypeHash) -> verworfen. */
        wr64(ni + 0x1900, vf);
        wr64(ni + 0x1900 + RBBRIDGE_LUAGRAPHNODE_L_OFF, 0x1234u);
        wr32(ni + 0x1900 + RBBRIDGE_LUAGRAPHNODE_REF_OFF, 5u);
        wr32(ni + 0x1900 + RBBRIDGE_LUAGRAPHNODE_TYPEHASH_OFF, 0xdeadbeefu);
        check(resolve_dom_node(ni) == NULL,
              "dom_node: falscher TypeHash -> NULL (Pool/Mission-Node)");

        /* dom_mananger-Node (TypeHash == fnv1a(DOM-Skript)) -> Treffer. */
        wr32(ni + 0x1900 + RBBRIDGE_LUAGRAPHNODE_TYPEHASH_OFF, dom_hash);
        check(resolve_dom_node(ni) == ni + 0x1900,
              "dom_node: TypeHash == fnv1a(DOM-Skript) -> instanz gefunden");

        /* Fehlender luabind-Objekt-Zeiger (L == 0) -> verworfen. */
        wr64(ni + 0x1900 + RBBRIDGE_LUAGRAPHNODE_L_OFF, 0u);
        check(resolve_dom_node(ni) == NULL,
              "dom_node: L == NULL -> NULL (kein luabind-Objekt)");
        free(ni);
    }

    free(img);

    printf("HOSTTEST_PASS=%d HOSTTEST_FAIL=%d\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
