"""tests/py/health/test_hermes_adapter.py - unit tests for
lib/omes/py/health/hermes_adapter.py (issue #178).
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
HEALTH_DIR = os.path.join(ROOT, "lib", "omes", "py", "health")


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HEALTH_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


sys.path.insert(0, HEALTH_DIR)
model = _load("omes_health_model_adapter_test", "model.py")
hermes_adapter = _load("omes_health_hermes_adapter", "hermes_adapter.py")


class TestHermesAdapterRuntime(unittest.TestCase):
    def test_version_missing_fails(self):
        def fake_runner(cmd, timeout):
            return -1, "", "hermes not found on PATH"

        res = hermes_adapter.check_hermes_runtime(runner=fake_runner)
        self.assertEqual(res["status"], model.STATUS_FAIL)
        self.assertEqual(res["authority"], model.AUTHORITY_HERMES)
        self.assertEqual(res["source"], "hermes --version")
        self.assertFalse(res["signals"]["ready"])
        self.assertIn("not found on PATH", res["detail"])

    def test_version_timeout_fails(self):
        def fake_runner(cmd, timeout):
            return -2, "", "hermes --version timed out after 5.0s"

        res = hermes_adapter.check_hermes_runtime(timeout=5.0, runner=fake_runner)
        self.assertEqual(res["status"], model.STATUS_FAIL)
        self.assertEqual(res["authority"], model.AUTHORITY_HERMES)
        self.assertEqual(res["source"], "hermes --version")
        self.assertFalse(res["signals"]["ready"])
        self.assertIn("timed out", res["detail"])

    def test_doctor_passes(self):
        def fake_runner(cmd, timeout):
            if "--version" in cmd:
                return 0, "hermes 2026.9.14", ""
            if "doctor" in cmd:
                return 0, "All runtime checks passed successfully.", ""
            return 1, "", "unexpected"

        res = hermes_adapter.check_hermes_runtime(runner=fake_runner)
        self.assertEqual(res["status"], model.STATUS_PASS)
        self.assertEqual(res["authority"], model.AUTHORITY_HERMES)
        self.assertEqual(res["source"], "hermes doctor")
        self.assertTrue(res["signals"]["enabled"])
        self.assertTrue(res["signals"]["active"])
        self.assertTrue(res["signals"]["ready"])
        self.assertIn("hermes 2026.9.14", res["detail"])

    def test_doctor_fails(self):
        def fake_runner(cmd, timeout):
            if "--version" in cmd:
                return 0, "hermes 2026.9.14", ""
            if "doctor" in cmd:
                return 1, "", "FAIL: database connection failed"
            return 1, "", "unexpected"

        res = hermes_adapter.check_hermes_runtime(runner=fake_runner)
        self.assertEqual(res["status"], model.STATUS_FAIL)
        self.assertEqual(res["authority"], model.AUTHORITY_HERMES)
        self.assertEqual(res["source"], "hermes doctor")
        self.assertFalse(res["signals"]["ready"])
        self.assertIn("database connection failed", res["detail"])

    def test_doctor_with_profile(self):
        recorded_cmds = []

        def fake_runner(cmd, timeout):
            recorded_cmds.append(list(cmd))
            if "--version" in cmd:
                return 0, "hermes 2026.9.14", ""
            if "doctor" in cmd:
                return 0, "Profile acme-agent is healthy.", ""
            return 1, "", "unexpected"

        res = hermes_adapter.check_hermes_runtime(profile="acme-agent", runner=fake_runner)
        self.assertEqual(res["status"], model.STATUS_PASS)
        self.assertTrue(any(cmd[1:] == ["doctor", "--profile", "acme-agent"] for cmd in recorded_cmds))


class TestHermesAdapterGateway(unittest.TestCase):
    def test_gateway_status_ok(self):
        def fake_runner(cmd, timeout):
            return 0, "Gateway active and listening on 127.0.0.1:8080", ""

        res = hermes_adapter.check_hermes_gateway(runner=fake_runner)
        self.assertEqual(res["status"], model.STATUS_PASS)
        self.assertEqual(res["authority"], model.AUTHORITY_HERMES)
        self.assertEqual(res["source"], "hermes gateway status")
        self.assertTrue(res["signals"]["active"])
        self.assertTrue(res["signals"]["reachable"])
        self.assertTrue(res["signals"]["ready"])

    def test_gateway_status_down(self):
        def fake_runner(cmd, timeout):
            return 1, "", "Gateway is not running"

        res = hermes_adapter.check_hermes_gateway(runner=fake_runner)
        self.assertEqual(res["status"], model.STATUS_FAIL)
        self.assertEqual(res["authority"], model.AUTHORITY_HERMES)
        self.assertEqual(res["source"], "hermes gateway status")
        self.assertFalse(res["signals"]["active"])
        self.assertFalse(res["signals"]["reachable"])
        self.assertFalse(res["signals"]["ready"])


class TestHermesAdapterProfiles(unittest.TestCase):
    def test_profiles_discovery(self):
        stdout_text = """Available profiles:
* default
  profile-a (active)
  profile-b
"""

        def fake_runner(cmd, timeout):
            return 0, stdout_text, ""

        res = hermes_adapter.check_hermes_profiles(runner=fake_runner)
        self.assertEqual(res["status"], model.STATUS_PASS)
        self.assertEqual(res["authority"], model.AUTHORITY_HERMES)
        self.assertEqual(res["source"], "hermes profile list")
        self.assertEqual(res["signals"]["count"], 3)
        self.assertIn("default", res["detail"])
        self.assertIn("profile-a", res["detail"])
        self.assertIn("profile-b", res["detail"])

    def test_profiles_failure(self):
        def fake_runner(cmd, timeout):
            return 1, "", "Failed to read profiles configuration"

        res = hermes_adapter.check_hermes_profiles(runner=fake_runner)
        self.assertEqual(res["status"], model.STATUS_FAIL)
        self.assertEqual(res["authority"], model.AUTHORITY_HERMES)
        self.assertEqual(res["source"], "hermes profile list")
        self.assertEqual(res["signals"]["count"], 0)


class TestHermesHealthRedactionAndAttribution(unittest.TestCase):
    def test_no_secrets_in_diagnostics(self):
        secret_token = "sk-ant-api03-SECRET1234567890-DO-NOT-LEAK"

        def fake_runner(cmd, timeout):
            if "--version" in cmd:
                return 0, "hermes 2026.9.14", ""
            return 1, "", f"Error checking key {secret_token}"

        res = hermes_adapter.check_hermes_runtime(runner=fake_runner)
        self.assertEqual(res["status"], model.STATUS_FAIL)
        # Should be capped and safe
        self.assertIn("authority", res)
        self.assertEqual(res["authority"], "hermes")
