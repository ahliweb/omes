"""tests/py/agent/test_cli_v2.py - end-to-end CLI tests for RuntimeDeployment v2 & migration (#174)."""
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
FIXTURES_V1 = OMES_ROOT / "contracts" / "agent" / "v1" / "fixtures" / "agent-deployment"
FIXTURES_V2 = OMES_ROOT / "contracts" / "agent" / "v2" / "fixtures" / "runtime-deployment"


class AgentCliV2TestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-agent-v2-cli-test-")
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
        self.env["SHIM_HERMES_PROFILES"] = "default,researcher,analyst"
        self.env["SHIM_USER_ENABLED_FILE"] = str(self.state_home / "shim-user-enabled")
        self.env["SHIM_USER_ACTIVE_FILE"] = str(self.state_home / "shim-user-active")
        self.env["SHIM_SYSTEM_ENABLED_FILE"] = str(self.state_home / "shim-system-enabled")
        self.env["SHIM_SYSTEM_ACTIVE_FILE"] = str(self.state_home / "shim-system-active")
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

    def _fixture_v1(self, name: str) -> dict:
        return json.loads((FIXTURES_V1 / name).read_text(encoding="utf-8"))

    def _fixture_v2(self, name: str) -> dict:
        return json.loads((FIXTURES_V2 / name).read_text(encoding="utf-8"))

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

    def test_check_v2_passes_when_profile_exists(self):
        data = self._fixture_v2("valid-native-user.json")
        self._write_manifest("researcher", data)
        proc = self._run("check", "researcher", "--json")
        self.assertEqual(proc.returncode, 0, f"check failed: {proc.stderr}\n{proc.stdout}")
        out = json.loads(proc.stdout)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("apiVersion"), "omes.ahliweb.com/v2")

    def test_check_v2_fails_when_profile_missing(self):
        data = self._fixture_v2("valid-native-user.json")
        data["runtime"]["profileRef"] = "non-existent-profile"
        self._write_manifest("researcher", data)
        proc = self._run("check", "researcher", "--json")
        self.assertEqual(proc.returncode, 4)
        out = json.loads(proc.stdout)
        self.assertFalse(out.get("ok"))
        self.assertTrue(any("non-existent-profile" in err for err in out.get("errors", [])))

    def test_plan_v2_outputs_structured_json(self):
        data = self._fixture_v2("valid-native-user.json")
        self._write_manifest("researcher", data)
        proc = self._run("plan", "researcher", "--json")
        self.assertEqual(proc.returncode, 0)
        plan_data = json.loads(proc.stdout)
        self.assertEqual(plan_data["unit"]["name"], "omes-agent-researcher.service")
        self.assertEqual(plan_data["profileRef"], "researcher")

    def test_apply_v2_succeeds_end_to_end(self):
        data = self._fixture_v2("valid-native-user.json")
        self._write_manifest("researcher", data)
        proc = self._run("apply", "researcher", "--yes", "--json")
        self.assertEqual(proc.returncode, 0, f"apply failed: {proc.stderr}\n{proc.stdout}")
        out = json.loads(proc.stdout)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("apiVersion"), "omes.ahliweb.com/v2")

    def test_migrate_dry_run_does_not_modify_file(self):
        data = self._fixture_v1("valid-generic-user.json")
        path = self._write_manifest("researcher", data)
        original_content = path.read_text(encoding="utf-8")

        proc = self._run("migrate", "researcher", "--dry-run", "--json")
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("dry_run"))
        migrated = out.get("migrated_manifest")
        self.assertEqual(migrated["apiVersion"], "omes.ahliweb.com/v2")
        self.assertEqual(migrated["runtime"]["profileRef"], "researcher")
        # Ensure file on disk was not modified
        self.assertEqual(path.read_text(encoding="utf-8"), original_content)

    def test_migrate_in_place_creates_backup(self):
        data = self._fixture_v1("valid-generic-user.json")
        path = self._write_manifest("researcher", data)

        proc = self._run("migrate", "researcher", "--json")
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertTrue(out.get("ok"))
        self.assertFalse(out.get("dry_run"))

        # Check .bak exists
        backup_path = path.with_suffix(".json.bak")
        self.assertTrue(backup_path.exists())
        self.assertIn("omes.ahliweb.com/v1", backup_path.read_text(encoding="utf-8"))

        # Check migrated file is v2
        new_data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(new_data["apiVersion"], "omes.ahliweb.com/v2")
        self.assertEqual(new_data["kind"], "RuntimeDeployment")
        self.assertEqual(new_data["runtime"]["profileRef"], "researcher")

    def test_migrate_to_explicit_output_path(self):
        data = self._fixture_v1("valid-generic-user.json")
        path = self._write_manifest("researcher", data)
        out_file = self.manifests_dir / "researcher-custom.json"

        proc = self._run("migrate", "researcher", "--output", str(out_file), "--json")
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(out_file.exists())
        new_data = json.loads(out_file.read_text(encoding="utf-8"))
        self.assertEqual(new_data["apiVersion"], "omes.ahliweb.com/v2")

    def test_doctor_reports_both_v1_and_v2_deployments(self):
        # Deploy a v1 agent
        v1_data = self._fixture_v1("valid-generic-user.json")
        self._write_manifest("researcher", v1_data)
        proc1 = self._run("apply", "researcher", "--yes", "--json")
        self.assertEqual(proc1.returncode, 0)

        # Deploy a v2 agent
        v2_data = self._fixture_v2("valid-native-user.json")
        v2_data["metadata"]["name"] = "specialist"
        v2_data["runtime"]["profileRef"] = "analyst"
        self._write_manifest("specialist", v2_data)
        proc2 = self._run("apply", "specialist", "--yes", "--json")
        self.assertEqual(proc2.returncode, 0, f"apply v2 failed: {proc2.stderr}\n{proc2.stdout}")

        # Run doctor
        proc = self._run("doctor", "--json")
        self.assertEqual(proc.returncode, 0)
        doc = json.loads(proc.stdout)
        self.assertTrue(doc.get("ok"))
        agent_names = {a["name"] for a in doc["agents"]}
        self.assertIn("researcher", agent_names)
        self.assertIn("specialist", agent_names)


if __name__ == "__main__":
    unittest.main()
