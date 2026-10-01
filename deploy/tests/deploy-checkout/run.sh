#!/usr/bin/env bash
# Hermetischer Selbsttest des Deploy-Dispatches (Issue #483, Ziel A; prod-only #1034).
#
# Prüft die ECHTEN Repo-Artefakte deploy/deploy-ssh.sh (forced command) und
# deploy/deploy-wrapper.sh (root-Wrapper) mit einer STUB-Umgebung — kein Host,
# kein SSH, kein sudo, kein git-Netz, kein Ansible:
#   * ref refs/tags/v*     -> Marker .deploy-prod.* + .deploy-env=prod + repo-prod
#                             + dispatcht deploy/deploy-prod.yml
#   * ungueltige SHA       -> exit 1 (kein Marker)
#   * ungueltiger ref      -> exit 1 (main/staging sind entfallen, #1034)
#   * Legacy-Fallback (ohne .deploy-env) -> bestehender Checkout `repo/`
#
# Läuft in deploy-check-local.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
ssh_sh="$repo/deploy/deploy-ssh.sh"
wrapper="$repo/deploy/deploy-wrapper.sh"

fail() { echo "::error::$1"; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
stubs="$work/bin"
root="$work/deploy-root"
args_file="$work/ansible-args"
mkdir -p "$stubs" "$root"

# --- Stubs ----------------------------------------------------------------
cat > "$stubs/git" <<'STUB'
#!/bin/sh
case "$1" in
  clone)
    # git clone [--quiet] <url> <dir> -> letztes Argument ist das Ziel
    dir=""
    for a in "$@"; do dir="$a"; done
    mkdir -p "$dir/.git"
    ;;
esac
exit 0
STUB
cat > "$stubs/chown" <<'STUB'
#!/bin/sh
exit 0
STUB
cat > "$stubs/ansible-playbook" <<'STUB'
#!/bin/sh
printf '%s\n' "$@" > "$RBB_TEST_ARGS"
exit 0
STUB
chmod +x "$stubs/git" "$stubs/chown" "$stubs/ansible-playbook"

export PATH="$stubs:$PATH"
export RBBATTLE_DEPLOY_ROOT="$root"
export RBBATTLE_DEPLOY_WRAPPER="$wrapper"
export RBBATTLE_DEPLOY_ANSIBLE_BIN="$stubs/ansible-playbook"
export RBBATTLE_DEPLOY_REPO_URL="file:///dev/null"
export RBB_TEST_ARGS="$args_file"

sha_a="0123456789abcdef0123456789abcdef01234567"
sha_b="89abcdef0123456789abcdef0123456789abcdef"

run_forced() { # <sha> <ref>
  RBBATTLE_DEPLOY_NO_SUDO=1 SSH_ORIGINAL_COMMAND="$1 $2" "$ssh_sh"
}

echo "== prod: refs/tags/v1.2.3 -> .deploy-prod.* / repo-prod / deploy-prod.yml =="
rm -f "$args_file"
run_forced "$sha_a" refs/tags/v1.2.3
[ "$(cat "$root/.deploy-prod.sha")" = "$sha_a" ] || fail "prod-SHA-Marker falsch"
[ "$(cat "$root/.deploy-prod.ref")" = "refs/tags/v1.2.3" ] || fail "prod-ref-Marker falsch"
[ "$(cat "$root/.deploy-env")" = "prod" ] || fail ".deploy-env != prod"
[ -d "$root/repo-prod/.git" ] || fail "repo-prod wurde nicht angelegt"
grep -q "deploy/deploy-prod.yml" "$args_file" || fail "prod dispatcht nicht deploy-prod.yml"

echo "== Negativ: ungueltige SHA -> exit 1, kein Marker =="
bad_root="$work/bad-root"; mkdir -p "$bad_root"
set +e
RBBATTLE_DEPLOY_ROOT="$bad_root" RBBATTLE_DEPLOY_NO_SUDO=1 \
  SSH_ORIGINAL_COMMAND="not-a-sha refs/tags/v1.2.3" "$ssh_sh" >/dev/null 2>&1
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "ungueltige SHA wurde akzeptiert (rc=0)"
[ ! -e "$bad_root/.deploy-prod.sha" ] || fail "Marker trotz ungueltiger SHA geschrieben"

echo "== Negativ: Branch-refs (main/staging) -> exit 1 (prod-only, #1034) =="
for badref in refs/heads/main refs/heads/staging refs/pull/7/head; do
  set +e
  RBBATTLE_DEPLOY_ROOT="$bad_root" RBBATTLE_DEPLOY_NO_SUDO=1 \
    SSH_ORIGINAL_COMMAND="$sha_a $badref" "$ssh_sh" >/dev/null 2>&1
  rc=$?
  set -e
  [ "$rc" -ne 0 ] || fail "ref '$badref' wurde akzeptiert (rc=0) — nur refs/tags/v* erlaubt (#1034)."
  [ ! -e "$bad_root/.deploy-env" ] || fail "Marker trotz ungueltigem ref '$badref' geschrieben"
done

echo "== Legacy-Fallback: ohne .deploy-env + Tag-ref -> bestehender Checkout repo/ =="
legacy_root="$work/legacy-root"; mkdir -p "$legacy_root"
printf '%s' "$sha_a" > "$legacy_root/.deploy-sha"
printf '%s' "refs/tags/v1.2.3" > "$legacy_root/.deploy-ref"
rm -f "$args_file"
RBBATTLE_DEPLOY_ROOT="$legacy_root" RBBATTLE_DEPLOY_ANSIBLE_BIN="$stubs/ansible-playbook" \
  RBB_TEST_ARGS="$args_file" "$wrapper"
[ -d "$legacy_root/repo/.git" ] || fail "Legacy-Fallback nutzt nicht den bestehenden Checkout repo/"
grep -q "deploy/deploy-prod.yml" "$args_file" || fail "Legacy-Fallback dispatcht nicht deploy-prod.yml"

echo "== Legacy-Fallback: Branch-ref -> exit 1 (fail loud, #1034) =="
legacy_bad="$work/legacy-bad"; mkdir -p "$legacy_bad"
printf '%s' "$sha_a" > "$legacy_bad/.deploy-sha"
printf '%s' "refs/heads/main" > "$legacy_bad/.deploy-ref"
set +e
RBBATTLE_DEPLOY_ROOT="$legacy_bad" RBBATTLE_DEPLOY_ANSIBLE_BIN="$stubs/ansible-playbook" \
  RBB_TEST_ARGS="$args_file" "$wrapper" >/dev/null 2>&1
rc=$?
set -e
[ "$rc" -ne 0 ] || fail "Legacy-Branch-ref wurde akzeptiert (rc=0) — nur refs/tags/v* erlaubt (#1034)."

echo "OK: prod-only Deploy-Dispatch (Tag -> deploy-prod.yml; main/staging fail loud; Issue #1034)."