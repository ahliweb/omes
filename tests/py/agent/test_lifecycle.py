"""tests/py/agent/test_lifecycle.py - the declared -> ... -> healthy /
degraded|failed|rolled-back state machine (issue #87)."""
from __future__ import annotations

import unittest

from . import _pathfix  # noqa: F401

from agent import lifecycle  # noqa: E402


class TestForwardPath(unittest.TestCase):
    def test_full_happy_path(self):
        state = "declared"
        for target in (
            "preflighted",
            "planned",
            "backed-up",
            "applied",
            "verified",
            "ready",
            "healthy",
        ):
            state = lifecycle.transition(state, target)
        self.assertEqual(state, "healthy")

    def test_skipping_a_state_is_illegal(self):
        with self.assertRaises(lifecycle.TransitionError):
            lifecycle.transition("declared", "applied")

    def test_self_transition_is_always_legal(self):
        for state in lifecycle.STATES:
            self.assertEqual(lifecycle.transition(state, state), state)


class TestFailureStates(unittest.TestCase):
    def test_any_in_progress_state_can_fail(self):
        for state in ("preflighted", "planned", "backed-up", "applied", "verified", "ready"):
            self.assertTrue(lifecycle.can_transition(state, "failed"))
            self.assertTrue(lifecycle.can_transition(state, "rolled-back"))

    def test_healthy_can_degrade(self):
        self.assertTrue(lifecycle.can_transition("healthy", "degraded"))

    def test_failed_can_only_redeclare_or_rollback(self):
        self.assertTrue(lifecycle.can_transition("failed", "declared"))
        self.assertTrue(lifecycle.can_transition("failed", "rolled-back"))
        self.assertFalse(lifecycle.can_transition("failed", "healthy"))

    def test_rolled_back_can_only_redeclare(self):
        self.assertTrue(lifecycle.can_transition("rolled-back", "declared"))
        self.assertFalse(lifecycle.can_transition("rolled-back", "healthy"))


class TestIdempotentReapply(unittest.TestCase):
    def test_ready_or_healthy_can_restart_the_cycle(self):
        for state in ("planned", "backed-up", "applied", "verified", "ready", "healthy", "degraded"):
            self.assertTrue(
                lifecycle.can_transition(state, "preflighted"),
                f"{state} -> preflighted should be legal (idempotent re-apply)",
            )


class TestInvalidStateNames(unittest.TestCase):
    def test_unknown_state_name_rejected(self):
        self.assertFalse(lifecycle.can_transition("declared", "bogus"))
        self.assertFalse(lifecycle.can_transition("bogus", "declared"))
        with self.assertRaises(lifecycle.TransitionError):
            lifecycle.transition("declared", "bogus")


if __name__ == "__main__":
    unittest.main()
