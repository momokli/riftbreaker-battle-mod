# Dedicated IO Interface — RE-Befunde (Build 2.0.58485)

Live-RE-Stand für den **READ-OUT**-Pfad des Dedicated IO Interface (Issue #363).
Ergänzt `docs/research/pdb-symbol-validation.md` (Issue #364) um die konkreten
Disasm-Befunde. Stand: 2026-09-13, auf `planet` gegen
`/srv/rbgame/bin/riftbreaker_dll_win_release.dll` + `.pdb` (Build 2.0.58485 /
GOG == Dedi) verifiziert.

## Namespace-Korrektur

Die Spiel-Objekte liegen im Namespace **`Riftbreaker`**, nicht `Exor` (das ist
nur die Engine: `Exor::Vector`, `Exor::TypeRegistry`, `Exor::UtfString`, …):

- `Riftbreaker::PlayerService`
- `Riftbreaker::ResourceAccount`
- `Riftbreaker::ResourceBasket`

Relevante public Symbole (aus `llvm-pdbutil dump -publics`, mit RVAs):

| Symbol | RVA |
|---|---|
| `Riftbreaker::ResourceBasket::SetResourceAmount(GameplayResourceDefHolder const&, ResourceValue)` | public |
| `Riftbreaker::ResourceBasket::RemoveNullResources()` | public |
| `Riftbreaker::ResourceAccount::CanAffordExpense(ResourceBasket const&)` | public |
| `Riftbreaker::PlayerService::AddResourceAmount(...)` | `0xF1E3D0` |
| `Riftbreaker::PlayerService::GetGlobalResourcesList(...)` | `0xF28060` |
| `Riftbreaker::ResourceBasket::GetResourceAmount(...)` | `0x2D3520` |

## FNV-1a bestätigt (im Binary)

`GetResourceAmount` enthält die Hash-Konstanten wörtlich:

```
mov r9d, 0x811c9dc5          ; FNV-1a Offset-Basis
...
imul r9d, eax, 0x1000193     ; FNV-1a Primzahl
```

→ carbonium-Hash = `0x659cc791` (FNV-1a("carbonium"), extern berechnet).

## ResourceBasket-Layout (aus GetResourceAmount-Disasm)

`this` = `ResourceBasket*`:

| Offset | Typ | Bedeutung |
|---|---|---|
| `+0x08` | `entry*` | sortiertes Array |
| `+0x10` | `size_t` | Anzahl Einträge |

Eintrag = **16 Byte**:
- `[entry+0x00]` = `uint32` StringHash (Sortierschlüssel, aufsteigend)
- `[entry+0x08]` = `uint64` ResourceValue

Zugriff = **Binärsuche** über das sortierte Array (im Disasm klar sichtbar:
`shl rdx,4` / Halbieren / `cmp [rax+r*8], hash`).

## Ketten-Anfang (PlayerService → Container)

Im Disasm der Funktion bei `0xF1E700` (direkt nach `AddResourceAmount`) und
`0x180C60050` bestätigt:

```
PlayerService      (vftable RVA 0x2E8E910)
  [+0x08]          → Resource-System (Pointer)
    [+0x30]        → Container (EMBEDDED, `lea` — NICHT dereferenziert!)
```

**Wichtige Korrektur:** der Container liegt **eingebettet** bei
`resource_system + 0x30` (Adresse), nicht als Pointer *an* Offset 0x30. Der
erste Probe-Entwurf dereferenzierte dort fälschlich (`*(rs + 0x30)`); Probe v2
nutzt `rs + 0x30`.

Container-Lookup wird über `0x180EF4AC0` / `0x180C26E50` / `0x181DD06F0`
abgewickelt (templatisierter `FlatMap`/HashMap-Code, TLS/Registry-Muster).

## Noch offen (2 Hops bis carbonium)

1. Container → `lookup(playerId=0)` → `ResourceAccount*`
2. `ResourceAccount` → `ResourceBasket` (Member-Offset; es existiert
   `ClassField<ResourceAccount, ResourceBasket>`)

Danach: Basket `[+8]`/`[+0x10]` → Binärsuche `0x659cc791` → carbonium-Wert.

**Ansatz:** empirisch statt weiterem Disasm — Probe v2 (in `rbbridge.c`)
dumpt bei einem Live-Restart Speicher-Fenster um `resource_system` + `container`
(`probe_dump`-Events) und liefert die Offsets aus echten Werten.

## PDB / Tooling

- PDB ist **stripped** (keine Typinfo, TPI/IPI leer) — siehe Issue #364. Symbole
  (Namen + Adressen) sind im `-publics`-Stream reichlich vorhanden.
- PDB/DLL liegen auf **planet** (`/srv/rbgame/bin/…`) **und lokal** (macOS/CrossOver,
  `~/Library/Application Support/CrossOver/Bottles/gams/drive_c/Program Files (x86)/The Riftbreaker/bin/…`),
  byte-identisch (77.885.440 / 252.334.080).
- Tooling: `llvm-pdbutil` / `llvm-readobj` (mac + planet), Disasm via
  `/opt/rb-re/venv` (pefile + capstone) + `/tmp/disasm.py` auf planet.
