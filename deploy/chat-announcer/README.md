# chat-announcer — ereignis-/schwellenbasierter Chat-Ansager (Issue #940)

Eigener Server-Sidecar analog `attack-cycle` / `send-tailer`: er **liest** den
State und **entscheidet was/wann** in den In-Game-Chat geht. Der Chat ist
append-only, darum wird ausschliesslich beim **Schwellen-Crossing** gesendet —
genau einmal pro Schwelle und Epoche, **kein 1-Hz-Spam**.

Senden laeuft ueber die Bridge (`POST /send_chat {"text","type","prefix"}`,
Issue #934). Die Bridge bleibt damit IO-Primitive; der Announcer ist reine
Entscheidungs-Logik.

```text
poll ~1s: GET /status (Attack-Cycle)   ── liest state + Zaehler
  ├─ warmup  : Schwellen 3:00/2:00/1:00/0:30/0:10 + GO am warmup→running-Edge
  ├─ running : next attack in 60s / 30s / 10s   (je attack_index genau einmal)
  ├─ Feuern  : attack_index-Sprung → incoming [W1]x3 [W4]x1 (aus last_fire)
  └─ Ende    : state warmup|running → game_over → round over - <Grund>
                 warmup→game_over  = "no HQ"       (HQ nie gebaut/survived)
                 running→game_over = "HQ destroyed"
paused: keine Sendung
senden: POST /send_chat an die Bridge
```

## Endpunkte / Flags

| Flag                 | Default                  | Zweck                                                   |
| -------------------- | ------------------------ | ------------------------------------------------------- |
| `--attack-cycle-url` | `http://127.0.0.1:9102`  | Basis-URL des Attack-Cycle (`GET /status`)              |
| `--bridge-url`       | `http://127.0.0.1:9001`  | Basis-URL der Bridge (`POST /send_chat`)                |
| `--interval`         | `1.0`                    | Poll-Intervall in Sekunden (nur Lesen)                  |
| `--dry-run`          | aus                      | Nur loggen, nicht senden                                |
| `--style`            | `short`                  | Format-Stil (`short` = Default, `aligned` = Platzhalter)|
| `--chat-type`        | `system`                 | Bridge-Chat-Typ                                         |
| `--timeout`          | `5.0`                    | HTTP-Timeout je Request                                 |
| `--once`             | aus                      | Einmal pollen, dann beenden                             |

Fallback: liefert `GET /status` (Attack-Cycle) kein 2xx, wird `GET
/attack_status` an der Bridge versucht (vom Cycle via `push_status()`
gespiegelt). HTTP-Fehler/Timeouts werden geloggt, der Loop laeuft weiter.

## Schwellen-Modell (kein Spam)

- Poll ~1s (nur lesen, billig); **gesendet** wird nur beim Crossing, genau
  **einmal pro Schwelle und Epoche** (`announced`-Set).
- Regel „groesste ueberschrittene, noch nicht gemeldete Schwelle": zu jeder
  Schwelle `t` (absteigend) — wenn `remaining <= t` und noch nicht gemeldet →
  senden und **alle** in diesem Tick ueberschrittenen Schwellen als gemeldet
  markieren. Damit genau **eine** Nachricht auch bei Poll-Aussetzern, nie
  Rueckstands-Flut.
- **Epochen-Reset**: `announced`-Set leeren bei neuem Warmup (Uebergang in
  `state=warmup`) bzw. bei `attack_index`-Aenderung (neuer Attack-Zyklus).

## Format (an EINER Stelle gekapselt)

Alle Texte laufen ueber `FormatConfig` + `fmt_*`-Funktionen (`fmt_warmup`,
`fmt_warmup_go`, `fmt_attack_next`, `fmt_incoming`, `fmt_round_end`) — **keine**
Format-Literale im Event-/Loop-Code.

- `style="short"` (Default) = konservative, ASCII-nahe Kurzform:

```text
warmup 3:00 / 2:00 / 1:00 / 0:30 / 0:10
GO
next attack in 60s / 30s / 10s
incoming [W1]x3 [W4]x1
round over - HQ destroyed
round over - no HQ
```

- `style="aligned"` ist **reserviert/ungeeicht** (Dot-Leader/Spalten) → erst
  nach dem Mess-Spike **#939** umstellen. Die Kapselung stellt sicher, dass
  #939 nur diesen einen Block aendert.

## Test

```bash
cd deploy/chat-announcer
python3 -m unittest test_announcer -v
```

Hermetisch (stdlib `unittest`, kein Netz): Detektor-Kern gegen synthetische
`status`-Dicts, Service gegen Fake-Getter/Fake-Poster.

## Offene Punkte

- **#939 offen**: Format-Messbericht fehlt → Default = konservative Kurzform,
  `aligned` ungeeicht; Umschaltung spaeter an der einen gekapselten Stelle.
- **Live-Abnahme** („eine Runde lang korrekt & ohne Spam") nur mit
  Spieler/Spiel/Umgebung pruefbar → Human-/Playtest-Punkt.