#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regressionstest für die Mod-Update-Erkennung der Server-Rolle (Issue #245).

Bug (Issue #245): Die vereinfachte Rolle (#241) entpackte das Mod-Zip nur per
`unarchive: creates: <mod_dir>`. Beim 2. Deploy existiert der Zielordner, der
Unpack wurde übersprungen und neue ``mod/lua/*``-Änderungen kamen NIE auf den
Server — die Iteration ändern → mergen → live war damit kaputt.

Dieser Test prüft **strukturell** (gegen den Rollen-Code), dass das
md5-Marker-Verhalten aus Issue #212 nicht wieder verloren geht:

  * Mod-Rollout nutzt KEIN `creates:`-Skip mehr.
  * Änderungs-Erkennung vergleicht Zip-md5 (md5) mit einem Marker.
  * Marker und Backups liegen AUSSERHALB von ``mods/`` (sonst lädt der
    Dedicated Server sie als zweite Mod: handler_errors).
  * Der Rollout-Block ist durch die Erkennung gegated und hat einen Rollback
    (rescue) sowie eine Artefakt-Verifikation.

Reine Struktur-/Regressionstest — benötigt kein docker/planet. Abhängigkeit:
PyYAML (auf dem CI-Runner via ansible-core vorhanden).

Aufruf:
  python3 -m unittest test_mod_update_detection -v
"""

import pathlib
import unittest

import yaml

ROLE_TASKS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "deploy"
    / "roles"
    / "riftbreaker-server"
    / "tasks"
    / "main.yml"
)


def _iter_tasks(tasks):
    """Alle Tasks rekursiv (inkl. block/rescue/always) durchlaufen."""
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for section in ("block", "rescue", "always"):
            yield from _iter_tasks(task.get(section))


def tasks():
    return list(_iter_tasks(yaml.safe_load(ROLE_TASKS.read_text(encoding="utf-8"))))


def by_name(substr):
    hits = [t for t in tasks() if substr in (t.get("name") or "")]
    if len(hits) != 1:
        raise AssertionError(
            "erwartet genau 1 Task mit '%s', gefunden %d" % (substr, len(hits))
        )
    return hits[0]


class ModUpdateDetectionTest(unittest.TestCase):
    def test_unarchive_has_no_creates_skip(self):
        task = by_name("Mod-Zip entpacken")
        self.assertIn("unarchive", task)
        self.assertNotIn(
            "creates",
            task["unarchive"],
            "unarchive darf kein `creates` nutzen (#245): der Skip lässt neue "
            "Mod-Inhalte unausgerollt.",
        )

    def test_zip_md5_is_read_with_md5_algorithm(self):
        task = by_name("Mod-Zip md5")
        self.assertEqual(task["stat"]["checksum_algorithm"], "md5")

    def test_detection_uses_marker_outside_mods_dir(self):
        task = by_name("Installierten Mod-Stand lesen (Marker)")
        src = task["slurp"]["src"]
        self.assertTrue(src.endswith("rbbattle.md5"))
        # Der Marker liegt im Deploy-Verzeichnis, NICHT in mods/.
        self.assertIn("riftbreaker_deploy_dir", src)
        self.assertNotIn("riftbreaker_mods_dir", src)

    def test_detection_expression_compares_marker_and_zip_md5(self):
        expr = by_name("Mod-Update nötig?")["set_fact"]["riftbreaker_mod_update_needed"]
        self.assertIn("mod_marker.content", expr)
        self.assertIn("rbbattle_zip_stat.stat.checksum", expr)
        self.assertIn("!=", expr)

    def test_backup_is_outside_mods_and_only_on_change(self):
        # Der konkrete Zielpfad wird aus riftbreaker_backup_dir abgeleitet …
        target = by_name("Backup-Zielpfad je Lauf festlegen")
        path_expr = target["set_fact"]["riftbreaker_mod_backup_path"]
        self.assertIn("riftbreaker_backup_dir", path_expr)
        self.assertNotIn("riftbreaker_mods_dir", path_expr)
        # … und erst bei erkanntem Update gesetzt.
        self.assertEqual(target["when"], "riftbreaker_mod_update_needed")

        # Das Backup selbst packt nur bei Änderung und nutzt diesen Pfad.
        task = by_name("Alten Mod-Stand sichern")
        self.assertEqual(task["command"]["argv"][2], "{{ riftbreaker_mod_backup_path }}")
        self.assertIn("riftbreaker_mod_update_needed", task["when"])

    def test_rollout_block_is_gated_and_has_rollback(self):
        task = by_name("Mod ausrollen, Container starten und verifizieren")
        self.assertEqual(task["when"], "riftbreaker_mod_update_needed")
        self.assertIn("block", task)
        self.assertIn("rescue", task)

    def test_artefact_verification_asserts_expected_version(self):
        task = by_name("deploytes Artefakt entspricht der erwarteten Version")
        self.assertIn("assert", task)
        joined = " ".join(str(cond) for cond in task["assert"]["that"])
        self.assertIn("riftbreaker_expected_version", joined)
        self.assertIn("riftbreaker_deployed_manifest_version", joined)
        self.assertIn("riftbreaker_deployed_lua_version", joined)

    def test_rollback_restores_backup_and_marker(self):
        names = [t.get("name") or "" for t in tasks()]
        self.assertTrue(
            any("Rollback — Backup nach mods/ einspielen" in n for n in names),
            "Rollback-Task zum Einspielen des Backups fehlt.",
        )
        self.assertTrue(
            any("Rollback — Marker auf den vorherigen Stand zurücksetzen" in n for n in names),
            "Rollback-Task zum Zurücksetzen des Markers fehlt (sonst gilt der "
            "nächste Lauf fälschlich als 'schon aktuell').",
        )


if __name__ == "__main__":
    unittest.main()
