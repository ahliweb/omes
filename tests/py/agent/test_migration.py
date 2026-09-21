"""tests/py/agent/test_migration.py - Tests for v1 -> v2 manifest migration (issue #174).
"""
import json
import unittest
from pathlib import Path

from lib.omes.py.agent import manifest as manifest_mod
from lib.omes.py.agent import migration as migration_mod


class TestMigration(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[3]
        self.fixtures_v1 = self.repo_root / "contracts" / "agent" / "v1" / "fixtures" / "agent-deployment"

    def test_migrate_valid_v1_generic_user(self):
        v1_path = self.fixtures_v1 / "valid-generic-user.json"
        with open(v1_path, "r", encoding="utf-8") as fh:
            v1_data = json.load(fh)

        v2_data, audit = migration_mod.migrate_manifest_v1_to_v2(v1_data)

        self.assertEqual(v2_data["apiVersion"], "omes.ahliweb.com/v2")
        self.assertEqual(v2_data["kind"], "RuntimeDeployment")
        self.assertEqual(v2_data["metadata"]["name"], "researcher")
        self.assertEqual(v2_data["runtime"]["profileRef"], "researcher")
        self.assertEqual(v2_data["placement"]["backend"], "systemd")
        self.assertEqual(v2_data["placement"]["serviceScope"], "user")
        self.assertEqual(v2_data["resources"]["memory"], "1G")

        # Validate that the generated v2 manifest passes strict v2 validation
        errors = manifest_mod.validate(v2_data, self.repo_root)
        self.assertEqual(errors, [], f"Validation errors on migrated v2 manifest: {errors}")

        # Verify audit classifications
        delegated = [r["v1_field"] for r in audit if r["action"] == "DELEGATE"]
        self.assertIn("spec.role", delegated)
        self.assertIn("spec.capabilities", delegated)
        self.assertIn("spec.deny", delegated)
        self.assertIn("spec.secrets", delegated)
        self.assertIn("spec.storage.memory", delegated)

    def test_migrate_valid_compose_generic(self):
        v1_path = self.fixtures_v1 / "valid-compose-generic.json"
        with open(v1_path, "r", encoding="utf-8") as fh:
            v1_data = json.load(fh)

        v2_data, audit = migration_mod.migrate_manifest_v1_to_v2(v1_data)
        self.assertEqual(v2_data["placement"]["backend"], "compose")
        self.assertIn("compose", v2_data)

        errors = manifest_mod.validate(v2_data, self.repo_root)
        self.assertEqual(errors, [], f"Validation errors on migrated compose v2 manifest: {errors}")

    def test_migrate_rejects_non_v1(self):
        with self.assertRaises(migration_mod.MigrationError) as ctx:
            migration_mod.migrate_manifest_v1_to_v2({"apiVersion": "omes.ahliweb.com/v2"})
        self.assertIn("Expected apiVersion 'omes.ahliweb.com/v1'", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
