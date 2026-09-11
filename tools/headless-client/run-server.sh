#!/usr/bin/env bash
#
# run-server.sh — startet den Riftbreaker-Dedicated-Server headless
# (xvfb-run + Wine). Entrypoint der Compose-Services (:6321/:6323 in Tests).
#
# Issue #239 — der Läufer muss WIEDERHERSTELLBAR joinable sein. Was dieses
# Skript tut (proven recipe, siehe progress-fix-239-server-joinable.md):
#   * `xvfb-run -a` statt eigenem Xvfb auf :99 — kein /tmp/.X99-lock, das einen
#     Container-Neustart in eine Boot-Schleife schickt.
#   * wine-init.sh (marker-guarded): wineboot + win10 + vcrun2022 +
#     d3dcompiler_47 im persistenten Prefix (heilt frische/alte Volumes).
#   * LAN-Modus (disable_steam "1"): WINEDLLOVERRIDES für die Steam-DLLs
#     (steamclient=n,b,steam_api64=n,b) + Steam-Env entfernt, damit der
#     Prozess direkt per IP erreichbar ist (kein Steam-Relay). Zusätzlich
#     WINEESYNC=0/WINEFSYNC=0 + LC_ALL=C.UTF-8 (Rig-Rezept): mit esync/fsync
#     crasht der Server im Boost-Thread, bevor er den UDP-Port bindet.
#   * SaveGames-Verzeichnis im Prefix auf $RB_SAVE_DIR symlinken (persistent).
#
# Aufruf (Compose): entrypoint: ["run-server.sh"] + command: [<exe>, <args…>]
# Alle Argumente werden UNVERÄNDERT an `wine` gereicht (keine Shell-Interpretation).
#
# Umgebung:
#   WINEPREFIX   Wine-Prefix (Default: /root/.wine)
#   RB_SAVE_DIR  Ziel für persistente Saves (Default: /srv/rbsaves)
set -euo pipefail

WINEPREFIX="${WINEPREFIX:-/root/.wine}"
export WINEPREFIX
export WINEARCH="${WINEARCH:-win64}"
export WINEDEBUG="${WINEDEBUG:--all,err+all}"
export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"
export GALLIUM_DRIVER="${GALLIUM_DRIVER:-llvmpipe}"
# Issue #239 — Rig-Rezept (1:1): die Wine-Sync-Primitive AUS + feste Locale.
# Mit esync/fsync crasht der DedicatedServer im Boost-Thread
# (thread_start_function), bevor er den UDP-Port bindet.
export WINEESYNC="${WINEESYNC:-0}"
export WINEFSYNC="${WINEFSYNC:-0}"
export LC_ALL="${LC_ALL:-C.UTF-8}"
# Rig nutzt $WINE (Default wine64); wine-init.sh honoriert dieselbe Variable.
WINE="${WINE:-wine}"
export WINE

RB_SAVE_DIR="${RB_SAVE_DIR:-/srv/rbsaves}"
export RB_SAVE_DIR

# ---------------------------------------------------------------------------
# 1) Save-Persistenz: Wine-SaveGames-Dir auf das Save-Volume symlinken.
#    Der Container läuft als root → Wine-User = root.
# ---------------------------------------------------------------------------
WINE_USER_DIR="${WINEPREFIX}/drive_c/users/root"
mkdir -p "${RB_SAVE_DIR}"

link_save_dir() {
  local dir="$1"
  mkdir -p "$(dirname "${dir}")"
  rm -rf "${dir}"
  ln -sfn "${RB_SAVE_DIR}" "${dir}"
  echo "[run-server] Save-Symlink: ${dir} -> ${RB_SAVE_DIR}"
}
link_save_dir "${WINE_USER_DIR}/AppData/LocalLow/The Riftbreaker - Dedicated Server/SaveGames"
link_save_dir "${WINE_USER_DIR}/Documents/The Riftbreaker - Dedicated Server"

# ---------------------------------------------------------------------------
# 2) Wine-Prefix-Runtime sicherstellen (marker-guarded, idempotent).
# ---------------------------------------------------------------------------
if command -v wine-init.sh >/dev/null 2>&1; then
  wine-init.sh
else
  echo "[run-server] WARNUNG: wine-init.sh fehlt — Runtime-Selbstheilung übersprungen." >&2
fi

# ---------------------------------------------------------------------------
# 3) LAN-Modus (disable_steam "1"): Steam aus dem Spielprozess heraushalten.
#    Steamclient/steam_api64 als native DLL blocken + Steam-Env entfernen,
#    damit der Server direkt per IP erreichbar ist (kein Steam-Relay).
# ---------------------------------------------------------------------------
export WINEDLLOVERRIDES="${WINEDLLOVERRIDES:-mscoree,mshtml=},steamclient=n,b,steam_api64=n,b"
unset SteamAppId SteamGameId STEAMAPPID LD_LIBRARY_PATH 2>/dev/null || true

# steam_appid.txt würde den Prozess sonst in den Steam-Modus zwingen.
EXE_DIR="$(dirname "${1:-.}")"
rm -f steam_appid.txt "${EXE_DIR}/steam_appid.txt" 2>/dev/null || true

# Issue #239 — Rig-Rezept (1:1): Steam-Env VOR dem exec zusätzlich per
# `env -u …` strippen (der Shell-`unset` oben deckt nur die aktuelle Shell ab).
echo "[run-server] starte: env -u SteamAppId -u SteamGameId -u STEAMAPPID -u LD_LIBRARY_PATH ${WINE} $*"
exec env -u SteamAppId -u SteamGameId -u STEAMAPPID -u LD_LIBRARY_PATH \
    xvfb-run -a "${WINE}" "$@"
