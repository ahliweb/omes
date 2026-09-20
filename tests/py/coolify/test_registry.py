import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from coolify import audit, paths, registry


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.record = {
            "instance_id": "coolify-primary",
            "base_url": "https://coolify.example.test/api/v1",
            "credential_ref": {"store": "env", "key": "OMES_COOLIFY_TOKEN_coolify-primary"},
            "description": "Primary Coolify instance",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_register_list_get_and_permissions(self):
        self.assertEqual(registry.register_instance(self.record, self.root), self.record)
        self.assertEqual(registry.list_instances(self.root), [self.record])
        self.assertEqual(registry.get_instance("coolify-primary", self.root), self.record)
        self.assertEqual(stat.S_IMODE(paths.instances_path(self.root).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(paths.coolify_root(self.root).stat().st_mode), 0o700)
        text = paths.instances_path(self.root).read_text(encoding="utf-8")
        self.assertNotIn("token-value", text)
        self.assertIn("OMES_COOLIFY_TOKEN_coolify-primary", text)

    def test_identical_replay_is_idempotent_and_different_record_is_rejected(self):
        registry.register_instance(self.record, self.root)
        self.assertEqual(registry.register_instance(self.record, self.root), self.record)
        changed = dict(self.record)
        changed["base_url"] = "https://other.example.test/api/v1"
        with self.assertRaises(registry.InstanceExistsError):
            registry.register_instance(changed, self.root)
        self.assertEqual(len(registry.list_instances(self.root)), 1)

    def test_raw_credential_is_rejected_and_not_written(self):
        invalid = dict(self.record)
        invalid["credential_ref"] = "raw-token-value"
        with self.assertRaises(registry.InvalidInstanceError):
            registry.register_instance(invalid, self.root)
        self.assertFalse(paths.instances_path(self.root).exists())

    def test_remove_is_audited_and_chain_verifies(self):
        registry.register_instance(self.record, self.root, correlation_id="corr-1")
        removed = registry.remove_instance("coolify-primary", self.root, correlation_id="corr-2")
        self.assertEqual(removed, self.record)
        self.assertEqual(registry.list_instances(self.root), [])
        audit.verify_chain(self.root)
        events = [json.loads(line)["event"] for line in paths.audit_log_path(self.root).read_text().splitlines()]
        self.assertEqual(events, ["instance_registered", "instance_removed"])

    def test_missing_instance_is_rejected(self):
        with self.assertRaises(registry.InstanceNotFoundError):
            registry.get_instance("missing", self.root)
        with self.assertRaises(registry.InstanceNotFoundError):
            registry.remove_instance("missing", self.root)


if __name__ == "__main__":
    unittest.main()
