"""tests/py/agent/test_plan.py - pure plan computation (issue #87)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from agent import plan as plan_mod  # noqa: E402
from agent import unitfile  # noqa: E402

FIXTURES = Path(_pathfix.OMES_ROOT) / "contracts" / "agent" / "v1" / "fixtures" / "agent-deployment"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestBuildPlan(unittest.TestCase):
    def test_unit_name_derivation(self):
        p = plan_mod.build_plan(_load("valid-generic-user.json"))
        self.assertEqual(p["unit"]["name"], "omes-agent-researcher.service")

    def test_user_mode_unit_dir_is_under_home(self):
        p = plan_mod.build_plan(_load("valid-generic-user.json"))
        self.assertIn(".config/systemd/user", p["unit"]["dir"])

    def test_system_mode_unit_dir_is_etc_systemd(self):
        p = plan_mod.build_plan(_load("valid-specialist-system.json"))
        self.assertEqual(p["unit"]["dir"], "/etc/systemd/system")

    def test_hermes_home_is_agent_specific_not_shared(self):
        p1 = plan_mod.build_plan(_load("valid-generic-user.json"))
        data2 = _load("valid-generic-user.json")
        data2["metadata"]["name"] = "reviewer"
        data2["spec"]["profile"] = "reviewer"
        p2 = plan_mod.build_plan(data2)
        self.assertNotEqual(p1["hermesHome"], p2["hermesHome"])
        self.assertIn("researcher", p1["hermesHome"])
        self.assertIn("reviewer", p2["hermesHome"])

    def test_secrets_are_reference_names_only_never_values(self):
        p = plan_mod.build_plan(_load("valid-generic-user.json"))
        self.assertEqual(p["secretReferences"], ["provider-primary"])
        # The plan must never resolve/expand a reference into a value -
        # only the (unread) file path a value might one day live in.
        self.assertTrue(p["environmentFileReference"].endswith(".env"))

    def test_no_secrets_means_no_environment_file_reference(self):
        data = _load("valid-specialist-system.json")
        p = plan_mod.build_plan(data)
        self.assertIsNone(p["environmentFileReference"])

    def test_resource_dropin_lines_reflect_manifest_values(self):
        p = plan_mod.build_plan(_load("valid-generic-user.json"))
        joined = "\n".join(p["resourceDropinLines"])
        self.assertIn("MemoryMax=1G", joined)
        self.assertIn("TasksMax=128", joined)
        self.assertIn("CPUQuota=100%", joined)
        self.assertIn("Restart=always", joined)

    def test_cpu_quota_percent_rounding(self):
        self.assertEqual(plan_mod._cpu_quota_percent("0.5"), "50%")
        self.assertEqual(plan_mod._cpu_quota_percent("2"), "200%")
        self.assertEqual(plan_mod._cpu_quota_percent("not-a-number"), "100%")


class TestUnitfileRendering(unittest.TestCase):
    def test_render_unit_never_contains_a_secret_value(self):
        p = plan_mod.build_plan(_load("valid-generic-user.json"))
        content = unitfile.render_unit(p)
        self.assertNotIn("provider-primary=", content)
        self.assertIn("EnvironmentFile=-", content)
        self.assertIn(p["hermesHome"], content)

    def test_render_dropin_deduplicates_resource_keys(self):
        p = plan_mod.build_plan(_load("valid-generic-user.json"))
        content = unitfile.render_dropin(p, Path(_pathfix.OMES_ROOT), profile="off")
        # profile "off" means no hardening body at all; only our own lines.
        self.assertEqual(content.count("MemoryMax="), 1)
        self.assertEqual(content.count("TasksMax="), 1)

    def test_render_dropin_with_conservative_profile_includes_hardening_directives(self):
        p = plan_mod.build_plan(_load("valid-generic-user.json"))
        content = unitfile.render_dropin(p, Path(_pathfix.OMES_ROOT), profile="conservative")
        self.assertIn("NoNewPrivileges=yes", content)
        # manifest-specific value must win over hardening_render's generic default
        self.assertEqual(content.count("MemoryMax="), 1)
        self.assertIn("MemoryMax=1G", content)


if __name__ == "__main__":
    unittest.main()
