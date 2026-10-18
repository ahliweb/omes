"""tests/py/health/test_hermes_config.py - unit tests for
lib/omes/py/health/hermes_config.py (issue #270), the shared allowlisted
`hermes config get <key> --json` reader.

The subprocess boundary (`hermes_config._run`) is patched for the mapping
tests, so argv and the value/absent/unknown classification are asserted
without Hermes. One class drives the real tests/shims/hermes through PATH.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
HEALTH_DIR = os.path.join(ROOT, "lib", "omes", "py", "health")
SHIM_DIR = os.path.join(ROOT, "tests", "shims")
if HEALTH_DIR not in sys.path:
    sys.path.insert(0, HEALTH_DIR)

import hermes_config  # noqa: E402

KEY = "delegation.max_spawn_depth"


def _run_returns(rc=0, out="", err=""):
    return mock.patch.object(hermes_config, "_run", return_value=(rc, out, err))


def _hermes_on_path():
    return mock.patch.object(hermes_config.shutil, "which", return_value="/x/hermes")


class TestStateMapping(unittest.TestCase):
    def test_value_for_rc0_and_valid_json(self):
        with _run_returns(0, "1\n"), _hermes_on_path():
            read = hermes_config.config_get_json(KEY)
        self.assertEqual((read.state, read.value, read.reason), ("value", 1, None))
        self.assertTrue(read.is_value)

    def test_falsy_json_values_are_still_values(self):
        for text, expected in (("false\n", False), ("0\n", 0), ('""\n', ""), ("null\n", None)):
            with _run_returns(0, text), _hermes_on_path():
                read = hermes_config.config_get_json(KEY)
            self.assertEqual(read.state, "value", text)
            self.assertEqual(read.value, expected, text)

    def test_absent_for_rc1_with_upstream_message(self):
        with _run_returns(1, "", "Config key not set: delegation.max_spawn_depth\n"), _hermes_on_path():
            read = hermes_config.config_get_json(KEY)
        self.assertEqual((read.state, read.value, read.reason), ("absent", None, None))
        self.assertFalse(read.is_value)

    def test_rc1_with_other_stderr_is_unknown_not_absent(self):
        with _run_returns(1, "", "Error: could not load config\n"), _hermes_on_path():
            read = hermes_config.config_get_json(KEY)
        self.assertEqual((read.state, read.reason), ("unknown", "nonzero_exit"))

    def test_absent_message_with_rc_other_than_1_is_unknown(self):
        with _run_returns(2, "", "Config key not set: x\n"), _hermes_on_path():
            read = hermes_config.config_get_json(KEY)
        self.assertEqual((read.state, read.reason), ("unknown", "nonzero_exit"))

    def test_older_hermes_rejecting_json_flag(self):
        with _run_returns(2, "", "hermes config get: error: unrecognized arguments: --json\n"), _hermes_on_path():
            read = hermes_config.config_get_json(KEY)
        self.assertEqual((read.state, read.reason), ("unknown", "unsupported_flag"))

    def test_timeout(self):
        with _run_returns(-2, "", "timed out"), _hermes_on_path():
            read = hermes_config.config_get_json(KEY)
        self.assertEqual((read.state, read.reason), ("unknown", "timeout"))

    def test_exec_error(self):
        with _run_returns(-1, "", "boom"), _hermes_on_path():
            read = hermes_config.config_get_json(KEY)
        self.assertEqual((read.state, read.reason), ("unknown", "exec_error"))

    def test_missing_binary_never_starts_a_subprocess(self):
        with mock.patch.object(hermes_config.shutil, "which", return_value=None), \
                mock.patch.object(hermes_config, "_run") as run:
            read = hermes_config.config_get_json(KEY)
        run.assert_not_called()
        self.assertEqual((read.state, read.reason), ("unknown", "binary_missing"))

    def test_garbage_json(self):
        for text in ("not json\n", "{", "1 2"):
            with _run_returns(0, text), _hermes_on_path():
                read = hermes_config.config_get_json(KEY)
            self.assertEqual((read.state, read.reason), ("unknown", "json_unparseable"), text)

    def test_empty_output(self):
        with _run_returns(0, "  \n"), _hermes_on_path():
            read = hermes_config.config_get_json(KEY)
        self.assertEqual((read.state, read.reason), ("unknown", "empty_output"))

    def test_oversized_output_is_unknown(self):
        with _run_returns(0, "1" * (hermes_config._MAX_OUTPUT_BYTES + 1)), _hermes_on_path():
            read = hermes_config.config_get_json(KEY)
        self.assertEqual((read.state, read.reason), ("unknown", "output_too_large"))

    def test_every_unknown_reason_is_in_the_bounded_vocabulary(self):
        cases = [(-2, "", ""), (-1, "", ""), (1, "", "x"), (2, "", "unrecognized arguments"), (0, "", ""), (0, "{", "")]
        for rc, out, err in cases:
            with _run_returns(rc, out, err), _hermes_on_path():
                read = hermes_config.config_get_json(KEY)
            self.assertEqual(read.state, "unknown")
            self.assertIn(read.reason, hermes_config.UNKNOWN_REASONS)


class TestArgvAndEnvironment(unittest.TestCase):
    def test_argv_is_fixed_list_without_raw_or_shell(self):
        with _run_returns(0, "1\n") as run, _hermes_on_path():
            hermes_config.config_get_json(KEY, timeout=3.5)
        argv = run.call_args.args[0]
        self.assertEqual(argv, ["hermes", "config", "get", KEY, "--json"])
        self.assertNotIn("--raw", argv)
        self.assertEqual(run.call_args.args[1], 3.5)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_build_argv_never_contains_raw_for_any_allowlisted_key(self):
        for key in hermes_config.ALLOWED_KEYS:
            self.assertNotIn("--raw", hermes_config.build_argv(key))

    def test_hermes_home_selects_profile_via_env_only(self):
        with _run_returns(0, "1\n") as run, _hermes_on_path():
            hermes_config.config_get_json(KEY, hermes_home="/srv/profile-a")
        self.assertEqual(run.call_args.kwargs["env"]["HERMES_HOME"], "/srv/profile-a")
        self.assertEqual(run.call_args.args[0], ["hermes", "config", "get", KEY, "--json"])

    def test_no_hermes_home_inherits_environment(self):
        with _run_returns(0, "1\n") as run, _hermes_on_path():
            hermes_config.config_get_json(KEY)
        self.assertIsNone(run.call_args.kwargs["env"])


class TestAllowlist(unittest.TestCase):
    def test_non_allowlisted_key_rejected_without_subprocess(self):
        with mock.patch.object(hermes_config, "_run") as run:
            read = hermes_config.config_get_json("display.skin")
        run.assert_not_called()
        self.assertEqual((read.state, read.reason), ("unknown", "key_not_allowlisted"))

    def test_secret_looking_keys_rejected_without_subprocess(self):
        for key in (
            "delegation.api_key", "delegation.base_url", "providers.openai.api_key", "gateway.telegram.bot_token",
            "model.password", "auth.secret", "delegation.bearer", "x.private_key", "delegation.credential_file",
        ):
            with mock.patch.object(hermes_config, "_run") as run:
                read = hermes_config.config_get_json(key)
            run.assert_not_called()
            self.assertEqual(read.state, "unknown", key)
            self.assertIn(read.reason, ("key_secret_pattern", "key_not_allowlisted"), key)
        self.assertEqual(hermes_config.config_get_json("delegation.api_key").reason, "key_secret_pattern")
        self.assertEqual(hermes_config.config_get_json("delegation.base_url").reason, "key_secret_pattern")

    def test_malformed_keys_rejected(self):
        for key in ("", "delegation.max_spawn_depth --raw", "delegation..x", "../etc/passwd", "a b", None, 5):
            with mock.patch.object(hermes_config, "_run") as run:
                read = hermes_config.config_get_json(key)  # type: ignore[arg-type]
            run.assert_not_called()
            self.assertEqual(read.reason, "key_not_allowlisted")

    def test_no_allowlisted_key_looks_secret(self):
        for key in hermes_config.ALLOWED_KEYS:
            self.assertIsNone(hermes_config.SECRET_KEY_PATTERN.search(key), key)

    def test_allowlist_is_a_frozenset_of_delegation_keys(self):
        self.assertIsInstance(hermes_config.ALLOWED_KEYS, frozenset)
        for key in hermes_config.ALLOWED_KEYS:
            self.assertTrue(key.startswith("delegation."), key)


class TestTimeout(unittest.TestCase):
    def test_default_and_override(self):
        with mock.patch.dict(os.environ, {"OMES_HEALTH_TIMEOUT": ""}):
            self.assertEqual(hermes_config.default_timeout(), 10.0)
        with mock.patch.dict(os.environ, {"OMES_HEALTH_TIMEOUT": "3"}):
            self.assertEqual(hermes_config.default_timeout(), 3.0)

    def test_invalid_values_fall_back_and_large_values_are_clamped(self):
        for raw, expected in (("abc", 10.0), ("0", 10.0), ("-5", 10.0), ("nan", 10.0), ("9999", 120.0)):
            with mock.patch.dict(os.environ, {"OMES_HEALTH_TIMEOUT": raw}):
                self.assertEqual(hermes_config.default_timeout(), expected, raw)

    def test_default_timeout_is_passed_to_run(self):
        with mock.patch.dict(os.environ, {"OMES_HEALTH_TIMEOUT": "4"}), _run_returns(0, "1\n") as run, _hermes_on_path():
            hermes_config.config_get_json(KEY)
        self.assertEqual(run.call_args.args[1], 4.0)


class TestAgainstShim(unittest.TestCase):
    """Real subprocess path through tests/shims/hermes (named `hermes`, on PATH)."""

    _SHIM_VARS = (
        "SHIM_HERMES_CONFIG_GET_SUPPORTED",
        "SHIM_HERMES_CONFIG_GET_JSON_UNSUPPORTED",
        "SHIM_HERMES_CONFIG_GET_DELEGATION_MAX_SPAWN_DEPTH",
    )

    def _env(self, **extra):
        env = {k: v for k, v in os.environ.items() if k not in self._SHIM_VARS}
        env["PATH"] = SHIM_DIR + os.pathsep + os.environ.get("PATH", "")
        env.update(extra)
        return mock.patch.dict(os.environ, env, clear=True)

    def test_value_and_absent(self):
        with self._env(SHIM_HERMES_CONFIG_GET_SUPPORTED="1", SHIM_HERMES_CONFIG_GET_DELEGATION_MAX_SPAWN_DEPTH="2"):
            self.assertEqual(hermes_config.config_get_json(KEY).value, 2)
            self.assertEqual(hermes_config.config_get_json("delegation.max_iterations").state, "absent")

    def test_json_flag_rejected_by_older_hermes(self):
        with self._env(SHIM_HERMES_CONFIG_GET_JSON_UNSUPPORTED="1", SHIM_HERMES_CONFIG_GET_DELEGATION_MAX_SPAWN_DEPTH="2"):
            read = hermes_config.config_get_json(KEY)
        self.assertEqual((read.state, read.reason), ("unknown", "unsupported_flag"))

    def test_hermes_without_config_get_is_unknown(self):
        with self._env():
            read = hermes_config.config_get_json(KEY)
        self.assertEqual((read.state, read.reason), ("unknown", "nonzero_exit"))


if __name__ == "__main__":
    unittest.main()
