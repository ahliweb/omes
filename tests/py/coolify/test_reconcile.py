"""Tests for lib/omes/py/coolify/reconcile.py (issue #97): reconciliation
merges observed-state fields only and can never overwrite an OMES logical
policy/entitlement field."""
import unittest

from . import _pathfix  # noqa: F401

from coolify import reconcile  # noqa: E402


class TestReconcileAllowsObservedFields(unittest.TestCase):
    def test_merges_allowed_observed_fields(self):
        observed = {"deployment_status": "queued"}
        patch = {"deployment_status": "finished", "external_resource_id": "researcher-worker"}
        merged = reconcile.reconcile(observed, patch)
        self.assertEqual(merged["deployment_status"], "finished")
        self.assertEqual(merged["external_resource_id"], "researcher-worker")

    def test_does_not_mutate_input_observed_state(self):
        observed = {"deployment_status": "queued"}
        reconcile.reconcile(observed, {"deployment_status": "finished"})
        self.assertEqual(observed["deployment_status"], "queued")

    def test_empty_patch_is_a_no_op(self):
        observed = {"deployment_status": "finished"}
        merged = reconcile.reconcile(observed, {})
        self.assertEqual(merged, observed)


class TestReconcileCannotOverwriteLogicalFields(unittest.TestCase):
    def test_rejects_role_field(self):
        with self.assertRaises(reconcile.ReconcileError):
            reconcile.reconcile({}, {"role": "admin"})

    def test_rejects_secret_ref_field(self):
        with self.assertRaises(reconcile.ReconcileError):
            reconcile.reconcile({}, {"secret_ref": {"store": "env", "key": "X"}})

    def test_rejects_entitlement_field(self):
        with self.assertRaises(reconcile.ReconcileError):
            reconcile.reconcile({}, {"entitlement_tier": "pro"})

    def test_rejects_backup_policy_field(self):
        with self.assertRaises(reconcile.ReconcileError):
            reconcile.reconcile({}, {"backup_policy": "daily"})

    def test_mixed_patch_with_one_logical_field_is_fully_rejected(self):
        """A patch with both a legitimate observed field and one logical
        field must be rejected in full, not partially applied."""
        observed = {"deployment_status": "queued"}
        with self.assertRaises(reconcile.ReconcileError):
            reconcile.reconcile(observed, {"deployment_status": "finished", "policy": "x"})
        # Nothing was applied - the caller's own copy is untouched either way,
        # but assert the function raised before returning anything usable.

    def test_error_message_names_the_disallowed_field(self):
        with self.assertRaises(reconcile.ReconcileError) as ctx:
            reconcile.reconcile({}, {"entitlement_tier": "pro"})
        self.assertIn("entitlement_tier", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
