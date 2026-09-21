# tools/net-router — userspace-UDP-Router für `:6321` (Spike zu Issue #825)

Löst das Kernproblem dieses Projekts: **der Client hängt den Port selbst an**
(`ip:%s:6321` als Format-String in der DLL) und kann deshalb nur `IP:6321`
erreichen. Dieser Router macht aus _einem_ Eingang viele Backends — und routet
optional **nach Spielernamen**.

```text
Client ──▶ Eingang:6321 (rbrouter) ──┬──▶ Backend A   (Fallback, /tmp/rbrouter_target)
                                     └──▶ Backend B   (Name, /tmp/rbrouter_names)
                ▲                            │
                └────────── Antwort ─────────┘   (Client sieht immer den Eingang)
```

## Zwei Stufen

**Part 1 — statisch:** pro Client-Flow ein eigenes Socket zum Ziel; das Ziel kommt
bei jedem _neuen_ Flow aus einer Datei → Umschalten ohne Neustart.

**Part 2 — Namens-Routing:** der Router lernt den Spielernamen aus dem Traffic
(`<name>=<ip:port>`-Regeln) und merkt sich `Quell-IP → Ziel`.

### Wie das Namens-Routing funktioniert (und warum es einen Reconnect kostet)

Live gemessen (Issue #825): **der Spielername steht erst ~0,3–1,2 s nach dem
Verbindungsbeginn im Client→Server-Strom — also erst _nach_ dem GNS-Handshake.**
Daraus folgt:

- Vor dem ersten Weiterleiten ist der Name **nicht** lesbar. Pakete zurückhalten
  bringt nichts: ohne Handshake sendet der Client den Namen gar nicht (Henne/Ei).
- Einen **laufenden** Flow umzuhängen ist destruktiv — der Client hat seine
  GNS-Session schon mit dem alten Backend aufgebaut und bekommt `connection lost`.

Deshalb: Name lernen → Merker setzen → **Flow verwerfen** → der Client verbindet
selbst neu (in der Praxis < 1 s, oft mit demselben Quellport) → der nächste Flow
wird aus dem Merker **sofort** richtig geroutet. Der Merker keyt auf der
**Quell-IP**, nicht auf `(ip, port)`, weil der Client den Port wiederverwendet
oder wechselt.

## Aufruf

```bash
# Part 1: statisch, umschaltbar per Datei
echo "65.21.27.234:6322" > /tmp/rbrouter_target
python3 rbrouter.py --bind 0.0.0.0:6321 --target-file /tmp/rbrouter_target --no-names

# Part 2: Namens-Routing
printf "momod=65.21.27.234:6321\nmomop=65.21.27.234:6322\nmomos=65.21.27.234:6323\n" > /tmp/rbrouter_names
python3 rbrouter.py --bind 0.0.0.0:6321 --target-file /tmp/rbrouter_target --names-file /tmp/rbrouter_names

# Nur Parser/Zieldatei prüfen, kein Netz
python3 rbrouter.py --self-test
```

Umschalten kostet keine Unterbrechung: Zieldatei wird bei jedem **neuen** Flow
gelesen; laufende Flows werden bei einem Zielwechsel verworfen (`--keep-flows-on-switch`
schaltet das ab).

### Wichtig beim Testaufbau

Auf dem Relay-Host muss eine vorhandene DNAT-Regel für den Eingangsport **weg**
sein, sonst greift `PREROUTING` die Pakete ab, bevor sie beim lokalen Socket
ankommen:

```bash
iptables -t nat -D PREROUTING -p udp --dport 6321 -j DNAT --to-destination 65.21.27.234:6323
# Rollback (Rolle satellite-relay, liegt auch auf sync):
bash /usr/local/bin/rbbattle-satellite-relay.sh
```

Der Spieler gibt im Spiel **nur die IP** ein — eine Eingabe _mit_ Port scheitert
still (`ip:…:6321:6321`).

## Ablesen, ob es funktioniert hat

1. **Ziel-Container-Log** (maschineller Beweis, unabhängig vom Spieler):
   ```bash
   ssh planet 'docker logs riftbreaker-dedicated-prod --since 5m 2>&1 | grep -a OnNetPlayerCreateRequest'
   ```
2. **Router-Log**: `flow neu …`, `entschieden … -> ziel (grund)`, `name gelernt: <name> -> <ziel>`,
   `name bestaetigt: <name> -> <ziel>`, `flow weg … (grund) c2s=… s2c=…`
3. **Drops**: die Statuszeile zeigt `kernel-drops(5s)`/`inerr(5s)` aus `/proc/net/snmp`.
   Manuell: `ss -uanm | grep 6321`, `nstat -az | grep -i udp`.

## Tests

```bash
cd tools/net-router
python3 -m unittest test_rbrouter -v     # hermetisch (Loopback), kein Netz/Spiel
```

## Grenzen (bewusst)

- Merker (`Quell-IP → Ziel`) hat **keine TTL** und wird nur durch einen erkannten
  Namen aktualisiert → jeder Namenswechsel kostet genau einen Reconnect.
- Der Payload wird als **ASCII** gescannt (längster registrierter Namenstreffer
  gewinnt). Liegt der Name in einem _komprimierten_ Zstd-Block, gibt es keinen
  Treffer — in den Live-Mitschnitten lag er im Raw-Block und war lesbar.
- Kein Payload-Parsing, keine Auth, keine Persistenz, keine Dekompression.
- Nur `disable_steam=1` (LAN/Direct-IP); im Steam-Mode läuft der Verkehr über das
  Steam-Relay und erreicht den Eingang nie.
- Idle-Flows werden nach `--idle-timeout` (Default 120 s) geschlossen.
