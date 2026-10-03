"""tests/py/health/test_budget_posture.py - unit tests for
lib/omes/py/health/budget_posture.py (issue #275).

The shared reader `hermes_config.config_get_json` is patched, so findings,
validation and status are asserted without Hermes. The main invariants: an
explicit null (configured unlimited) is a different fact from an absent or an
unreadable key; unknown evidence is never `ok`; token usage, spend and cost
are always `unknown` and never 0; no `hermes usage` or `hermes insights`
command is ever run and `--raw` is never passed.
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

import budget_posture as posture  # noqa: E402
import hermes_config  # noqa: E402

TURNS = "agent.max_turns"
BUDGET = "agent.run_budget_seconds"
SUBAGENTS = "agent.loop_caps.max_subagents"
SEARCHES = "agent.loop_caps.max_web_searches"
ALL_KEYS = (TURNS, BUDGET, SUBAGENTS, SEARCHES)

#: Sentinel for an explicit JSON null (None is a legitimate parsed value).
NULL = object()


def _read(key, spec):
    """spec: a JSON value => value; NULL => explicit null; "ABSENT" => absent;
    ("UNKNOWN", reason) => unknown."""
    if spec == "ABSENT":
        return hermes_config.ConfigRead(key=key, state="absent")
    if isinstance(spec, tuple) and spec and spec[0] == "UNKNOWN":
        return hermes_config.ConfigRead(key=key, state="unknown", reason=spec[1])
    if spec is NULL:
        return hermes_config.ConfigRead(key=key, state="value", value=None)
    return hermes_config.ConfigRead(key=key, state="value", value=spec)


def collect_with(turns=100, budget=3600, subagents=50, searches=50):
    config = {TURNS: turns, BUDGET: budget, SUBAGENTS: subagents, SEARCHES: searches}

    def fake(key, *, hermes_home=None, timeout=None):
        return _read(key, config[key])

    with mock.patch.object(posture.hermes_config, "config_get_json", side_effect=fake) as reader:
        result = posture.collect()
    return result, reader


def finding_ids(result):
    return [f["id"] for f in result["findings"]]


class TestFindings(unittest.TestCase):
    def test_finite_limits_are_ok_with_info_findings_only(self):
        result, _ = collect_with()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["scope"], "configured")
        self.assertNotIn("budget.unlimited_turns", finding_ids(result))
        self.assertNotIn("budget.no_run_time_budget", finding_ids(result))
        self.assertTrue(all(f["severity"] == "info" for f in result["findings"]))
        caps = [f for f in result["findings"] if f["id"] == "budget.loop_cap_configured"]
        self.assertEqual({f["key"]: f["value"] for f in caps}, {SUBAGENTS: 50, SEARCHES: 50})

    def test_explicit_null_turns_warns_and_is_not_absent_or_unknown(self):
        result, _ = collect_with(turns=NULL)
        self.assertEqual(result["status"], "warn")
        self.assertEqual(result["keys"][TURNS], {"state": "value", "value": None})
        finding = next(f for f in result["findings"] if f["id"] == "budget.unlimited_turns")
        self.assertEqual(finding["severity"], "warn")
        self.assertEqual(finding["key"], TURNS)
        self.assertNotIn("budget.no_run_time_budget", finding_ids(result))

    def test_explicit_null_run_budget_warns(self):
        result, _ = collect_with(budget=NULL)
        self.assertEqual(result["status"], "warn")
        self.assertEqual(result["keys"][BUDGET], {"state": "value", "value": None})
        finding = next(f for f in result["findings"] if f["id"] == "budget.no_run_time_budget")
        self.assertEqual(finding["severity"], "warn")
        self.assertNotIn("budget.unlimited_turns", finding_ids(result))

    def test_both_null_gives_both_warnings(self):
        result, _ = collect_with(turns=NULL, budget=NULL)
        self.assertEqual(result["status"], "warn")
        self.assertIn("budget.unlimited_turns", finding_ids(result))
        self.assertIn("budget.no_run_time_budget", finding_ids(result))

    def test_loop_caps_are_info_only_whatever_their_value(self):
        for cap in (1, 50, 100000):
            result, _ = collect_with(subagents=cap, searches=cap)
            self.assertEqual(result["status"], "ok", cap)
            for f in result["findings"]:
                if f["id"] == "budget.loop_cap_configured":
                    self.assertEqual(f["severity"], "info")
                    self.assertEqual(f["value"], cap)

    def test_authority_and_unobservable_findings_are_always_present(self):
        for kwargs in ({}, {"turns": NULL}, {"turns": "ABSENT"}, {"turns": ("UNKNOWN", "timeout")}):
            result, _ = collect_with(**kwargs)
            ids = finding_ids(result)
            self.assertIn("budget.token_spend_unobservable", ids, kwargs)
            self.assertIn("budget.policy_authority_awcms", ids, kwargs)
            for fid in ("budget.token_spend_unobservable", "budget.policy_authority_awcms"):
                self.assertEqual(next(f for f in result["findings"] if f["id"] == fid)["severity"], "info")
            authority = next(f for f in result["findings"] if f["id"] == "budget.policy_authority_awcms")
            self.assertIn("AWCMS", authority["message"])
            self.assertIn("Hermes", authority["message"])
            self.assertIn("infrastructure", authority["message"])

    def test_messages_never_claim_omes_enforces_a_limit(self):
        result, _ = collect_with(turns=NULL, budget=NULL)
        text = json.dumps(result).lower()
        self.assertIn("omes enforces no spend ceiling", text)
        self.assertNotIn("omes enforces the", text)


class TestUsageIsNeverZero(unittest.TestCase):
    def test_usage_is_always_unknown_with_no_source(self):
        expected = {"tokens": "unknown", "cost": "unknown", "source": "none", "confidence": "none"}
        for kwargs in ({}, {"turns": NULL, "budget": NULL}, {"turns": "ABSENT"}, {"budget": ("UNKNOWN", "timeout")}):
            result, _ = collect_with(**kwargs)
            self.assertEqual(result["usage"], expected, kwargs)
            self.assertNotEqual(result["usage"]["tokens"], 0)
            self.assertNotEqual(result["usage"]["cost"], 0)

    def test_usage_dict_is_a_copy_not_a_shared_mutable(self):
        result, _ = collect_with()
        result["usage"]["tokens"] = 123
        self.assertEqual(posture.USAGE_UNKNOWN["tokens"], "unknown")
        again, _ = collect_with()
        self.assertEqual(again["usage"]["tokens"], "unknown")

    def test_host_limits_are_not_collected_and_related_check_is_named(self):
        result, _ = collect_with()
        self.assertEqual(result["host_limits"], "not_collected")
        self.assertEqual(result["related_checks"], {"delegation_limits": "omes health agent-runtime"})


class TestUnknownIsNeverOk(unittest.TestCase):
    def test_required_key_unreadable_is_unknown(self):
        for key_kwarg in ("turns", "budget"):
            result, _ = collect_with(**{key_kwarg: ("UNKNOWN", "timeout")})
            self.assertEqual(result["status"], "unknown", key_kwarg)
            self.assertIn("budget.required_key_timeout", result["reason_codes"])

    def test_absent_required_key_is_unknown_not_unlimited_and_not_ok(self):
        # Hermes says "not set"; OMES hardcodes no upstream default, so absence
        # is neither a warning about unlimited turns nor an ok.
        for key_kwarg, key in (("turns", TURNS), ("budget", BUDGET)):
            result, _ = collect_with(**{key_kwarg: "ABSENT"})
            self.assertEqual(result["status"], "unknown", key_kwarg)
            self.assertEqual(result["keys"][key], {"state": "absent"})
            self.assertNotIn("budget.unlimited_turns", finding_ids(result))
            self.assertNotIn("budget.no_run_time_budget", finding_ids(result))
            self.assertIn("budget.required_key_absent", result["reason_codes"])

    def test_unknown_stays_unknown_when_a_warning_would_otherwise_apply(self):
        result, _ = collect_with(turns=NULL, budget=("UNKNOWN", "nonzero_exit"))
        self.assertEqual(result["status"], "unknown")

    def test_malformed_required_values_are_unknown_and_not_echoed(self):
        bad = ("many", "100", True, False, 1.5, {"a": 1}, [1], 0, -1, 2**31)
        for value in bad:
            for key, kwargs in ((TURNS, {"turns": value}), (BUDGET, {"budget": value})):
                result, _ = collect_with(**kwargs)
                self.assertEqual(result["status"], "unknown", (key, value))
                self.assertEqual(result["keys"][key]["state"], "unknown", (key, value))
                self.assertIn(result["keys"][key]["reason"], ("invalid_type", "out_of_range"))
                self.assertNotIn("value", result["keys"][key])

    def test_malformed_loop_cap_is_unknown_info_and_does_not_change_status(self):
        for value in ("x", True, 0, NULL, 1.5):
            for key, kwargs in ((SUBAGENTS, {"subagents": value}), (SEARCHES, {"searches": value})):
                result, _ = collect_with(**kwargs)
                self.assertEqual(result["keys"][key]["state"], "unknown", (key, value))
                self.assertEqual(result["status"], "ok", (key, value))
                self.assertIn("budget.optional_key_unavailable", finding_ids(result))

    def test_unreadable_or_absent_loop_caps_do_not_make_status_unknown(self):
        result, _ = collect_with(subagents=("UNKNOWN", "timeout"), searches="ABSENT")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["keys"][SEARCHES], {"state": "absent"})

    def test_evaluate_with_no_evidence_is_unknown(self):
        self.assertEqual(posture.evaluate({})["status"], "unknown")

    def test_hermes_missing_through_the_real_reader_is_unknown(self):
        with mock.patch.object(hermes_config.shutil, "which", return_value=None):
            result = posture.collect()
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(all(e["reason"] == "binary_missing" for e in result["keys"].values()))
        self.assertEqual(result["usage"]["tokens"], "unknown")


class TestCollect(unittest.TestCase):
    def test_reads_only_the_four_allowlisted_keys_and_never_raw(self):
        _, reader = collect_with()
        requested = {call.args[0] for call in reader.call_args_list}
        self.assertEqual(requested, set(ALL_KEYS))
        self.assertLessEqual(requested, hermes_config.ALLOWED_KEYS)
        for key in requested:
            argv = hermes_config.build_argv(key)
            self.assertNotIn("--raw", argv)
            self.assertEqual(argv, ["hermes", "config", "get", key, "--json"])

    def test_keys_are_allowlisted_and_not_secret_shaped(self):
        for key in ALL_KEYS:
            self.assertIn(key, hermes_config.ALLOWED_KEYS)
            self.assertIsNone(hermes_config.SECRET_KEY_PATTERN.search(key), key)

    def test_no_token_spend_or_cost_key_is_allowlisted(self):
        for key in hermes_config.ALLOWED_KEYS:
            for word in ("token", "spend", "cost", "usage", "budget_usd"):
                self.assertNotIn(word, key, key)

    def test_only_config_get_is_run_through_the_real_reader_never_usage_or_insights(self):
        argvs = []

        def fake_run(argv, timeout, env=None):
            argvs.append(list(argv))
            return 0, "100\n", ""

        with mock.patch.object(hermes_config.shutil, "which", return_value="/usr/bin/hermes"), \
                mock.patch.object(hermes_config, "_run", side_effect=fake_run):
            result = posture.collect()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(argvs), len(ALL_KEYS))
        for argv in argvs:
            self.assertEqual(argv[:3], ["hermes", "config", "get"])
            self.assertEqual(argv[-1], "--json")
            self.assertNotIn("--raw", argv)
            self.assertNotIn("usage", argv)
            self.assertNotIn("insights", argv)
        self.assertEqual({a[3] for a in argvs}, set(ALL_KEYS))

    def test_module_has_no_subprocess_of_its_own(self):
        source = open(os.path.join(HEALTH_DIR, "budget_posture.py"), encoding="utf-8").read()
        self.assertNotIn("subprocess", source)
        self.assertNotIn("_run(", source)
        # The only references to usage/insights commands are in prose and findings.
        self.assertNotIn('"usage"]', source)
        self.assertNotIn('"insights"', source)

    def test_explicit_null_survives_the_real_reader_json_parse(self):
        def fake_run(argv, timeout, env=None):
            return 0, "null\n", ""

        with mock.patch.object(hermes_config.shutil, "which", return_value="/usr/bin/hermes"), \
                mock.patch.object(hermes_config, "_run", side_effect=fake_run):
            result = posture.collect()
        self.assertEqual(result["keys"][TURNS], {"state": "value", "value": None})
        self.assertEqual(result["keys"][BUDGET], {"state": "value", "value": None})
        # Loop caps do not accept null: invalid, optional, status unaffected by them.
        self.assertEqual(result["keys"][SUBAGENTS]["state"], "unknown")
        self.assertEqual(result["status"], "warn")

    def test_profile_home_and_timeout_are_forwarded_to_the_shared_reader(self):
        with mock.patch.object(posture.hermes_config, "config_get_json",
                               side_effect=lambda key, **kw: _read(key, 10)) as m:
            result = posture.collect(hermes_home="/srv/p", timeout=2.0)
        for call in m.call_args_list:
            self.assertEqual(call.kwargs, {"hermes_home": "/srv/p", "timeout": 2.0})
        self.assertTrue(result["profile_home_override"])
        self.assertNotIn("/srv", json.dumps(result))

    def test_output_shape_and_scope_note(self):
        result, _ = collect_with(turns=NULL)
        self.assertEqual(result["check"], "budget_posture")
        self.assertEqual(result["scope"], "configured")
        self.assertIn("never zero", result["scope_note"])
        self.assertIn("AWCMS", result["scope_note"])
        self.assertEqual(result["keys"][BUDGET], {"state": "value", "value": 3600})
        json.dumps(result)

    def test_exit_code_convention(self):
        for status, code in (("ok", 0), ("warn", 0), ("unknown", 7)):
            with mock.patch.object(posture, "run", return_value={"status": status}), mock.patch("builtins.print"):
                self.assertEqual(posture.main([]), code)


if __name__ == "__main__":
    unittest.main()
