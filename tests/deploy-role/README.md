# deploy-role — Statische Regressionstests für Ansible-Deploy-Rollen

Hier liegen **strukturelle Regressionstests** für die Rollen unter
`deploy/roles/`. Sie laufen ohne `docker`/`planet` (reine YAML-Analyse) und
sichern Regeln ab, die sonst still verloren gehen — Bugs, die erst live nach
einem Deploy sichtbar werden.

## Tests

| Datei | Issue | Prüft |
| --- | --- | --- |
| `test_mod_update_detection.py` | #245 | Mod-Update-Erkennung der Rolle `riftbreaker-server`: md5-Marker statt `creates`-Skip, Backup AUSSERHALB von `mods/`, Rollback + Artefakt-Verifikation. |

## Ausführen

```bash
cd tests/deploy-role
python3 -m unittest test_mod_update_detection -v
```

Voraussetzung: Python 3 + **PyYAML** (auf dem CI-Runner über `ansible-core`
vorhanden). Kein `docker`, kein Host-Zugriff.

## Warum strukturell?

Die Rolle ist Ansible-Code; ihr Verhalten hängt von Host-/Vault-Zustand ab und
ist damit nicht ohne Weiteres unit-testbar. Der teuerste Fehler (#245) war ein
**Weglassen** von Logik (Änderungs-Erkennung) — genau solche Regressionen
fängt die strukturelle Prüfung: sie schlägt fehl, sobald der Rollout wieder auf
`unarchive: creates:` zurückfällt oder Marker/Backup in `mods/` wandern.
