"""Tests for lib/omes/py/coolify/mapping.py (issue #97): register/remap/
resolve fail-closed on a missing or ambiguous mapping, correlation_id/
created_at immutability, and observed-state drift detection."""
import shutil
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from coolify import mapping  # noqa: E402
from coolify.provider import MappingRequiredFieldsError  # noqa: E402


def make_coolify(**overrides) -> dict:
    coolify = {
        "instance_id": "coolify-production",
        "project": "agent-platform",
        "environment": "production",
        "resource": "researcher-worker",
    }
    coolify.update(overrides)
    return coolify


class MappingTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-coolify-mapping-test-")
        self.root = Path(self._tmp)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestRegisterFailsClosed(MappingTestBase):
    def test_register_rejects_missing_field(self):
        coolify = make_coolify()
        del coolify["project"]
        with self.assertRaises(MappingRequiredFieldsError):
            mapping.register("dep-1", coolify, "corr-1", "2026-09-19T00:00:00Z", root=self.root)

    def test_register_rejects_ambiguous_resource(self):
        coolify = make_coolify(resource=["a", "b"])
        with self.assertRaises(MappingRequiredFieldsError):
            mapping.register("dep-1", coolify, "corr-1", "2026-09-19T00:00:00Z", root=self.root)

    def test_register_rejects_empty_deployment_id(self):
        with self.assertRaises(MappingRequiredFieldsError):
            mapping.register("", make_coolify(), "corr-1", "2026-09-19T00:00:00Z", root=self.root)


class TestRegisterAndResolve(MappingTestBase):
    def test_register_then_resolve_round_trips(self):
        mapping.register("dep-1", make_coolify(), "corr-1", "2026-09-19T00:00:00Z", root=self.root)
        record = mapping.resolve("dep-1", root=self.root)
        self.assertEqual(record["correlation_id"], "corr-1")
        self.assertEqual(record["created_at"], "2026-09-19T00:00:00Z")
        self.assertEqual(record["coolify"], make_coolify())

    def test_resolve_unregistered_deployment_fails_closed(self):
        with self.assertRaises(mapping.MappingNotFoundError):
            mapping.resolve("never-registered", root=self.root)

    def test_register_refuses_to_overwrite_existing_mapping(self):
        mapping.register("dep-1", make_coolify(), "corr-1", "2026-09-19T00:00:00Z", root=self.root)
        with self.assertRaises(mapping.MappingAlreadyExistsError):
            mapping.register(
                "dep-1", make_coolify(resource="other"), "corr-2", "2026-09-19T01:00:00Z", root=self.root
            )

    def test_remap_replaces_with_new_immutable_correlation_id(self):
        mapping.register("dep-1", make_coolify(), "corr-1", "2026-09-19T00:00:00Z", root=self.root)
        mapping.remap(
            "dep-1", make_coolify(resource="researcher-worker-v2"), "corr-2", "2026-09-19T02:00:00Z", root=self.root
        )
        record = mapping.resolve("dep-1", root=self.root)
        self.assertEqual(record["correlation_id"], "corr-2")
        self.assertEqual(record["coolify"]["resource"], "researcher-worker-v2")


class TestDriftDetection(unittest.TestCase):
    def test_no_drift_when_observed_state_unchanged(self):
        desired = {"deployment_status": "finished", "external_resource_id": "researcher-worker"}
        fresh = dict(desired)
        report = mapping.detect_drift(make_coolify(), desired, fresh)
        self.assertFalse(report["has_drift"])
        self.assertEqual(report["fields"], [])

    def test_drift_detected_on_status_change(self):
        desired = {"deployment_status": "finished", "external_resource_id": "researcher-worker"}
        fresh = {"deployment_status": "failed", "external_resource_id": "researcher-worker"}
        report = mapping.detect_drift(make_coolify(), desired, fresh)
        self.assertTrue(report["has_drift"])
        self.assertIn("deployment_status", report["fields"])
        self.assertNotIn("external_resource_id", report["fields"])

    def test_drift_report_includes_the_mapping(self):
        coolify = make_coolify()
        report = mapping.detect_drift(coolify, {}, {})
        self.assertEqual(report["mapping"], coolify)

    def test_drift_detection_fails_closed_on_ambiguous_mapping(self):
        with self.assertRaises(MappingRequiredFieldsError):
            mapping.detect_drift(make_coolify(resource=["a", "b"]), {}, {})


if __name__ == "__main__":
    unittest.main()
