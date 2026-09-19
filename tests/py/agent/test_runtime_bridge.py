"""tests/py/agent/test_runtime_bridge.py - lib/omes/py/agent/runtime_bridge.py
(issue #85/#87/#96 follow-up: "lib/omes/runtime.sh integration"). Proves
the Python side reads the per-agent systemd unit name from
lib/omes/runtime.sh's `runtime_agent_service_unit` (the one source of
truth), rather than re-deriving "omes-agent-<name>.service" itself, and
that `lib/omes/py/agent/plan.py`'s `build_plan` uses that value when a
caller supplies it while staying a pure/subprocess-free function when it
does not.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from agent import plan as plan_mod  # noqa: E402
from agent import runtime_bridge  # noqa: E402

OMES_ROOT = Path(_pathfix.OMES_ROOT)


def _load(name: str) -> dict:
    import json

    fixtures = OMES_ROOT / "contracts" / "agent" / "v1" / "fixtures" / "agent-deployment"
    with open(fixtures / name, "r", encoding="utf-8") as fh:
        return json.load(fh)


class RuntimeBridgeTestCase(unittest.TestCase):
    def test_agent_service_unit_matches_plan_pys_own_default_shape(self):
        got = runtime_bridge.agent_service_unit("researcher", "user", OMES_ROOT)
        self.assertEqual(got, "omes-agent-researcher.service")
        # The bridge's output must equal plan.py's own fallback shape -
        # they must never silently diverge (that is the entire point of
        # having one source of truth).
        self.assertEqual(got, plan_mod.unit_name("researcher"))

    def test_agent_service_unit_rejects_invalid_scope_by_returning_none(self):
        # runtime.sh's runtime_agent_service_unit dies (non-zero exit) for
        # an invalid scope; the bridge must not raise for that - it
        # returns None so callers fall back rather than crashing
        # `omes agent plan`.
        got = runtime_bridge.agent_service_unit("researcher", "bogus-scope", OMES_ROOT)
        self.assertIsNone(got)

    def test_agent_service_unit_returns_none_when_runtime_sh_is_missing(self):
        got = runtime_bridge.agent_service_unit("researcher", "user", Path("/nonexistent-omes-root"))
        self.assertIsNone(got)

    def test_build_plan_uses_bridge_resolved_override_verbatim(self):
        manifest = _load("valid-generic-user.json")
        p = plan_mod.build_plan(manifest, unit_name_override="omes-agent-custom-override.service")
        self.assertEqual(p["unit"]["name"], "omes-agent-custom-override.service")

    def test_build_plan_falls_back_to_its_own_unit_name_without_an_override(self):
        manifest = _load("valid-generic-user.json")
        p = plan_mod.build_plan(manifest)
        self.assertEqual(p["unit"]["name"], plan_mod.unit_name(manifest["metadata"]["name"]))


if __name__ == "__main__":
    unittest.main()
