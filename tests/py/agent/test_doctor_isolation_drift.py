"""tests/py/agent/test_doctor_isolation_drift.py - `omes agent doctor` runs
the read-only isolation-drift check for compose agents (issue #276), driven
through subprocess against tests/shims/docker (never a real docker daemon).
"""
from __future__ import annotations

import json
import re
import unittest

from . import _pathfix  # noqa: F401
from . import test_cli_compose as base


class TestDoctorIsolationDrift(base.ComposeCliTestCase):
    # Reuse the drift-fixture helpers without re-collecting their tests.
    _plan = base.TestComposeEgressAndIsolationDrift._plan
    _inspect_json = base.TestComposeEgressAndIsolationDrift._inspect_json
    _drift_env = base.TestComposeEgressAndIsolationDrift._drift_env

    def _deploy_compose(self):
        self._write_manifest("compose-worker", self._fixture("valid-compose-generic.json"))
        proc = self._run("apply", "compose-worker", "--yes", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def _doctor(self, env):
        proc = self._run("doctor", "--json", env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)  # doctor always exits 0
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["agents"]), 1)
        return data, data["agents"][0]

    def test_compose_agent_without_drift_is_ok(self):
        self._deploy_compose()
        env = self._drift_env(self._inspect_json(self._plan()))
        data, entry = self._doctor(env)
        self.assertEqual(entry["isolationDrift"]["status"], "ok")
        self.assertEqual(entry["isolationDrift"]["findings"], [])
        self.assertTrue(entry["ok"])
        self.assertNotIn("warning", entry)
        self.assertTrue(data["ok"])

    def test_drift_detected_is_a_warning_not_ok(self):
        self._deploy_compose()
        env = self._drift_env(self._inspect_json(self._plan(), ReadonlyRootfs=False))
        data, entry = self._doctor(env)
        self.assertEqual(entry["isolationDrift"]["status"], "drift")
        self.assertTrue(entry["isolationDrift"]["findings"])
        self.assertTrue(entry["ready"])  # health itself is fine
        self.assertFalse(entry["ok"])
        self.assertIn("isolation drift drift", entry["warning"])
        self.assertNotIn("error", entry)
        self.assertFalse(data["ok"])

    def test_daemon_unreachable_is_unknown_never_ok(self):
        self._deploy_compose()
        # Shim default: `docker inspect` exits 1 (no daemon / no container).
        data, entry = self._doctor(dict(self.env))
        self.assertEqual(entry["isolationDrift"]["status"], "unknown")
        self.assertTrue(entry["isolationDrift"]["reason"])
        self.assertFalse(entry["ok"])
        self.assertIn("isolation drift unknown", entry["warning"])
        self.assertFalse(data["ok"])

    def test_non_compose_backend_is_not_applicable(self):
        self._write_manifest("researcher", self._fixture("valid-generic-user.json"))
        proc = self._run("apply", "researcher", "--yes", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        _, entry = self._doctor(dict(self.env))
        self.assertEqual(entry["backend"], "systemd")
        self.assertEqual(entry["isolationDrift"]["status"], "not_applicable")
        self.assertNotIn("warning", entry)

    def test_json_shape_and_existing_fields_unchanged(self):
        self._deploy_compose()
        env = self._drift_env(self._inspect_json(self._plan()))
        _, entry = self._doctor(env)
        for key in ("name", "state", "backend", "serviceMode", "ok", "ready", "connected", "health"):
            self.assertIn(key, entry)
        self.assertEqual(
            set(entry["isolationDrift"]),
            {"status", "container", "findings", "unverified", "reason"},
        )

    def test_human_output_shows_isolation_status(self):
        self._deploy_compose()
        env = self._drift_env(self._inspect_json(self._plan(), ReadonlyRootfs=False))
        proc = self._run("doctor", env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("compose-worker: WARN", proc.stdout)
        self.assertIn("isolation=drift", proc.stdout)

    def test_doctor_runs_only_read_only_inspects_for_drift(self):
        self._deploy_compose()
        log = self.home / "docker.log"
        env = self._drift_env(self._inspect_json(self._plan()), log=log)
        self._doctor(env)
        calls = log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(c.startswith("docker inspect --type container") for c in calls))
        for c in calls:
            self.assertIsNone(re.search(r"docker (rm|stop|start|restart|kill|run)\b", c), c)
            self.assertIsNone(re.search(r"docker compose .*\b(up|down|restart|rm)\b", c), c)


if __name__ == "__main__":
    unittest.main()
