"""Tests for lib/omes/py/jobs/entitlement.py (issue #92): the pure
entitlement evaluator and the idempotent subscription-event applier."""
import os
import unittest

from . import _pathfix  # noqa: F401
from jobs import entitlement, states

_THIS = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS, "..", "..", ".."))
_TABLE_PATH = os.path.join(_REPO_ROOT, "contracts", "control-center", "v1", "subscription.states.json")


def _entitlement(**overrides):
    base = {
        "entitlement_id": "ent-1",
        "tenant_id": "tenant-acme",
        "subscription_id": "sub-1",
        "state": "active",
        "limits": {
            "servers": 2,
            "logical_agents": 5,
            "specialist_agents": 1,
            "isolated_workers": 0,
            "storage_gb": 100,
            "backup_retention_days": 14,
        },
        "effective_from": "2026-01-01T00:00:00Z",
        "source_event_id": "evt-0",
    }
    base.update(overrides)
    return base


class TestLimitEnforcement(unittest.TestCase):
    def test_provision_within_limit_is_allowed(self):
        result = entitlement.evaluate(
            _entitlement(),
            {"type": "provision_new", "tenant_id": "tenant-acme", "resource": "servers", "requested_total": 2},
        )
        self.assertTrue(result["allow"])

    def test_provision_over_limit_is_denied(self):
        result = entitlement.evaluate(
            _entitlement(),
            {"type": "provision_new", "tenant_id": "tenant-acme", "resource": "servers", "requested_total": 3},
        )
        self.assertFalse(result["allow"])
        self.assertIn("limit_exceeded", result["reason"])

    def test_unknown_resource_is_denied(self):
        result = entitlement.evaluate(
            _entitlement(),
            {"type": "provision_new", "tenant_id": "tenant-acme", "resource": "gpus", "requested_total": 1},
        )
        self.assertFalse(result["allow"])

    def test_missing_requested_total_is_denied(self):
        result = entitlement.evaluate(
            _entitlement(), {"type": "provision_new", "tenant_id": "tenant-acme", "resource": "servers"}
        )
        self.assertFalse(result["allow"])


class TestUpgradeDowngrade(unittest.TestCase):
    def test_upgrade_within_new_higher_limit_is_allowed(self):
        upgraded = _entitlement(limits={**_entitlement()["limits"], "servers": 5})
        result = entitlement.evaluate(
            upgraded, {"type": "upgrade", "tenant_id": "tenant-acme", "resource": "servers", "requested_total": 5}
        )
        self.assertTrue(result["allow"])

    def test_downgrade_below_current_usage_denies_further_provisioning(self):
        downgraded = _entitlement(limits={**_entitlement()["limits"], "servers": 1})
        result = entitlement.evaluate(
            downgraded, {"type": "provision_new", "tenant_id": "tenant-acme", "resource": "servers", "requested_total": 2}
        )
        self.assertFalse(result["allow"])


class TestExpiryAndGracePeriod(unittest.TestCase):
    def test_expired_denies_new_provisioning(self):
        result = entitlement.evaluate(
            _entitlement(state="expired"),
            {"type": "provision_new", "tenant_id": "tenant-acme", "resource": "servers", "requested_total": 1},
        )
        self.assertFalse(result["allow"])
        self.assertIn("expired", result["reason"])

    def test_expired_still_keeps_healthy_deployment_running_by_default(self):
        result = entitlement.evaluate(
            _entitlement(state="expired"), {"type": "keep_existing_running", "tenant_id": "tenant-acme"}
        )
        self.assertTrue(result["allow"])

    def test_expired_stops_running_when_policy_says_stop(self):
        policy = {
            "new_provisioning_action": "block",
            "upgrade_action": "block",
            "optional_workers_action": "stop",
            "existing_healthy_deployments_action": "stop",
        }
        result = entitlement.evaluate(
            _entitlement(state="expired"),
            {"type": "keep_existing_running", "tenant_id": "tenant-acme"},
            resource_policy=policy,
        )
        self.assertFalse(result["allow"])

    def test_grace_period_blocks_new_provisioning_by_default(self):
        result = entitlement.evaluate(
            _entitlement(state="grace_period"),
            {"type": "provision_new", "tenant_id": "tenant-acme", "resource": "servers", "requested_total": 1},
        )
        self.assertFalse(result["allow"])

    def test_grace_period_allows_new_provisioning_when_policy_says_allow(self):
        policy = {
            "new_provisioning_action": "allow",
            "upgrade_action": "block",
            "optional_workers_action": "stop",
            "existing_healthy_deployments_action": "keep_running",
        }
        result = entitlement.evaluate(
            _entitlement(state="grace_period"),
            {"type": "provision_new", "tenant_id": "tenant-acme", "resource": "servers", "requested_total": 1},
            resource_policy=policy,
        )
        self.assertTrue(result["allow"])

    def test_grace_period_keeps_existing_running_by_default(self):
        result = entitlement.evaluate(
            _entitlement(state="grace_period"), {"type": "keep_existing_running", "tenant_id": "tenant-acme"}
        )
        self.assertTrue(result["allow"])

    def test_suspended_blocks_optional_worker_by_default(self):
        result = entitlement.evaluate(
            _entitlement(state="suspended"), {"type": "start_optional_worker", "tenant_id": "tenant-acme", "resource": "isolated_workers", "requested_total": 1}
        )
        self.assertFalse(result["allow"])


class TestCrossTenant(unittest.TestCase):
    def test_action_from_a_different_tenant_is_denied_regardless_of_state_or_limits(self):
        result = entitlement.evaluate(
            _entitlement(),
            {"type": "provision_new", "tenant_id": "tenant-globex", "resource": "servers", "requested_total": 1},
        )
        self.assertFalse(result["allow"])
        self.assertIn("cross_tenant_denied", result["reason"])


class TestSubscriptionEventReplay(unittest.TestCase):
    def setUp(self):
        self.machine = states.load_state_machine(_TABLE_PATH)

    def test_first_event_creates_a_record_in_an_initial_state(self):
        record = entitlement.apply_subscription_event(
            None,
            {
                "event_id": "evt-1",
                "tenant_id": "tenant-acme",
                "subscription_id": "sub-1",
                "to_state": "trialing",
                "effective_from": "2026-01-01T00:00:00Z",
            },
            self.machine,
        )
        self.assertEqual(record["state"], "trialing")
        self.assertEqual(record["source_event_id"], "evt-1")

    def test_creating_a_record_in_a_non_initial_state_is_rejected(self):
        with self.assertRaises(ValueError):
            entitlement.apply_subscription_event(
                None,
                {"event_id": "evt-1", "to_state": "suspended", "effective_from": "2026-01-01T00:00:00Z"},
                self.machine,
            )

    def test_valid_transition_updates_state(self):
        record = entitlement.apply_subscription_event(
            None,
            {"event_id": "evt-1", "to_state": "trialing", "effective_from": "2026-01-01T00:00:00Z"},
            self.machine,
        )
        record = entitlement.apply_subscription_event(
            record,
            {"event_id": "evt-2", "to_state": "active", "effective_from": "2026-01-15T00:00:00Z"},
            self.machine,
        )
        self.assertEqual(record["state"], "active")

    def test_invalid_transition_raises(self):
        record = entitlement.apply_subscription_event(
            None,
            {"event_id": "evt-1", "to_state": "trialing", "effective_from": "2026-01-01T00:00:00Z"},
            self.machine,
        )
        with self.assertRaises(states.TransitionError):
            entitlement.apply_subscription_event(
                record,
                {"event_id": "evt-2", "to_state": "suspended", "effective_from": "2026-01-15T00:00:00Z"},
                self.machine,
            )

    def test_replaying_the_same_event_id_is_a_no_op(self):
        record = entitlement.apply_subscription_event(
            None,
            {"event_id": "evt-1", "to_state": "trialing", "effective_from": "2026-01-01T00:00:00Z"},
            self.machine,
        )
        record = entitlement.apply_subscription_event(
            record,
            {"event_id": "evt-2", "to_state": "active", "effective_from": "2026-01-15T00:00:00Z"},
            self.machine,
        )
        replayed = entitlement.apply_subscription_event(
            record,
            {"event_id": "evt-2", "to_state": "cancelled", "effective_from": "2099-01-01T00:00:00Z"},
            self.machine,
        )
        # Same event_id as already applied -> unchanged, even though the
        # replayed payload claims a different (and otherwise invalid from
        # "active") target state.
        self.assertEqual(replayed, record)
        self.assertEqual(replayed["state"], "active")
