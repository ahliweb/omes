"""tests/py/agent/test_compose_preflight.py - rootless Docker preflight
checks for the Compose isolation backend (issue #96). Driven against
tests/shims/docker (never a real docker daemon)."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from agent import compose_preflight  # noqa: E402

OMES_ROOT = Path(_pathfix.OMES_ROOT)
SHIMS = OMES_ROOT / "tests" / "shims"


class ComposePreflightTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{SHIMS}{os.pathsep}{self._orig_path}"
        self._orig_env = {
            k: os.environ.get(k)
            for k in (
                "SHIM_DOCKER_CONTEXT",
                "SHIM_DOCKER_ENDPOINT",
                "SHIM_DOCKER_SECURITY_OPTIONS",
                "OMES_TEST",
                "OMES_FAKE_GROUPS",
            )
        }

    def tearDown(self):
        os.environ["PATH"] = self._orig_path
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_rootless_daemon_passes(self):
        os.environ["SHIM_DOCKER_CONTEXT"] = "rootless"
        os.environ["SHIM_DOCKER_ENDPOINT"] = "unix:///run/user/1000/docker.sock"
        os.environ["SHIM_DOCKER_SECURITY_OPTIONS"] = "[name=seccomp,profile=default name=rootless]"
        os.environ["OMES_TEST"] = "1"
        os.environ["OMES_FAKE_GROUPS"] = "users"
        result = compose_preflight.check()
        self.assertTrue(result["ok"], result["errors"])

    def test_rootful_daemon_fails(self):
        os.environ["SHIM_DOCKER_CONTEXT"] = "default"
        os.environ["SHIM_DOCKER_ENDPOINT"] = "unix:///var/run/docker.sock"
        os.environ["SHIM_DOCKER_SECURITY_OPTIONS"] = "[name=seccomp,profile=default]"
        os.environ["OMES_TEST"] = "1"
        os.environ["OMES_FAKE_GROUPS"] = "users"
        result = compose_preflight.check()
        self.assertFalse(result["ok"])
        self.assertTrue(any("rootless" in e for e in result["errors"]))

    def test_rootful_socket_path_fails_even_if_security_options_lie(self):
        os.environ["SHIM_DOCKER_CONTEXT"] = "default"
        os.environ["SHIM_DOCKER_ENDPOINT"] = "unix:///var/run/docker.sock"
        os.environ["SHIM_DOCKER_SECURITY_OPTIONS"] = "[name=seccomp,profile=default name=rootless]"
        os.environ["OMES_TEST"] = "1"
        os.environ["OMES_FAKE_GROUPS"] = "users"
        result = compose_preflight.check()
        self.assertFalse(result["ok"])
        self.assertTrue(any("rootful socket path" in e for e in result["errors"]))

    def test_docker_group_only_access_on_rootful_daemon_fails(self):
        os.environ["SHIM_DOCKER_CONTEXT"] = "default"
        os.environ["SHIM_DOCKER_ENDPOINT"] = "unix:///run/user/1000/docker.sock"
        os.environ["SHIM_DOCKER_SECURITY_OPTIONS"] = "[name=seccomp,profile=default]"
        os.environ["OMES_TEST"] = "1"
        os.environ["OMES_FAKE_GROUPS"] = "users,docker"
        result = compose_preflight.check()
        self.assertFalse(result["ok"])
        self.assertTrue(any("docker" in e and "group" in e for e in result["errors"]))

    def test_docker_group_member_on_rootless_daemon_is_fine(self):
        os.environ["SHIM_DOCKER_CONTEXT"] = "rootless"
        os.environ["SHIM_DOCKER_ENDPOINT"] = "unix:///run/user/1000/docker.sock"
        os.environ["SHIM_DOCKER_SECURITY_OPTIONS"] = "[name=seccomp,profile=default name=rootless]"
        os.environ["OMES_TEST"] = "1"
        os.environ["OMES_FAKE_GROUPS"] = "users,docker"
        result = compose_preflight.check()
        self.assertTrue(result["ok"], result["errors"])

    def test_require_returns_none_on_success(self):
        os.environ["SHIM_DOCKER_CONTEXT"] = "rootless"
        os.environ["SHIM_DOCKER_ENDPOINT"] = "unix:///run/user/1000/docker.sock"
        os.environ["SHIM_DOCKER_SECURITY_OPTIONS"] = "[name=seccomp,profile=default name=rootless]"
        os.environ["OMES_TEST"] = "1"
        os.environ["OMES_FAKE_GROUPS"] = ""
        self.assertIsNone(compose_preflight.require())

    def test_require_returns_message_on_failure(self):
        os.environ["SHIM_DOCKER_CONTEXT"] = "default"
        os.environ["SHIM_DOCKER_ENDPOINT"] = "unix:///var/run/docker.sock"
        os.environ["SHIM_DOCKER_SECURITY_OPTIONS"] = "[name=seccomp,profile=default]"
        os.environ["OMES_TEST"] = "1"
        os.environ["OMES_FAKE_GROUPS"] = ""
        self.assertIsNotNone(compose_preflight.require())


if __name__ == "__main__":
    unittest.main()
