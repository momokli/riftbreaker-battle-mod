#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/riftbreaker}"
WINEPREFIX="${WINEPREFIX:-/data/.wine}"
CONFIG_FILE="${CONFIG_FILE:-/data/config/config.cfg}"
SAVE_MOUNT="${SAVE_MOUNT:-/data/saves}"
STEAM_USER="${STEAM_USER:-steamuser}"
WINE="/usr/bin/wine"
LOG_DIR="${WINEPREFIX}/logs"
CONFIG_DEST="${INSTALL_DIR}/config.cfg"
STARTUP_CHECK_SECS="${STARTUP_CHECK_SECS:-90}"

# Set later from the deployed config: 0 = LAN/direct-connect, 1 = Steam (server browser).
STEAM_MODE=0
# App id the game process registers under in Steam mode (game = 780310, server tool = 4114030).
STEAM_GAME_APPID="${STEAM_GAME_APPID:-780310}"

export WINEPREFIX
# Eine Quelle für WINEDEBUG: -all (vgl. Dockerfile-ENV + wine-init.sh). Verhindert
# widersprüchliche Syntax (-all,err+all); per Compose-Env übersteuerbar.
export WINEDEBUG="${WINEDEBUG:--all}"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo "[entrypoint] ERROR: missing ${CONFIG_FILE}" >&2
  echo "[entrypoint] Copy config/config.cfg.example to config/config.cfg and edit it." >&2
  exit 1
fi

WINE_USER_DIR="${WINEPREFIX}/drive_c/users/${STEAM_USER}"
WINE_SAVE_DIR="${WINE_USER_DIR}/AppData/LocalLow/The Riftbreaker - Dedicated Server/SaveGames"

# --- Trainer-I/O-Bridge / Injection-Supervisor (Issue #265) -------------------
# Die Rolle rbtools stagt die Windows-Tools nach ${RBBRIDGE_TOOLS_DIR} (read-only
# nach /opt/rbtools gemountet). Beim Start injiziert der Supervisor rbbridge.dll
# in DedicatedServer.exe (Retry/Backoff) und startet danach pipe_bridge.exe
# (HTTP 9001 -> Named-Pipe). Fehlt das Verzeichnis: nur Warnung, der Server
# laeuft normal weiter.
RBBRIDGE_TOOLS_DIR="${RBBRIDGE_TOOLS_DIR:-/opt/rbtools}"
# Wine sieht den Container-Root als Z: -> die DLL liegt als Z:\opt\rbtools\...
RBBRIDGE_DLL_WIN="${RBBRIDGE_DLL_WIN:-Z:\\opt\\rbtools\\rbbridge.dll}"
RBBRIDGE_DISPLAY="${RBBRIDGE_DISPLAY:-:99}"
INJECT_TIMEOUT_SECS="${INJECT_TIMEOUT_SECS:-180}"

copy_server_config() {
  local dest="$1"
  mkdir -p "$(dirname "${dest}")"
  sed 's/\r$//' "${CONFIG_FILE}" | sed 's/^\xEF\xBB\xBF//' > "${dest}"
}

follow_server_logs() {
  local logfile="$1"
  mkdir -p "$(dirname "${logfile}")" 2>/dev/null || true
  echo "[entrypoint] Streaming ${logfile} to docker logs"
  stdbuf -oL tail -n 0 -F "${logfile}" 2>/dev/null | while IFS= read -r line || [[ -n "${line}" ]]; do
    printf '[server] %s\n' "${line}"
  done &
}

log_search_paths() {
  local paths=(
    "${WINE_USER_DIR}/Documents/The Riftbreaker/exor_logs.txt"
    "${WINE_USER_DIR}/AppData/LocalLow/The Riftbreaker - Dedicated Server/exor_logs.txt"
  )
  if [[ -n "${SERVER_DIR:-}" ]]; then
    paths+=("${SERVER_DIR}/exor_logs.txt" "${INSTALL_DIR}/exor_logs.txt")
  fi
  printf '%s\n' "${paths[@]}"
}

start_log_watchers() {
  # tail -F retries on not-yet-existing files and follows rotation, so the
  # known candidate paths are enough — no periodic find/discovery needed.
  while IFS= read -r logfile; do
    follow_server_logs "${logfile}"
  done < <(log_search_paths)
}

# start_ingress_supervisor: startet Xvfb :99 (Socket-Readiness statt xdpyinfo —
# das Tool fehlt im Image) und danach eine Hintergrund-Subshell, die auf den
# Serverprozess wartet, rbbridge.dll injiziert (Retry/Backoff) und zuletzt die
# HTTP-Bridge startet. Die Subshell ueberlebt das spaetere `exec` des Servers.
start_ingress_supervisor() {
  local tools_dir="${RBBRIDGE_TOOLS_DIR}"

  if [[ ! -x "${tools_dir}/injector.exe" || ! -f "${tools_dir}/rbbridge.dll" \
        || ! -x "${tools_dir}/pipe_bridge.exe" ]]; then
    echo "[entrypoint] WARNING: ${tools_dir} unvollstaendig" >&2
    echo "[entrypoint]          (injector.exe/rbbridge.dll/pipe_bridge.exe) —" >&2
    echo "[entrypoint]          Injection/Bridge uebersprungen, Server laeuft normal." >&2
    return 0
  fi

  # Xvfb SYNCHRON vor dem Server-Start: so hat der Server-xvfb-run (-a) :99
  # bereits belegt und waehlt eine andere Nummer (kein Race um :99).
  if [[ ! -S /tmp/.X11-unix/X99 ]]; then
    echo "[entrypoint] ingress: starte Xvfb :99 (1920x1080x24)"
    Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 &
  fi
  for _ in $(seq 1 40); do
    [[ -S /tmp/.X11-unix/X99 ]] && break
    sleep 0.25
  done

  (
    set +e

    if [[ -S /tmp/.X11-unix/X99 ]]; then
      export DISPLAY="${RBBRIDGE_DISPLAY}"
      echo "[entrypoint] ingress: DISPLAY=${DISPLAY} bereit"
    else
      echo "[entrypoint] ingress: WARNUNG: /tmp/.X11-unix/X99 fehlt — fahre ohne DISPLAY fort" >&2
    fi

    echo "[entrypoint] ingress: warte auf DedicatedServer.exe ..."
    for _ in $(seq 1 120); do
      pgrep -f 'DedicatedServer.exe' >/dev/null 2>&1 && break
      sleep 1
    done
    if ! pgrep -f 'DedicatedServer.exe' >/dev/null 2>&1; then
      echo "[entrypoint] ingress: WARNUNG: DedicatedServer.exe nicht gefunden — Injection uebersprungen" >&2
      return 0
    fi

    echo "[entrypoint] ingress: injiziere rbbridge.dll in DedicatedServer.exe (bis ${INJECT_TIMEOUT_SECS}s)"
    deadline=$((SECONDS + INJECT_TIMEOUT_SECS))
    attempt=0
    injected=0
    while (( SECONDS < deadline )); do
      attempt=$((attempt + 1))
      out="$("${WINE}" "${tools_dir}/injector.exe" DedicatedServer.exe "${RBBRIDGE_DLL_WIN}" 2>&1)"
      rc=$?
      printf '%s\n' "${out}" | sed 's/^/[rbtools] /'
      if (( rc == 0 )); then
        injected=1
        echo "[entrypoint] ingress: Injection erfolgreich (Versuch ${attempt})"
        break
      fi
      if (( attempt < 5 )); then backoff=$((attempt * 2)); else backoff=10; fi
      echo "[entrypoint] ingress: Injection fehlgeschlagen (rc=${rc}), Retry in ${backoff}s"
      sleep "${backoff}"
    done
    if (( injected == 0 )); then
      echo "[entrypoint] ingress: WARNUNG: Injection nach ${INJECT_TIMEOUT_SECS}s nicht erfolgreich — Bridge startet trotzdem" >&2
    fi

    echo "[entrypoint] ingress: starte pipe_bridge.exe (HTTP 9001 -> Named-Pipe rbbattle)"
    exec "${WINE}" "${tools_dir}/pipe_bridge.exe" 2>&1
  ) &
}

grep_logs() {
  local pattern="$1" logfile
  while IFS= read -r logfile; do
    [[ -f "${logfile}" ]] || continue
    if grep -q "${pattern}" "${logfile}" 2>/dev/null; then
      return 0
    fi
  done < <(log_search_paths)
  return 1
}

watch_server_startup() {
  local ready_logged=0
  local deadline=$((SECONDS + STARTUP_CHECK_SECS))

  while (( SECONDS < deadline )); do
    if grep_logs 'activating: MenuState' || grep_logs 'missions/inventory.mission'; then
      echo "[entrypoint] ERROR: config was not loaded (MenuState / inventory.mission in logs)." >&2
      echo "[entrypoint] Ensure config.cfg exists at ${CONFIG_DEST} (install root, not bin/)." >&2
      pkill -f 'DedicatedServer.exe' 2>/dev/null || true
      sleep 2
      kill -TERM 1 2>/dev/null || true
      exit 1
    fi
    if (( ready_logged == 0 )) && grep_logs 'ServerGameplayState'; then
      echo "[entrypoint] Server entered ServerGameplayState — config loaded successfully"
      ready_logged=1
    fi
    if (( ready_logged == 0 )) && grep_logs '\[NetServerGNS\] running as:'; then
      echo "[entrypoint] NetServer is listening"
      ready_logged=1
    fi
    if (( ready_logged == 0 )) && (( STEAM_MODE == 1 )) && grep_logs 'listening for P2P connection as'; then
      echo "[entrypoint] Steam P2P networking is active — server should appear in the in-game server browser"
      ready_logged=1
    fi
    sleep 5
  done
}

watch_server_port() {
  if (( STEAM_MODE == 1 )); then
    echo "[entrypoint] Steam mode — traffic uses Steam relay/P2P; not waiting for a local UDP 6321 bind"
    return 0
  fi
  (
    for _ in $(seq 1 36); do
      if ss -H -uln 2>/dev/null | grep -q ':6321 '; then
        echo "[entrypoint] UDP port 6321 is open — server should accept connections"
        return 0
      fi
      if ss -H -ltn 2>/dev/null | grep -q ':6321 '; then
        echo "[entrypoint] TCP port 6321 is open"
      fi
      sleep 5
    done
    echo "[entrypoint] WARNING: port 6321 not open after 3 minutes — check logs and config.cfg" >&2
  ) &
}

# Content kommt als bind-mount (deploy: /srv/rbgame -> /opt/riftbreaker). Kein
# SteamCMD im Image -> kein Runtime-Content-Update nötig.

mkdir -p "${WINEPREFIX}" "${SAVE_MOUNT}" "${LOG_DIR}" "$(dirname "${WINE_SAVE_DIR}")"
/scripts/wine-init.sh

link_save_dir() {
  local dir="$1"
  mkdir -p "$(dirname "${dir}")"
  rm -rf "${dir}"
  ln -sfn "${SAVE_MOUNT}" "${dir}"
  echo "[entrypoint] Save symlink: ${dir} -> ${SAVE_MOUNT}"
}

# Dedicated server (not the main-game client path Documents/The Riftbreaker/campaignV2).
link_save_dir "${WINE_USER_DIR}/AppData/LocalLow/The Riftbreaker - Dedicated Server/SaveGames"
link_save_dir "${WINE_USER_DIR}/Documents/The Riftbreaker - Dedicated Server"

SERVER_EXE="$(find "${INSTALL_DIR}" -iname 'DedicatedServer.exe' -print -quit 2>/dev/null || true)"
if [[ -z "${SERVER_EXE}" || ! -f "${SERVER_EXE}" ]]; then
  echo "[entrypoint] ERROR: DedicatedServer.exe not found in ${INSTALL_DIR}" >&2
  exit 1
fi

SERVER_DIR="$(dirname "${SERVER_EXE}")"

# Game resolves config=config.cfg from the install root (not bin/).
copy_server_config "${CONFIG_DEST}"
rm -f "${SERVER_DIR}/config.cfg"

if ! grep -q 'set app_mode "server"' "${CONFIG_DEST}" 2>/dev/null; then
  echo "[entrypoint] WARNING: set app_mode \"server\" not found in ${CONFIG_DEST}" >&2
fi

# Mode is driven by disable_steam in the deployed config:
#   set disable_steam "1"            -> LAN/direct-connect (Steam fully blocked)
#   set disable_steam "0" or absent  -> Steam mode (visible in the in-game server browser)
if grep -E '^\s*set\s+disable_steam\s+"1"' "${CONFIG_DEST}" >/dev/null 2>&1; then
  STEAM_MODE=0
else
  STEAM_MODE=1
fi

if (( STEAM_MODE == 0 )); then
  # LAN: keep Steam out of the game process so direct connect works.
  # STEAMAPPID in the image is for SteamCMD; it must not reach the game process.
  rm -f "${SERVER_DIR}/steam_appid.txt" "${INSTALL_DIR}/steam_appid.txt" 2>/dev/null || true
  export WINEDLLOVERRIDES="${WINEDLLOVERRIDES:-mscoree,mshtml=},steamclient=n,b,steam_api64=n,b"
  unset SteamAppId SteamGameId STEAMAPPID LD_LIBRARY_PATH 2>/dev/null || true
  echo "[entrypoint] LAN mode (disable_steam \"1\") — STEAMAPPID cleared for game process (connect \"127.0.0.1\" on same host)"
else
  # Steam mode: let the game initialise Steam networking so it appears in the server browser.
  echo "${STEAM_GAME_APPID}" > "${SERVER_DIR}/steam_appid.txt"
  echo "${STEAM_GAME_APPID}" > "${INSTALL_DIR}/steam_appid.txt"
  export STEAMAPPID="${STEAM_GAME_APPID}"
  export SteamAppId="${STEAM_GAME_APPID}"
  export SteamGameId="${STEAM_GAME_APPID}"
  # Do NOT block the Steam DLLs in this mode — they are required for Steam networking.
  export WINEDLLOVERRIDES="${WINEDLLOVERRIDES:-mscoree,mshtml=}"
  echo "[entrypoint] Steam mode (disable_steam not \"1\") — app id ${STEAM_GAME_APPID}; server should appear in the in-game server browser"
fi

cd "${SERVER_DIR}"

start_log_watchers
watch_server_port

echo "[entrypoint] Active config at ${CONFIG_DEST}:"
grep -vE '^\s*//' "${CONFIG_DEST}" | grep -vE '^\s*$' || true

watch_server_startup &

echo "[entrypoint] Deployed config: ${CONFIG_DEST}"

# Trainer-I/O: Injection-Supervisor + HTTP-Bridge VOR dem Server-`exec` starten
# (die Subshell ueberlebt das exec).
start_ingress_supervisor

echo "[entrypoint] Starting: DedicatedServer.exe cli=1 config=config.cfg (cwd=${SERVER_DIR})"
if (( STEAM_MODE == 0 )); then
  # LAN: strip Steam env so the game cannot pick up an app id and falls back to direct IP.
  exec env -u SteamAppId -u SteamGameId -u STEAMAPPID -u LD_LIBRARY_PATH \
    bash -c 'xvfb-run -a "'"${WINE}"'" DedicatedServer.exe cli=1 config=config.cfg 2>&1'
else
  # Steam: keep the Steam env so the game registers with Steam networking.
  exec bash -c 'xvfb-run -a "'"${WINE}"'" DedicatedServer.exe cli=1 config=config.cfg 2>&1'
fi
