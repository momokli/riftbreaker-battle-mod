#!/usr/bin/env bash
#
# xdo-nav.sh — xdotool-Navigation-Wrapper für den headless Client.
#
# Kleine Helfer für Maus/Tastatur und Screenshots gegen den virtuellen
# Framebuffer (Xvfb). Der Client läuft fullscreen auf DISPLAY=:99; alle
# Koordinaten sind daher Bildschirmkoordinaten.
#
# Sub-Kommandos:
#   xdo-nav.sh click <x> <y> [button]   Klick (Default: Linksklick button=1)
#   xdo-nav.sh move  <x> <y>            Maus bewegen
#   xdo-nav.sh key   <keysym>           Taste drücken (z. B. Return, Escape)
#   xdo-nav.sh type  <text>             Text tippen
#   xdo-nav.sh shot  <file> [delay]     Screenshot (ImageMagick import)

set -euo pipefail

DISPLAY="${DISPLAY:-:99}"
export DISPLAY

usage() {
    cat >&2 <<'EOF'
xdo-nav.sh — xdotool-Navigation-Wrapper für den headless Client.

Sub-Kommandos:
  xdo-nav.sh click <x> <y> [button]   Klick (Default: Linksklick button=1)
  xdo-nav.sh move  <x> <y>            Maus bewegen
  xdo-nav.sh key   <keysym>           Taste drücken (z. B. Return, Escape)
  xdo-nav.sh type  <text>             Text tippen
  xdo-nav.sh shot  <file> [delay]     Screenshot (ImageMagick import)
EOF
    exit 64
}

cmd="${1:-}"
shift || true

case "$cmd" in
    click)
        x="${1:?x-Koordinate fehlt}"
        y="${2:?y-Koordinate fehlt}"
        btn="${3:-1}"
        xdotool mousemove "$x" "$y" click "$btn"
        ;;
    move)
        x="${1:?x-Koordinate fehlt}"
        y="${2:?y-Koordinate fehlt}"
        xdotool mousemove "$x" "$y"
        ;;
    key)
        key="${1:?Keysym fehlt}"
        xdotool key "$key"
        ;;
    type)
        text="${1:?Text fehlt}"
        xdotool type --delay 50 "$text"
        ;;
    shot)
        file="${1:?Dateipfad fehlt}"
        delay="${2:-0}"
        if [[ "$delay" -gt 0 ]]; then
            sleep "$delay"
        fi
        import -window root "$file"
        echo "[xdo-nav] Screenshot: ${file}"
        ;;
    *)
        usage
        ;;
esac
