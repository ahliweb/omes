"""tests/py/health/test_acp_posture.py - unit tests for
lib/omes/py/health/acp_posture.py (issue #273).

The shared reader `hermes_config.config_get_json` and the probe runner are
patched, so findings, validation and status are asserted without Hermes. The
main invariants: `unknown` evidence is never `ok`; the `hermes acp --version`
probe is installability only and is never parsed as exposure; live session
exposure is always reported as unknown.
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

import acp_posture as posture  # noqa: E402
import hermes_config  # noqa: E402

ACP = "platform_toolsets.acp"
DISABLED = "agent.disabled_toolsets"


def _read(key, spec):
    """spec: a JSON value => value; "ABSENT" => absent; ("UNKNOWN", reason) => unknown."""
    if spec == "ABSENT":
        return hermes_config.ConfigRead(key=key, state="absent")
    if isinstance(spec, tuple) and spec and spec[0] == "UNKNOWN":
        return hermes_config.ConfigRead(key=key, state="unknown", reason=spec[1])
    return hermes_config.ConfigRead(key=key, state="value", value=spec)


def collect_with(acp="ABSENT", disabled="ABSENT", probe_rc=1):
    config = {ACP: acp, DISABLED: disabled}

    def fake(key, *, hermes_home=None, timeout=None):
        return _read(key, config[key])

    with mock.patch.object(posture.hermes_config, "config_get_json", side_effect=fake) as reader, \
            mock.patch.object(posture, "_run", return_value=(probe_rc, "", "")) as runner:
        result = posture.collect()
    return result, reader, runner


def finding_ids(result):
    return [f["id"] for f in result["findings"]]


class TestFindings(unittest.TestCase):
    def test_absent_acp_toolsets_warns_that_the_curated_default_includes_execution(self):
        result, _, _ = collect_with(acp="ABSENT")
        self.assertEqual(result["status"], "warn")
        self.assertEqual(result["scope"], "configured")
        self.assertIn("acp.default_toolset_includes_execution", finding_ids(result))
        finding = next(f for f in result["findings"] if f["id"] == "acp.default_toolset_includes_execution")
        self.assertEqual(finding["severity"], "warn")
        self.assertEqual(finding["value"], ["code_execution", "terminal"])
        self.assertNotIn("acp.toolset_restricted", finding_ids(result))

    def test_explicit_restricted_list_is_ok_with_info_finding(self):
        for restricted in (["web", "file"], []):
            result, _, _ = collect_with(acp=restricted)
            self.assertEqual(result["status"], "ok", restricted)
            finding = next(f for f in result["findings"] if f["id"] == "acp.toolset_restricted")
            self.assertEqual(finding["severity"], "info")
            self.assertIn("plugin toolsets are still added", finding["message"])
            self.assertNotIn("acp.default_toolset_includes_execution", finding_ids(result))

    def test_list_with_execution_toolsets_warns(self):
        for listed, expected in (
            (["terminal"], ["terminal"]),
            (["web", "execute_code"], ["code_execution"]),
            (["Code-Execution"], ["code_execution"]),
            (["hermes-acp"], ["code_execution", "terminal"]),
        ):
            result, _, _ = collect_with(acp=listed)
            self.assertEqual(result["status"], "warn", listed)
            finding = next(f for f in result["findings"] if f["id"] == "acp.default_toolset_includes_execution")
            self.assertEqual(finding["value"], expected, listed)

    def test_disabled_toolsets_covering_execution_clears_the_warning(self):
        result, _, _ = collect_with(acp="ABSENT", disabled=["terminal", "execute_code"])
        self.assertEqual(result["status"], "ok")
        self.assertIn("acp.toolset_restricted", finding_ids(result))

    def test_disabled_toolsets_covering_only_terminal_still_warns_for_code_execution(self):
        result, _, _ = collect_with(acp="ABSENT", disabled=["terminal"])
        self.assertEqual(result["status"], "warn")
        finding = next(f for f in result["findings"] if f["id"] == "acp.default_toolset_includes_execution")
        self.assertEqual(finding["value"], ["code_execution"])

    def test_unrelated_disabled_toolsets_do_not_clear_the_warning(self):
        result, _, _ = collect_with(acp=["terminal"], disabled=["browser"])
        self.assertEqual(result["status"], "warn")

    def test_exposure_and_outbound_info_findings_are_always_present(self):
        for acp in ("ABSENT", [], ["terminal"], ("UNKNOWN", "timeout")):
            result, _, _ = collect_with(acp=acp)
            ids = finding_ids(result)
            self.assertIn("acp.session_exposure_unobservable", ids, acp)
            self.assertIn("acp.outbound_not_supported_upstream", ids, acp)
            outbound = next(f for f in result["findings"] if f["id"] == "acp.outbound_not_supported_upstream")
            self.assertIn("hermes-agent/issues/5257", outbound["message"])
            self.assertEqual(outbound["severity"], "info")
            self.assertEqual(result["session_exposure"], "unknown")

    def test_exposure_finding_says_it_is_not_observable(self):
        result, _, _ = collect_with(acp=[])
        finding = next(f for f in result["findings"] if f["id"] == "acp.session_exposure_unobservable")
        self.assertIn("not observable", finding["message"])


class TestUnknownIsNeverOk(unittest.TestCase):
    def test_each_key_unknown_makes_status_unknown_even_if_the_other_is_restricted(self):
        result, _, _ = collect_with(acp=("UNKNOWN", "timeout"), disabled=["terminal"])
        self.assertEqual(result["status"], "unknown")
        self.assertIn("acp.key_timeout", result["reason_codes"])
        result, _, _ = collect_with(acp=[], disabled=("UNKNOWN", "nonzero_exit"))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("acp.key_nonzero_exit", result["reason_codes"])

    def test_unknown_stays_unknown_when_a_warning_would_otherwise_apply(self):
        result, _, _ = collect_with(acp=["terminal"], disabled=("UNKNOWN", "timeout"))
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("acp.default_toolset_includes_execution", finding_ids(result))

    def test_no_execution_claim_is_made_without_evidence(self):
        result, _, _ = collect_with(acp=("UNKNOWN", "binary_missing"), disabled=("UNKNOWN", "binary_missing"))
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("acp.toolset_restricted", finding_ids(result))

    def test_malformed_values_are_unknown_and_not_echoed(self):
        bad = ("terminal", 5, True, None, {"a": 1}, [1], [["terminal"]], ["bad name"], ["x" * 65], ["a\nb"], ["t"] * 65)
        for value in bad:
            for key, kwargs in ((ACP, {"acp": value}), (DISABLED, {"disabled": value})):
                result, _, _ = collect_with(**kwargs)
                self.assertEqual(result["status"], "unknown", (key, value))
                self.assertEqual(result["keys"][key]["state"], "unknown", (key, value))
                self.assertIn(result["keys"][key]["reason"], ("invalid_type", "out_of_range"))
                self.assertNotIn("value", result["keys"][key])

    def test_evaluate_with_no_evidence_is_unknown(self):
        self.assertEqual(posture.evaluate({})["status"], "unknown")

    def test_hermes_missing_through_the_real_reader_is_unknown(self):
        with mock.patch.object(hermes_config.shutil, "which", return_value=None):
            result = posture.collect()
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(all(e["reason"] == "binary_missing" for e in result["keys"].values()))
        self.assertEqual(result["installability"]["state"], "not_installed_or_unknown")


class TestProbe(unittest.TestCase):
    def test_probe_argv_is_fixed_and_bounded_and_has_no_check_or_raw(self):
        _, _, runner = collect_with()
        self.assertEqual(runner.call_count, 1)
        argv = runner.call_args.args[0]
        self.assertEqual(argv, ["hermes", "acp", "--version"])
        self.assertNotIn("--check", argv)
        self.assertNotIn("--raw", argv)
        self.assertIsInstance(runner.call_args.args[1], float)

    def test_probe_exit_zero_is_installed_and_other_exits_are_not_installed_or_unknown(self):
        for rc, expected in ((0, "installed"), (1, "not_installed_or_unknown"), (2, "not_installed_or_unknown"),
                             (-1, "not_installed_or_unknown"), (-2, "not_installed_or_unknown")):
            result, _, _ = collect_with(acp=[], probe_rc=rc)
            self.assertEqual(result["installability"]["state"], expected, rc)
            self.assertIn("installability only", result["installability"]["meaning"])

    def test_probe_output_is_never_parsed_as_exposure(self):
        # Output that reads like "ACP is running/exposed" must not change anything.
        with mock.patch.object(posture, "_run", return_value=(0, "ACP server listening on 0.0.0.0:8000 exposed", "")):
            with mock.patch.object(posture.hermes_config, "config_get_json",
                                   side_effect=lambda key, **kw: _read(key, [])):
                result = posture.collect()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["session_exposure"], "unknown")
        self.assertEqual(result["installability"]["state"], "installed")
        text = json.dumps(result)
        self.assertNotIn("0.0.0.0", text)
        self.assertNotIn("listening", text)

    def test_probe_result_does_not_change_status(self):
        for rc in (0, 1):
            self.assertEqual(collect_with(acp="ABSENT", probe_rc=rc)[0]["status"], "warn")
            self.assertEqual(collect_with(acp=[], probe_rc=rc)[0]["status"], "ok")

    def test_profile_home_is_passed_to_the_probe_environment(self):
        with mock.patch.object(posture, "_run", return_value=(0, "", "")) as runner:
            posture.probe_installability("/srv/p", 2.0)
        self.assertEqual(runner.call_args.kwargs["env"]["HERMES_HOME"], "/srv/p")
        self.assertEqual(runner.call_args.args[1], 2.0)
        with mock.patch.object(posture, "_run", return_value=(0, "", "")) as runner:
            posture.probe_installability(None, 2.0)
        self.assertIsNone(runner.call_args.kwargs["env"])


class TestCollect(unittest.TestCase):
    def test_reads_only_the_two_allowlisted_keys_and_never_raw(self):
        _, reader, _ = collect_with()
        requested = {call.args[0] for call in reader.call_args_list}
        self.assertEqual(requested, {ACP, DISABLED})
        self.assertLessEqual(requested, hermes_config.ALLOWED_KEYS)
        for key in requested:
            self.assertNotIn("--raw", hermes_config.build_argv(key))

    def test_keys_are_allowlisted_and_not_secret_shaped(self):
        for key in (ACP, DISABLED):
            self.assertIn(key, hermes_config.ALLOWED_KEYS)
            self.assertIsNone(hermes_config.SECRET_KEY_PATTERN.search(key))

    def test_profile_home_and_timeout_are_forwarded_to_the_shared_reader(self):
        with mock.patch.object(posture.hermes_config, "config_get_json",
                               side_effect=lambda key, **kw: _read(key, [])) as m, \
                mock.patch.object(posture, "_run", return_value=(1, "", "")):
            result = posture.collect(hermes_home="/srv/p", timeout=2.0)
        for call in m.call_args_list:
            self.assertEqual(call.kwargs, {"hermes_home": "/srv/p", "timeout": 2.0})
        self.assertTrue(result["profile_home_override"])
        self.assertNotIn("/srv", json.dumps(result))

    def test_probe_can_be_skipped(self):
        with mock.patch.object(posture.hermes_config, "config_get_json",
                               side_effect=lambda key, **kw: _read(key, [])), \
                mock.patch.object(posture, "_run") as runner:
            result = posture.collect(probe=False)
        runner.assert_not_called()
        self.assertEqual(result["installability"], {"state": "not_probed"})

    def test_output_shape_and_scope_note(self):
        result, _, _ = collect_with(acp=["web"])
        self.assertEqual(result["check"], "acp_posture")
        self.assertEqual(result["scope"], "configured")
        self.assertIn("not observable", result["scope_note"])
        self.assertEqual(result["keys"][ACP], {"state": "value", "value": ["web"]})
        self.assertEqual(result["keys"][DISABLED], {"state": "absent"})
        json.dumps(result)

    def test_exit_code_convention(self):
        for status, code in (("ok", 0), ("warn", 0), ("unknown", 7)):
            with mock.patch.object(posture, "run", return_value={"status": status}), mock.patch("builtins.print"):
                self.assertEqual(posture.main([]), code)


if __name__ == "__main__":
    unittest.main()
