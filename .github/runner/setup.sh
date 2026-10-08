#!/usr/bin/env bash
# ============================================================================
# .github/runner/setup.sh — Setup-as-Code für den self-hosted GitHub-Actions-
# Runner auf planet (Label `planet`), der die Jobs `test`/`build` aus ci.yml
# ausführt.
#
# Ziel (Issue #153): Build-/Test-Dependencies reproduzierbar und idempotent auf
# einem frischen Host aufziehen (< 10 min), damit der per-Job-
# `sudo apt-get install gcc-mingw-w64-x86-64` aus ci.yml entfällt und der
# sudoers-NOPASSWD-Hack für apt obsolet wird.
#
# Was dieses Skript macht:
#   1. System-Pakete installieren (MinGW-w64, ccache, zip).
#   2. ccache als Compiler-Wrapper für x86_64-w64-mingw32-gcc/g++ registrieren.
#   3. Rust-Toolchain (rustup/cargo) provisionieren (Issue #318): für den
#      cargo-Build des tournament-server.
#   4. Node.js / npm / Python 3 verifizieren (CI liefert Node 20 via
#      actions/setup-node; hier wird nur geprüft, dass node im PATH ist).
#   5. Toolchain-Verifikation ausgeben (Exit 0 = fertig, sonst Fehler).
#
# Idempotent und race-frei: kann beliebig oft laufen. apt wird nur bemüht, wenn
# ein Paket fehlt; Symlinks werden überschrieben statt dupliziert; die
# Rust-Toolchain entsteht unter flock-Schutz und wird erst nach
# erfolgreichem Install atomar an ihren Zielort verschoben (Variante A: mehrere
# Runner-Instanzen teilen sich denselben $HOME -> setup.sh nie zweimal parallel
# bauen lassen).
#
# Bewusst NICHT Teil dieses Skripts (Host-Änderungen bleiben manuell):
#   - Registrierung des Actions-Runner (siehe CONTRIBUTING.md).
#   - tmpfs-Mount für `_work` und Scale auf 2 Runner-Instanzen (README.md).
#   - Entfernen der alten sudoers-Regel /etc/sudoers.d/runner-apt — sie wird
#     erst obsolet, sobald ci.yml kein sudo mehr braucht; die Löschung selbst
#     erfolgt manuell (siehe README.md, Abschnitt „Offen").
#
# Aufruf (Benutzer mit sudo-Rechten, z. B. auf planet):
#   bash .github/runner/setup.sh
# ============================================================================
set -euo pipefail

# Privilegierte Aufrufe (apt-get, /opt-Symlinks) laufen über sudo, sofern das
# Skript nicht bereits als root ausgeführt wird.
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

echo "== [1/5] System-Pakete (MinGW-w64, ccache, zip) =="
for pkg in gcc-mingw-w64-x86-64 ccache zip python3; do
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed'; then
        echo "  OK: $pkg bereits installiert."
    else
        echo "  installiere $pkg ..."
        $SUDO apt-get update -y
        $SUDO apt-get install -y "$pkg"
    fi
done

echo "== [2/5] ccache-Wrapper für x86_64-w64-mingw32-gcc/g++ =="
CCACHE_BIN_DIR="${CCACHE_BIN_DIR:-/opt/ccache-rbbattle/bin}"
$SUDO mkdir -p "$CCACHE_BIN_DIR"
for tool in x86_64-w64-mingw32-gcc x86_64-w64-mingw32-g++; do
    $SUDO ln -sf "$(command -v ccache)" "$CCACHE_BIN_DIR/$tool"
done
ls -l "$CCACHE_BIN_DIR"

echo "== [3/5] Rust-Toolchain (rustup/cargo für den tournament-server-Build) =="
if [ -x "$HOME/.cargo/bin/cargo" ]; then
    echo "  OK: cargo vorhanden ($("$HOME/.cargo/bin/cargo" --version))."
else
    if ! command -v curl >/dev/null 2>&1; then
        echo "  FEHLER: curl fehlt — Rust-Toolchain kann nicht installiert werden." >&2
        exit 1
    fi
    mkdir -p "$HOME/.cargo"
    (
        flock -x 9
        if [ ! -x "$HOME/.cargo/bin/cargo" ]; then
            curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
                sh -s -- -y --default-toolchain stable --profile minimal --no-modify-path
            echo "  erstellt: $HOME/.cargo/bin/cargo"
        else
            echo "  OK: cargo parallel von anderem Lauf provisioniert."
        fi
    ) 9>"$HOME/.cargo/.rustup-install.lock"
    "$HOME/.cargo/bin/cargo" --version
fi

echo "== [4/5] Node.js / npm / Python verifizieren =="
if command -v node >/dev/null 2>&1; then
    echo "  node $(node --version)"
else
    echo "  FEHLER: node fehlt im PATH." >&2
    echo "          CI nutzt actions/setup-node@v4 (Node 20); auf dem Runner-Host" >&2
    echo "          sollte Node trotzdem vorhanden sein (npm/PATH-Verfügbarkeit)." >&2
    exit 1
fi
if command -v npm >/dev/null 2>&1; then
    echo "  npm $(npm --version)"
else
    echo "  FEHLER: npm fehlt im PATH." >&2
    exit 1
fi
echo "  python3 $(python3 --version 2>&1)"

echo "== [5/5] Toolchain-Verifikation =="
if command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1; then
    echo "  OK: $(command -v x86_64-w64-mingw32-gcc)"
else
    echo "  FEHLER: x86_64-w64-mingw32-gcc nicht im PATH." >&2
    exit 1
fi
ccache --version | head -n 1

echo
echo "Setup abgeschlossen. Der build-Job in ci.yml prepended automatisch"
echo "$CCACHE_BIN_DIR auf den PATH und nutzt so den ccache-Wrapper."
echo "Nächste Schritte (tmpfs/Scale/Health): siehe .github/runner/README.md"
echo "und docs/ci-parallel-safety.md."
