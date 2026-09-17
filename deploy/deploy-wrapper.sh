#!/bin/sh
# Root-Wrapper des CD (kanonische Quelle; Installation als
# /usr/local/bin/rbbattle-deploy — Anleitung in deploy/README.md → „CD: SSH-Deploy").
#
# Issue #483 (Ziel A / Live-Befund #2): je Env ein EIGENER Checkout
# (`/opt/rbbattle-deploy/repo-<env>`) mit eigenem Ref/SHA-Marker. dev (main) und
# prod (v*) können so PARALLEL laufen, ohne sich Ref/SHA im gemeinsamen repo
# gegenseitig zu überschreiben.
#
# Der forced command (deploy/deploy-ssh.sh) hat vorher geschrieben:
#   .deploy-<env>.sha / .deploy-<env>.ref  (pro Env)
#   .deploy-env                            (welche Env)
#
# Rollout/Rückwärts-Kompatibilität: fehlt .deploy-env (Bestand vor dem Umbau),
# wird die Env aus den LEGACY-Markern .deploy-sha/-ref abgeleitet und der
# bestehende Checkout `repo/` weiterbenutzt (Einmal-Migration der Marker/
# Checkouts, siehe deploy/README.md). Danach arbeitet jeder Lauf env-lokal.
#
# Das Playbook läuft mit `become: true`; root gibt es ausschließlich über das
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
  # Fallback (Bestand vor dem Umbau): Env aus den Legacy-Markern ableiten und
  # den bestehenden Checkout `repo/` benutzen.
  legacy=1
  legacy_ref="$(cat "$deploy_root/.deploy-ref" 2>/dev/null || true)"
  case "$legacy_ref" in
    refs/tags/v*) env=prod ;;
    *) env=dev ;;
  esac
fi

case "$env" in
  dev|prod|staging) ;;
  *) echo "rbbattle-deploy: ungültige env '${env}'" >&2; exit 1 ;;
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

# Eigener Checkout je Env: beim ersten prod-Lauf nach dem Umbau frisch klonen
# (der dev-Checkout bleibt unangetastet). Vorher: git clone (als root) +
# Ownership normalisieren. Agent-/RE-Arbeit legt auf planet teils root-owned
# Dateien ab — liefe der Checkout als deploy, schlüge er mit
# "unable to unlink … Permission denied" fehl.
if [ ! -d "$repo/.git" ]; then
  git clone --quiet "$repo_url" "$repo"
fi

cd "$repo"
git fetch --prune --quiet origin
if [ -n "$sha" ]; then
  git checkout --force "$sha" >/dev/null
fi
chown -R deploy:deploy "$repo"

# Dispatch anhand des ref: Tag v* -> prod, Branch staging -> staging, sonst dev.
case "$ref" in
  refs/tags/v*)
    exec "$ansible_bin" \
      -i deploy/inventory deploy/deploy-prod.yml \
      -e @deploy/prod-vars.yml \
      --vault-password-file "$vault_file"
    ;;
  refs/heads/staging)
    exec "$ansible_bin" \
      -i deploy/inventory deploy/deploy-staging.yml \
      -e @deploy/staging-vars.yml \
      --vault-password-file "$vault_file"
    ;;
  *)
    exec "$ansible_bin" \
      -i deploy/inventory deploy/site.yml \
      --vault-password-file "$vault_file"
    ;;
esac
