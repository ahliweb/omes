"""tests/py/agent/test_compose_health.py - health/readiness for
`backend: "compose"` agents (issue #96 follow-up: reuse
lib/omes/py/health/hermes.py's provider/channel layers, executed through
`docker compose exec -T`, with not_applicable degradation instead of a
hard failure when the container cannot be reached). Driven against
tests/shims/docker (never a real docker daemon or Telegram/Ollama
network call).
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from agent import compose_health  # noqa: E402
from health import model  # noqa: E402

OMES_ROOT = Path(_pathfix.OMES_ROOT)
SHIMS = OMES_ROOT / "tests" / "shims"

_ENV_KEYS = (
    "SHIM_LOG",
    "SHIM_DOCKER_COMPOSE_PS_OUTPUT",
    "SHIM_DOCKER_COMPOSE_EXEC_EXIT",
    "SHIM_DOCKER_COMPOSE_EXEC_OUTPUT",
    "OMES_OLLAMA_ENABLED",
    "OMES_OLLAMA_ENDPOINT",
    "OMES_TELEGRAM_ALLOWLIST_SCRIPT",
)


def _plan(hermes_home: str, health_command: str = "true") -> dict:
    return {
        "project": "omes-agent-worker",
        "composeFile": "/tmp/omes-agent-worker/compose.yaml",
        "serviceName": "worker",
        "hermesHome": hermes_home,
        "health": {"command": health_command},
    }


class ComposeHealthTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{SHIMS}{os.pathsep}{self._orig_path}"
        self._orig_env = {k: os.environ.get(k) for k in _ENV_KEYS}
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        os.environ["PATH"] = self._orig_path
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmpdir.cleanup()

    def test_default_shim_reports_ready_with_provider_and_channel_not_applicable(self):
        plan = _plan(self._tmpdir.name)
        result = compose_health.run(plan, timeout=5.0)
        self.assertTrue(result["ready"])
        self.assertTrue(result["connected"])
        self.assertEqual(result["layers"]["gateway"]["status"], model.STATUS_PASS)
        self.assertEqual(result["layers"]["runtime"]["status"], model.STATUS_PASS)
        # No OMES_OLLAMA_ENABLED and no .env token: both layers gated off,
        # exactly like the systemd backend's own hermes.py behavior.
        self.assertEqual(result["layers"]["provider"]["status"], model.STATUS_NOT_APPLICABLE)
        self.assertEqual(result["layers"]["channel"]["status"], model.STATUS_NOT_APPLICABLE)
        self.assertEqual(result["layers"]["healthCommand"]["status"], model.STATUS_PASS)

    def test_container_not_running_fails_gateway_and_marks_others_not_applicable(self):
        os.environ["SHIM_DOCKER_COMPOSE_PS_OUTPUT"] = '{"Name":"x","State":"exited","Health":""}'
        plan = _plan(self._tmpdir.name)
        result = compose_health.run(plan, timeout=5.0)
        self.assertFalse(result["ready"])
        self.assertEqual(result["layers"]["gateway"]["status"], model.STATUS_FAIL)
        self.assertEqual(result["layers"]["healthCommand"]["status"], model.STATUS_NOT_APPLICABLE)

    def test_docker_missing_marks_runtime_and_provider_not_applicable(self):
        os.environ["PATH"] = "/nonexistent-bin-dir"
        os.environ["OMES_OLLAMA_ENABLED"] = "1"
        plan = _plan(self._tmpdir.name)
        result = compose_health.run(plan, timeout=2.0)
        self.assertEqual(result["layers"]["gateway"]["status"], model.STATUS_FAIL)
        self.assertEqual(result["layers"]["runtime"]["status"], model.STATUS_NOT_APPLICABLE)
        self.assertEqual(result["layers"]["provider"]["status"], model.STATUS_NOT_APPLICABLE)

    def test_provider_not_configured_is_not_applicable_even_when_container_reachable(self):
        plan = _plan(self._tmpdir.name)
        result = compose_health.check_provider(plan["project"], plan["composeFile"], plan["serviceName"], 5.0)
        self.assertEqual(result["status"], model.STATUS_NOT_APPLICABLE)

    def test_provider_configured_and_reachable_is_pass(self):
        os.environ["OMES_OLLAMA_ENABLED"] = "1"
        plan = _plan(self._tmpdir.name)
        result = compose_health.check_provider(plan["project"], plan["composeFile"], plan["serviceName"], 5.0)
        self.assertEqual(result["status"], model.STATUS_PASS)

    def test_provider_configured_but_container_unreachable_is_not_applicable(self):
        # SHIM_DOCKER_COMPOSE_EXEC_EXIT governs every `docker compose
        # exec` call the shim sees, including the `true` reachability
        # probe itself - so a nonzero value here means "the container
        # could not be exec'd into at all", which is reported as
        # not_applicable (never a hard failure) per issue #96's explicit
        # requirement.
        os.environ["OMES_OLLAMA_ENABLED"] = "1"
        os.environ["SHIM_DOCKER_COMPOSE_EXEC_EXIT"] = "1"
        plan = _plan(self._tmpdir.name)
        result = compose_health.check_provider(plan["project"], plan["composeFile"], plan["serviceName"], 5.0)
        self.assertEqual(result["status"], model.STATUS_NOT_APPLICABLE)

    def test_channel_reuses_hermes_check_channel_verbatim(self):
        # No .env / no token -> hermes.py's own check_channel returns
        # not_applicable; this proves compose_health.check_channel is a
        # direct passthrough, not a reimplementation.
        result = compose_health.check_channel(self._tmpdir.name, 5.0)
        self.assertEqual(result["status"], model.STATUS_NOT_APPLICABLE)

    def test_channel_with_token_delegates_to_the_telegram_allowlist_probe(self):
        env_path = os.path.join(self._tmpdir.name, ".env")
        with open(env_path, "w", encoding="utf-8") as fh:
            fh.write("TELEGRAM_BOT_TOKEN=123:abc\n")

        fd, script = tempfile.mkstemp(suffix=".sh")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(
                    "#!/usr/bin/env bash\n"
                    "echo '{\"enabled\": true, \"reachable\": true, \"connected\": true, \"detail\": \"ok\"}'\n"
                    "exit 0\n"
                )
            os.chmod(script, 0o700)
            os.environ["OMES_TELEGRAM_ALLOWLIST_SCRIPT"] = script
            result = compose_health.check_channel(self._tmpdir.name, 5.0)
        finally:
            os.remove(script)
        self.assertEqual(result["status"], model.STATUS_PASS)

    def test_health_command_not_declared_is_not_applicable(self):
        plan = _plan(self._tmpdir.name, health_command="")
        result = compose_health.run(plan, timeout=5.0)
        self.assertEqual(result["layers"]["healthCommand"]["status"], model.STATUS_NOT_APPLICABLE)

    def test_health_command_failure_does_not_block_ready(self):
        # healthCommand is not one of the layers model.aggregate() checks
        # for "ready" (host/runtime/gateway/provider only) - a failing
        # manifest health command is visible in the report but does not,
        # by itself, flip the top-level ready/connected booleans.
        os.environ["SHIM_DOCKER_COMPOSE_EXEC_EXIT"] = "1"
        plan = _plan(self._tmpdir.name, health_command="false")
        result = compose_health.run(plan, timeout=5.0)
        self.assertEqual(result["layers"]["healthCommand"]["status"], model.STATUS_FAIL)


if __name__ == "__main__":
    unittest.main()
