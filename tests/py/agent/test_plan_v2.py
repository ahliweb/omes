"""tests/py/agent/test_plan_v2.py - plan computation for RuntimeDeployment v2 (#174)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from agent import compose as compose_mod  # noqa: E402
from agent import plan as plan_mod  # noqa: E402
from agent import unitfile  # noqa: E402

FIXTURES_V2 = Path(_pathfix.OMES_ROOT) / "contracts" / "agent" / "v2" / "fixtures" / "runtime-deployment"


def _load(name: str) -> dict:
    return json.loads((FIXTURES_V2 / name).read_text(encoding="utf-8"))


class TestBuildPlanV2(unittest.TestCase):
    def test_native_plan_derivation(self):
        manifest = _load("valid-native-user.json")
        p = plan_mod.build_plan(manifest)
        self.assertEqual(p["unit"]["name"], "omes-agent-researcher.service")
        self.assertIn(".config/systemd/user", p["unit"]["dir"])
        self.assertIn("researcher", p["hermesHome"])
        self.assertEqual(p["secretReferences"], [])
        self.assertIsNone(p["environmentFileReference"])

    def test_native_resource_limits_dropin(self):
        manifest = _load("valid-native-user.json")
        p = plan_mod.build_plan(manifest)
        joined = "\n".join(p["resourceDropinLines"])
        self.assertIn("MemoryMax=1G", joined)
        self.assertIn("TasksMax=128", joined)
        self.assertIn("CPUQuota=100%", joined)
        self.assertIn("Restart=always", joined)

    def test_system_scope_unit_dir(self):
        manifest = _load("valid-system-scope.json")
        p = plan_mod.build_plan(manifest)
        self.assertEqual(p["unit"]["dir"], "/etc/systemd/system")
        self.assertEqual(p["secretReferences"], [])
        self.assertIsNone(p["environmentFileReference"])

    def test_render_unitfile_for_v2(self):
        manifest = _load("valid-native-user.json")
        p = plan_mod.build_plan(manifest)
        content = unitfile.render_unit(p)
        self.assertIn(p["hermesHome"], content)
        self.assertIn("HERMES_HOME=", content)

    def test_compose_plan_derivation(self):
        manifest = _load("valid-compose-user.json")
        p = compose_mod.build_plan(manifest)
        self.assertEqual(p["serviceName"], "researcher-container")
        self.assertEqual(p["project"], "omes-agent-researcher-container")
        self.assertTrue(p["composeFile"].endswith("compose.yaml"))
        content = compose_mod.render_compose_yaml(p)
        self.assertIn("ghcr.io/nousresearch/hermes-agent@", content)
        self.assertIn('mem_limit: "2g"', content)
        self.assertIn('cpus: "2.0"', content)
        self.assertIn('restart: "on-failure"', content)


if __name__ == "__main__":
    unittest.main()
