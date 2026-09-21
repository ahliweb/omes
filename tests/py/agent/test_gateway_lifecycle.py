"""tests/py/agent/test_gateway_lifecycle.py - tests for upstream Hermes gateway lifecycle delegation (#175)."""
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
FIXTURES_V2 = OMES_ROOT / "contracts" / "agent" / "v2" / "fixtures" / "runtime-deployment"


class GatewayLifecycleTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-agent-gw-test-")
        self.home = Path(self._tmp) / "home"
        self.home.mkdir()
        self.config_home = self.home / ".config"
        self.state_home = self.home / ".local" / "state"

        self.shim_log = Path(self._tmp) / "shim.log"

        self.env = dict(os.environ)
        self.env["HOME"] = str(self.home)
        self.env["XDG_CONFIG_HOME"] = str(self.config_home)
        self.env["XDG_STATE_HOME"] = str(self.state_home)
        self.env["OMES_ROOT"] = str(OMES_ROOT)
        self.env["PYTHONPATH"] = str(PY_ROOT)
        self.env["PATH"] = f"{SHIMS}{os.pathsep}{self.env.get('PATH', '')}"
        self.env["SHIM_HERMES_VERSION"] = "1.2.3"
        self.env["SHIM_HERMES_PROFILES"] = "default,researcher,analyst"
        self.env["SHIM_LOG"] = str(self.shim_log)
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

    def test_apply_delegates_to_hermes_gateway_install_and_writes_overlay_only(self):
        data = self._fixture_v2("valid-native-user.json")
        self._write_manifest("researcher", data)

        proc = self._run("apply", "researcher", "--yes", "--json")
        self.assertEqual(proc.returncode, 0, f"apply failed: {proc.stderr}\n{proc.stdout}")

        unit_file = self.config_home / "systemd" / "user" / "hermes-gateway-researcher.service"
        dropin_file = self.config_home / "systemd" / "user" / "hermes-gateway-researcher.service.d" / "10-omes-agent-resources.conf"

        self.assertTrue(unit_file.exists(), "upstream unit should be created by hermes gateway install")
        self.assertTrue(dropin_file.exists(), "OMES drop-in overlay should exist")

        # Verify managedPaths in state only tracks the dropin
        state_file = self.state_home / "agents" / "researcher" / "state.json"
        self.assertTrue(state_file.exists())
        st = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(st.get("managedPaths"), [str(dropin_file)])

        # Verify shim log shows hermes gateway install was invoked
        if self.shim_log.exists():
            log_content = self.shim_log.read_text(encoding="utf-8")
            self.assertIn("hermes gateway install --profile researcher", log_content)

    def test_rollback_removes_overlay_and_preserves_base_unit(self):
        data = self._fixture_v2("valid-native-user.json")
        self._write_manifest("researcher", data)

        apply_proc = self._run("apply", "researcher", "--yes", "--json")
        self.assertEqual(apply_proc.returncode, 0)

        unit_file = self.config_home / "systemd" / "user" / "hermes-gateway-researcher.service"
        dropin_file = self.config_home / "systemd" / "user" / "hermes-gateway-researcher.service.d" / "10-omes-agent-resources.conf"
        self.assertTrue(unit_file.exists())
        self.assertTrue(dropin_file.exists())

        rb_proc = self._run("rollback", "researcher", "--yes", "--json")
        self.assertEqual(rb_proc.returncode, 0, f"rollback failed: {rb_proc.stderr}")

        # Dropin overlay removed, but upstream unit file remains intact
        self.assertFalse(dropin_file.exists(), "dropin overlay must be unlinked during rollback")
        self.assertTrue(unit_file.exists(), "upstream unit file must be preserved during rollback")

        st_proc = self._run("status", "researcher", "--json")
        st_data = json.loads(st_proc.stdout)
        self.assertEqual(st_data.get("state"), "rolled-back")

    def test_restart_invokes_hermes_gateway(self):
        data = self._fixture_v2("valid-native-user.json")
        self._write_manifest("researcher", data)

        self._run("apply", "researcher", "--yes", "--json")

        proc = self._run("restart", "researcher", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("unit"), "hermes-gateway-researcher.service")

    def test_legacy_unit_migration_cleans_up_old_unit(self):
        # Create legacy omes-agent-researcher.service
        user_systemd = self.config_home / "systemd" / "user"
        user_systemd.mkdir(parents=True, exist_ok=True)
        legacy_unit = user_systemd / "omes-agent-researcher.service"
        legacy_unit.write_text("[Unit]\nDescription=Legacy\n", encoding="utf-8")
        legacy_dropin_dir = user_systemd / "omes-agent-researcher.service.d"
        legacy_dropin_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dropin_dir / "old.conf").write_text("[Service]\nOld=1\n", encoding="utf-8")

        # Mark legacy unit active in shim
        with open(self.env["SHIM_USER_ACTIVE_FILE"], "a", encoding="utf-8") as f:
            f.write("omes-agent-researcher.service\n")

        data = self._fixture_v2("valid-native-user.json")
        self._write_manifest("researcher", data)

        proc = self._run("apply", "researcher", "--yes", "--json")
        self.assertEqual(proc.returncode, 0, f"apply with migration failed: {proc.stderr}\n{proc.stdout}")

        # Assert legacy files are removed
        self.assertFalse(legacy_unit.exists(), "legacy unit file must be unlinked during migration")
        self.assertFalse(legacy_dropin_dir.exists(), "legacy dropin directory must be cleaned up")

        # Assert new upstream service and overlay exist
        new_unit = user_systemd / "hermes-gateway-researcher.service"
        new_dropin = user_systemd / "hermes-gateway-researcher.service.d" / "10-omes-agent-resources.conf"
        self.assertTrue(new_unit.exists())
        self.assertTrue(new_dropin.exists())

    def test_multiplexed_mode_awareness(self):
        env = dict(self.env)
        env["SHIM_HERMES_MULTIPLEXED"] = "1"

        data = self._fixture_v2("valid-native-user.json")
        self._write_manifest("researcher", data)

        proc = self._run("apply", "researcher", "--yes", "--json", env=env)
        self.assertEqual(proc.returncode, 0, f"multiplexed apply failed: {proc.stderr}")

        st_proc = self._run("status", "researcher", "--json", env=env)
        st_data = json.loads(st_proc.stdout)
        self.assertTrue(st_data.get("multiplexed"))
