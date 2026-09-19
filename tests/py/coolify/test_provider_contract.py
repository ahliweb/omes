"""Fake-provider contract tests for lib/omes/py/coolify (issue #97):
apply/status/health/redeploy/rollback idempotency, fail-closed mapping
validation, and fail-closed rollback-reference validation."""
import shutil
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from coolify import audit  # noqa: E402
from coolify.fake import FakeCoolifyProvider  # noqa: E402
from coolify.provider import (  # noqa: E402
    MappingRequiredFieldsError,
    RollbackReferenceUnknownError,
    require_mapping_fields,
)


def make_mapping(**overrides) -> dict:
    mapping = {
        "instance_id": "coolify-production",
        "project": "agent-platform",
        "environment": "production",
        "resource": "researcher-worker",
    }
    mapping.update(overrides)
    return mapping


class CoolifyProviderTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-coolify-test-")
        self.root = Path(self._tmp)
        self.provider = FakeCoolifyProvider(audit_root=self.root)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestMappingFailsClosed(unittest.TestCase):
    def test_missing_field_fails_closed(self):
        for missing in ("instance_id", "project", "environment", "resource"):
            mapping = make_mapping()
            del mapping[missing]
            with self.assertRaises(MappingRequiredFieldsError):
                require_mapping_fields(mapping)

    def test_empty_string_field_fails_closed(self):
        mapping = make_mapping(resource="")
        with self.assertRaises(MappingRequiredFieldsError):
            require_mapping_fields(mapping)

    def test_ambiguous_resource_list_fails_closed(self):
        mapping = make_mapping(resource=["researcher-worker", "researcher-worker-canary"])
        with self.assertRaises(MappingRequiredFieldsError):
            require_mapping_fields(mapping)

    def test_valid_mapping_passes(self):
        require_mapping_fields(make_mapping())  # must not raise


class TestApplyIdempotency(CoolifyProviderTestBase):
    def test_same_idempotency_key_returns_same_result(self):
        mapping = make_mapping()
        first = self.provider.apply(mapping, "corr-1", "idem-apply-1")
        second = self.provider.apply(mapping, "corr-1", "idem-apply-1")
        self.assertEqual(first, second)

    def test_same_idempotency_key_never_deploys_twice(self):
        mapping = make_mapping()
        self.provider.apply(mapping, "corr-1", "idem-apply-1")
        self.provider.apply(mapping, "corr-1", "idem-apply-1")
        self.provider.apply(mapping, "corr-1", "idem-apply-1")
        self.assertEqual(self.provider.deploy_count(mapping), 1)

    def test_different_idempotency_key_deploys_again(self):
        mapping = make_mapping()
        self.provider.apply(mapping, "corr-1", "idem-apply-1")
        self.provider.apply(mapping, "corr-1", "idem-apply-2")
        self.assertEqual(self.provider.deploy_count(mapping), 2)

    def test_apply_rejects_ambiguous_mapping(self):
        mapping = make_mapping(resource=["a", "b"])
        with self.assertRaises(MappingRequiredFieldsError):
            self.provider.apply(mapping, "corr-1", "idem-apply-1")


class TestRedeployIdempotency(CoolifyProviderTestBase):
    def test_same_idempotency_key_returns_same_result(self):
        mapping = make_mapping()
        first = self.provider.redeploy(mapping, "corr-1", "idem-redeploy-1")
        second = self.provider.redeploy(mapping, "corr-1", "idem-redeploy-1")
        self.assertEqual(first, second)
        self.assertEqual(self.provider.deploy_count(mapping), 1)


class TestStatusAndHealth(CoolifyProviderTestBase):
    def test_status_before_apply_is_unknown(self):
        mapping = make_mapping()
        status = self.provider.status(mapping, "corr-1")
        self.assertEqual(status["result"], "unknown")

    def test_health_before_apply_is_unhealthy(self):
        mapping = make_mapping()
        health = self.provider.health(mapping, "corr-1")
        self.assertEqual(health["result"], "unhealthy")

    def test_status_and_health_after_apply(self):
        mapping = make_mapping()
        self.provider.apply(mapping, "corr-1", "idem-apply-1")
        self.assertEqual(self.provider.status(mapping, "corr-1")["result"], "applied")
        self.assertEqual(self.provider.health(mapping, "corr-1")["result"], "healthy")

    def test_status_rejects_missing_mapping_field(self):
        mapping = make_mapping()
        del mapping["environment"]
        with self.assertRaises(MappingRequiredFieldsError):
            self.provider.status(mapping, "corr-1")


class TestRollback(CoolifyProviderTestBase):
    def test_rollback_to_unknown_reference_fails_closed(self):
        mapping = make_mapping()
        self.provider.apply(mapping, "corr-1", "idem-apply-1")
        with self.assertRaises(RollbackReferenceUnknownError):
            self.provider.rollback(mapping, "corr-1", "never-observed-uuid", "idem-rollback-1")

    def test_rollback_to_known_reference_succeeds(self):
        mapping = make_mapping()
        applied = self.provider.apply(mapping, "corr-1", "idem-apply-1")
        rollback_ref = applied["external"]["deployment_uuid"]
        result = self.provider.rollback(mapping, "corr-1", rollback_ref, "idem-rollback-1")
        self.assertEqual(result["result"], "rolled_back")
        self.assertEqual(result["message"], f"rolled back to {rollback_ref}")

    def test_rollback_idempotent_on_idempotency_key(self):
        mapping = make_mapping()
        applied = self.provider.apply(mapping, "corr-1", "idem-apply-1")
        rollback_ref = applied["external"]["deployment_uuid"]
        first = self.provider.rollback(mapping, "corr-1", rollback_ref, "idem-rollback-1")
        second = self.provider.rollback(mapping, "corr-1", rollback_ref, "idem-rollback-1")
        self.assertEqual(first, second)

    def test_rollback_ref_is_a_previously_observed_deployment_uuid(self):
        """The rollback_ref used must be one this provider actually
        produced from a prior apply - never a value the caller invents."""
        mapping = make_mapping()
        applied = self.provider.apply(mapping, "corr-1", "idem-apply-1")
        observed = self.provider.observed_state(mapping, "corr-1")
        self.assertEqual(
            applied["external"]["deployment_uuid"],
            observed["build_history_ref"]["deployment_uuid"],
        )


class TestObservedStateNeverCarriesLogBodies(CoolifyProviderTestBase):
    def test_logs_metadata_has_no_body_field(self):
        mapping = make_mapping()
        self.provider.apply(mapping, "corr-1", "idem-apply-1")
        observed = self.provider.observed_state(mapping, "corr-1")
        logs_metadata = observed["logs_metadata"]
        self.assertNotIn("body", logs_metadata)
        self.assertNotIn("content", logs_metadata)
        self.assertNotIn("lines", logs_metadata)


class TestAuditTrail(CoolifyProviderTestBase):
    def test_every_operation_is_audited_and_chain_verifies(self):
        mapping = make_mapping()
        applied = self.provider.apply(mapping, "corr-1", "idem-apply-1")
        self.provider.status(mapping, "corr-1")
        self.provider.health(mapping, "corr-1")
        self.provider.rollback(
            mapping, "corr-1", applied["external"]["deployment_uuid"], "idem-rollback-1"
        )
        audit.verify_chain(self.root)  # must not raise

        audit_path = self.root / "coolify" / "audit.jsonl"
        events = [
            __import__("json").loads(line)["event"]
            for line in audit_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertIn("coolify.apply", events)
        self.assertIn("coolify.status", events)
        self.assertIn("coolify.health", events)
        self.assertIn("coolify.rollback", events)


if __name__ == "__main__":
    unittest.main()
