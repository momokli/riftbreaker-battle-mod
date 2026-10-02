# RFC 0002 — Read-Modell: reine Speicher-Reads (kein Engine-Call)

- **Status:** Entwurf / zur Entscheidung
- **Kontext:** #1033 (RFC 0001), #1070 (Prod-Crash), #1073 (Follow-up), #1074
- **Betroffen:** `server/dll/rbbridge.c` (Read-Pfad), `docs/research/*`

> Dieses Dokument legt fest, **was „richtig" heißt** — und was noch **offen** ist.
> Es ändert keinen Code. Es ist die Grundlage, damit wir nicht weiter Symptome
> patchen (Scan → Cache → Fan-out → Game-Thread-Calls).

---

## 1. Warum dieses Dokument

Wir haben mehrfach „schnell" gemacht, ohne ein festgelegtes Read-Modell:

| Schritt | Was | Ergebnis |
| --- | --- | --- |
| #1061 | Heap-Scan gecacht | Scan weg — aber die **Werte** kommen weiter aus **Engine-Calls** |
| #1067 | `pipe_bridge`-Fan-out | weniger Last — Reads bleiben Engine-Calls |
| #1068 | Reads in den Game-Thread-Hook | **Crash** (#1070) → revert |

Kernproblem: **Engine-Funktionen aufzurufen ist die Crash-Quelle** — egal von
welchem Thread. Das ist kein Performance-Thema, sondern ein **Korrektheits-/
Stabilitäts-Thema**. Deshalb zuerst das Modell.

---

## 2. Belegte Fakten

- **Engine-Calls zum Lesen crashen** — off-thread (#436/#479/#512/#573/#655)
  **und** aus dem Game-Thread-Hook (#1070). Crash-Bundles:
  `snapshot_update → GetPlayerAccount → GetPlayerTeam → LinearEcs::GetComponent`
  (Fault bei `0x1e0`, read).
- Der **Heap-Scan** war nur ein Workaround für fehlende stabile Pointer — kein
  Design.
- **`World*` ist scan-frei** aus dem Hook lesbar (`self + 0x358 + type*0x58`),
  live bewiesen (S1, #1035).
- Der Hook läuft auf **einem stabilen Game-Thread, 30 Hz** (S2, #1036).
- Teil-Offsets sind dokumentiert (`docs/research/dedicated-io-direct-reads.md`):
  `HealthService[+8]=World`, `World[+0x30]=ECS`, `HealthComponent[+0x00/+0x04]`,
  `ResourceAccount[+8]=Array`, `+0x10=Count`, Einträge 16 B.

---

## 3. Prinzipien (was „richtig" heißt)

1. **Keine Engine-Calls zum Lesen.** Nur **Speicher lesen** (Werte per Offset).
2. **Crash-sicher:** guarded reads (`VirtualQuery`/`ReadProcessMemory` → 0 statt
   Fault).
3. **Race-bewusst:** Welt-**Epoch** (World-Wechsel) + vftable-Revalidierung;
   kein Torn-Read-Crash.
4. **Build-gebunden:** Offsets pro Build, dokumentiert + live validiert.
5. **Observability:** Generation/Zeit + was gelesen wurde.
6. **Writes** bleiben selten + guarded (unverändert).

---

## 4. Entscheidung (Read-Modell)

- **Root:** `World*` — vom Game-Thread-Hook per **reinem Read** publiziert (S1).
  Kein Engine-Call. (Alternativ ein statischer Root — offen, R4.)
- **Werte:** der **Pipe-Thread** liest **reine, guarded Speicher-Reads** über
  `World*` + Offsets. **Kein Engine-Call.**
- **Guard:** `VirtualQuery`/`ReadProcessMemory` → 0 statt Fault (wie
  `scan_qword_instance`).
- **Epoch:** ändert sich `World*`, werden abgeleitete Pointer invalidiert;
  vftable-Revalidierung pro Nutzung.
- **Konsistenz:** Reads sind „best effort" (Anzeige). Bei Bedarf Retry, wenn
  sich `count` während des Lesens ändert. **Kein Seqlock nötig**, weil guarded
  reads nicht crashen — Inkonsistenz ist harmlos, ein Fault wäre es nicht.
- **Writes:** bleiben guarded + selten.

**Warum der Pipe-Thread (nicht der Game-Thread):** reine guarded Reads sind
**thread-agnostisch crash-sicher** (kein Engine-Call). Der Thread beeinflusst nur
die *Konsistenz*, nicht die *Sicherheit*. Der Pipe-Thread ist der einfachste Ort
(kein Hook nötig für die Reads; der Hook liefert nur `World*` + Tick).

---

## 5. Offene RE-Aufgaben (das fehlt für „richtig")

- **R1 — Ressourcen:** `GetPlayerAccount` (RVA `0xC60050`) disassemblieren →
  Kette `World → ResourceAccount`. Dann `account+8` (Array), `+0x10` (Count),
  Einträge 16 B (`{uint32 hash, int64 value}`). *Teil-Offsets dokumentiert.*
- **R2 — Players:** `World+0xC0` → Sessions-Map → Count (reiner Walk).
- **R3 — HQ:** `World+0x30` (ECS) → Component-Lookup (Entity-id + TypeHash) →
  `HealthComponent[+0x00/+0x04]`. **Härtester Fall** — der Lookup ist eine
  Funktion; „reiner Read" heißt: Container manuell walken oder einen stabilen
  Pointer auf die HQ-Entity finden.
- **R4 — Root-Stabilität:** `World*` nur über den Hook, oder ein statischer Root
  (RE, z. B. `ApplicationAbstract::ms_singleton` → GameplayState → World)?

---

## 6. Alternativen (verworfen)

- **Engine-Calls off-thread / im Hook:** crashen (#436/#479/#512/#573/#655/#1070). ✗
- **Heap-Scan:** Workaround, teuer, fragil. ✗ (nur Cold-Path-Fallback).
- **Lua-Reads:** anderer Pfad (Lua-Thread); der Mod loggt bereits. Möglich, aber
  nicht „C++ direct" und mit eigenem Thread-Modell.
- **Game-Thread-Marshal:** Engine-Calls crashen trotzdem; reine Reads brauchen
  ihn nicht.

---

## 7. Konsequenzen / Risiken

- Offsets sind **build-gebunden** → bei Engine-Update neu RE.
- Der **HQ-ECS-Walk** ist komplex → ggf. HQ zunächst über einen anderen (reinen)
  Pfad oder später.
- Reads sind ggf. **leicht inkonsistent** (ok für Anzeige; nicht für
  Entscheidungen, die `try_spend` o. Ä. ohnehin guarded prüfen).

---

## 8. Migrationsplan

1. **R1 (Ressourcen)** RE + guarded Read → live gegen den Call-Pfad abgleichen.
2. **R2 (Players)** RE + Read.
3. **R3 (HQ)** RE + Read (oder dokumentierter Sonderweg).
4. `get_state` auf **reine Reads** umstellen; die Engine-Call-Pfade entfernen.
5. Erst dann ist „scan-frei" **und** „call-frei" erreicht.
