# Full-Chain-Disziplin — Contract-Items der v2-Architektur

**Regel (ein Satz):** Ein Contract-Item (ein Spielzustands-Primitiv Read/Write)
gilt erst dann als *proven*, wenn es die **volle Kette** durchläuft — vom
C++-Offset/Method über `rbbridge.dll` und die HTTP-Bridge bis zum sichtbaren
Feld/Button in der WebUI — und dort nachweislich den Spielwert liest bzw.
schreibt; ein bloß nachgewiesener RE-Offset ist **nicht** „proven“.

## Die Kette („Baukasten“)

```
            ┌────────────────────────────────────────────────────────────────┐
            │  Game (C++/Lua-State, DedicatedServer)                         │
            │  rbbridge.dll  bausteine/04-trainer-io/rbbridge/rbbridge.c     │
            │  pipe_bridge.c  bausteine/04-trainer-io/bridge/pipe_bridge.c   │
            │  WebUI  bausteine/04-trainer-io/bridge/cockpit.html            │
            │         bzw. tournament/web/                                    │
            └────────────────────────────────────────────────────────────────┘

  Backend (autoritative State-Machine) = Rust  tournament/  (src/referee.rs, state.rs)
```

Zwei Richtungen, **beide** müssen geschlossen sein:

```
  READ  (Egress):   Game  →  rbbridge.dll (get_state)  →  pipe_bridge.c (POST /get_state)  →  WebUI-Feld
  WRITE (Ingress):  WebUI-Button  →  pipe_bridge.c (POST /add_resource | /exec)  →  rbbridge.dll  →  Game
```

Die Kette ist **kein** Selbstzweck: `ok:true` aus `exec_result` belegt nur, dass
ein Kommando ausgeführt wurde — **nicht**, dass der Spielwert sich geändert hat
(Falsch-Grün, #288/#376). „Proven“ heißt, dass das End-to-End-Ergebnis sichtbar
ist (Wert im Feld ändert sich, Log zeigt die Wirkung, kein Crash).

## PR-Gate (verbindlich) — Leitfrage

Jeder PR, der das Interface berührt (DLL / Bridge / WebUI), muss diese Frage
beantworten:

> **„Ist es an die Web-UI verdrahtet?“**

- **Read:** Erscheint der Wert als Feld in der Web-UI — live via
  `POST /get_state`?
- **Write:** Gibt es einen Button / ein Steuerelement, das den Wert ändert —
  via `POST /add_resource` bzw. `POST /exec`?
- **Vorlage:** `carbonium` in `bausteine/04-trainer-io/bridge/cockpit.html`
  (`feature/v2` noch: `contract.html`): Feld `carbonium`/`carbonium_max` **+**
  Buttons `+ add` / `− subtract` — die Referenz für Read **und** Write.

Ein PR, der **nur** den RE-Offset oder **nur** den DLL-Read liefert, ist
**NICHT** fertig. Er muss die Kette bis zur Web-UI schließen — oder das
Reststück als verlinktes Follow-up ausweisen.

## Worked Example — carbonium (proven)

Carbonium ist der Referenzfall, an dem alle vier Stufen durchgespielt sind.

**1. RE (C++-Pfad belegt, Build 2.0.58485).**

```
PlayerService vftable 0x2E8E910  (instance[0] == base + this)
  PlayerService + 0x8 = World*
  GetPlayerAccount(World*, playerId=0) = RVA 0xC60050  -> ResourceAccount*
  ResourceAccount + 0x8 = sortiertes Array, +0x10 = count
    Eintrag 16 B = { uint32 StringHash, int64 ResourceValue }  (x10^6)
    carbonium StringHash = 0x659cc791
  ResourceAccount + 0x20 = UnorderedMap<StringHash, float max>  -> carbonium_max
    lookup RVA 0x28AC00 ; scale-Globale RVA 0x4794210
  WRITE: PlayerService::AddResourceAmount RVA 0xF1E3D0
```

**2. DLL (`rbbridge.c`).**

- Read: `dispatch_get_state` liest den Account-Basket, löst `0x659cc791` auf
  und liefert `{"event":"get_state_result","ok":true,"carbonium":…,"carbonium_max":…,…}`.
- Write: `dispatch_add_resource` ruft `AddResourceAmount` direkt (kein
  Lua/Console-Hop) und liefert `{"event":"add_resource_result","ok":true,…}`.

**3. Bridge (`pipe_bridge.c`).**

- `POST /get_state` → `handle_get_state` → Pipe-Kommando `{"cmd":"get_state"}`.
- `POST /add_resource` → `handle_add_resource` → Pipe-Kommando
  `{"cmd":"add_resource","amount":"…"}`.

**4. WebUI (`cockpit.html`; auf `feature/v2` noch `contract.html`).**

- Feld `carbonium`/`carbonium_max` (Read via `get_state`).
- Buttons `+ add` / `− subtract` (Write via `add_resource`).

Erst mit Stufe 4 ist das Item geschlossen. Ein neuer Wert folgt derselben
Vorlage — z. B. eine hypothetische Ressource **„ionium“**:

1. RE des Offsets/Methods für `ionium` (Hash + Read/Write-RVA).
2. In der DLL `get_state` (Read) und `add_resource` (Write) auf `ionium` erweitern.
3. Falls nötig Endpoint in der Bridge routen (`pipe_bridge.c`).
4. WebUI-Feld + Button hinzufügen (Read + Write).

Erst danach ist „ionium“ proven. (Hinweis aus #371: `mythium` war kein echter
Spielwert — RE vor dem Verdrahten, sonst greift der Write ins Leere.)

## Definition of Done — Contract-Item (volle Kette proven)

- [ ] **RE belegt & build-gebunden:** Offset/Method + RVA dokumentiert
      (Research-Doc + `riftbreaker-re`-Skill), Build `2.0.58485` gepinnt.
- [ ] **DLL-Read:** Feld kommt in `get_state`/`get_state_result` an
      (`rbbridge.c`), Werte ≠ Platzhalter.
- [ ] **DLL-Write:** Primitive schreibt nativ (typed Command oder
      `add_resource`) und liefert ein Ergebnis-Event.
- [ ] **Bridge:** HTTP-Endpoint in `pipe_bridge.c` geroutet und getestet.
- [ ] **WebUI:** Feld zeigt den Live-Wert (Read) **und** Button treibt den
      Write (Read + Write sichtbar).
- [ ] **End-to-End-Beweis:** UI/`curl` → Spielwert ändert sich nachweisbar
      (Log/`get_state`), kein Crash.
- [ ] **Thread-Modell respektiert (#378):** Lua-Reads/`ExecuteCommand` nur auf
      dem Lua/main-Thread (Lua-Hook); native C++-Reads/Writes thread-agnostisch.

## Referenzen

- **#394** — diese Regel (Full-Chain-Disziplin, PR-Gate). Verlinkt aus
  `CONTRIBUTING.md`, `README.md`, `docs/concept.md`, `docs/INGRESS_IO.md`,
  `docs/relay-pipe-contract.md` und den RE-Research-Docs.
- **#378** — v2-Vision: „Full C++ Control, no Lua“ (Thread-Modell + C++-Egress).
- Contract-Items **#385–#391**: `MissionService::ActivateMissionFlow`,
  `Database*`-Objekt, `dom_mananger`-Auflösung + `SetSuspended`,
  `CampaignService`-Difficulty-Methoden, `MissionService::DeactivateMissionFlow`,
  Player-Count via C++, WebUI-Control-Panel.
- Detailbelege: `docs/research/dedicated-io-direct-reads.md`,
  `docs/research/dedicated-io-write-functions.md`,
  `docs/research/resource-hash-map.md`, `docs/INGRESS_IO.md`.
