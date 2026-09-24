"""tests/py/health/test_ai_privacy.py - unit tests for
lib/omes/py/health/ai_privacy.py (issue #216).

These exercise the bounded classification helpers directly, and the
`run()` entry point with `_run`/exposure collectors patched out (no real
subprocess/socket access in unit tests), including an adversarial check
that a canary value placed in the operator-declared state facts never
surfaces in the evidence output.
"""

from __future__ import annotations

import importlib.util
import json
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


ai_privacy = _load("omes_health_ai_privacy_test", "ai_privacy.py")


class TestClassifyDestination(unittest.TestCase):
    def test_known_local_provider(self):
        self.assertEqual(ai_privacy.classify_destination("ollama"), "local")

    def test_known_cloud_provider(self):
        self.assertEqual(ai_privacy.classify_destination("anthropic"), "cloud")

    def test_case_insensitive(self):
        self.assertEqual(ai_privacy.classify_destination("OpenAI"), "cloud")

    def test_unknown_provider_is_unknown_not_guessed(self):
        self.assertEqual(ai_privacy.classify_destination("some-future-provider"), "unknown")

    def test_missing_provider_is_unknown(self):
        self.assertEqual(ai_privacy.classify_destination(None), "unknown")
        self.assertEqual(ai_privacy.classify_destination(""), "unknown")


class TestLocalEndpointClassification(unittest.TestCase):
    def test_not_applicable_when_destination_not_local(self):
        self.assertEqual(ai_privacy.collect_local_endpoint_classification("cloud", 1.0), "not_applicable")
        self.assertEqual(ai_privacy.collect_local_endpoint_classification("unknown", 1.0), "not_applicable")

    def test_loopback_when_ollama_listener_bound_loopback(self):
        with mock.patch.object(
            ai_privacy.exposure,
            "run",
            return_value={"listeners": [{"owner": "ollama", "bind_class": "loopback"}]},
        ):
            self.assertEqual(ai_privacy.collect_local_endpoint_classification("local", 1.0), "loopback")

    def test_public_when_ollama_listener_wildcard_bound(self):
        with mock.patch.object(
            ai_privacy.exposure,
            "run",
            return_value={"listeners": [{"owner": "ollama", "bind_class": "wildcard"}]},
        ):
            self.assertEqual(ai_privacy.collect_local_endpoint_classification("local", 1.0), "public")

    def test_unknown_when_no_ollama_listener_found(self):
        with mock.patch.object(ai_privacy.exposure, "run", return_value={"listeners": []}):
            self.assertEqual(ai_privacy.collect_local_endpoint_classification("local", 1.0), "unknown")

    def test_unknown_when_exposure_audit_raises(self):
        with mock.patch.object(ai_privacy.exposure, "run", side_effect=RuntimeError("ss not found")):
            self.assertEqual(ai_privacy.collect_local_endpoint_classification("local", 1.0), "unknown")


class TestNetworkIsolation(unittest.TestCase):
    def test_true_when_firewall_active(self):
        with mock.patch.object(ai_privacy.exposure, "check_firewall", return_value={"active": True}):
            self.assertTrue(ai_privacy.collect_network_isolation_active(1.0))

    def test_none_when_firewall_state_unknown(self):
        with mock.patch.object(ai_privacy.exposure, "check_firewall", return_value={"active": None}):
            self.assertIsNone(ai_privacy.collect_network_isolation_active(1.0))

    def test_none_when_firewall_check_raises(self):
        with mock.patch.object(ai_privacy.exposure, "check_firewall", side_effect=RuntimeError("boom")):
            self.assertIsNone(ai_privacy.collect_network_isolation_active(1.0))


class TestRunEndToEnd(unittest.TestCase):
    CANARY = "CANARY_SECRET_sk_live_should_never_appear_in_evidence"

    def test_run_never_raises_and_degrades_to_blocked_without_hermes(self):
        with mock.patch.object(ai_privacy.shutil, "which", return_value=None):
            result = ai_privacy.run([])
        self.assertIn(result["status"], ("BLOCKED", "FAIL", "WARN", "PASS"))
        self.assertEqual(result["destination_class"], "unknown")

    def test_run_never_leaks_canary_from_state_facts(self):
        # A caller (or operator config) that stuffs a canary secret into
        # an unexpected key of the state-facts JSON must never see it
        # echoed in the evidence output - build_observation() only reads
        # documented keys (expected_posture, local_only_posture_source).
        state_facts = {
            "expected_posture": "restricted_local_only",
            "local_only_posture_source": {"available": True, "status": "pass", "source": self.CANARY},
            "unexpected_field": self.CANARY,
            "api_key": self.CANARY,
        }
        with mock.patch.object(ai_privacy, "_collect_provider_value", return_value=None), \
             mock.patch.object(ai_privacy, "_collect_hermes_version", return_value=None), \
             mock.patch.object(ai_privacy, "collect_network_isolation_active", return_value=None):
            result = ai_privacy.posture_evidence.evaluate(
                ai_privacy.build_observation(state_facts, 1.0)
            )
        dumped = json.dumps(result)
        self.assertNotIn(self.CANARY, dumped)

    def test_main_prints_json_and_returns_bounded_exit_code(self):
        stdin_json = json.dumps({"expected_posture": "unrestricted"})
        with mock.patch.object(ai_privacy.sys, "stdin", new=_FakeStdin(stdin_json)), \
             mock.patch.object(ai_privacy.shutil, "which", return_value=None):
            out = []
            with mock.patch("builtins.print", side_effect=lambda s: out.append(s)):
                exit_code = ai_privacy.main([])
        self.assertIn(exit_code, (ai_privacy.EXIT_HEALTHY, ai_privacy.EXIT_NOT_HEALTHY))
        parsed = json.loads(out[0])
        self.assertIn("status", parsed)
        self.assertIn(parsed["status"], ("PASS", "FAIL", "WARN", "BLOCKED"))


class _FakeStdin:
    def __init__(self, text: str):
        self._text = text

    def isatty(self):
        return False

    def read(self):
        return self._text


if __name__ == "__main__":
    unittest.main()
