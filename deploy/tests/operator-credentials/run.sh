#!/usr/bin/env bash
# Issue #535: Abtrennungs-/Entkopplungs-Guard der Operator-Credentials.
#
# Hermetisch (localhost, kein Host, kein SSH, kein Vault, keine Prod-Aktion).
# Gerendert wird die ECHTE rift-caddy.Caddyfile.j2 + die ECHTEN Rollen-Defaults.
#
# Drei Faelle:
#   1. dev-Default  -> contract_basic_auth_hash == Legacy-Hash (byte-identisch),
#      das Caddyfile traegt den Legacy-Hash in allen drei Operator-Bloecken.
#   2. prod-Override (Play-Var) -> der Vault-Wert steht in allen drei
#      Bloecken, der geteilte Legacy-Hash ist NIRGENDS mehr vorhanden.
#   3. Entkopplung der GNS-Lobby:
#      (a) mit eigenem vault_proxy_basic_auth_hash -> Lobby traegt genau diesen,
#          unabhaengig vom Cockpit-Override (Lobby-Hash != Cockpit-Hash);
#      (b) ohne eigenen Vault-Wert, aber mit Cockpit-Override -> Lobby faellt auf
#          den Legacy-Hash zurueck und NICHT auf den Cockpit-Hash.
#
# Laeuft im deploy-check-local Gate.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
play="$here/render.yml"
ANSIBLE_PLAYBOOK="${ANSIBLE_PLAYBOOK:-ansible-playbook}"

# Zwei verschiedene, vom Legacy abweichende Dummy-Hashes (kein Secret).
COCKPIT_HASH='$2a$14$opcredcombatcockpithash0000000000000000000000000000000'
LOBBY_HASH='$2a$14$opcredgnslobbyhash00000000000000000000000000000000000'

run_case() {
  local label="$1"; shift
  echo "== $label"
  RB_OPCRED_RENDER_DIR="$(mktemp -d)" "$ANSIBLE_PLAYBOOK" "$play" "$@"
}

# ---------------------------------------------------------------------------
# Fall 1: dev-Default (kein Play-Var, keine Vault-Werte) -> Legacy byte-identisch.
# ---------------------------------------------------------------------------
run_case "Fall 1: dev-Default -> Legacy-Hash (byte-identisch)"

# ---------------------------------------------------------------------------
# Fall 2: prod-Override (Play-Var) -> Vault-Hash, kein Legacy-Rest.
# ---------------------------------------------------------------------------
run_case "Fall 2: Override -> Vault-Hash, Legacy absent" \
  -e "vault_contract_basic_auth_hash=$COCKPIT_HASH" \
  -e "expect_legacy_absent=true"

# ---------------------------------------------------------------------------
# Fall 3a: Entkopplung mit eigenem Lobby-Vault-Wert (Cockpit != Lobby).
# ---------------------------------------------------------------------------
run_case "Fall 3a: Lobby eigener Vault-Wert, entkoppelt vom Cockpit" \
  -e "vault_contract_basic_auth_hash=$COCKPIT_HASH" \
  -e "vault_proxy_basic_auth_hash=$LOBBY_HASH" \
  -e "expect_legacy_absent=true" \
  -e "expect_lobby_equals_proxy_vault=true"

# ---------------------------------------------------------------------------
# Fall 3b: Lobby OHNE eigenen Vault-Wert, aber MIT Cockpit-Override -> Legacy-
# Fallback (NICHT der Cockpit-Hash). Das ist der Kern der Entkopplung: vorher
# war proxy_basic_auth_hash == contract_basic_auth_hash.
# ---------------------------------------------------------------------------
run_case "Fall 3b: Lobby faellt auf Legacy zurueck, nicht auf Cockpit-Hash" \
  -e "vault_contract_basic_auth_hash=$COCKPIT_HASH" \
  -e "expect_legacy_absent=true" \
  -e "expect_lobby_legacy_fallback=true"

echo
echo "OK: Cockpit-Hash je Env getrennt, GNS-Lobby entkoppelt (Issue #535)."
