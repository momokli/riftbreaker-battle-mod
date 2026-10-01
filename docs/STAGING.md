# STAGING — RETIRED (Issue #1034)

**Status: archiviert / retired.** Die Staging-Umgebung ist mit dem
Deployment-Cleanup (Issue #1034) **vollständig abgebaut**. Dieses Dokument
bleibt nur als historischer Verweis erhalten.

## Was entfallen ist

- Playbook `deploy/deploy-staging.yml` und Override-Datei
  `deploy/staging-vars.yml` (gelöscht).
- CD-Trigger `push: branches: [staging]` und der Job `deploy-staging` in
  `.github/workflows/deploy.yml` (gelöscht).
- Der staging-Case im forced command (`deploy/deploy-ssh.sh`) und im
  root-Wrapper (`deploy/deploy-wrapper.sh`) — ein `refs/heads/staging` ist jetzt
  ein **Fehler** (fail loud).
- Der Staging-Relay-Host `sync` im Inventory (`deploy/inventory/hosts.yml`).
- `staging` als gültige Env im Env-Isolations-Schema
  (`deploy/env-schema.yml`, `tools/deploy-gate/check_env_isolation.py`).
- Die GNS-Route `*-staging` (`deploy/roles/gns-relay/templates/routes.j2`).

## Aktuelle Topologie (prod-only)

Es gibt nur noch die prod-Welten **A** (`:6322`) und **B** (`:6325`) auf planet,
erreichbar über den **GNS-Entry-Relay** auf `planet:6321` (Suffix-Routing:
`*-a` → A, `*-b` → B, Default → A). Der CD deployt ausschließlich bei Tag-Push
`v*` das Playbook `deploy/deploy-prod.yml`.

Maßgebliche, aktuelle Doku: [`deploy/README.md`](../deploy/README.md)
(Abschnitte „Deploy", „Continuous Deploy (CD)" und „Teardown dev/staging").

## Host-Teardown

Die Host-Ressourcen der entfallenen staging-Welt (Container, Volumes,
systemd-Units, Pfade) räumt das manuelle, idempotente Playbook
[`deploy/teardown-dev-staging.yml`](../deploy/teardown-dev-staging.yml) ab — es
ist **nicht** im CD verdrahtet. Runbook: `deploy/README.md` → „Teardown
dev/staging (#1034)".