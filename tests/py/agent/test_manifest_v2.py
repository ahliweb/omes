"""tests/py/agent/test_manifest_v2.py - Tests for RuntimeDeployment v2 manifest
loading, schema validation, and semantic bounds (issue #174).
"""
import unittest
from pathlib import Path

from lib.omes.py.agent import manifest as manifest_mod


class TestManifestV2(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[3]
        self.fixtures_v2 = self.repo_root / "contracts" / "agent" / "v2" / "fixtures" / "runtime-deployment"

    def test_valid_native_user_manifest(self):
        path = self.fixtures_v2 / "valid-native-user.json"
        data = manifest_mod.load_and_validate(path, self.repo_root, expected_name="researcher")
        self.assertEqual(data["apiVersion"], "omes.ahliweb.com/v2")
        self.assertEqual(data["kind"], "RuntimeDeployment")
        self.assertEqual(data["runtime"]["profileRef"], "researcher")
        self.assertEqual(data["placement"]["backend"], "native")
        self.assertEqual(data["placement"]["serviceScope"], "user")

    def test_valid_compose_user_manifest(self):
        path = self.fixtures_v2 / "valid-compose-user.json"
        data = manifest_mod.load_and_validate(path, self.repo_root, expected_name="researcher-container")
        self.assertEqual(data["placement"]["backend"], "compose")
        self.assertIn("compose", data)

    def test_valid_system_scope_manifest(self):
        path = self.fixtures_v2 / "valid-system-scope.json"
        data = manifest_mod.load_and_validate(path, self.repo_root, expected_name="system-monitor")
        self.assertEqual(data["placement"]["serviceScope"], "system")
        self.assertEqual(data["kind"], "AgentDeployment")

    def test_invalid_missing_profile_ref(self):
        path = self.fixtures_v2 / "invalid-missing-profile-ref.json"
        with self.assertRaises(manifest_mod.ManifestError) as ctx:
            manifest_mod.load_and_validate(path, self.repo_root)
        self.assertTrue(any("profileRef" in err for err in ctx.exception.errors))

    def test_invalid_smuggled_runtime_role(self):
        path = self.fixtures_v2 / "invalid-smuggled-runtime-role.json"
        with self.assertRaises(manifest_mod.ManifestError) as ctx:
            manifest_mod.load_and_validate(path, self.repo_root)
        self.assertTrue(any("additional properties not allowed" in err for err in ctx.exception.errors))

    def test_invalid_smuggled_capabilities(self):
        path = self.fixtures_v2 / "invalid-smuggled-capabilities.json"
        with self.assertRaises(manifest_mod.ManifestError) as ctx:
            manifest_mod.load_and_validate(path, self.repo_root)
        self.assertTrue(any("additional properties not allowed" in err for err in ctx.exception.errors))

    def test_invalid_bad_name_traversal(self):
        path = self.fixtures_v2 / "invalid-bad-name-traversal.json"
        with self.assertRaises(manifest_mod.ManifestError) as ctx:
            manifest_mod.load_and_validate(path, self.repo_root)
        self.assertTrue(any("does not match pattern" in err or "unsafe fragment" in err for err in ctx.exception.errors))

    def test_invalid_wrong_backend(self):
        path = self.fixtures_v2 / "invalid-wrong-backend.json"
        with self.assertRaises(manifest_mod.ManifestError) as ctx:
            manifest_mod.load_and_validate(path, self.repo_root)
        self.assertTrue(any("kubernetes" in err for err in ctx.exception.errors))

    def test_smuggled_runtime_fields_rejected(self):
        """Hermes runtime fields (skills, memory, storage, secrets) cannot be smuggled into v2."""
        base = {
            "apiVersion": "omes.ahliweb.com/v2",
            "kind": "RuntimeDeployment",
            "metadata": {"name": "test-agent", "environment": "production"},
            "runtime": {"kind": "hermes", "profileRef": "default"},
            "placement": {"backend": "native", "serviceScope": "user"},
        }
        for forbidden_key in ("skills", "storage", "secrets", "deny", "capabilities", "memory"):
            payload = dict(base)
            payload[forbidden_key] = "smuggled"
            errors = manifest_mod.validate(payload, self.repo_root)
            self.assertTrue(len(errors) > 0, f"Expected error for smuggled key '{forbidden_key}'")
            self.assertTrue(any("additional properties not allowed" in err for err in errors))


if __name__ == "__main__":
    unittest.main()
