# 939 — Messbericht: In-Game-Chat-Format (Monospace? Zeilenlänge/Wrap? Encoding?)

**Spike zu #939** (Milestone **1.0.12**). Liefert die Messgrundlage für den
Chat-Announcer **#940** (`deploy/chat-announcer`), der die Format-Entscheidung
an *einer* gekapselten Stelle (`FormatConfig` / `fmt_*`, `style="short"` vs.
`"aligned"`) trifft.

- **Methode A — Asset-Inspektion:** `packs/00_win_data.zip` (Build 2026-04-02,
  Game 2.0.x) → `gui/hud/hud_chat.gui`, `gui/menu/multiplayer_lobby.gui`,
  `gui/hud/minimap.gui`, `gui/hud/hud_text.gui`, `lua/player/mech_action.lua`.
- **Methode B — Live-Send:** laufende Bridge (`POST /send_chat`, #934) auf den
  Container-Ports `127.0.0.1:9001` (dev) / `9002` (prod); Byte-Vergleich der
  `send_chat_result`-Antwort **und** der `rbbridge.log`-Zeile (`send_chat: text=…`).

> Diese Messung belegt **Transport/Serialisierung** byte-genau (Methode B) und
> den **Aufbau** des Chat-Widgets (Methode A). Die rein *visuelle* Bestätigung
> (Font-Glyphen, exakte Wrap-Schwelle am Schirm) braucht einen Client/Spieler —
> als offener Playtest-Punkt unten markiert.

---

## TL;DR — Format-Entscheidung für #940

| Frage | Befund | Konsequenz |
| --- | --- | --- |
| **1. Monospace?** | **Nein** — proportional. Kein Monospace-Font am Chat; das einzige Monospace-Font der UI steckt in der Minimap-Koordinate. | **`style="aligned"` (Dot-Leader/Spalten) verwerfen** → Default **`short`** bleibt. |
| **2. Zeilenlänge/Wrap** | Wrap ist **pixelbasiert** (~400–470 px HUD / ~795–800 px Lobby), **keine** harte Server-Kappung. Spieler-**Eingabe** ist auf **120 Zeichen** begrenzt. | Server-Sends dürfen länger sein; kurz halten (~< 80 Zeichen) für 1–2 Zeilen. |
| **3. Encoding** | **BMP**-Zeichen (`· … │ ▪ × ✅ ä ö ü ß`, NBSP, ZWSP) überleben den Pfad **byte-identisch**. **Non-BMP** (Emoji wie `👍 🎵`, ZWJ-Folgen) werden verstümmelt (ungültiges UTF-8 / CESU-8). | **Nur BMP-Zeichen** senden; **keine Emoji**. |

Auf den Chat-Announcer übertragen bleibt damit die bestehende konservative
Kurzform korrekt:

```text
warmup 3:00 / 2:00 / 1:00 / 0:30 / 0:10
GO
next attack in 60s / 30s / 10s
incoming [W1]x3 [W4]x1
round over - HQ destroyed
round over - no HQ
```

---

## 1. Monospace? — **Nein (proportional)**

Der Chat rendert mit den proportionalen UI-SDF-Fonts des Spiels. Belege:

- **Lobby-Chat** (`gui/menu/multiplayer_lobby.gui`, Block `chat_message`):
  `GuiTextDef … style_name "exo2_bold_20_white"`. **Exo2** ist proportional
  (keine feste Vorschubbreite) — Spalten/Dot-Leader richten damit nicht aus.
- **HUD-Chat** (`gui/hud/hud_chat.gui`): die Nachricht nutzt benannte Styles
  `chat_message` (Spieler, `type=8`) bzw. Fallback `chat_gray` (System), plus
  Rich-Text-Tags (`<style=…>`), siehe `lua/player/mech_action.lua:32`
  (`<style='chat_user'>${sender:plain}:</style> …`).
- **Fontinventar der UI** (Grep über alle `gui/**/*.gui`):
  `orbitron-medium_sdf` (372×), `Exo2_Bold_sdf` (133×) — proportional.
  Der **einzige** Monospace-Font `orbitron-medium_monospaced_sdf` wird
  ausschließlich in **`gui/hud/minimap.gui:338,585`** (Minimap-Koordinaten)
  verwendet; `AurulentSansMono` nur in `gui/menu/debug_flow_graph_menu.gui:322`.

→ **Feste Spalten/Dot-Leader sind nicht darstellbar.** `#940`s `--style aligned`
ist damit **nicht sinnvoll** und bleibt besser ungeeicht/entfernt; `short`
(konservativ, ohne Ausrichtung) ist die richtige Wahl.

## 2. Zeilenlänge / Wrap — pixelbasiert, keine harte Kappung

**Spieler-Eingabe (Tippfeld):** `gui/hud/hud_chat.gui` Nav-Node
`display.text_input` → `max_char_count "120"`. Also **120 Zeichen**
Eingabe-Limit (Vanilla). Das ist ein *Eingabe*-Limit, **kein** Display-Limit
für Server-Nachrichten.

**Nachrichten-Anzeige:** Wrap erfolgt über die **Pixelbreite** des Widgets, nicht
über eine Zeichenzahl:

- HUD-Chat (`hud_chat.gui`): Stackpanel `size x "400"`, Nachrichten-Widget
  `max_size x "470"`, Container `message` `size x "500" y "26"`; Zeilenhöhe
  ≈ 24–26 px, `max_size y "200"`.
- Lobby-Chat (`multiplayer_lobby.gui`): Nachricht `max_size x "795"`,
  `size x "800"`, `size y "26"`.

**Live gegengeprüft:** eine 120-Zeichen-Zeile lief **vollständig** durch
(`send_chat_result` echot alle 120 Zeichen); die Bridge puffert `text[256]`
(`server/pipe_bridge/pipe_bridge.c` `handle_send_chat`), d.h. der Server kappt
nicht bei 120. Lange Zeilen **brechen im Client um** (mehrere visuelle Zeilen)
statt abgeschnitten zu werden.

→ Empfehlung an den Announcer: Zeilen **kurz** halten (Faustregel ≲ 80 Zeichen
≈ 1 Zeile bei Standardbreite). Der `short`-Stil erfüllt das bereits.

## 3. Encoding — BMP sicher, Non-BMP (Emoji) kaputt

Live-Test: Sendezeilen über `POST /send_chat` (`type=system`) und Byte-Vergleich
der `send_chat_result`-Antwort (`"text"`) sowie der DLL-Log-Zeile.

| Eingabe | UTF-8 (in) | Antwort (bytes out) | Ergebnis |
| --- | --- | --- | --- |
| `·` | `c2b7` | `c2b7` | ✅ identisch |
| `…` | `e280a6` | `e280a6` | ✅ identisch |
| `│` | `e29482` | `e29482` | ✅ identisch |
| `▪` | `e296aa` | `e296aa` | ✅ identisch |
| `×` | `c397` | `c397` | ✅ identisch |
| `✅` (U+2705) | `e29c85` | `e29c85` | ✅ identisch |
| `äöüß` | `c3a4c3b6c3bcc39f` | identisch | ✅ identisch |
| `·`-Lookalikes NBSP/ZWSP | `c2a0` / `e2808b` | identisch | ✅ identisch |
| `👍` (U+1F44D) | `f09f918d` | `eda0bdedb18d` | ❌ **ungültiges UTF-8 (CESU-8)** |
| `🎵` (U+1F3B5) | `f09f8eb5` | `eda0bcedbeb5` | ❌ **ungültiges UTF-8 (CESU-8)** |
| `👨‍👩‍👧` (ZWJ) | `f09f91a8 e2808d f09f91a9 e2808d f09f91a7` | Emoji-Teile → `eda0bd…` | ❌ Emoji-Teile kaputt |

Bestätigung im DLL-Log (`…/Temp/rbbridge.log`):
`send_chat: text='✅'` / `text='äöüß'` **lesbar**, dagegen `send_chat: text='������'`
für die Emoji-Sends.

**Interpretation:** Der Windows-Pfad serialisiert Non-BMP-Codepoints als
Surrogat-Halves (`0xED …`, CESU-8) statt als 4-Byte-UTF-8. Alles, was in der
BMP liegt (U+0000–U+FFFF), überlebt byte-identisch.

→ **Regel für #940 / Cockpit-Toolbar:** ausschließlich **BMP-Zeichen** verwenden.
`·  …  │  ▪  ×  ✅` sind sicher; **Emoji (non-BMP) vermeiden** (sie kommen als
Müll/Boxen an).

### Nebenbefund (kein Blocker für #939)

Die Non-BMP-Verstümmelung ist ein **Serialisierungs-Defekt** im Send-/Echo-Pfad
(`send_chat_result` liefert ungültiges UTF-8 zurück; entsprechend verzerrt die
DLL-Log-Zeile). Für den Announcer irrelevant (nur BMP), aber als Folge-Issue
notierbar, falls Emoji/Chat-Clients non-BMP senden sollen.

---

## Offene Punkte (nur mit Client/Spieler abschließbar)

1. **Font-Glyphen visuell:** Ob der konkrete Chat-Font `· … │ ▪ × ✅` als
   *Glyphe* besitzt (nicht nur der Byte-Pfad sie überlebt), ist nicht aus den
   gepackten Asssets ableitbar (Font-Atlanten liegen nicht entpackt vor) →
   am Schirm bestätigen.
2. **Exakte Wrap-Schwelle** in Zeichen pro Zeile bei realer UI-Skalierung
   (Pixelbreite oben gemessen; Zeichenzahl hängt vom Font/Skalierung ab).

Beide Punkte ändern die obige Entscheidung voraussichtlich **nicht** (proportional
+ kurze Zeilen + BMP), sind aber fair als Playtest-Vorbehalt.

---

## Reproduzieren

```bash
# Live-Sende-Probe (dev-Bridge)
curl -s -X POST http://127.0.0.1:9001/send_chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"·…│▪×","type":"system"}'
# → {"event":"send_chat_result","ok":true,"text":"·…│▪×","sent":"true"}
```

```bash
# Assets
unzip -o packs/00_win_data.zip 'gui/hud/hud_chat.gui' 'gui/menu/multiplayer_lobby.gui'
grep -n 'chat_message\|max_char_count\|style_name\|font "' gui/hud/hud_chat.gui
```

- Issue: **#939** · Milestone **1.0.12** · Game-Build 2.0.x (Pack 2026-04-02)
- Quelle Bridge: `POST /send_chat` (#934/PR #944), Chat-Widget: `gui/hud/hud_chat.gui`
