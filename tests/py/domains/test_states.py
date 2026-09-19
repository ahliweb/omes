import unittest

from . import _pathfix  # noqa: F401

from domains import states

ACTOR = {"type": "service", "id": "svc-test"}


def _order(state="pending"):
    return {
        "order_id": "order-0001",
        "tenant_id": "tenant-1",
        "correlation_id": "corr-1",
        "idempotency_key": "idem-00000001",
        "domain": "example.com",
        "provider": "cloudflare",
        "operation": "registration",
        "state": state,
        "previous_state": None,
        "actor": ACTOR,
        "transitioned_at": "2026-09-19T00:00:00Z",
    }


class TestStates(unittest.TestCase):
    def test_pending_to_succeeded_is_legal(self):
        order = _order("pending")
        new_order = states.apply_transition(order, "succeeded", ACTOR, "2026-09-19T00:01:00Z")
        self.assertEqual(new_order["state"], "succeeded")
        self.assertEqual(new_order["previous_state"], "pending")
        # original object is untouched
        self.assertEqual(order["state"], "pending")

    def test_succeeded_to_active_is_legal_then_terminal_states_reject_further(self):
        order = states.apply_transition(_order("pending"), "succeeded", ACTOR, "t")
        order = states.apply_transition(order, "active", ACTOR, "t")
        self.assertEqual(order["state"], "active")
        with self.assertRaises(states.InvalidTransitionError):
            states.apply_transition(_order("failed"), "succeeded", ACTOR, "t")

    def test_pending_to_active_directly_is_illegal(self):
        # Payment/queued success must never fast-forward straight to
        # "active" without an intermediate reconciled "succeeded".
        with self.assertRaises(states.InvalidTransitionError):
            states.apply_transition(_order("pending"), "active", ACTOR, "t")

    def test_action_required_can_return_to_pending(self):
        order = states.apply_transition(_order("pending"), "action_required", ACTOR, "t")
        order = states.apply_transition(order, "pending", ACTOR, "t")
        self.assertEqual(order["state"], "pending")

    def test_renewal_due_to_expired_is_legal(self):
        order = states.apply_transition(_order("renewal_due"), "expired", ACTOR, "t")
        self.assertEqual(order["state"], "expired")

    def test_reconciliation_payload_is_attached(self):
        reconciliation = {"last_checked_at": "2026-09-19T00:02:00Z", "matches_desired": True, "provider_status": "active"}
        order = states.apply_transition(_order("pending"), "succeeded", ACTOR, "t", reconciliation=reconciliation)
        self.assertEqual(order["reconciliation"], reconciliation)

    def test_terminal_states_are_flagged(self):
        self.assertTrue(states.is_terminal("succeeded"))
        self.assertTrue(states.is_terminal("failed"))
        self.assertTrue(states.is_terminal("cancelled"))
        self.assertFalse(states.is_terminal("pending"))
        self.assertFalse(states.is_terminal("active"))

    def test_unknown_state_raises_value_error(self):
        with self.assertRaises(ValueError):
            states.can_transition("not-a-state", "pending")
        with self.assertRaises(ValueError):
            states.can_transition("pending", "not-a-state")


if __name__ == "__main__":
    unittest.main()
