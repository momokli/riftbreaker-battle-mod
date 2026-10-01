#!/bin/sh
# Root-Wrapper des CD (kanonische Quelle; Installation als
# /usr/local/bin/rbbattle-deploy — Anleitung in deploy/README.md → „CD: SSH-Deploy").
#
# Issue #483 (Ziel A / Live-Befund #2): je Env ein EIGENER Checkout
# (`/opt/rbbattle-deploy/repo-<env>`) mit eigenem Ref/SHA-Marker.
#
# PROD-ONLY (Issue #1034): es gibt nur noch die prod-Welt. Der forced command
# (deploy/deploy-ssh.sh) akzeptiert nur `refs/tags/v*` -> env=prod; hier wird
# entsprechend immer deploy/deploy-prod.yml gefahren. dev (main)/staging sind
# entfallen — ein unbekannter/ungueltiger ref bricht fail loud ab.
#
# Der forced command hat vorher geschrieben:
#   .deploy-prod.sha / .deploy-prod.ref  (env-spezifisch)
#   .deploy-env                          (welche Env = prod)
#
# Rollout/Rueckwaerts-Kompatibilitaet: fehlt .deploy-env (Bestand vor dem
# Umbau), wird die Env aus den LEGACY-Markern .deploy-sha/-ref abgeleitet und
# der bestehende Checkout `repo/` weiterbenutzt (Einmal-Migration der Marker/
# Checkouts, siehe deploy/README.md). Danach arbeitet jeder Lauf env-lokal.
#
# Das Playbook laeuft mit `become: true`; root gibt es ausschliesslich über das
# enge sudoers-Snippet (NOPASSWD nur für diesen Wrapper, ohne Argumente).
#
# Testbare Übersteuerungen (Default = Produktion):
#   RBBATTLE_DEPLOY_ROOT         -> /opt/rbbattle-deploy
#   RBBATTLE_DEPLOY_REPO_URL     -> GitHub-URL des Repos
#   RBBATTLE_DEPLOY_ANSIBLE_BIN  -> /opt/rb-ansible/bin/ansible-playbook
#   RBBATTLE_DEPLOY_VAULT_FILE   -> /etc/rbbattle-deploy/vault.pass
#   RBBATTLE_DEPLOY_ANSIBLE_CONFIG -> /etc/rbbattle-deploy/ansible.cfg
set -eu

deploy_root="${RBBATTLE_DEPLOY_ROOT:-/opt/rbbattle-deploy}"
repo_url="${RBBATTLE_DEPLOY_REPO_URL:-https://github.com/momokli/riftbreaker-battle-mod.git}"
ansible_bin="${RBBATTLE_DEPLOY_ANSIBLE_BIN:-/opt/rb-ansible/bin/ansible-playbook}"
vault_file="${RBBATTLE_DEPLOY_VAULT_FILE:-/etc/rbbattle-deploy/vault.pass}"

export HOME="$deploy_root"
export ANSIBLE_CONFIG="${RBBATTLE_DEPLOY_ANSIBLE_CONFIG:-/etc/rbbattle-deploy/ansible.cfg}"

env="$(cat "$deploy_root/.deploy-env" 2>/dev/null || true)"
legacy=0
if [ -z "$env" ]; then
  # Fallback (Bestand vor dem Umbau, Issue #1034): nur prod ist noch gueltig.
  legacy=1
  legacy_ref="$(cat "$deploy_root/.deploy-ref" 2>/dev/null || true)"
  case "$legacy_ref" in
    refs/tags/v*) env=prod ;;
    *) echo "rbbattle-deploy: ungültiger Legacy-ref '${legacy_ref}' (nur refs/tags/v*, Issue #1034)" >&2; exit 1 ;;
  esac
fi

case "$env" in
  prod) ;;
  *) echo "rbbattle-deploy: ungültige env '${env}' (nur prod, Issue #1034)" >&2; exit 1 ;;
esac

if [ "$legacy" -eq 1 ]; then
  repo="$deploy_root/repo"
  sha="$(cat "$deploy_root/.deploy-sha" 2>/dev/null || true)"
  ref="$legacy_ref"
else
  repo="$deploy_root/repo-$env"
  sha="$(cat "$deploy_root/.deploy-$env.sha" 2>/dev/null || true)"
  ref="$(cat "$deploy_root/.deploy-$env.ref" 2>/dev/null || true)"
fi

# Eigener Checkout je Env: beim ersten prod-Lauf nach dem Umbau frisch klonen.
# Vorher: git clone (als root) + Ownership normalisieren. Agent-/RE-Arbeit legt
# auf planet teils root-owned Dateien ab — liefe der Checkout als deploy, schlüge
# er mit "unable to unlink … Permission denied" fehl.
if [ ! -d "$repo/.git" ]; then
  git clone --quiet "$repo_url" "$repo"
fi

cd "$repo"
git fetch --prune --quiet origin
if [ -n "$sha" ]; then
  git checkout --force "$sha" >/dev/null
fi
chown -R deploy:deploy "$repo"

# Dispatch: seit #1034 gibt es nur noch EINEN Deploy-Pfad (Tag v* -> prod A+B).
#
# Issue #995: deploy/deploy-prod.yml faehrt mehrere Plays (Play 0 host-services
# + Prod-A + Prod-B), jede laedt ihre Vars per `vars_files` (prod-vars.yml /
# prod-b-vars.yml). Ein globales `-e @deploy/prod-vars.yml` haette Extra-Vars-
# Precedence und wuerde damit auch den B-Play ueberschreiben -> deshalb hier
# KEIN `-e` mehr.
case "$ref" in
  refs/tags/v*)
    exec "$ansible_bin" \
      -i deploy/inventory deploy/deploy-prod.yml \
      --vault-password-file "$vault_file"
    ;;
  *)
    echo "rbbattle-deploy: ungültiger ref '${ref}' (nur refs/tags/v*, Issue #1034)" >&2
    exit 1
    ;;
esac
