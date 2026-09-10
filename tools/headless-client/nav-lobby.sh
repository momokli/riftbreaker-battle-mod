#!/usr/bin/env bash
#
# nav-lobby.sh — Screenshot-gesteuerte Navigation bis zur Lobby + Server-Connect.
#
# Führt einen Navigationsplan (Liste aus xdotool-Aktionen + Screenshot-
# Verifikation) gegen den laufenden headless Client aus, bis die Multiplayer-
# Lobby erreicht und die Verbindung zum Testserver (Default: rb-winetest)
# hergestellt ist.
#
# Der Plan ist eine Textdatei (eine Aktion je Zeile, '#' = Kommentar, leere
# Zeilen werden ignoriert). Nach jeder Eingabe wird per Screenshot geprüft, dass
# der Frame noch gerendert ist (Heuristik wie wait-menu.sh) — ein schwarzer
# Frame bedeutet "Client abgestürzt/beendet" und bricht die Navigation ab.
# Der eigentliche Menü-Übergang wird über `wait-menu` erkannt (gerenderter
# Frame nach einem Klick/Tastendruck).
#
# Aktionen:
#   shot <file>           Screenshot ablegen (Beweis)
#   click <x> <y> [btn]   Klick (xdotool, Default button=1)
#   key <keysym>          Taste drücken (z. B. Return, Escape)
#   type <text>           Text tippen (Rest der Zeile = Text)
#   wait <sec>            Sekunden warten
#   wait-menu <out.png>   Warten bis Frame gerendert + Screenshot ablegen
#   connect <server>      Server-Adresse auflösen + eintippen + Return
#                         (ohne <server>: Default aus --server/NAV_SERVER)
#   verify <cmd...>       Verifikationskommando (exit 0 = ok)
#
# Nutzung:
#   nav-lobby.sh [--server NAME] [--plan FILE] [--timeout SEC] [--dry-run]
#   docker compose exec rb-headless nav-lobby.sh
#
# Umgebung (alles überschreibbar):
#   XDO_NAV         xdo-nav.sh-Pfad (Default: xdo-nav.sh)
#   DISPLAY         X-Display (Default: :99)
#   NAV_SERVER      Server-Name (Default: rb-winetest)
#   NAV_SERVERS     "name=host:port ..."-Liste für die Auflösung
#   NAV_TIMEOUT     Gesamt-Timeout in s (Default: 180)
#   NAV_RENDER_CHECK Nach jeder Eingabe Frame-Render prüfen (1|0, Default: 1)
#   IMPORT_BIN / CONVERT_BIN  überschreibbar für Tests (wie wait-menu.sh)

set -euo pipefail

DISPLAY="${DISPLAY:-:99}"
export DISPLAY

XDO_NAV="${XDO_NAV:-xdo-nav.sh}"
NAV_SERVER="${NAV_SERVER:-rb-winetest}"
NAV_TIMEOUT="${NAV_TIMEOUT:-180}"
NAV_RENDER_CHECK="${NAV_RENDER_CHECK:-1}"
IMPORT_BIN="${IMPORT_BIN:-import}"
CONVERT_BIN="${CONVERT_BIN:-convert}"
RENDER_MIN_STD="${RENDER_MIN_STD:-0.02}"
RENDER_POLL="${RENDER_POLL:-3}"

# Server-Liste "name=host:port" (Leerzeichen-getrennt).
NAV_SERVERS="${NAV_SERVERS:-rb-winetest=65.21.27.234:6322}"

# Eingebauter Default-Plan: Hauptmenü -> Multiplayer -> Connect -> Beweis.
# Koordinaten/Timing sind bewusst konservativ und müssen beim echten
# In-Game-Test auf planet festgezurrt werden (siehe README, Status OFFEN).
default_plan() {
    cat <<'EOF'
# 1) Auf das gerenderte Hauptmenü warten (Beweis ablegen).
wait-menu /srv/rbclient/screenshots/step-01-menu.png

# 2) Multiplayer/Lobby öffnen — Koordinaten TBD (live auf planet festzurren).
click 960 540
wait 3
wait-menu /srv/rbclient/screenshots/step-02-multiplayer.png

# 3) Mit dem Testserver verbinden (Adresse auflösen + eintippen + Return).
#    Ohne Argument: Server aus --server/NAV_SERVER (Default rb-winetest).
connect
wait 5
shot /srv/rbclient/screenshots/step-03-connected.png
EOF
}

usage() {
    cat >&2 <<'EOF'
nav-lobby.sh — Screenshot-gesteuerte Navigation bis zur Lobby + Server-Connect.

Optionen:
  --server NAME   Server-Name (Default: rb-winetest)
  --plan FILE     Navigationsplan (Default: eingebauter Plan)
  --timeout SEC   Gesamt-Timeout (Default: 180)
  --dry-run       Plan parsen + validieren, ohne xdotool/X auszuführen
EOF
    exit 64
}

# --- Argumente ----------------------------------------------------------------
PLAN_SRC=""        # "" = eingebauter Plan
DRY_RUN=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --server)  NAV_SERVER="${2:?--server braucht einen Wert}"; shift 2 ;;
        --plan)    PLAN_SRC="${2:?--plan braucht einen Pfad}"; shift 2 ;;
        --timeout) NAV_TIMEOUT="${2:?--timeout braucht einen Wert}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage ;;
        *) echo "nav-lobby: unbekanntes Argument: $1" >&2; usage ;;
    esac
done

# --- Server-Auflösung ---------------------------------------------------------
# "name=host:port"-Einträge parsen; Default-Server muss aufgelöst werden.
resolve_addr() {
    local name="$1" entry k v
    # shellcheck disable=SC2086  # NAV_SERVERS ist bewusst leerzeichen-getrennt.
    for entry in $NAV_SERVERS; do
        k="${entry%%=*}"; v="${entry#*=}"
        if [[ "$k" == "$name" ]]; then
            printf '%s' "$v"
            return 0
        fi
    done
    echo "nav-lobby: FEHLER: Server '$name' nicht in NAV_SERVERS aufgelöst" >&2
    return 1
}

# --- Frame-Render-Heuristik (wie wait-menu.sh) --------------------------------
frame_rendered() {
    local tmp std rendered
    tmp="$(mktemp --suffix=.png)" || return 1
    "$IMPORT_BIN" -window root "$tmp" 2>/dev/null || true
    std="0"
    if [[ -s "$tmp" ]]; then
        std="$("$CONVERT_BIN" "$tmp" -colorspace Gray -format '%[fx:standard_deviation]' info: 2>/dev/null || echo 0)"
    fi
    rm -f "$tmp"
    rendered="$(awk -v s="$std" -v t="$RENDER_MIN_STD" 'BEGIN { print (s >= t) ? "1" : "0" }')"
    [[ "$rendered" == "1" ]]
}

# --- wait-menu (gerichteter Frame) --------------------------------------------
wait_menu() {
    local out="$1" start_ts
    start_ts="$(date +%s)"
    echo "[nav-lobby] warte auf gerenderten Frame …"
    while :; do
        if frame_rendered; then
            mkdir -p "$(dirname "$out")"
            "$IMPORT_BIN" -window root "$out" 2>/dev/null || true
            echo "[nav-lobby] Frame gerendert; Screenshot: ${out}"
            return 0
        fi
        if [[ $(( $(date +%s) - start_ts )) -ge "$NAV_TIMEOUT" ]]; then
            echo "[nav-lobby] FEHLER: kein gerenderter Frame nach ${NAV_TIMEOUT}s" >&2
            mkdir -p "$(dirname "$out")"
            "$IMPORT_BIN" -window root "$out" 2>/dev/null || true
            return 1
        fi
        sleep "$RENDER_POLL"
    done
}

# --- Einzelne Aktion ausführen ------------------------------------------------
run_action() {
    local action="$1" args="$2"
    case "$action" in
        shot)
            [[ -n "$args" ]] || { echo "nav-lobby: shot braucht <file>" >&2; return 1; }
            "$XDO_NAV" shot "$args"
            ;;
        click)
            local x y btn rest
            read -r x y btn rest <<< "$args"
            [[ -n "$x" && -n "$y" ]] || { echo "nav-lobby: click braucht <x> <y>" >&2; return 1; }
            if [[ -n "$btn" ]]; then "$XDO_NAV" click "$x" "$y" "$btn"; else "$XDO_NAV" click "$x" "$y"; fi
            ;;
        key)
            [[ -n "$args" ]] || { echo "nav-lobby: key braucht <keysym>" >&2; return 1; }
            "$XDO_NAV" key "$args"
            ;;
        type)
            [[ -n "$args" ]] || { echo "nav-lobby: type braucht <text>" >&2; return 1; }
            "$XDO_NAV" type "$args"
            ;;
        wait)
            [[ -n "$args" ]] || { echo "nav-lobby: wait braucht <sec>" >&2; return 1; }
            sleep "$args"
            ;;
        wait-menu)
            [[ -n "$args" ]] || { echo "nav-lobby: wait-menu braucht <out.png>" >&2; return 1; }
            wait_menu "$args"
            ;;
        connect)
            local server addr
            server="${args%% *}"
            [[ -n "$server" ]] || server="$NAV_SERVER"
            addr="$(resolve_addr "$server")" || return 1
            echo "[nav-lobby] verbinde mit ${server} (${addr}) …"
            "$XDO_NAV" type "$addr"
            "$XDO_NAV" key Return
            ;;
        verify)
            [[ -n "$args" ]] || { echo "nav-lobby: verify braucht <cmd>" >&2; return 1; }
            echo "[nav-lobby] Verifikation: ${args}"
            # Plan-Datei ist vertrauenswürdiger Input (Repo/Operator).
            bash -c "$args"
            ;;
        *)
            echo "nav-lobby: unbekannte Aktion: ${action}" >&2
            return 1
            ;;
    esac
}

# --- Plan laden ---------------------------------------------------------------
plan_text=""
if [[ -n "$PLAN_SRC" ]]; then
    plan_text="$(cat "$PLAN_SRC")"
else
    plan_text="$(default_plan)"
fi

# --- Rendered-Check nach Eingaben (Screenshot-Loop) ---------------------------
render_check() {
    [[ "$NAV_RENDER_CHECK" == "1" ]] || return 0
    if ! frame_rendered; then
        echo "[nav-lobby] FEHLER: Frame nach Aktion nicht mehr gerendert (Client weg?)" >&2
        return 1
    fi
    return 0
}

# --- Hauptschleife ------------------------------------------------------------
start_ts="$(date +%s)"
echo "[nav-lobby] starte Navigation (Server: ${NAV_SERVER}, Timeout: ${NAV_TIMEOUT}s, dry-run: ${DRY_RUN})"

while IFS= read -r line; do
    # Kommentare + Leerzeilen überspringen (nur Vollzeilen-Kommentare,
    # damit `type`-Text auch ein '#' enthalten darf).
    line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [[ -z "$line" ]] && continue
    [[ "${line:0:1}" == "#" ]] && continue

    action="${line%% *}"
    args="${line#"$action"}"
    args="$(printf '%s' "$args" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"

    if [[ "$(date +%s)" -ge $(( start_ts + NAV_TIMEOUT )) ]]; then
        echo "[nav-lobby] FEHLER: Gesamt-Timeout ${NAV_TIMEOUT}s überschritten" >&2
        exit 1
    fi

    echo "[nav-lobby] Schritt: ${action} ${args}"
    if [[ "$DRY_RUN" == "1" ]]; then
        continue
    fi
    run_action "$action" "$args" || exit 1
    # Nach Eingaben (Maus/Tastatur/Connect) per Screenshot prüfen, dass der
    # Client noch gerendert ist (schwarz = Client weg → Abbruch).
    case "$action" in
        click|key|type|connect) render_check || exit 1 ;;
    esac
done <<< "$plan_text"

echo "[nav-lobby] Navigation abgeschlossen."
