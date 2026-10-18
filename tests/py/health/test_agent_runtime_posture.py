"""tests/py/health/test_agent_runtime_posture.py - unit tests for
lib/omes/py/health/agent_runtime_posture.py (issue #270).

The shared reader `hermes_config.config_get_json` is patched with a fake
that serves per-key ConfigRead results, so findings, validation and the
overall status are asserted without Hermes. The main invariant under test:
`unknown` evidence is never reported as `ok`.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
HEALTH_DIR = os.path.join(ROOT, "lib", "omes", "py", "health")
if HEALTH_DIR not in sys.path:
    sys.path.insert(0, HEALTH_DIR)

import agent_runtime_posture as posture  # noqa: E402
import hermes_config  # noqa: E402

# A fully healthy-looking, fully read configuration (values only illustrate
# shape; nothing here is asserted to be a Hermes default).
GOOD = {
    "delegation.max_concurrent_children": 4,
    "delegation.max_spawn_depth": 1,
    "delegation.max_iterations": 100,
    "delegation.child_timeout_seconds": 600,
    "delegation.subagent_auto_approve": False,
    "delegation.model": "worker-model",
    "delegation.provider": "worker-provider",
    "delegation.orchestrator_enabled": True,
    "delegation.worktree_isolation": True,
    "delegation.oneshot_max_children": 2,
}


def _read(key, spec):
    """spec: a JSON value => value; "ABSENT" => absent; ("UNKNOWN", reason) => unknown."""
    if spec == "ABSENT":
        return hermes_config.ConfigRead(key=key, state="absent")
    if isinstance(spec, tuple) and spec and spec[0] == "UNKNOWN":
        return hermes_config.ConfigRead(key=key, state="unknown", reason=spec[1])
    return hermes_config.ConfigRead(key=key, state="value", value=spec)


def collect_with(overrides=None, base=None):
    config = dict(GOOD if base is None else base)
    config.update(overrides or {})

    def fake(key, *, hermes_home=None, timeout=None):
        return _read(key, config[key])

    with mock.patch.object(posture.hermes_config, "config_get_json", side_effect=fake) as m:
        result = posture.collect()
    return result, m


def finding_ids(result):
    return [f["id"] for f in result["findings"]]


class TestStatus(unittest.TestCase):
    def test_all_read_no_warnings_is_ok_with_scope_and_note(self):
        result, _ = collect_with()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["scope"], "configured")
        self.assertIn("not observed", result["scope_note"])
        self.assertIn("Environment-variable overrides", result["scope_note"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["reason_codes"], [])

    def test_no_child_timeout_warns(self):
        result, _ = collect_with({"delegation.child_timeout_seconds": 0})
        self.assertEqual(result["status"], "warn")
        self.assertIn("delegation.no_child_timeout", finding_ids(result))

    def test_subagent_auto_approve_warns(self):
        result, _ = collect_with({"delegation.subagent_auto_approve": True})
        self.assertEqual(result["status"], "warn")
        self.assertIn("delegation.subagent_auto_approve_enabled", finding_ids(result))

    def test_depth_above_one_is_info_with_value_and_stays_ok(self):
        result, _ = collect_with({"delegation.max_spawn_depth": 2})
        self.assertEqual(result["status"], "ok")
        finding = next(f for f in result["findings"] if f["id"] == "delegation.nested_orchestration")
        self.assertEqual((finding["severity"], finding["value"]), ("info", 2))

    def test_depth_above_documented_range_warns(self):
        result, _ = collect_with({"delegation.max_spawn_depth": 7})
        self.assertEqual(result["status"], "warn")
        finding = next(f for f in result["findings"] if f["id"] == "delegation.spawn_depth_above_documented")
        self.assertEqual((finding["severity"], finding["value"]), ("warn", 7))

    def test_depth_one_has_no_depth_finding(self):
        result, _ = collect_with({"delegation.max_spawn_depth": 1})
        self.assertFalse([i for i in finding_ids(result) if "depth" in i or "nested" in i])

    def test_empty_model_is_info_about_tiering_gap_and_stays_ok(self):
        result, _ = collect_with({"delegation.model": ""})
        self.assertEqual(result["status"], "ok")
        finding = next(f for f in result["findings"] if f["id"] == "delegation.model_not_pinned")
        self.assertEqual(finding["severity"], "info")
        self.assertIn("ADR-0032 rule 2", finding["message"])
        self.assertIn("separate Hermes profiles", finding["message"])

    def test_pinned_model_has_no_tiering_finding(self):
        result, _ = collect_with({"delegation.model": "worker-model"})
        self.assertNotIn("delegation.model_not_pinned", finding_ids(result))

    def test_warn_outranks_info(self):
        result, _ = collect_with({"delegation.model": "", "delegation.child_timeout_seconds": 0})
        self.assertEqual(result["status"], "warn")


class TestUnknownIsNeverOk(unittest.TestCase):
    REQUIRED = (
        "delegation.max_concurrent_children",
        "delegation.max_spawn_depth",
        "delegation.max_iterations",
        "delegation.child_timeout_seconds",
        "delegation.subagent_auto_approve",
    )
    OPTIONAL = (
        "delegation.model",
        "delegation.provider",
        "delegation.orchestrator_enabled",
        "delegation.worktree_isolation",
        "delegation.oneshot_max_children",
    )

    def test_each_required_key_unknown_makes_status_unknown(self):
        for key in self.REQUIRED:
            result, _ = collect_with({key: ("UNKNOWN", "timeout")})
            self.assertEqual(result["status"], "unknown", key)
            self.assertIn("delegation.required_key_timeout", result["reason_codes"], key)
            self.assertEqual(result["keys"][key], {"state": "unknown", "reason": "timeout"})

    def test_each_required_key_absent_makes_status_unknown(self):
        for key in self.REQUIRED:
            result, _ = collect_with({key: "ABSENT"})
            self.assertEqual(result["status"], "unknown", key)
            self.assertIn("delegation.required_key_absent", result["reason_codes"], key)

    def test_unknown_stays_unknown_even_when_a_warning_is_present(self):
        result, _ = collect_with({
            "delegation.max_iterations": ("UNKNOWN", "nonzero_exit"),
            "delegation.subagent_auto_approve": True,
        })
        self.assertEqual(result["status"], "unknown")

    def test_hermes_missing_is_unknown_not_ok(self):
        everything_unknown = {key: ("UNKNOWN", "binary_missing") for key in GOOD}
        result, _ = collect_with(base=everything_unknown)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason_codes"].count("delegation.required_key_binary_missing"), 1)

    def test_optional_key_unreadable_does_not_change_status_but_is_recorded(self):
        for key in self.OPTIONAL:
            result, _ = collect_with({key: ("UNKNOWN", "timeout")})
            self.assertEqual(result["status"], "ok", key)
            self.assertIn("delegation.optional_key_unavailable", finding_ids(result), key)
            self.assertEqual(result["keys"][key]["state"], "unknown")

    def test_absent_optional_keys_do_not_change_status(self):
        for key in self.OPTIONAL:
            result, _ = collect_with({key: "ABSENT"})
            self.assertEqual(result["status"], "ok", key)
            self.assertEqual(result["keys"][key]["state"], "absent")

    def test_worktree_isolation_absent_reports_inferred_interpretation(self):
        result, _ = collect_with({"delegation.worktree_isolation": "ABSENT"})
        self.assertEqual(result["status"], "ok")
        entry = result["keys"]["delegation.worktree_isolation"]
        self.assertEqual(entry["state"], "absent")
        self.assertEqual(entry["interpretation"], "default false (inferred)")

    def test_evaluate_with_no_evidence_at_all_is_unknown(self):
        self.assertEqual(posture.evaluate({})["status"], "unknown")


class TestValidation(unittest.TestCase):
    def test_wrong_types_are_unknown_with_reason_and_value_not_echoed(self):
        bad = {
            "delegation.max_concurrent_children": "ten",
            "delegation.max_spawn_depth": 1.5,
            "delegation.max_iterations": True,
            "delegation.child_timeout_seconds": None,
            "delegation.subagent_auto_approve": "yes",
        }
        for key, value in bad.items():
            result, _ = collect_with({key: value})
            self.assertEqual(result["status"], "unknown", key)
            self.assertEqual(result["keys"][key], {"state": "unknown", "reason": "invalid_type"}, key)
            self.assertNotIn(str(value), json.dumps(result["keys"][key]))

    def test_int_ranges(self):
        cases = (
            ("delegation.max_concurrent_children", 0),
            ("delegation.max_spawn_depth", 0),
            ("delegation.max_iterations", -1),
            ("delegation.child_timeout_seconds", -1),
            ("delegation.max_spawn_depth", 2**31),
        )
        for key, value in cases:
            result, _ = collect_with({key: value})
            self.assertEqual(result["keys"][key], {"state": "unknown", "reason": "out_of_range"}, (key, value))
            self.assertEqual(result["status"], "unknown")

    def test_zero_is_valid_only_for_child_timeout(self):
        result, _ = collect_with({"delegation.child_timeout_seconds": 0})
        self.assertEqual(result["keys"]["delegation.child_timeout_seconds"], {"state": "value", "value": 0})

    def test_string_bounds(self):
        for value in ("x" * 129, "bad\nmodel", 5):
            result, _ = collect_with({"delegation.model": value})
            self.assertEqual(result["keys"]["delegation.model"]["state"], "unknown")
            self.assertEqual(result["status"], "ok")  # optional key: recorded, does not change status

    def test_json_object_for_a_scalar_key_is_invalid(self):
        result, _ = collect_with({"delegation.max_iterations": {"a": 1}})
        self.assertEqual(result["status"], "unknown")


class TestCollect(unittest.TestCase):
    def test_reads_only_allowlisted_delegation_keys_via_shared_reader(self):
        _, mocked = collect_with()
        requested = {call.args[0] for call in mocked.call_args_list}
        self.assertEqual(requested, set(GOOD))
        self.assertLessEqual(requested, hermes_config.ALLOWED_KEYS)

    def test_profile_home_and_timeout_are_forwarded(self):
        with mock.patch.object(
            posture.hermes_config, "config_get_json",
            side_effect=lambda key, **kw: _read(key, GOOD[key]),
        ) as m:
            result = posture.collect(hermes_home="/srv/p", timeout=2.0)
        for call in m.call_args_list:
            self.assertEqual(call.kwargs, {"hermes_home": "/srv/p", "timeout": 2.0})
        self.assertTrue(result["profile_home_override"])

    def test_output_is_json_serialisable_and_has_no_path_or_secret_fields(self):
        result, _ = collect_with()
        text = json.dumps(result)
        self.assertNotIn("/srv", text)
        for forbidden in ("api_key", "base_url", "token", "password"):
            self.assertNotIn(forbidden, text)

    def test_run_through_the_real_reader_without_hermes_is_unknown(self):
        with mock.patch.object(hermes_config.shutil, "which", return_value=None):
            result = posture.collect()
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(all(e["state"] == "unknown" and e["reason"] == "binary_missing" for e in result["keys"].values()))

    def test_exit_code_convention(self):
        with mock.patch.object(posture, "run", return_value={"status": "ok"}), mock.patch("builtins.print"):
            self.assertEqual(posture.main([]), 0)
        with mock.patch.object(posture, "run", return_value={"status": "warn"}), mock.patch("builtins.print"):
            self.assertEqual(posture.main([]), 0)
        with mock.patch.object(posture, "run", return_value={"status": "unknown"}), mock.patch("builtins.print"):
            self.assertEqual(posture.main([]), 7)


if __name__ == "__main__":
    unittest.main()
