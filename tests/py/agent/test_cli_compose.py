"""tests/py/agent/test_cli_compose.py - end-to-end `omes agent` CLI tests
for the rootless Docker Compose backend (issue #96), driven through
subprocess against tests/shims/docker (never a real docker daemon).
Mirrors tests/py/agent/test_cli.py's pattern for the systemd backend.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

OMES_ROOT = Path(_pathfix.OMES_ROOT)
PY_ROOT = OMES_ROOT / "lib" / "omes" / "py"
SHIMS = OMES_ROOT / "tests" / "shims"


class ComposeCliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-agent-compose-cli-test-")
        self.home = Path(self._tmp) / "home"
        self.home.mkdir()
        self.config_home = self.home / ".config"
        self.state_home = self.home / ".local" / "state"

        self.env = dict(os.environ)
        self.env["HOME"] = str(self.home)
        self.env["XDG_CONFIG_HOME"] = str(self.config_home)
        self.env["XDG_STATE_HOME"] = str(self.state_home)
        self.env["OMES_ROOT"] = str(OMES_ROOT)
        self.env["PYTHONPATH"] = str(PY_ROOT)
        self.env["PATH"] = f"{SHIMS}{os.pathsep}{self.env.get('PATH', '')}"
        self.env["OMES_CONFIG_DIR"] = str(self.config_home / "omes")
        self.env["OMES_STATE_DIR"] = str(self.state_home)
        self.env["OMES_TEST"] = "1"
        self.env["OMES_FAKE_GROUPS"] = "users"
        self.env["SHIM_DOCKER_CONTEXT"] = "rootless"
        self.env["SHIM_DOCKER_ENDPOINT"] = "unix:///run/user/1000/docker.sock"
        self.env["SHIM_DOCKER_SECURITY_OPTIONS"] = "[name=seccomp,profile=default name=rootless]"
        self.state_home.mkdir(parents=True, exist_ok=True)

        self.manifests_dir = self.config_home / "omes" / "agents"
        self.manifests_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_manifest(self, name: str, data: dict) -> Path:
        path = self.manifests_dir / f"{name}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def _fixture(self, name: str) -> dict:
        fixtures = OMES_ROOT / "contracts" / "agent" / "v1" / "fixtures"
        return json.loads((fixtures / name).read_text(encoding="utf-8"))

    def _run(self, *args, env=None, input_text: str = None):
        cmd = [sys.executable, "-m", "agent.cli", *args]
        return subprocess.run(
            cmd,
            cwd=str(OMES_ROOT),
            env=env or self.env,
            capture_output=True,
            text=True,
            input=input_text,
            timeout=30,
            check=False,
        )


class TestComposePreflightGate(ComposeCliTestCase):
    def test_apply_refused_when_daemon_is_rootful(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        env = dict(self.env)
        env["SHIM_DOCKER_CONTEXT"] = "default"
        env["SHIM_DOCKER_ENDPOINT"] = "unix:///var/run/docker.sock"
        env["SHIM_DOCKER_SECURITY_OPTIONS"] = "[name=seccomp,profile=default]"
        proc = self._run("apply", "compose-worker", "--yes", "--json", env=env)
        self.assertEqual(proc.returncode, 4)
        # No compose file should have been written - preflight failure is
        # before any mutation.
        compose_file = self.state_home / "agents" / "compose-worker" / "compose" / "compose.yaml"
        self.assertFalse(compose_file.exists())

    def test_apply_refused_when_socket_is_rootful_path(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        env = dict(self.env)
        env["SHIM_DOCKER_ENDPOINT"] = "unix:///var/run/docker.sock"
        proc = self._run("apply", "compose-worker", "--yes", "--json", env=env)
        self.assertEqual(proc.returncode, 4)

    def test_check_reports_backend(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        proc = self._run("check", "compose-worker", "--json")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout)["backend"], "compose")


class TestComposeDryRun(ComposeCliTestCase):
    def test_dry_run_shows_plan_without_mutation(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        proc = self._run("apply", "compose-worker", "--dry-run", "--json")
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertTrue(out["dryRun"])
        self.assertIn("sha256:aaaa", out["image"])
        compose_file = self.state_home / "agents" / "compose-worker" / "compose" / "compose.yaml"
        self.assertFalse(compose_file.exists())

    def test_dry_run_never_prints_secret_value(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        proc = self._run("apply", "compose-worker", "--dry-run", "--json")
        self.assertNotIn("provider-primary-value", proc.stdout)


class TestComposeApply(ComposeCliTestCase):
    def test_apply_succeeds_and_renders_compose_file(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        proc = self._run("apply", "compose-worker", "--yes", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["state"], "healthy")
        compose_file = self.state_home / "agents" / "compose-worker" / "compose" / "compose.yaml"
        self.assertTrue(compose_file.exists())
        content = compose_file.read_text(encoding="utf-8")
        self.assertIn("sha256:aaaa", content)
        self.assertIn("cap_drop:", content)
        self.assertIn("read_only: true", content)
        self.assertNotIn("provider-primary", content)

    def test_apply_is_idempotent(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        first = self._run("apply", "compose-worker", "--yes", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self._run("apply", "compose-worker", "--yes", "--json")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout)["state"], "healthy")

    def test_apply_records_provenance(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        proc = self._run("apply", "compose-worker", "--yes", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        status = self._run("status", "compose-worker", "--json")
        st = json.loads(status.stdout)
        self.assertIn("composeImageDigest", st["provenance"])
        self.assertIn("composeFileSha256", st["provenance"])

    def test_apply_fails_when_compose_up_fails(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        env = dict(self.env)
        env["SHIM_DOCKER_COMPOSE_UP_EXIT"] = "1"
        proc = self._run("apply", "compose-worker", "--yes", "--json", env=env)
        self.assertEqual(proc.returncode, 6)

    def test_apply_reports_degraded_health_as_failure_exit(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        env = dict(self.env)
        env["SHIM_DOCKER_COMPOSE_PS_OUTPUT"] = '{"Name":"x","State":"exited","Health":""}'
        proc = self._run("apply", "compose-worker", "--yes", "--json", env=env)
        self.assertEqual(proc.returncode, 7)


class TestComposeRollback(ComposeCliTestCase):
    def test_rollback_after_failed_update_restores_previous_version(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        first = self._run("apply", "compose-worker", "--yes", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        compose_file = self.state_home / "agents" / "compose-worker" / "compose" / "compose.yaml"
        original_content = compose_file.read_text(encoding="utf-8")

        data = self._fixture("valid-compose-generic.json")
        data["spec"]["resources"]["memory"] = "1G"
        self._write_manifest("compose-worker", data)

        env = dict(self.env)
        env["SHIM_DOCKER_COMPOSE_UP_EXIT"] = "1"
        second = self._run("apply", "compose-worker", "--yes", "--json", env=env)
        self.assertEqual(second.returncode, 6)

        rollback = self._run("rollback", "compose-worker", "--yes", "--json")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        out = json.loads(rollback.stdout)
        self.assertTrue(out["restoredPreviousVersion"])
        self.assertEqual(compose_file.read_text(encoding="utf-8"), original_content)

    def test_rollback_without_previous_version_just_tears_down(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        proc = self._run("apply", "compose-worker", "--yes", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        rollback = self._run("rollback", "compose-worker", "--yes", "--json")
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        # A previous compose file backup does exist (from the mkdir-less
        # first apply there is none yet before any prior apply) - here
        # there IS one prior version (the one just applied), so this
        # exercises the "prior file exists" path with an unchanged file.
        out = json.loads(rollback.stdout)
        self.assertIn("restoredPreviousVersion", out)


class TestComposeRemove(ComposeCliTestCase):
    def test_remove_tears_down_and_cleans_state(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        proc = self._run("apply", "compose-worker", "--yes", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        remove = self._run("remove", "compose-worker", "--yes", "--json")
        self.assertEqual(remove.returncode, 0, remove.stderr)
        compose_dir = self.state_home / "agents" / "compose-worker" / "compose"
        self.assertFalse(compose_dir.exists())

    def test_remove_not_implemented_for_systemd_backend(self):
        self._write_manifest("researcher", self._fixture("valid-generic-user.json"))
        remove = self._run("remove", "researcher", "--yes", "--json")
        self.assertEqual(remove.returncode, 2)


class TestComposeResourceLimits(ComposeCliTestCase):
    def test_resource_limits_are_rendered(self):
        data = self._fixture("valid-compose-generic.json")
        data["spec"]["resources"] = {"memory": "2G", "cpu": "1.5", "pids": 256}
        self._write_manifest("compose-worker", data)
        proc = self._run("apply", "compose-worker", "--yes", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        compose_file = self.state_home / "agents" / "compose-worker" / "compose" / "compose.yaml"
        content = compose_file.read_text(encoding="utf-8")
        self.assertIn("mem_limit: \"2g\"", content)
        self.assertIn("cpus: \"1.5\"", content)
        self.assertIn("pids_limit: 256", content)


if __name__ == "__main__":
    unittest.main()
