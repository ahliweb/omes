"""tests/py/health/test_hermes.py - unit tests for
lib/omes/py/health/hermes.py and model.py (issue #79). Uses mocks for
subprocess/HTTP boundaries rather than real Hermes/systemd/Telegram.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
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
model = _load("omes_health_model", "model.py")
hermes = _load("omes_health_hermes", "hermes.py")


class TestModelAggregate(unittest.TestCase):
    def test_all_pass_is_ready_and_connected(self):
        layers = {
            "host": model.layer_result(model.STATUS_PASS),
            "runtime": model.layer_result(model.STATUS_PASS),
            "gateway": model.layer_result(model.STATUS_PASS),
            "provider": model.layer_result(model.STATUS_NOT_APPLICABLE),
            "channel": model.layer_result(model.STATUS_NOT_APPLICABLE),
        }
        result = model.build_result(layers)
        self.assertTrue(result["ready"])
        self.assertTrue(result["connected"])

    def test_gateway_failure_is_not_ready(self):
        layers = {
            "host": model.layer_result(model.STATUS_PASS),
            "runtime": model.layer_result(model.STATUS_PASS),
            "gateway": model.layer_result(model.STATUS_FAIL),
        }
        ready, connected = model.aggregate(layers)
        self.assertFalse(ready)
        self.assertTrue(connected)  # channel absent -> not blocking connected

    def test_channel_failure_affects_connected_not_ready(self):
        layers = {
            "host": model.layer_result(model.STATUS_PASS),
            "runtime": model.layer_result(model.STATUS_PASS),
            "gateway": model.layer_result(model.STATUS_PASS),
            "channel": model.layer_result(model.STATUS_FAIL),
        }
        ready, connected = model.aggregate(layers)
        self.assertTrue(ready)
        self.assertFalse(connected)

    def test_configured_provider_failure_blocks_ready(self):
        layers = {
            "host": model.layer_result(model.STATUS_PASS),
            "runtime": model.layer_result(model.STATUS_PASS),
            "gateway": model.layer_result(model.STATUS_PASS),
            "provider": model.layer_result(model.STATUS_FAIL),
        }
        ready, _ = model.aggregate(layers)
        self.assertFalse(ready)


class TestCheckHost(unittest.TestCase):
    def test_no_facts_is_not_applicable(self):
        result = hermes.check_host(None)
        self.assertEqual(result["status"], model.STATUS_NOT_APPLICABLE)

    def test_low_disk_fails(self):
        result = hermes.check_host({"systemd_present": True, "disk_free_mb": 10, "mem_mb": 4096})
        self.assertEqual(result["status"], model.STATUS_FAIL)

    def test_healthy_host_passes(self):
        result = hermes.check_host({"systemd_present": True, "disk_free_mb": 5000, "mem_mb": 4096})
        self.assertEqual(result["status"], model.STATUS_PASS)


class TestCheckRuntime(unittest.TestCase):
    def test_binary_missing_is_service_down(self):
        with mock.patch.object(hermes, "_run", return_value=(-1, "", "hermes not found")):
            result = hermes.check_runtime(2.0)
        self.assertEqual(result["status"], model.STATUS_FAIL)
        self.assertFalse(result["signals"]["enabled"])

    def test_version_timeout(self):
        with mock.patch.object(hermes, "_run", return_value=(-2, "", "timed out")):
            result = hermes.check_runtime(2.0)
        self.assertEqual(result["status"], model.STATUS_FAIL)

    def test_doctor_failure_fails_layer(self):
        def fake_run(cmd, timeout):
            if cmd[:2] == ["hermes", "--version"]:
                return 0, "hermes 1.0.0", ""
            return 1, "", "FAIL: something broke"

        with mock.patch.object(hermes, "_run", side_effect=fake_run):
            result = hermes.check_runtime(2.0)
        self.assertEqual(result["status"], model.STATUS_FAIL)

    def test_healthy_runtime(self):
        def fake_run(cmd, timeout):
            if cmd[:2] == ["hermes", "--version"]:
                return 0, "hermes 1.0.0", ""
            return 0, "All checks passed", ""

        with mock.patch.object(hermes, "_run", side_effect=fake_run):
            result = hermes.check_runtime(2.0)
        self.assertEqual(result["status"], model.STATUS_PASS)


class TestCheckGateway(unittest.TestCase):
    def test_unreachable_gateway(self):
        def fake_run(cmd, timeout):
            if "gateway" in cmd:
                return 1, "", "no such unit"
            return 1, "", "inactive"

        with mock.patch.object(hermes, "_run", side_effect=fake_run):
            result = hermes.check_gateway("user", 2.0)
        self.assertEqual(result["status"], model.STATUS_FAIL)
        self.assertFalse(result["signals"]["reachable"])

    def test_healthy_gateway(self):
        def fake_run(cmd, timeout):
            if cmd[:2] == ["hermes", "gateway"]:
                return 0, "running", ""
            return 0, "", ""

        with mock.patch.object(hermes, "_run", side_effect=fake_run):
            result = hermes.check_gateway("user", 2.0)
        self.assertEqual(result["status"], model.STATUS_PASS)


class TestCheckProvider(unittest.TestCase):
    def test_not_configured(self):
        with mock.patch.dict(os.environ, {"OMES_OLLAMA_ENABLED": "0", "OMES_OLLAMA_MODEL": ""}, clear=False):
            with mock.patch.object(hermes.shutil, "which", return_value=None):
                result = hermes.check_provider(2.0)
        self.assertEqual(result["status"], model.STATUS_NOT_APPLICABLE)

    def test_provider_failure_when_configured(self):
        fake_ollama = mock.MagicMock()
        fake_ollama.run.return_value = {"ready": False, "provider": "ollama-local", "service": {"status": "fail"}, "model": {"status": "fail"}}
        with mock.patch.dict(os.environ, {"OMES_OLLAMA_ENABLED": "1"}, clear=False):
            with mock.patch.dict(sys.modules, {"ollama": fake_ollama}):
                result = hermes.check_provider(2.0)
        self.assertEqual(result["status"], model.STATUS_FAIL)

    def test_provider_pass_when_configured(self):
        fake_ollama = mock.MagicMock()
        fake_ollama.run.return_value = {"ready": True, "provider": "ollama-local", "service": {"status": "pass"}, "model": {"status": "pass"}}
        with mock.patch.dict(os.environ, {"OMES_OLLAMA_ENABLED": "1"}, clear=False):
            with mock.patch.dict(sys.modules, {"ollama": fake_ollama}):
                result = hermes.check_provider(2.0)
        self.assertEqual(result["status"], model.STATUS_PASS)


class TestCheckChannel(unittest.TestCase):
    def _fake_script(self, body: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".sh")
        with os.fdopen(fd, "w") as fh:
            fh.write("#!/usr/bin/env bash\n" + body)
        os.chmod(path, 0o700)
        return path

    def test_no_token_is_not_applicable(self):
        with tempfile.TemporaryDirectory() as home:
            with open(os.path.join(home, ".env"), "w", encoding="utf-8") as fh:
                fh.write("FOO=bar\n")
            result = hermes.check_channel(home, 2.0)
        self.assertEqual(result["status"], model.STATUS_NOT_APPLICABLE)

    def test_stale_channel_state_is_fail(self):
        script = self._fake_script('echo \'{"enabled": true, "reachable": true, "pending_update_count": 500, "connected": false, "detail": "stale"}\'\nexit 1\n')
        try:
            with tempfile.TemporaryDirectory() as home:
                with open(os.path.join(home, ".env"), "w", encoding="utf-8") as fh:
                    fh.write("TELEGRAM_BOT_TOKEN=123:abc\n")
                with mock.patch.dict(os.environ, {"OMES_TELEGRAM_ALLOWLIST_SCRIPT": script}):
                    result = hermes.check_channel(home, 2.0)
            self.assertEqual(result["status"], model.STATUS_FAIL)
        finally:
            os.remove(script)

    def test_connected_channel_is_pass(self):
        script = self._fake_script('echo \'{"enabled": true, "reachable": true, "pending_update_count": 0, "connected": true, "detail": "ok"}\'\nexit 0\n')
        try:
            with tempfile.TemporaryDirectory() as home:
                with open(os.path.join(home, ".env"), "w", encoding="utf-8") as fh:
                    fh.write("TELEGRAM_BOT_TOKEN=123:abc\n")
                with mock.patch.dict(os.environ, {"OMES_TELEGRAM_ALLOWLIST_SCRIPT": script}):
                    result = hermes.check_channel(home, 2.0)
            self.assertEqual(result["status"], model.STATUS_PASS)
        finally:
            os.remove(script)


class TestRunIntegration(unittest.TestCase):
    def test_healthy_end_to_end(self):
        def fake_run(cmd, timeout):
            if cmd[:2] == ["hermes", "--version"]:
                return 0, "hermes 1.0.0", ""
            if cmd[:2] == ["hermes", "gateway"]:
                return 0, "running", ""
            if cmd[:1] == ["hermes"]:
                return 0, "All checks passed", ""
            if "is-enabled" in cmd or "is-active" in cmd:
                return 0, "active", ""
            return 0, "", ""

        with tempfile.TemporaryDirectory() as home:
            with open(os.path.join(home, ".env"), "w", encoding="utf-8") as fh:
                fh.write("FOO=bar\n")
            with mock.patch.object(hermes, "_run", side_effect=fake_run):
                with mock.patch.object(sys, "stdin", mock.MagicMock(isatty=lambda: True)):
                    result = hermes.run(["--target", "agent", "--mode", "user", "--hermes-home", home])
        self.assertTrue(result["ready"])
        self.assertTrue(result["connected"])

    def test_timeout_end_to_end_is_not_ready(self):
        with mock.patch.object(hermes, "_run", return_value=(-2, "", "timed out")):
            with mock.patch.object(sys, "stdin", mock.MagicMock(isatty=lambda: True)):
                result = hermes.run(["--target", "agent"])
        self.assertFalse(result["ready"])


class TestLayerAuthorityAndAttribution(unittest.TestCase):
    def test_authorities_and_sources(self):
        host_res = hermes.check_host({"systemd_present": True, "disk_free_mb": 5000, "mem_mb": 4096})
        self.assertEqual(host_res["authority"], model.AUTHORITY_OMES_HOST)
        self.assertEqual(host_res["source"], "systemd")

        def fake_run(cmd, timeout):
            if cmd[:2] == ["hermes", "--version"]:
                return 0, "hermes 2026.9.14", ""
            if "doctor" in cmd:
                return 0, "doctor ok", ""
            if "gateway" in cmd:
                return 0, "gateway running", ""
            return 0, "active", ""

        with mock.patch.object(hermes, "_run", side_effect=fake_run):
            rt_res = hermes.check_runtime(2.0)
            self.assertEqual(rt_res["authority"], model.AUTHORITY_HERMES)
            self.assertEqual(rt_res["source"], "hermes doctor")

            gw_res = hermes.check_gateway("user", 2.0)
            self.assertEqual(gw_res["authority"], model.AUTHORITY_HERMES)
            self.assertEqual(gw_res["source"], "hermes gateway status")

    def test_channel_compatibility_fallback(self):
        res = hermes.check_channel("/nonexistent-dir", 2.0)
        self.assertEqual(res["status"], model.STATUS_NOT_APPLICABLE)
        self.assertEqual(res["authority"], model.AUTHORITY_EXTERNAL_PROVIDER)


if __name__ == "__main__":
    unittest.main()
