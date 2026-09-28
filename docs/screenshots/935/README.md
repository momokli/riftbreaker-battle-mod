# Screenshots — Issue #935 / PR #945 (Cockpit-Chat-Panel)

Beleg für das neue **Chat-Panel** im Operator-Cockpit (`cockpit/cockpit.html`).

Enthalten: **Chat-Verlauf** (kappbar, 200), **Absender-Umschalter** (`#chat_as`,
frei wählbar + Vorschläge), **Eingabezeile** (`send` / Enter, `maxlength`) und die
**Formatier-Toolbar** (Symbole/Templates).

## Dateien

| Datei | Zeigt |
| --- | --- |
| `01-chat-verlauf-operator.png` | Chat-Verlauf mit Spieler- (`PLAYER`) und Server-Nachrichten (`OPERATOR`), Absender **`Operator`** → Server-Nachricht wird als `[Operator] …` vorangestellt. Eingabezeile `send` + Formatier-Toolbar (`INSERT`) sichtbar. |
| `02-chat-absender-system.png` | Derselbe Verlauf mit Absender **`system`** → **kein** Prefix, Server-Nachrichten ohne `[Operator]`-Marker. Zeigt den Unterschied des Absender-Umschalters. |

## Erzeugung

Die **unveränderte** `cockpit/cockpit.html` aus dem Branch wurde mit headless
Chromium (CLI, `--headless=new --no-sandbox --disable-gpu --force-device-scale-factor=2
--virtual-time-budget=6000`) gegen einen minimalen lokalen Mock der Cockpit-API
gerendert (Python `http.server`); die Sicht auf das Chat-Feldset zugeschnitten
(`convert -crop`).

Der Mock injiziert einen kleinen Demo-Block **innerhalb der page-eigenen IIFE**, d. h.
es werden die echten Branch-Funktionen `chatAppend` und `chatSend` (inkl.
Absender-Normalisierung) ausgeführt — kein nachgebautes Markup. Mock-Endpunkte:
`GET /` → `cockpit.html`, `POST /get_state`, `GET /server/status|/server/logs`,
`GET /capsule/status`, `POST /send_chat` → `{"ok":true,”text”:…,"sent":"true"}`.

Keine Secrets/Tokens in Bild oder Dokument.

- Issue: #935 · PR: #945 · Branch: `feat/935-chat-panel`
