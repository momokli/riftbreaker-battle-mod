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
#define TEXT_VSIZE  0xA00

#define SIG_OFF     0x1100 /* ExecuteCommand-Signatur in .text        */
#define DGET_SIG_OFF 0x1180 /* CampaignService difficulty-Get-Signatur  */
#define DSET_SIG_OFF 0x11A0 /* CampaignService difficulty-Set-Signatur  */
#define DINC_SIG_OFF 0x11C0 /* CampaignService difficulty-Inc-Signatur  */
#define DDEC_SIG_OFF 0x11E0 /* CampaignService difficulty-Dec-Signatur  */
#define ACT_SIG_OFF 0x1300 /* ActivateMissionFlow-Signatur in .text   */
#define CONNP_OFF   0x1420 /* #512 GetConnectedPlayers-AOB (114 B)    */
#define CONNP2_OFF  0x1710 /* #512: 2. Kopie (Mehrdeutigkeits-Test)   */
#define DEACT_SIG_OFF 0x1340 /* DeactivateMissionFlow-Signatur (#389)  */
#define DBSS_SIG_OFF 0x1240 /* Database::SetString-Signatur (#386)     */
#define DBGS_SIG_OFF 0x1260 /* Database::GetString-Signatur (#386)     */
#define DBCTOR_OFF   0x1280 /* Database::Database()-Prolog (#386)      */
#define NEWDB_OFF    0x12A0 /* `new Database`-Call-Site (#386)         */
#define HQFN_SIG_OFF 0x1800 /* #573 FindEntityByName-AOB (152 B)       */
#define HQGH_SIG_OFF 0x18A0 /* #573 GetHealth-AOB                      */
#define HQGM_SIG_OFF 0x18F0 /* #573 GetMaxHealth-AOB                   */
#define NAME_OFF    0x1400 /* RTTI-Namensstring                       */
#define COL_OFF     0x1500 /* CompleteObjectLocator                   */
#define VFT_REF_OFF 0x15F8 /* QWORD == base+COL_OFF (vftable-8)       */
#define VFT_OFF     0x1600 /* vftable-Start (= VFT_REF_OFF + 8)       */
#define INST_OFF    0x1700 /* QWORD == base+VFT_OFF (Instanz)         */
#define PAT_OFF     0x1A00 /* freies Byte-Muster fuer scan_bytes      */

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
        /* #512: GetConnectedPlayers-AOB (nur Scan, rel32 nicht dekodiert). */
        memcpy(img + CONNP_OFF, RBBRIDGE_CONNPLAYERS_SIG,
               sizeof(RBBRIDGE_CONNPLAYERS_SIG));
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
        /* #573: HQ-Signaturen (FindEntityByName + GetHealth/GetMaxHealth). */
        memcpy(img + HQFN_SIG_OFF, RBBRIDGE_HQ_FINDNAME_SIG,
               sizeof(RBBRIDGE_HQ_FINDNAME_SIG));
        memcpy(img + HQGH_SIG_OFF, RBBRIDGE_HQ_GETHEALTH_SIG,
               sizeof(RBBRIDGE_HQ_GETHEALTH_SIG));
        memcpy(img + HQGM_SIG_OFF, RBBRIDGE_HQ_GETMAXHEALTH_SIG,
               sizeof(RBBRIDGE_HQ_GETMAXHEALTH_SIG));
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


/* --- #573: Stubs fuer hq_health_from_calls (reine Host-Logik) -------- */
static uint32_t g_hq_stub_entity;
static int g_hq_stub_find_calls;
static int g_hq_stub_get_calls;
static int g_hq_stub_getmax_calls;
static float g_hq_stub_hp;
static float g_hq_stub_hpmax;
static char g_hq_stub_name[32];

static uint32_t hq_stub_find(void *self, const char *name)
{
    (void)self;
    g_hq_stub_find_calls++;
    if (name) {
        strncpy(g_hq_stub_name, name, sizeof(g_hq_stub_name) - 1);
        g_hq_stub_name[sizeof(g_hq_stub_name) - 1] = '\0';
    }
    return g_hq_stub_entity;
}

static float hq_stub_get(void *self, uint32_t entity)
{
    (void)self;
    (void)entity;
    g_hq_stub_get_calls++;
    return g_hq_stub_hp;
}

static float hq_stub_getmax(void *self, uint32_t entity)
{
    (void)self;
    (void)entity;
    g_hq_stub_getmax_calls++;
    return g_hq_stub_hpmax;
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

    /* -------------------------------------------------------------- */
    /* #512: Spielerzahl — AOB-Resolver + Vektor-Count (host-testbar)  */
    /* -------------------------------------------------------------- */
    {
        const unsigned char *fn = NULL;

        /* Positiv: genau EIN Treffer im .text -> aufrufbar. */
        ht_set_module(img, IMG_SIZE);
        check(connplayers_resolve(img, IMG_SIZE, &fn) == 1 &&
                  fn == img + CONNP_OFF,
              "connplayers_resolve: eindeutiger AOB-Treffer -> fn");

        /* Negativ: AOB fehlt -> 0/NULL (kein Aufruf). */
        {
            unsigned char *img_x = build_image(0, 1, 1, 1, 1);
            ht_set_module(img_x, IMG_SIZE);
            fn = (const unsigned char *)0x1;
            check(connplayers_resolve(img_x, IMG_SIZE, &fn) == 0 && !fn,
                  "connplayers_resolve: kein Treffer -> 0/NULL (kein Aufruf)");
            free(img_x);
        }

        /* Negativ: zweiter Treffer -> mehrdeutig -> kein Aufruf. */
        {
            unsigned char *img_y = build_image(1, 1, 1, 1, 1);
            memcpy(img_y + CONNP2_OFF, RBBRIDGE_CONNPLAYERS_SIG,
                   sizeof(RBBRIDGE_CONNPLAYERS_SIG));
            ht_set_module(img_y, IMG_SIZE);
            check(connplayers_resolve(img_y, IMG_SIZE, &fn) == 0,
                  "connplayers_resolve: 2 Treffer -> mehrdeutig -> 0");
            free(img_y);
        }

        /* Vektor-Layout (Disasm-belegt): count = vec[+0x10]. */
        {
            unsigned char vec[0x20];
            int n = -1;
            memset(vec, 0xAB, sizeof(vec));
            wr64(vec + 0x10, 3);
            check(connplayers_count_from_vec(vec, &n) == 1 && n == 3,
                  "connplayers_count_from_vec: vec[+0x10] == count");

            wr64(vec + 0x10, 0);
            check(connplayers_count_from_vec(vec, &n) == 1 && n == 0,
                  "connplayers_count_from_vec: 0 Spieler (leerer Server)");

            wr64(vec + 0x10, (uint64_t)RBBRIDGE_CONNPLAYERS_MAX + 1);
            check(connplayers_count_from_vec(vec, &n) == 0,
                  "connplayers_count_from_vec: > MAX -> 0 (Muell)");

            check(connplayers_count_from_vec(NULL, &n) == 0,
                  "connplayers_count_from_vec: NULL -> 0");
        }

        /* Sig-Selbstkontrolle: Laenge == Maske, 3 E8-Opcodes Pflicht,
         * 15 Wildcards (3x E8-rel32 = 12 + 3 rel8). */
        {
            size_t i, wild = 0, e8 = 0;
            check(sizeof(RBBRIDGE_CONNPLAYERS_SIG) ==
                      sizeof(RBBRIDGE_CONNPLAYERS_SIG_MASK),
                  "connplayers-Sig: Laenge == Maske");
            for (i = 0; i < sizeof(RBBRIDGE_CONNPLAYERS_SIG); i++) {
                if (RBBRIDGE_CONNPLAYERS_SIG_MASK[i] == 0x00)
                    wild++;
                if (RBBRIDGE_CONNPLAYERS_SIG[i] == 0xE8 &&
                    RBBRIDGE_CONNPLAYERS_SIG_MASK[i] == 0xFF)
                    e8++;
            }
            check(wild == 15 && e8 == 3,
                  "connplayers-Sig: 15 Wildcards (3x E8-rel32 + 3 rel8), "
                  "3 E8-Opcodes Pflicht");
        }

        /* Review PR #524: vtable-Slot (+0x10) NICHT roh dereferenzieren.
         * Disasm 0x26F340 = ZWEI Indirektionen (vptr -> Slot); der Slot
         * wird per safe_read_u64 gelesen und auf 0 + Modulbereich
         * geprueft -> kein Blind-Call. */
        {
            unsigned char *mod = (unsigned char *)calloc(1, 0x200);
            unsigned char *alloc_obj = mod + 0x40; /* vec[+0x00] */
            unsigned char *vtable = mod + 0x100;   /* vptr -> Slot +0x10 */
            uintptr_t fnaddr = (uintptr_t)(mod + 0x180);
            uintptr_t out = 0;

            check(mod != NULL, "connplayers_dealloc_target: Testbuffer");

            /* Korrekt: alloc->vptr = vtable; vtable[+0x10] = fnaddr. */
            wr64(alloc_obj, (uint64_t)(uintptr_t)vtable);
            wr64(vtable + 0x10, (uint64_t)fnaddr);
            check(connplayers_dealloc_target(alloc_obj, mod, 0x200, &out) == 1 &&
                      out == fnaddr,
                  "connplayers_dealloc_target: vptr->Slot+0x10 -> Ziel");

            /* Slot 0 -> kein Aufruf. */
            wr64(vtable + 0x10, 0);
            out = 0;
            check(connplayers_dealloc_target(alloc_obj, mod, 0x200, &out) == 0 &&
                      out == 0,
                  "connplayers_dealloc_target: Slot 0 -> kein Aufruf");

            /* Ziel ausserhalb des Modulbereichs -> kein Aufruf. */
            wr64(vtable + 0x10, (uint64_t)(uintptr_t)(mod + 0x10000));
            check(connplayers_dealloc_target(alloc_obj, mod, 0x200, &out) == 0,
                  "connplayers_dealloc_target: Ziel ausserhalb Modul -> kein "
                  "Aufruf");

            /* vptr 0 (abgeraeumtes Objekt) -> kein Aufruf. */
            wr64(alloc_obj, 0);
            check(connplayers_dealloc_target(alloc_obj, mod, 0x200, &out) == 0,
                  "connplayers_dealloc_target: vptr 0 -> kein Aufruf");

            /* NULL-Objekt -> kein Aufruf. */
            check(connplayers_dealloc_target(NULL, mod, 0x200, &out) == 0,
                  "connplayers_dealloc_target: NULL -> kein Aufruf");

            free(mod);
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

    /* ---- #516 nativer Round-Reset: reine Decoder/Finder ---- */
    {
        const unsigned char body[8] = {0xC6, 0x81, 0x2A, 0x05, 0x00, 0x00,
                                       0x01, 0xC3};
        const unsigned char bad1[8] = {0xC6, 0x81, 0x2A, 0x05, 0x00, 0x00,
                                       0x00, 0xC3}; /* imm != 1 */
        const unsigned char bad2[8] = {0xC7, 0x81, 0x2A, 0x05, 0x00, 0x00,
                                       0x01, 0xC3}; /* opcode */
        const unsigned char zero[8] = {0xC6, 0x81, 0x00, 0x00, 0x00, 0x00,
                                       0x01, 0xC3};
        uint32_t off = 0;

        check(restart_decode(body, &off) == 1 && off == 0x52Au,
              "#516 restart_decode: mov byte [rcx+0x52a],1 -> off 0x52a");
        check(restart_decode(bad1, &off) == 0,
              "#516 restart_decode: falsches imm -> 0");
        check(restart_decode(bad2, &off) == 0,
              "#516 restart_decode: falscher Opcode -> 0");
        check(restart_decode(NULL, &off) == 0,
              "#516 restart_decode: NULL -> 0 (kein Crash)");
        check(restart_decode(zero, &off) == 0,
              "#516 restart_decode: Offset 0 -> 0 (verworfen)");

        {
            unsigned char cons[30] = {
                0x41, 0x80, 0xBE, 0x2A, 0x05, 0x00, 0x00, 0x00, 0x74, 0x14,
                0x49, 0x8B, 0x06, 0x49, 0x8B, 0xCE, 0xFF, 0x90, 0x90, 0x00,
                0x00, 0x00, 0x41, 0xC6, 0x86, 0x2A, 0x05, 0x00, 0x00, 0x00
            };
            uint32_t slot = 0;
            check(restart_decode_consumer(cons, &off, &slot) == 1 &&
                      off == 0x52Au && slot == 0x90u,
                  "#516 restart_decode_consumer: off 0x52a + slot 0x90");
            cons[25] = 0x2B; /* 2. disp32 abweichend -> inkonsistent */
            check(restart_decode_consumer(cons, &off, &slot) == 0,
                  "#516 restart_decode_consumer: disp32 ungleich -> 0");
            check(restart_decode_consumer(NULL, &off, &slot) == 0,
                  "#516 restart_decode_consumer: NULL -> 0 (kein Crash)");
        }

        {
            unsigned char buf[0x200];
            uintptr_t fnv = 0x11223344u;
            uintptr_t out[4] = {0, 0, 0, 0};
            uintptr_t vt2 = 0;
            memset(buf, 0, sizeof(buf));
            memcpy(buf + 0x40 + 0x20, &fnv, sizeof(fnv)); /* vtable @0x40 */
            memcpy(buf + 0x100 + 0x20, &fnv, sizeof(fnv)); /* vtable @0x100 */
            vt2 = (uintptr_t)(buf + 0x100);
            check(restart_find_vtables(buf, sizeof(buf), fnv, out, 4) == 2 &&
                      out[0] == (uintptr_t)(buf + 0x40) && out[1] == vt2,
                  "#516 restart_find_vtables: 2 vtables abgeleitet");
            check(restart_find_vtables(buf, sizeof(buf), fnv, out, 1) == 1 &&
                      out[0] == (uintptr_t)(buf + 0x40),
                  "#516 restart_find_vtables: out_cap begrenzt");
            check(restart_find_vtables(buf, sizeof(buf), 0xDEADBEEFu, out,
                                       4) == 0,
                  "#516 restart_find_vtables: kein Treffer -> 0");
            check(restart_find_vtables(NULL, sizeof(buf), fnv, out, 4) == 0,
                  "#516 restart_find_vtables: NULL -> 0 (kein Crash)");
        }

        {
            unsigned char txt[64];
            memset(txt, 0x90, sizeof(txt));
            memcpy(txt + 0x00, "\xC6\x81\x11\x00\x00\x00\x01\xC3", 8);
            memcpy(txt + 0x10, "\xC6\x81\x2A\x05\x00\x00\x01\xC3", 8);
            check(restart_find_setter(txt, sizeof(txt), 0x52Au) == txt + 0x10,
                  "#516 restart_find_setter: passender Offset gewaehlt");
            check(restart_find_setter(txt, sizeof(txt), 0x11u) == txt + 0x00,
                  "#516 restart_find_setter: erster Kandidat bei 0x11");
            check(restart_find_setter(txt, sizeof(txt), 0x99u) == NULL,
                  "#516 restart_find_setter: kein passender -> NULL "
                  "(graceful)");
            check(restart_find_setter(NULL, sizeof(txt), 0x52Au) == NULL,
                  "#516 restart_find_setter: NULL -> NULL (kein Crash)");
        }

        /* #516-Review: die Beschreibbarkeits-Pruefung ist rein und damit
         * host-testbar (restart_scan_instance/restart_write_u8 nutzen sie). */
        {
            MEMORY_BASIC_INFORMATION mi;
            memset(&mi, 0, sizeof(mi));
            mi.State = MEM_COMMIT;

            mi.Protect = PAGE_READWRITE;
            check(is_writable_region(&mi) == 1,
                  "#516 is_writable_region: READWRITE -> 1");
            mi.Protect = PAGE_WRITECOPY;
            check(is_writable_region(&mi) == 1,
                  "#516 is_writable_region: WRITECOPY -> 1");
            mi.Protect = PAGE_EXECUTE_READWRITE;
            check(is_writable_region(&mi) == 1,
                  "#516 is_writable_region: EXECUTE_READWRITE -> 1");
            mi.Protect = PAGE_EXECUTE_WRITECOPY;
            check(is_writable_region(&mi) == 1,
                  "#516 is_writable_region: EXECUTE_WRITECOPY -> 1");
            mi.Protect = PAGE_READONLY;
            check(is_writable_region(&mi) == 0,
                  "#516 is_writable_region: READONLY -> 0 (kein Stray-Write)");
            mi.Protect = PAGE_EXECUTE_READ;
            check(is_writable_region(&mi) == 0,
                  "#516 is_writable_region: EXECUTE_READ -> 0");
            mi.Protect = PAGE_READWRITE | PAGE_GUARD;
            check(is_writable_region(&mi) == 0,
                  "#516 is_writable_region: PAGE_GUARD -> 0");
            mi.Protect = PAGE_READWRITE;
            mi.State = MEM_FREE;
            check(is_writable_region(&mi) == 0,
                  "#516 is_writable_region: MEM_FREE -> 0");
        }
    }


    /* -------------------------------------------------------------- */
    /* HQ-Health-AOBs (Issue #573/#511)                                */
    /* -------------------------------------------------------------- */
    check(sizeof(RBBRIDGE_HQ_FINDNAME_SIG) == 152 &&
              sizeof(RBBRIDGE_HQ_FINDNAME_SIG) ==
                  sizeof(RBBRIDGE_HQ_FINDNAME_SIG_MASK),
          "HQ-FindEntityByName-AOB: 152 B + Maske gleich lang (#573)");
    check(sizeof(RBBRIDGE_HQ_GETHEALTH_SIG) ==
              sizeof(RBBRIDGE_HQ_GETHEALTH_SIG_MASK) &&
              sizeof(RBBRIDGE_HQ_GETMAXHEALTH_SIG) ==
                  sizeof(RBBRIDGE_HQ_GETMAXHEALTH_SIG_MASK),
          "HQ-Get(Health|MaxHealth)-AOB: Signal/Maske gleich lang (#573)");

    ht_set_module(img, IMG_SIZE);
    check(scan_bytes_mask(img + TEXT_RVA, TEXT_VSIZE,
                     RBBRIDGE_HQ_FINDNAME_SIG,
                     RBBRIDGE_HQ_FINDNAME_SIG_MASK,
                     sizeof(RBBRIDGE_HQ_FINDNAME_SIG)) == img + HQFN_SIG_OFF,
          "HQ-FindEntityByName-AOB im .text gefunden (#573)");
    check(scan_bytes_mask(img + TEXT_RVA, TEXT_VSIZE, RBBRIDGE_HQ_GETHEALTH_SIG,
                     RBBRIDGE_HQ_GETHEALTH_SIG_MASK,
                     sizeof(RBBRIDGE_HQ_GETHEALTH_SIG)) == img + HQGH_SIG_OFF,
          "HQ-GetHealth-AOB im .text gefunden (#573)");
    check(scan_bytes_mask(img + TEXT_RVA, TEXT_VSIZE, RBBRIDGE_HQ_GETMAXHEALTH_SIG,
                     RBBRIDGE_HQ_GETMAXHEALTH_SIG_MASK,
                     sizeof(RBBRIDGE_HQ_GETMAXHEALTH_SIG)) == img + HQGM_SIG_OFF,
          "HQ-GetMaxHealth-AOB im .text gefunden (#573)");

    /* GetHealth/GetMaxHealth teilen sich den Prolog -> die Signaturen MUESSEN
     * sich gegenseitig ausschliessen, sonst trifft die falsche Funktion. */
    {
        unsigned char *imgA = build_image(1, 1, 1, 1, 1);
        memset(imgA + HQFN_SIG_OFF, 0, 0x180); /* HQ-Bereich leeren */
        memcpy(imgA + HQGH_SIG_OFF, RBBRIDGE_HQ_GETMAXHEALTH_SIG,
               sizeof(RBBRIDGE_HQ_GETMAXHEALTH_SIG));
        ht_set_module(imgA, IMG_SIZE);
        check(scan_bytes_mask(imgA + TEXT_RVA, TEXT_VSIZE,
                         RBBRIDGE_HQ_GETHEALTH_SIG,
                         RBBRIDGE_HQ_GETHEALTH_SIG_MASK,
                         sizeof(RBBRIDGE_HQ_GETHEALTH_SIG)) == NULL,
              "HQ-GetHealth-AOB: kein Treffer in GetMaxHealth-Bytes (#573)");
        check(scan_bytes_mask(imgA + TEXT_RVA, TEXT_VSIZE,
                         RBBRIDGE_HQ_GETMAXHEALTH_SIG,
                         RBBRIDGE_HQ_GETMAXHEALTH_SIG_MASK,
                         sizeof(RBBRIDGE_HQ_GETMAXHEALTH_SIG))
                  == imgA + HQGH_SIG_OFF,
              "HQ-GetMaxHealth-AOB: disambiguierendes Tail trifft (#573)");
        free(imgA);
    }
    {
        unsigned char *imgB = build_image(1, 1, 1, 1, 1);
        memset(imgB + HQFN_SIG_OFF, 0, 0x180);
        memcpy(imgB + HQGH_SIG_OFF, RBBRIDGE_HQ_GETHEALTH_SIG,
               sizeof(RBBRIDGE_HQ_GETHEALTH_SIG));
        ht_set_module(imgB, IMG_SIZE);
        check(scan_bytes_mask(imgB + TEXT_RVA, TEXT_VSIZE,
                         RBBRIDGE_HQ_GETMAXHEALTH_SIG,
                         RBBRIDGE_HQ_GETMAXHEALTH_SIG_MASK,
                         sizeof(RBBRIDGE_HQ_GETMAXHEALTH_SIG)) == NULL,
              "HQ-GetMaxHealth-AOB: kein Treffer in GetHealth-Bytes (#573)");
        free(imgB);
    }
    {
        unsigned char *imgC = build_image(1, 1, 1, 1, 1);
        memset(imgC + HQFN_SIG_OFF, 0, 0x180); /* Signaturen fehlen */
        ht_set_module(imgC, IMG_SIZE);
        check(scan_bytes_mask(imgC + TEXT_RVA, TEXT_VSIZE,
                         RBBRIDGE_HQ_FINDNAME_SIG,
                         RBBRIDGE_HQ_FINDNAME_SIG_MASK,
                         sizeof(RBBRIDGE_HQ_FINDNAME_SIG)) == NULL,
              "HQ-FindEntityByName-AOB: fehlend -> kein Treffer (#573)");
        check(scan_bytes_mask(imgC + TEXT_RVA, TEXT_VSIZE,
                         RBBRIDGE_HQ_GETHEALTH_SIG,
                         RBBRIDGE_HQ_GETHEALTH_SIG_MASK,
                         sizeof(RBBRIDGE_HQ_GETHEALTH_SIG)) == NULL,
              "HQ-GetHealth-AOB: fehlend -> kein Treffer (#573)");
        free(imgC);
    }

    /* -------------------------------------------------------------- */
    /* HQ-Core-Logik (hq_health_from_calls, Issue #573/#511)           */
    /* -------------------------------------------------------------- */
    {
        float hp = -1.0f, hpmax = -1.0f;
        int dead = -1;
        hq_dead_state_t st = {0, 0.0f};

        g_hq_stub_entity = 0x1234u;
        g_hq_stub_hp = 850.5f;
        g_hq_stub_hpmax = 1000.0f;
        g_hq_stub_find_calls = g_hq_stub_get_calls = g_hq_stub_getmax_calls = 0;
        g_hq_stub_name[0] = '\0';
        check(hq_health_from_calls((void *)1, (void *)2, hq_stub_find,
                                   hq_stub_get, hq_stub_getmax, &st,
                                   &hp, &hpmax, &dead) == 1 &&
                  hp == 850.5f && hpmax == 1000.0f && dead == 0 &&
                  strcmp(g_hq_stub_name, "headquarters") == 0 &&
                  g_hq_stub_find_calls == 1 && g_hq_stub_get_calls == 1 &&
                  g_hq_stub_getmax_calls == 1,
              "hq core: HQ per Namen gefunden -> hp/hp_max/dead (#573)");

        /* #573-KERNFALL: HQ noch NICHT gebaut (Entity INVALID_ID) -> der
         * Health-Read laeuft GAR NICHT (kein Off-Thread-ECS-Zugriff, kein
         * Crash), Ergebnis `nicht verfuegbar` -> null. */
        g_hq_stub_entity = RBBRIDGE_HQ_INVALID_ENTITY;
        g_hq_stub_get_calls = g_hq_stub_getmax_calls = 0;
        hp = -1.0f; hpmax = -1.0f; dead = -1;
        check(hq_health_from_calls((void *)1, (void *)2, hq_stub_find,
                                   hq_stub_get, hq_stub_getmax,
                                   &(hq_dead_state_t){0, 0.0f},
                                   &hp, &hpmax, &dead) == 0 &&
                  g_hq_stub_get_calls == 0 && g_hq_stub_getmax_calls == 0 &&
                  hp == -1.0f,
              "hq core: kein HQ -> kein Health-Call, graceful 0 (#573)");

        /* #573-KERNFALL 2: Entity existiert, Health-Component aber (noch)
         * nicht -> GetHealth/GetMax liefern 0/0 -> NICHT als 'tot' ausgeben,
         * sondern 'nicht verfuegbar' (0) -> null. */
        g_hq_stub_entity = 0x1234u;
        g_hq_stub_hp = 0.0f;
        g_hq_stub_hpmax = 0.0f;
        check(hq_health_from_calls((void *)1, (void *)2, hq_stub_find,
                                   hq_stub_get, hq_stub_getmax,
                                   &(hq_dead_state_t){0, 0.0f},
                                   &hp, &hpmax, &dead) == 0,
              "hq core: Component fehlt (0/0) -> 0, kein falsches dead (#573)");

        /* HP 0 bei hp_max > 0 -> tot (Interface-Konvention hp <= 0). */
        g_hq_stub_hp = 0.0f;
        g_hq_stub_hpmax = 1000.0f;
        check(hq_health_from_calls((void *)1, (void *)2, hq_stub_find,
                                   hq_stub_get, hq_stub_getmax, &st,
                                   &hp, &hpmax, &dead) == 1 && dead == 1,
              "hq core: hp==0 (max>0) -> dead=true (#573)");

        /* INVALID_ID MIT HQ-Vorgeschichte -> zerstoert (Entity verschwunden =
         * tot, hp 0, hp_max letzter bekannter Wert), kein Health-Call. */
        g_hq_stub_entity = RBBRIDGE_HQ_INVALID_ENTITY;
        g_hq_stub_get_calls = g_hq_stub_getmax_calls = 0;
        st.seen_alive = 1;
        st.last_hp_max = 1000.0f;
        hp = -1.0f; hpmax = -1.0f; dead = -1;
        check(hq_health_from_calls((void *)1, (void *)2, hq_stub_find,
                                   hq_stub_get, hq_stub_getmax, &st,
                                   &hp, &hpmax, &dead) == 1 &&
                  hp == 0.0f && hpmax == 1000.0f && dead == 1 &&
                  g_hq_stub_get_calls == 0 && g_hq_stub_getmax_calls == 0,
              "hq core: Entity weg nach HQ-Leben -> dead=true, kein Call (#573)");

        /* HQ taucht wieder auf (Map-/Welt-Reload) -> Latch heilt sich. */
        g_hq_stub_entity = 0x1234u;
        g_hq_stub_hp = 500.0f;
        g_hq_stub_hpmax = 1000.0f;
        check(hq_health_from_calls((void *)1, (void *)2, hq_stub_find,
                                   hq_stub_get, hq_stub_getmax, &st,
                                   &hp, &hpmax, &dead) == 1 &&
                  hp == 500.0f && dead == 0 && st.last_hp_max == 1000.0f,
              "hq core: HQ wieder da -> dead=false (Latch heilt) (#573)");

        /* Unvollstaendige Aufloesung -> 0, kein Call, kein Crash. */
        check(hq_health_from_calls(NULL, (void *)2, hq_stub_find, hq_stub_get,
                                   hq_stub_getmax, &st, &hp, &hpmax,
                                   &dead) == 0,
              "hq core: find_svc NULL -> 0 (graceful) (#573)");
        check(hq_health_from_calls((void *)1, (void *)2, NULL, hq_stub_get,
                                   hq_stub_getmax, &st, &hp, &hpmax,
                                   &dead) == 0,
              "hq core: find_fn NULL -> 0 (graceful) (#573)");
    }

    free(img);

    printf("HOSTTEST_PASS=%d HOSTTEST_FAIL=%d\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
