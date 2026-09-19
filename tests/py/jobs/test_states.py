"""Tests for lib/omes/py/jobs/states.py (issue #92): the generic,
data-driven finite-state-machine transition checker, exercised against
the real contracts/control-center/v1/subscription.states.json table."""
import os
import unittest

from . import _pathfix  # noqa: F401
from jobs import states

_THIS = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS, "..", "..", ".."))
_TABLE_PATH = os.path.join(_REPO_ROOT, "contracts", "control-center", "v1", "subscription.states.json")


class TestStateMachineTable(unittest.TestCase):
    """Malformed-table handling using small, inline tables."""

    def test_rejects_empty_states(self):
        with self.assertRaises(states.StateMachineError):
            states.StateMachine({"states": [], "transitions": []})

    def test_rejects_transition_with_unknown_from_state(self):
        with self.assertRaises(states.StateMachineError):
            states.StateMachine({"states": ["a", "b"], "transitions": [{"from": "z", "to": "b"}]})

    def test_rejects_transition_with_unknown_to_state(self):
        with self.assertRaises(states.StateMachineError):
            states.StateMachine({"states": ["a", "b"], "transitions": [{"from": "a", "to": "z"}]})

    def test_rejects_initial_state_not_in_states(self):
        with self.assertRaises(states.StateMachineError):
            states.StateMachine({"states": ["a"], "initial_states": ["z"], "transitions": []})

    def test_rejects_malformed_transition_entry(self):
        with self.assertRaises(states.StateMachineError):
            states.StateMachine({"states": ["a", "b"], "transitions": [{"from": "a"}]})


class TestSubscriptionStateMachine(unittest.TestCase):
    def setUp(self):
        self.machine = states.load_state_machine(_TABLE_PATH)

    def test_trialing_to_active_is_valid(self):
        self.assertTrue(self.machine.is_valid_transition("trialing", "active"))
        self.machine.assert_transition("trialing", "active")  # must not raise

    def test_active_to_suspended_is_valid(self):
        self.assertTrue(self.machine.is_valid_transition("active", "suspended"))

    def test_grace_period_to_active_recovery_is_valid(self):
        self.assertTrue(self.machine.is_valid_transition("grace_period", "active"))

    def test_suspended_back_to_active_recovery_is_valid(self):
        self.assertTrue(self.machine.is_valid_transition("suspended", "active"))

    def test_cancelled_to_active_is_invalid(self):
        self.assertFalse(self.machine.is_valid_transition("cancelled", "active"))
        with self.assertRaises(states.TransitionError):
            self.machine.assert_transition("cancelled", "active")

    def test_active_to_expired_directly_is_invalid(self):
        # active must go through suspended/grace_period/past_due first
        self.assertFalse(self.machine.is_valid_transition("active", "expired"))

    def test_same_state_transition_is_always_valid(self):
        self.assertTrue(self.machine.is_valid_transition("active", "active"))
        self.machine.assert_transition("suspended", "suspended")

    def test_unknown_state_raises(self):
        with self.assertRaises(states.TransitionError):
            self.machine.assert_transition("bogus", "active")

    def test_terminal_states_are_cancelled_and_expired(self):
        self.assertTrue(self.machine.is_terminal("cancelled"))
        self.assertTrue(self.machine.is_terminal("expired"))
        self.assertFalse(self.machine.is_terminal("active"))

    def test_initial_states_are_trialing_and_active(self):
        self.assertIn("trialing", self.machine.initial_states)
        self.assertIn("active", self.machine.initial_states)
        self.assertNotIn("suspended", self.machine.initial_states)

    def test_allowed_next_states_from_active(self):
        nxt = self.machine.allowed_next_states("active")
        self.assertEqual(nxt, frozenset({"past_due", "suspended", "cancelled"}))
