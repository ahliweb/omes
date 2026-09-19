"""tests/py/agent/test_state.py - per-agent state file read/write/advance
(issue #87)."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from agent import lifecycle, state as state_mod  # noqa: E402


class StateTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-agent-state-test-")
        self._old_state_dir = os.environ.get("OMES_STATE_DIR")
        os.environ["OMES_STATE_DIR"] = self._tmp

    def tearDown(self):
        if self._old_state_dir is None:
            os.environ.pop("OMES_STATE_DIR", None)
        else:
            os.environ["OMES_STATE_DIR"] = self._old_state_dir
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestDefaultAndRoundtrip(StateTestCase):
    def test_load_missing_returns_declared_default(self):
        st = state_mod.load("researcher")
        self.assertEqual(st["state"], "declared")
        self.assertEqual(st["history"], [])

    def test_save_and_load_roundtrip(self):
        st = state_mod.default_state("researcher")
        st["state"] = "ready"
        state_mod.save("researcher", st)
        loaded = state_mod.load("researcher")
        self.assertEqual(loaded["state"], "ready")

    def test_state_file_is_mode_0600(self):
        state_mod.save("researcher", state_mod.default_state("researcher"))
        path = Path(_pathfix.__file__).parent  # unused, keeps flake happy
        from agent import paths as paths_mod

        mode = oct(os.stat(paths_mod.agent_state_file("researcher")).st_mode)[-3:]
        self.assertEqual(mode, "600")


class TestAdvance(StateTestCase):
    def test_advance_records_history_entry(self):
        state_mod.advance("researcher", "preflighted", detail="ok")
        st = state_mod.advance("researcher", "planned", detail="ok2")
        self.assertEqual(st["state"], "planned")
        self.assertEqual(len(st["history"]), 2)
        self.assertEqual(st["history"][-1]["to"], "planned")

    def test_advance_rejects_illegal_transition(self):
        with self.assertRaises(lifecycle.TransitionError):
            state_mod.advance("researcher", "applied")

    def test_advance_stores_managed_paths(self):
        state_mod.advance("researcher", "preflighted")
        state_mod.advance("researcher", "planned")
        state_mod.advance("researcher", "backed-up")
        st = state_mod.advance("researcher", "applied", managed_paths=["/a", "/b"])
        self.assertEqual(st["managedPaths"], ["/a", "/b"])

    def test_advance_merges_provenance_without_credentials(self):
        state_mod.advance("researcher", "preflighted")
        state_mod.advance("researcher", "planned")
        state_mod.advance("researcher", "backed-up")
        st = state_mod.advance(
            "researcher", "applied", provenance={"omesVersion": "0.1.0", "gitRef": "abc123"}
        )
        self.assertEqual(st["provenance"]["omesVersion"], "0.1.0")
        for value in st["provenance"].values():
            self.assertNotIn("TOKEN", str(value).upper())
            self.assertNotIn("SECRET", str(value).upper())

    def test_history_is_bounded(self):
        current = "declared"
        cycle = ["preflighted", "planned", "backed-up", "applied", "verified", "ready", "healthy"]
        for _ in range(state_mod.HISTORY_LIMIT + 10):
            for target in cycle:
                state_mod.advance("researcher", target)
            state_mod.advance("researcher", "preflighted")  # restart the cycle (idempotent re-apply)
        st = state_mod.load("researcher")
        self.assertLessEqual(len(st["history"]), state_mod.HISTORY_LIMIT)


class TestListAndRemove(StateTestCase):
    def test_list_agents_empty_when_none_declared(self):
        self.assertEqual(state_mod.list_agents(), [])

    def test_list_agents_after_advance(self):
        state_mod.advance("researcher", "preflighted")
        state_mod.advance("reviewer", "preflighted")
        self.assertEqual(state_mod.list_agents(), ["researcher", "reviewer"])

    def test_remove_deletes_only_that_agents_state_dir(self):
        state_mod.advance("researcher", "preflighted")
        state_mod.advance("reviewer", "preflighted")
        state_mod.remove("researcher")
        self.assertEqual(state_mod.list_agents(), ["reviewer"])


if __name__ == "__main__":
    unittest.main()
