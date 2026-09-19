"""tests/py/provenance/test_versions.py - unit tests for
lib/omes/py/provenance/versions.py (issue #83). Mocks subprocess/shutil
entirely - no real binaries required, and asserts the "missing binary ->
null with reason" and "unexpected --version format -> null, never a
false value" contracts.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PROVENANCE_DIR = os.path.join(ROOT, "lib", "omes", "py", "provenance")

spec = importlib.util.spec_from_file_location("omes_provenance_versions", os.path.join(PROVENANCE_DIR, "versions.py"))
versions = importlib.util.module_from_spec(spec)
sys.modules["omes_provenance_versions"] = versions
spec.loader.exec_module(versions)  # type: ignore[union-attr]


class TestBinaryVersion(unittest.TestCase):
    def test_missing_binary_is_null_with_reason(self):
        with mock.patch.object(versions.shutil, "which", return_value=None):
            fact = versions._binary_version("nope", ["--version"], "nope --version")
        self.assertIsNone(fact["value"])
        self.assertIn("not found", fact["reason"])

    def test_nonzero_exit_is_null_never_a_false_value(self):
        with mock.patch.object(versions.shutil, "which", return_value="/usr/bin/nope"), mock.patch.object(
            versions, "_run", return_value=(1, "", "boom")
        ):
            fact = versions._binary_version("nope", ["--version"], "nope --version")
        self.assertIsNone(fact["value"])
        self.assertIsNotNone(fact["reason"])

    def test_empty_output_is_null_never_a_false_value(self):
        with mock.patch.object(versions.shutil, "which", return_value="/usr/bin/nope"), mock.patch.object(
            versions, "_run", return_value=(0, "", "")
        ):
            fact = versions._binary_version("nope", ["--version"], "nope --version")
        self.assertIsNone(fact["value"])

    def test_timeout_is_null_with_reason(self):
        with mock.patch.object(versions.shutil, "which", return_value="/usr/bin/nope"), mock.patch.object(
            versions, "_run", return_value=(-2, "", "timed out")
        ):
            fact = versions._binary_version("nope", ["--version"], "nope --version")
        self.assertIsNone(fact["value"])
        self.assertIn("timed out", fact["reason"])

    def test_normal_output_is_first_line(self):
        with mock.patch.object(versions.shutil, "which", return_value="/usr/bin/node"), mock.patch.object(
            versions, "_run", return_value=(0, "v20.11.1\n", "")
        ):
            fact = versions._binary_version("node", ["--version"], "node --version")
        self.assertEqual(fact["value"], "v20.11.1")
        self.assertIsNone(fact["reason"])


class TestProviderConfig(unittest.TestCase):
    def test_missing_hermes_binary_marks_every_key_not_available(self):
        with mock.patch.object(versions.shutil, "which", return_value=None):
            result = versions.collect_provider_config("/home/u/.hermes")
        self.assertEqual(set(result.keys()), set(versions.ALLOWED_HERMES_CONFIG_KEYS))
        for fact in result.values():
            self.assertIsNone(fact["value"])
            self.assertIn("not found", fact["reason"])

    def test_unrecognized_subcommand_marks_not_available(self):
        def fake_which(name):
            return "/usr/bin/hermes" if name == "hermes" else None

        with mock.patch.object(versions.shutil, "which", side_effect=fake_which), mock.patch.object(
            versions, "_run", return_value=(1, "", "unknown command 'get'")
        ):
            result = versions.collect_provider_config("/home/u/.hermes")
        for fact in result.values():
            self.assertIsNone(fact["value"])
            self.assertIn("not_available", fact["reason"])

    def test_supported_key_returns_value(self):
        def fake_which(name):
            return "/usr/bin/hermes" if name == "hermes" else None

        with mock.patch.object(versions.shutil, "which", side_effect=fake_which), mock.patch.object(
            versions, "_run", return_value=(0, "openai\n", "")
        ):
            result = versions.collect_provider_config("/home/u/.hermes")
        for fact in result.values():
            self.assertEqual(fact["value"], "openai")

    def test_never_reads_dot_env(self):
        # collect_provider_config only ever calls `hermes config get <key>`
        # via _run; it must never open/read a filesystem path at all.
        with mock.patch.object(versions.shutil, "which", return_value="/usr/bin/hermes"), mock.patch.object(
            versions, "_run", return_value=(0, "value\n", "")
        ), mock.patch("builtins.open", side_effect=AssertionError("must not open any file")):
            versions.collect_provider_config("/home/u/.hermes")


class TestWarnings(unittest.TestCase):
    def _components(self, **overrides):
        base = {
            "os": {"id": versions._fact("ubuntu", "x", "y"), "version_id": versions._fact("24.04", "x", "y")},
            "python3": versions._fact("Python 3.12.3", "x", "y"),
            "gateway_mode": versions._fact(None, "x", "y", reason="none"),
            "hermes": versions._fact(None, "x", "y", reason="none"),
        }
        base.update(overrides)
        return base

    def test_unsupported_os_warns(self):
        components = self._components(os={"id": versions._fact("fedora", "x", "y"), "version_id": versions._fact("40", "x", "y")})
        warnings = versions.build_warnings(components)
        self.assertTrue(any("unsupported OS" in w for w in warnings))

    def test_supported_os_no_warning(self):
        components = self._components()
        warnings = versions.build_warnings(components)
        self.assertFalse(any("unsupported OS" in w for w in warnings))

    def test_old_python_warns(self):
        components = self._components(python3=versions._fact("Python 3.8.5", "x", "y"))
        warnings = versions.build_warnings(components)
        self.assertTrue(any("older than 3.10" in w for w in warnings))

    def test_gateway_mode_set_but_hermes_missing_warns(self):
        components = self._components(
            gateway_mode=versions._fact("user", "x", "y"),
            hermes=versions._fact(None, "x", "y", reason="binary not found"),
        )
        warnings = versions.build_warnings(components)
        self.assertTrue(any("hermes may be broken" in w for w in warnings))


class TestCollectAndMain(unittest.TestCase):
    def _fake_run_ok(self, cmd, timeout=5.0):
        return 0, f"fake {cmd[0]} output\n", ""

    def test_collect_returns_expected_top_level_shape(self):
        with mock.patch.object(versions.shutil, "which", return_value="/usr/bin/x"), mock.patch.object(
            versions, "_run", side_effect=self._fake_run_ok
        ):
            result = versions.collect(
                {
                    "omes": {"version": "0.1.0", "git_ref": "abc123"},
                    "os": {"id": "ubuntu", "version_id": "24.04", "codename": "noble", "pretty": "Ubuntu 24.04", "kernel": "6.8.0"},
                    "arch": "amd64",
                    "hermes_home": "/home/u/.hermes",
                    "gateway_mode": "user",
                }
            )
        self.assertTrue(result["ok"])
        self.assertIn("components", result)
        self.assertIn("warnings", result)
        for key in ("omes", "os", "arch", "hermes", "gateway_mode", "python3", "node", "browser", "ffmpeg", "docker", "ollama", "provider_config"):
            self.assertIn(key, result["components"])

    def test_main_with_invalid_json_input_exits_nonzero(self):
        stdin = io.StringIO("not json")
        buf = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), redirect_stdout(buf):
            rc = versions.main([])
        self.assertEqual(rc, versions.EXIT_ERROR)
        out = json.loads(buf.getvalue())
        self.assertFalse(out["ok"])

    def test_main_with_empty_stdin_defaults_to_empty_object(self):
        stdin = io.StringIO("")
        buf = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(
            versions.shutil, "which", return_value=None
        ), redirect_stdout(buf):
            rc = versions.main([])
        self.assertEqual(rc, versions.EXIT_OK)
        out = json.loads(buf.getvalue())
        self.assertTrue(out["ok"])

    def test_output_never_contains_environ_dump(self):
        os.environ["OMES_TEST_CANARY_SECRET"] = "must-not-leak"
        try:
            stdin = io.StringIO("{}")
            buf = io.StringIO()
            with mock.patch.object(sys, "stdin", stdin), mock.patch.object(
                versions.shutil, "which", return_value=None
            ), redirect_stdout(buf):
                versions.main([])
            self.assertNotIn("must-not-leak", buf.getvalue())
        finally:
            del os.environ["OMES_TEST_CANARY_SECRET"]


if __name__ == "__main__":
    unittest.main()
