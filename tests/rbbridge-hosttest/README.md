# rbbridge host-test

Kompiliert zur Laufzeit die **reinen** Scan-/RTTI-Funktionen aus `rbbridge.c`
(Host-CC `cc`, dann `gcc` — **kein mingw**) gegen einen **synthetischen,
PE-artigen Puffer** (`hosttest/rbbridge_hosttest.c`) und führt sie aus: kein
Windows, kein Spielprozess, kein Netz, kein Player.

Geprüft:

- `scan_bytes` / `scan_bytes_mask` (rel32-Wildcards + E8-Pflicht)
- `resolve_console_vftable` (RTTI-Walk)
- `resolve_console_service` (Signatur + vftable + Instanz + Cache)
- `resolve_module` (Wine-robust, Stufe d `sigbase`, Negativfälle)
- `pe_image_size` (defensive Grenzfälle)
- Fehlerpfade (Modul/RTTI/Signatur/Instanz fehlt → `0`/`NULL`, kein Crash)

Ist kein Host-C-Compiler vorhanden, wird der Test **sichtbar übersprungen**
(skip mit Begründung, nie stillschweigend grün).

## Aufruf

```bash
cd tests/rbbridge-hosttest
npm ci        # keine externen Deps mehr (nur node:test)
npm test      # = node --test
```

Voraussetzung: ein Host-C-Compiler (`cc`/`gcc`).
