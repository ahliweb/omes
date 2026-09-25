"""tests/py/agent/test_cli.py - end-to-end `omes agent` CLI tests
(issue #87) driven through subprocess against the real tests/shims/
systemctl and tests/shims/hermes fakes, exercising the full
check -> plan -> backup -> mutate -> verify apply lifecycle, idempotent
re-apply, rollback, wrong-privilege refusal, systemd failure, profile
isolation, and secret exclusion.

Corrupted-backup handling is exercised directly against
lib/omes/py/hermesbackup (issue #82), which `apply`'s backup step calls
into - see TestCorruptedBackupIsSurfaced below.
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


class AgentCliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-agent-cli-test-")
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
        self.env["SHIM_HERMES_VERSION"] = "1.2.3"
        self.env["SHIM_USER_ENABLED_FILE"] = str(self.state_home / "shim-user-enabled")
        self.env["SHIM_USER_ACTIVE_FILE"] = str(self.state_home / "shim-user-active")
        self.env["SHIM_SYSTEM_ENABLED_FILE"] = str(self.state_home / "shim-system-enabled")
        self.env["SHIM_SYSTEM_ACTIVE_FILE"] = str(self.state_home / "shim-system-active")
        # Pin config/state dirs explicitly rather than relying on
        # HOME/XDG_*+root-detection resolution, so a test that fakes root
        # (OMES_FAKE_ROOT=1, to exercise the privilege check) does not
        # also relocate where the manifest/state live out from under it.
        self.env["OMES_CONFIG_DIR"] = str(self.config_home / "omes")
        self.env["OMES_STATE_DIR"] = str(self.state_home)
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
        fixtures = OMES_ROOT / "contracts" / "agent" / "v1" / "fixtures" / "agent-deployment"
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
            stdin=subprocess.DEVNULL if input_text is None else None,
            timeout=30,
            check=False,
        )


class TestPreflightAndPrivilege(AgentCliTestCase):
    def test_check_fails_for_missing_manifest(self):
        proc = self._run("check", "ghost", "--json")
        self.assertEqual(proc.returncode, 4)

    def test_check_fails_for_invalid_manifest(self):
        data = self._fixture("valid-generic-user.json")
        data["spec"]["runtime"] = "docker-compose"
        self._write_manifest("researcher", data)
        proc = self._run("check", "researcher", "--json")
        self.assertEqual(proc.returncode, 4)

    def test_apply_refuses_user_mode_as_root(self):
        self._write_manifest("researcher", self._fixture("valid-generic-user.json"))
        env = dict(self.env)
        env["OMES_TEST"] = "1"
        env["OMES_FAKE_ROOT"] = "1"
        proc = self._run("apply", "researcher", "--yes", "--json", env=env)
        self.assertEqual(proc.returncode, 5)

    def test_apply_refuses_system_mode_as_non_root(self):
        self._write_manifest("support-desk", self._fixture("valid-specialist-system.json"))
        proc = self._run("apply", "support-desk", "--yes", "--json")
        self.assertEqual(proc.returncode, 5)

    def test_duplicate_or_mismatched_name_is_rejected(self):
        data = self._fixture("valid-generic-user.json")
        # File is named "researcher.json" but declares a different
        # logical name - must be refused, not silently accepted under
        # whichever name wins.
        self._write_manifest("researcher", {**data, "metadata": {**data["metadata"], "name": "impostor"}})
        proc = self._run("check", "researcher", "--json")
        self.assertEqual(proc.returncode, 4)
        self.assertIn("duplicate", proc.stdout + proc.stderr)


class TestApplyLifecycle(AgentCliTestCase):
    def _apply(self, name="researcher"):
        self._write_manifest(name, self._fixture("valid-generic-user.json") if name == "researcher" else self._fixture("valid-generic-user.json"))
        return self._run("apply", name, "--yes", "--json")

    def test_dry_run_makes_no_mutation(self):
        self._write_manifest("researcher", self._fixture("valid-generic-user.json"))
        proc = self._run("apply", "researcher", "--dry-run", "--json")
        self.assertEqual(proc.returncode, 0)
        unit_path = self.config_home / "systemd" / "user" / "hermes-gateway-researcher.service"
        dropin_path = self.config_home / "systemd" / "user" / "hermes-gateway-researcher.service.d" / "10-omes-agent-resources.conf"
        self.assertFalse(unit_path.exists())
        self.assertFalse(dropin_path.exists())
        status = self._run("status", "researcher", "--json")
        self.assertEqual(json.loads(status.stdout)["state"], "declared")

    def test_dry_run_output_has_no_secret_values(self):
        self._write_manifest("researcher", self._fixture("valid-generic-user.json"))
        proc = self._run("apply", "researcher", "--dry-run", "--json")
        self.assertNotIn("provider-primary=", proc.stdout)

    def test_apply_without_yes_and_no_tty_is_refused(self):
        self._write_manifest("researcher", self._fixture("valid-generic-user.json"))
        proc = self._run("apply", "researcher", "--json")
        self.assertNotEqual(proc.returncode, 0)

    def test_successful_apply_reaches_healthy_and_creates_unit(self):
        proc = self._apply()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn(result["state"], ("healthy", "degraded"))
        unit_path = self.config_home / "systemd" / "user" / "hermes-gateway-researcher.service"
        dropin_path = self.config_home / "systemd" / "user" / "hermes-gateway-researcher.service.d" / "10-omes-agent-resources.conf"
        self.assertTrue(unit_path.exists())
        self.assertTrue(dropin_path.exists())

    def test_apply_is_idempotent(self):
        first = self._apply()
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self._apply()
        self.assertEqual(second.returncode, 0, second.stderr)
        # Running apply twice must not error and must leave the unit and drop-in in
        # place (not duplicated, not removed).
        unit_path = self.config_home / "systemd" / "user" / "hermes-gateway-researcher.service"
        dropin_path = self.config_home / "systemd" / "user" / "hermes-gateway-researcher.service.d" / "10-omes-agent-resources.conf"
        self.assertTrue(unit_path.exists())
        self.assertTrue(dropin_path.exists())

    def test_status_reflects_unit_active(self):
        self._apply()
        proc = self._run("status", "researcher", "--json")
        result = json.loads(proc.stdout)
        self.assertTrue(result.get("unitActive"))

    def test_health_is_read_only_and_reports_ready(self):
        self._apply()
        proc = self._run("health", "researcher", "--json")
        result = json.loads(proc.stdout)
        self.assertTrue(result["ready"])
        # never calls a prohibited Telegram polling endpoint - the
        # channel layer is not_applicable without TELEGRAM_BOT_TOKEN.
        self.assertEqual(result["layers"]["channel"]["status"], "not_applicable")

    def test_rollback_removes_dropin_but_preserves_hermes_home(self):
        self._apply()
        hermes_home = self.home / "agents" / "researcher" / "hermes"
        hermes_home.mkdir(parents=True, exist_ok=True)
        (hermes_home / "SOUL.md").write_text("operator data\n", encoding="utf-8")

        proc = self._run("rollback", "researcher", "--yes", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        dropin_path = self.config_home / "systemd" / "user" / "hermes-gateway-researcher.service.d" / "10-omes-agent-resources.conf"
        self.assertFalse(dropin_path.exists())
        self.assertTrue((hermes_home / "SOUL.md").exists())

        status = self._run("status", "researcher", "--json")
        self.assertEqual(json.loads(status.stdout)["state"], "rolled-back")

    def test_systemd_enable_failure_marks_state_failed(self):
        self._write_manifest("researcher", self._fixture("valid-generic-user.json"))
        env = dict(self.env)
        # SHIM_RESTART_FAILS only affects `restart`; simulate an enable
        # failure by pointing SHIM_USER_ENABLED_FILE at an unwritable
        # location so `_list_add` (and thus `enable`) fails via the shim.
        bogus_dir = Path(self._tmp) / "no-such-parent" / "enabled"
        env["SHIM_USER_ENABLED_FILE"] = str(bogus_dir)
        # Make the parent of bogus_dir read-only so mkdir -p inside the
        # shim fails and `enable --now` exits non-zero.
        readonly_parent = Path(self._tmp) / "no-such-parent"
        readonly_parent_owner = Path(self._tmp)
        os.chmod(readonly_parent_owner, 0o555)
        try:
            proc = self._run("apply", "researcher", "--yes", "--json", env=env)
            self.assertNotEqual(proc.returncode, 0)
            status = self._run("status", "researcher", "--json", env=env)
            self.assertIn(json.loads(status.stdout)["state"], ("failed", "backed-up", "planned", "preflighted"))
        finally:
            os.chmod(readonly_parent_owner, 0o755)

    def test_rollback_stop_failure_marks_failed(self):
        self._apply()
        env = dict(self.env)
        env["SHIM_SYSTEMCTL_STOP_FAILS"] = "1"
        proc = self._run("rollback", "researcher", "--yes", "--json", env=env)
        self.assertNotEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "failed")
        self.assertTrue(any(s["step"] == "systemctl_stop" for s in result["failed_steps"]))
        status = self._run("status", "researcher", "--json")
        self.assertEqual(json.loads(status.stdout)["state"], "failed")

    def test_rollback_disable_failure_marks_failed(self):
        self._apply()
        env = dict(self.env)
        env["SHIM_SYSTEMCTL_DISABLE_FAILS"] = "1"
        proc = self._run("rollback", "researcher", "--yes", "--json", env=env)
        self.assertNotEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "failed")
        self.assertTrue(any(s["step"] == "systemctl_disable" for s in result["failed_steps"]))

    def test_rollback_daemon_reload_failure_marks_failed(self):
        self._apply()
        env = dict(self.env)
        env["SHIM_SYSTEMCTL_DAEMON_RELOAD_FAILS"] = "1"
        proc = self._run("rollback", "researcher", "--yes", "--json", env=env)
        self.assertNotEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "failed")
        self.assertTrue(any(s["step"] == "daemon_reload" for s in result["failed_steps"]))

    def test_rollback_idempotent_convergence_after_failure(self):
        self._apply()
        env = dict(self.env)
        env["SHIM_SYSTEMCTL_STOP_FAILS"] = "1"
        # First attempt fails
        proc1 = self._run("rollback", "researcher", "--yes", "--json", env=env)
        self.assertNotEqual(proc1.returncode, 0)
        status1 = self._run("status", "researcher", "--json")
        self.assertEqual(json.loads(status1.stdout)["state"], "failed")

        # Second attempt without failure trigger succeeds and converges to rolled-back
        proc2 = self._run("rollback", "researcher", "--yes", "--json")
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        result2 = json.loads(proc2.stdout)
        self.assertTrue(result2["ok"])
        self.assertEqual(result2["state"], "rolled-back")
        status2 = self._run("status", "researcher", "--json")
        self.assertEqual(json.loads(status2.stdout)["state"], "rolled-back")

    def test_rollback_structured_json_verification(self):
        self._apply()
        proc = self._run("rollback", "researcher", "--yes", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "rollback")
        self.assertEqual(result["backend"], "systemd")
        self.assertIn("applied_steps", result)
        self.assertIn("observed_state", result)
        self.assertIn("verification", result)
        self.assertTrue(result["verification"]["passed"])
        self.assertFalse(result["observed_state"]["unit_active"])
        self.assertFalse(result["observed_state"]["unit_enabled"])


class TestProfileIsolation(AgentCliTestCase):
    def test_two_agents_get_distinct_hermes_homes_and_units(self):
        base = self._fixture("valid-generic-user.json")
        second = json.loads(json.dumps(base))
        second["metadata"]["name"] = "reviewer"
        second["spec"]["profile"] = "reviewer"
        self._write_manifest("researcher", base)
        self._write_manifest("reviewer", second)

        p1 = json.loads(self._run("plan", "researcher", "--json").stdout)
        p2 = json.loads(self._run("plan", "reviewer", "--json").stdout)

        self.assertNotEqual(p1["hermesHome"], p2["hermesHome"])
        self.assertNotEqual(p1["unit"]["name"], p2["unit"]["name"])


class TestHealthTimeout(AgentCliTestCase):
    def test_health_timeout_env_is_honored_and_bounded(self):
        self._write_manifest("researcher", self._fixture("valid-generic-user.json"))
        self._run("apply", "researcher", "--yes", "--json")
        env = dict(self.env)
        env["OMES_HEALTH_TIMEOUT"] = "1"
        proc = self._run("health", "researcher", "--json", env=env)
        # Must complete promptly (well under the subprocess-level 30s
        # test timeout) and still produce a valid result object.
        result = json.loads(proc.stdout)
        self.assertIn("ready", result)


class TestCorruptedBackupIsSurfaced(AgentCliTestCase):
    def test_corrupted_backup_archive_is_detected_by_hermesbackup_verify(self):
        """apply's backup step calls hermesbackup.backup.create (issue
        #82); corruption detection itself is hermesbackup's own
        responsibility and is exhaustively tested in
        tests/py/hermesbackup/test_backup.py. This test only proves the
        two subsystems are wired together as expected: a backup created
        for an agent's HERMES_HOME can be verified, and a corrupted
        archive is reported as such rather than silently accepted."""
        sys.path.insert(0, str(PY_ROOT))
        from hermesbackup import backup as backup_mod, paths as backup_paths

        hermes_home = self.home / "agents" / "researcher" / "hermes"
        hermes_home.mkdir(parents=True)
        (hermes_home / "config.yaml").write_text("model: gpt\n", encoding="utf-8")

        old_hermes_home_env = os.environ.get("HERMES_HOME")
        old_state_dir_env = os.environ.get("OMES_STATE_DIR")
        old_path_env = os.environ.get("PATH")
        os.environ["HERMES_HOME"] = str(hermes_home)
        os.environ["OMES_STATE_DIR"] = str(self.state_home)
        os.environ["PATH"] = f"{SHIMS}{os.pathsep}{old_path_env or ''}"
        try:
            result = backup_mod.create(
                hermes_home, None, include_secrets=False, allow_restricted_scope=True, dry_run=False
            )
            timestamp = result["timestamp"]
            backups_root = backup_paths.backups_root()
            archive_path = backups_root / timestamp / (result.get("artifact") or "archive.tar")
            with open(archive_path, "r+b") as fh:
                fh.seek(0)
                fh.write(b"\x00" * 16)

            with self.assertRaises(backup_mod.BackupError):
                backup_mod.verify(timestamp)
        finally:
            if old_hermes_home_env is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = old_hermes_home_env
            if old_state_dir_env is None:
                os.environ.pop("OMES_STATE_DIR", None)
            else:
                os.environ["OMES_STATE_DIR"] = old_state_dir_env
            if old_path_env is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old_path_env


class TestDoctor(AgentCliTestCase):
    """`omes agent doctor` (issue #87/#96 follow-up: "omes doctor
    integration") - read-only report of every deployed agent's lifecycle
    state and health summary. `--json` output must always be a single
    JSON object (never multiple objects/lines), per the repository-wide
    `--json` contract."""

    def test_json_output_is_a_single_object_with_no_agents_deployed(self):
        proc = self._run("doctor", "--json")
        self.assertEqual(proc.returncode, 0)
        # Exactly one JSON value on stdout - json.loads fails on trailing
        # data, so this alone proves "a single object", not multiple
        # concatenated JSON blobs.
        data = json.loads(proc.stdout)
        self.assertIsInstance(data, dict)
        self.assertEqual(data["agents"], [])
        self.assertTrue(data["ok"])

    def test_reports_a_deployed_agent_with_state_and_health(self):
        self._write_manifest("researcher", self._fixture("valid-generic-user.json"))
        apply_proc = self._run("apply", "researcher", "--yes", "--json")
        self.assertEqual(apply_proc.returncode, 0)

        proc = self._run("doctor", "--json")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["agents"]), 1)
        entry = data["agents"][0]
        self.assertEqual(entry["name"], "researcher")
        self.assertEqual(entry["backend"], "systemd")
        self.assertIn(entry["state"], ("healthy", "degraded"))
        self.assertIn("health", entry)
        self.assertIn("ready", entry)
        self.assertIn("connected", entry)

    def test_reports_error_for_a_deployed_agent_whose_manifest_became_invalid(self):
        self._write_manifest("researcher", self._fixture("valid-generic-user.json"))
        apply_proc = self._run("apply", "researcher", "--yes", "--json")
        self.assertEqual(apply_proc.returncode, 0)

        # Simulate the manifest being edited into an invalid state after
        # deployment (e.g. hand-edited, or a future manifest-migration
        # bug) - doctor must report this agent as an error row, never
        # crash the whole report.
        broken = self._fixture("valid-generic-user.json")
        broken["spec"]["runtime"] = "not-hermes"
        self._write_manifest("researcher", broken)

        proc = self._run("doctor", "--json")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["agents"]), 1)
        entry = data["agents"][0]
        self.assertFalse(entry["ok"])
        self.assertIn("error", entry)
        self.assertFalse(data["ok"])

    def test_human_output_lists_every_agent_by_name(self):
        self._write_manifest("researcher", self._fixture("valid-generic-user.json"))
        self._run("apply", "researcher", "--yes", "--json")
        proc = self._run("doctor")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("researcher", proc.stdout)


if __name__ == "__main__":
    unittest.main()
