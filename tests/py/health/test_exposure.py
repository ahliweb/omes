"""tests/py/health/test_exposure.py - unit tests for
lib/omes/py/health/exposure.py (issue #80). Mocks subprocess entirely -
no real `ss`/`ufw` required.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
HEALTH_DIR = os.path.join(ROOT, "lib", "omes", "py", "health")

spec = importlib.util.spec_from_file_location("omes_health_exposure", os.path.join(HEALTH_DIR, "exposure.py"))
exposure = importlib.util.module_from_spec(spec)
sys.modules["omes_health_exposure"] = exposure
spec.loader.exec_module(exposure)  # type: ignore[union-attr]

SS_LOOPBACK = 'tcp   LISTEN 0      128    127.0.0.1:11434      0.0.0.0:*     users:(("ollama",pid=111,fd=3))\n'
SS_WILDCARD = 'tcp   LISTEN 0      128    0.0.0.0:8642          0.0.0.0:*     users:(("hermes",pid=222,fd=10))\n'
SS_LAN = 'tcp   LISTEN 0      128    192.168.1.5:9222       0.0.0.0:*     users:(("chrome",pid=333,fd=4))\n'


class TestParsing(unittest.TestCase):
    def test_split_host_port_ipv4(self):
        self.assertEqual(exposure.split_host_port("127.0.0.1:11434"), ("127.0.0.1", 11434))

    def test_split_host_port_ipv6_bracketed(self):
        self.assertEqual(exposure.split_host_port("[::1]:8080"), ("::1", 8080))

    def test_classify_bind(self):
        self.assertEqual(exposure.classify_bind("127.0.0.1"), "loopback")
        self.assertEqual(exposure.classify_bind("0.0.0.0"), "wildcard")
        self.assertEqual(exposure.classify_bind("192.168.1.5"), "lan")

    def test_classify_owner_by_port_and_name(self):
        self.assertEqual(exposure.classify_owner(11434, ""), "ollama")
        self.assertEqual(exposure.classify_owner(9222, "chrome"), "browser-control")
        self.assertEqual(exposure.classify_owner(None, "hermes-gateway"), "hermes-gateway")
        self.assertEqual(exposure.classify_owner(5000, "some-mcp-server"), "mcp-server")
        self.assertEqual(exposure.classify_owner(5000, "unknown"), "other")

    def test_parse_ss_extracts_listeners(self):
        listeners = exposure.parse_ss(SS_LOOPBACK + SS_WILDCARD + SS_LAN)
        self.assertEqual(len(listeners), 3)
        self.assertEqual(listeners[0]["bind_class"], "loopback")
        self.assertEqual(listeners[1]["bind_class"], "wildcard")
        self.assertEqual(listeners[2]["bind_class"], "lan")


class TestFindings(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ.pop("OMES_EXPOSURE_ALLOW", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_loopback_is_never_a_finding(self):
        listeners = exposure.parse_ss(SS_LOOPBACK)
        findings = exposure.build_findings(listeners, set(), {"active": False, "rules": []})
        self.assertEqual(findings, [])

    def test_lan_bind_is_a_finding(self):
        listeners = exposure.parse_ss(SS_LAN)
        findings = exposure.build_findings(listeners, set(), {"active": False, "rules": []})
        self.assertEqual(len(findings), 1)
        self.assertFalse(findings[0]["approved"])
        self.assertEqual(findings[0]["status"], "fail")

    def test_wildcard_bind_is_a_finding(self):
        listeners = exposure.parse_ss(SS_WILDCARD)
        findings = exposure.build_findings(listeners, set(), {"active": True, "rules": [{"to": "8642", "action": "ALLOW"}]})
        self.assertEqual(len(findings), 1)
        self.assertFalse(findings[0]["approved"])
        self.assertTrue(findings[0]["firewall_allows"])

    def test_approved_exposure_via_allowlist_passes(self):
        listeners = exposure.parse_ss(SS_WILDCARD)
        findings = exposure.build_findings(listeners, {"0.0.0.0:8642"}, {"active": True, "rules": []})
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0]["approved"])
        self.assertEqual(findings[0]["status"], "pass")


class TestRunIntegration(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_missing_ss_tool_is_exit_4(self):
        with mock.patch.object(exposure.shutil, "which", return_value=None):
            result = exposure.run([])
        self.assertEqual(result["exit_code"], exposure.EXIT_TOOLS_MISSING)
        self.assertFalse(result["ok"])

    def test_loopback_only_is_ok(self):
        def fake_which(name):
            return "/usr/bin/" + name

        def fake_run(cmd, timeout):
            if cmd[0] == "ss":
                return 0, SS_LOOPBACK, ""
            if cmd[0] == "ufw":
                return 0, "Status: active\n\n22/tcp ALLOW Anywhere\n", ""
            return 1, "", ""

        with mock.patch.object(exposure.shutil, "which", side_effect=fake_which):
            with mock.patch.object(exposure, "_run", side_effect=fake_run):
                result = exposure.run([])
        self.assertEqual(result["exit_code"], exposure.EXIT_OK)
        self.assertTrue(result["ok"])

    def test_lan_exposure_is_exit_7(self):
        def fake_which(name):
            return "/usr/bin/" + name

        def fake_run(cmd, timeout):
            if cmd[0] == "ss":
                return 0, SS_LAN, ""
            if cmd[0] == "ufw":
                return 0, "Status: inactive\n", ""
            return 1, "", ""

        with mock.patch.object(exposure.shutil, "which", side_effect=fake_which):
            with mock.patch.object(exposure, "_run", side_effect=fake_run):
                result = exposure.run([])
        self.assertEqual(result["exit_code"], exposure.EXIT_FINDINGS)
        self.assertFalse(result["ok"])

    def test_approved_exposure_via_env_is_ok(self):
        def fake_which(name):
            return "/usr/bin/" + name

        def fake_run(cmd, timeout):
            if cmd[0] == "ss":
                return 0, SS_WILDCARD, ""
            if cmd[0] == "ufw":
                return 0, "Status: active\n\n8642/tcp ALLOW Anywhere\n", ""
            return 1, "", ""

        with mock.patch.dict(os.environ, {"OMES_EXPOSURE_ALLOW": "0.0.0.0:8642"}):
            with mock.patch.object(exposure.shutil, "which", side_effect=fake_which):
                with mock.patch.object(exposure, "_run", side_effect=fake_run):
                    result = exposure.run([])
        self.assertEqual(result["exit_code"], exposure.EXIT_OK)

    def test_missing_ufw_does_not_block_the_audit(self):
        def fake_which(name):
            if name == "ufw":
                return None
            return "/usr/bin/" + name

        def fake_run(cmd, timeout):
            if cmd[0] == "ss":
                return 0, SS_LOOPBACK, ""
            return -1, "", "not found"

        with mock.patch.object(exposure.shutil, "which", side_effect=fake_which):
            with mock.patch.object(exposure, "_run", side_effect=fake_run):
                result = exposure.run([])
        self.assertEqual(result["exit_code"], exposure.EXIT_OK)
        self.assertFalse(result["firewall"]["available"])


class TestCollectListeners(unittest.TestCase):
    """exposure.collect_listeners() (issue #298): the same one `ss` probe as
    run(), but it says whether the listener list is complete, so a consumer
    that must fail closed can tell "nothing is listening" from "could not
    tell"."""

    def _collect(self, which, run_result):
        with mock.patch.object(exposure.shutil, "which", side_effect=which):
            with mock.patch.object(exposure, "_run", return_value=run_result):
                return exposure.collect_listeners(1.0)

    def test_complete_table_is_collected(self):
        result = self._collect(lambda n: "/usr/bin/" + n, (0, SS_WILDCARD, ""))
        self.assertTrue(result["collected"])
        self.assertFalse(result["tool_missing"])
        self.assertEqual([l["port"] for l in result["listeners"]], [8642])

    def test_empty_but_successful_table_is_collected(self):
        result = self._collect(lambda n: "/usr/bin/" + n, (0, "", ""))
        self.assertEqual((result["collected"], result["listeners"]), (True, []))

    def test_missing_ss_is_not_collected_and_flagged(self):
        result = self._collect(lambda n: None, (0, "", ""))
        self.assertEqual((result["collected"], result["tool_missing"], result["listeners"]), (False, True, []))

    def test_failed_or_timed_out_ss_is_not_collected(self):
        for rc in (1, -1, -2):
            with self.subTest(rc=rc):
                result = self._collect(lambda n: "/usr/bin/" + n, (rc, "", "boom"))
                self.assertEqual((result["collected"], result["tool_missing"], result["listeners"]), (False, False, []))

    def test_partial_output_with_a_nonzero_exit_is_parsed_but_not_complete(self):
        result = self._collect(lambda n: "/usr/bin/" + n, (1, SS_LAN, ""))
        self.assertFalse(result["collected"])
        self.assertEqual([l["port"] for l in result["listeners"]], [9222])

    def test_run_output_shape_and_failure_behaviour_are_unchanged(self):
        """run() keeps its historical fail-open-looking results (exit 0 for an
        unreadable table, exit 4 only for a missing ss) so `omes audit
        exposure` is byte-for-byte what #80 shipped."""

        def which(name):
            return "/usr/bin/" + name

        def failing_ss(cmd, timeout):
            return (1, "", "boom") if cmd[0] == "ss" else (1, "", "")

        with mock.patch.object(exposure.shutil, "which", side_effect=which):
            with mock.patch.object(exposure, "_run", side_effect=failing_ss):
                result = exposure.run([])
        self.assertEqual(set(result), {"listeners", "findings", "firewall", "ok", "exit_code", "detail"})
        self.assertEqual((result["listeners"], result["exit_code"], result["ok"]), ([], exposure.EXIT_OK, True))
        with mock.patch.object(exposure.shutil, "which", return_value=None):
            missing = exposure.run([])
        self.assertEqual(set(missing), {"listeners", "findings", "firewall", "ok", "exit_code", "detail"})
        self.assertEqual(missing["exit_code"], exposure.EXIT_TOOLS_MISSING)


if __name__ == "__main__":
    unittest.main()
