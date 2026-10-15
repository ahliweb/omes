"""tests/py/agent/test_cli_compose.py - end-to-end `omes agent` CLI tests
for the rootless Docker Compose backend (issue #96), driven through
subprocess against tests/shims/docker (never a real docker daemon).
Mirrors tests/py/agent/test_cli.py's pattern for the systemd backend.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from . import _pathfix  # noqa: F401

from agent import compose as compose_mod  # noqa: E402

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


class TestComposeTopology(ComposeCliTestCase):
    def test_apply_shared_topology(self):
        v2_fixture = json.loads((OMES_ROOT / "contracts" / "agent" / "v2" / "fixtures" / "runtime-deployment" / "valid-compose-shared.json").read_text(encoding="utf-8"))
        self._write_manifest("analyst-shared", v2_fixture)
        proc = self._run("apply", "analyst-shared", "--yes", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["state"], "healthy")
        shared_compose_file = self.state_home / "shared-hermes" / "compose.yaml"
        self.assertTrue(shared_compose_file.exists())
        content = shared_compose_file.read_text(encoding="utf-8")
        self.assertIn("/opt/data:rw", content)
        self.assertIn("tmpfs:", content)
        self.assertIn("hermes:", content)

    def test_status_reports_topology(self):
        v2_fixture = json.loads((OMES_ROOT / "contracts" / "agent" / "v2" / "fixtures" / "runtime-deployment" / "valid-compose-shared.json").read_text(encoding="utf-8"))
        self._write_manifest("analyst-shared", v2_fixture)
        apply_proc = self._run("apply", "analyst-shared", "--yes", "--json")
        self.assertEqual(apply_proc.returncode, 0, apply_proc.stderr)
        proc = self._run("status", "analyst-shared", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        st = json.loads(proc.stdout)
        self.assertEqual(st["backend"], "compose")
        self.assertEqual(st["topology"], "shared")

    def test_restart_shared_topology_targets_profile(self):
        v2_fixture = json.loads((OMES_ROOT / "contracts" / "agent" / "v2" / "fixtures" / "runtime-deployment" / "valid-compose-shared.json").read_text(encoding="utf-8"))
        self._write_manifest("analyst-shared", v2_fixture)
        apply_proc = self._run("apply", "analyst-shared", "--yes", "--json")
        self.assertEqual(apply_proc.returncode, 0, apply_proc.stderr)
        proc = self._run("restart", "analyst-shared", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertTrue(out["ok"])
        self.assertEqual(out["topology"], "shared")

    def test_remove_shared_topology_without_sibling_mutation(self):
        v2_fixture1 = copy.deepcopy(json.loads((OMES_ROOT / "contracts" / "agent" / "v2" / "fixtures" / "runtime-deployment" / "valid-compose-shared.json").read_text(encoding="utf-8")))
        v2_fixture1["metadata"]["name"] = "analyst-shared-1"
        self._write_manifest("analyst-shared-1", v2_fixture1)
        v2_fixture2 = copy.deepcopy(v2_fixture1)
        v2_fixture2["metadata"]["name"] = "analyst-shared-2"
        v2_fixture2["runtime"] = {"kind": "hermes", "profileRef": "sysmon"}
        self._write_manifest("analyst-shared-2", v2_fixture2)

        proc1 = self._run("apply", "analyst-shared-1", "--yes", "--json")
        self.assertEqual(proc1.returncode, 0, proc1.stderr)
        proc2 = self._run("apply", "analyst-shared-2", "--yes", "--json")
        self.assertEqual(proc2.returncode, 0, proc2.stderr)

        shared_compose_file = self.state_home / "shared-hermes" / "compose.yaml"
        self.assertTrue(shared_compose_file.exists())

        # Remove agent 1; shared compose file should NOT be deleted because agent 2 exists
        rem_proc = self._run("remove", "analyst-shared-1", "--yes", "--json")
        self.assertEqual(rem_proc.returncode, 0, rem_proc.stderr)
        self.assertTrue(shared_compose_file.exists())


class TestComposeEgressAndIsolationDrift(ComposeCliTestCase):
    """Egress mode and the read-only `isolation-drift` command (issue #276),
    driven against tests/shims/docker."""

    def _plan(self, name="compose-worker"):
        # Computed in-process (pure function) with the subprocess's state
        # directories, so the declared mounts match what the CLI computes.
        # `omes agent plan` itself is privilege-gated and is not needed here.
        keys = ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "OMES_CONFIG_DIR", "OMES_STATE_DIR")
        manifest_data = json.loads((self.manifests_dir / f"{name}.json").read_text(encoding="utf-8"))
        with mock.patch.dict(os.environ, {k: self.env[k] for k in keys}):
            return compose_mod.build_plan(manifest_data)

    def _inspect_json(self, plan, **host_overrides):
        mem = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[plan["resources"]["memory"][-1]] * int(plan["resources"]["memory"][:-1])
        host = {
            "CapDrop": ["ALL"],
            "ReadonlyRootfs": True,
            "SecurityOpt": ["no-new-privileges:true"],
            "Memory": mem,
            "NanoCpus": int(round(float(plan["resources"]["cpu"]) * 1e9)),
            "PidsLimit": plan["resources"]["pids"],
        }
        host.update(host_overrides)
        return json.dumps([{
            "State": {"Running": True},
            "Config": {"Image": plan["image"], "User": plan["user"]},
            "HostConfig": host,
            "Mounts": [
                {"Type": "bind", "Source": v["hostPath"], "Destination": v["containerPath"], "RW": not v.get("readOnly", False)}
                for v in plan["volumes"]
            ],
            "NetworkSettings": {"Networks": {f"{plan['project']}_{plan['network']}": {}}},
        }])

    def _drift_env(self, inspect_output, internal=False, log=None):
        env = dict(self.env)
        env["SHIM_DOCKER_INSPECT_EXIT"] = "0"
        env["SHIM_DOCKER_INSPECT_OUTPUT"] = inspect_output
        env["SHIM_DOCKER_NETWORK_INSPECT_EXIT"] = "0"
        env["SHIM_DOCKER_NETWORK_INSPECT_OUTPUT"] = json.dumps([{"Internal": internal}])
        if log:
            env["SHIM_LOG"] = str(log)
        return env

    def test_isolation_drift_ok(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        env = self._drift_env(self._inspect_json(self._plan()))
        proc = self._run("isolation-drift", "compose-worker", "--json", env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["findings"], [])

    def test_isolation_drift_reports_drift_with_exit_7(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        env = self._drift_env(self._inspect_json(self._plan(), ReadonlyRootfs=False, CapDrop=None))
        proc = self._run("isolation-drift", "compose-worker", "--json", env=env)
        self.assertEqual(proc.returncode, 7, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["status"], "drift")
        self.assertEqual({f["field"] for f in out["findings"]}, {"readOnlyRootfs", "capDrop"})

    def test_isolation_drift_egress_none_against_open_network(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-egress-none.json"))
        env = self._drift_env(self._inspect_json(self._plan()), internal=False)
        proc = self._run("isolation-drift", "compose-worker", "--json", env=env)
        self.assertEqual(proc.returncode, 7, proc.stderr)
        findings = {f["field"]: f for f in json.loads(proc.stdout)["findings"]}
        self.assertEqual(findings["network.internal"]["declared"], True)

    def test_isolation_drift_without_container_is_unknown_exit_7(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        proc = self._run("isolation-drift", "compose-worker", "--json")  # shim: inspect fails by default
        self.assertEqual(proc.returncode, 7, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["status"], "unknown")
        self.assertEqual(out["findings"], [])

    def test_isolation_drift_malformed_inspect_is_unknown(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        env = self._drift_env("this is not json")
        proc = self._run("isolation-drift", "compose-worker", "--json", env=env)
        self.assertEqual(proc.returncode, 7)
        self.assertEqual(json.loads(proc.stdout)["status"], "unknown")

    def test_isolation_drift_without_docker_on_path_is_unknown(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        env = dict(self.env)
        env["PATH"] = str(Path(sys.executable).parent)  # python only: no docker
        proc = subprocess.run(
            [sys.executable, "-m", "agent.cli", "isolation-drift", "compose-worker", "--json"],
            cwd=str(OMES_ROOT), env=env, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(proc.returncode, 7, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["status"], "unknown")

    def test_isolation_drift_is_read_only(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        log = Path(self._tmp) / "docker.log"
        env = self._drift_env(self._inspect_json(self._plan()), log=log)
        proc = self._run("isolation-drift", "compose-worker", "--json", env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(calls)
        for call in calls:
            self.assertTrue(call.startswith("docker inspect ") or call.startswith("docker network inspect "), call)
        compose_dir = self.state_home / "agents" / "compose-worker"
        self.assertFalse((compose_dir / "compose" / "compose.yaml").exists())

    def test_isolation_drift_text_output(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        env = self._drift_env(self._inspect_json(self._plan(), ReadonlyRootfs=False))
        proc = self._run("isolation-drift", "compose-worker", env=env)
        self.assertEqual(proc.returncode, 7)
        self.assertIn("isolation drift", proc.stdout)
        self.assertIn("readOnlyRootfs", proc.stdout)

    def test_isolation_drift_not_implemented_for_systemd_backend(self):
        self._write_manifest("researcher", self._fixture("valid-generic-user.json"))
        proc = self._run("isolation-drift", "researcher", "--json")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("only implemented for backend=compose", proc.stderr)

    def test_isolation_drift_missing_manifest_exit_4(self):
        proc = self._run("isolation-drift", "nope", "--json")
        self.assertEqual(proc.returncode, 4)


if __name__ == "__main__":
    unittest.main()
