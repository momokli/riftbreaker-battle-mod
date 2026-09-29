# Raw-Belege — #909 „Parked Solo" Messung (2026-09-24)

Rohe Konsolen-/Container-Logs der Messung aus `deploy/parked/MEASUREMENT.md`.
Sie sind der **reproduzierbare Rohbeleg** zu den dort genannten Zahlen (Review
Blocker 2) — kein aufbereiteter Report, sondern der tatsächliche Output.

Umgebung: Host `planet` (Linux 6.8.0-139, x64) · Image `rb-dedicated:9f578c405d0d`
· Datum 2026-09-24.

| Datei | Inhalt |
|---|---|
| `909-coldboot-handover-2026-09-24.txt` | Strenger Live-Lauf: frischer Container mit denselben Mounts wie die Deploy-Compose, Cold-Boot-Timestamp + erste `pause_game`/`resume_game`-Round-Trips + Bridge-/Server-Log-Auszug. |
| `909-idle-roundtrips-2026-09-24.txt` | Ruhige (idle) `pause_game` / `resume_game`-Round-Trips **nach** dem Boot — die Handover-Kernzahl. |
| `909-probe-container-2026-09-24.txt` | Vollständiges Container-Log (655 Zeilen) des Probe-Laufs (Entrypoint, Injection, MapGenerator, Bridge). |
| `909-harness-live-attempt-2026-09-24.txt` | Fehlgeschlagener Live-Lauf von `measure_boot.py --json` — belegt die Provisioner-Live-Pfad-Lücke (siehe Abschnitt 4 in `MEASUREMENT.md`). |