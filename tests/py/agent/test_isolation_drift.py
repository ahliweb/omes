"""tests/py/agent/test_isolation_drift.py - read-only declared-versus-running
isolation drift check for the rootless Compose backend (issue #276).

The module is exercised through an injected fake runner; no docker daemon
is ever contacted. The end-to-end CLI path (against tests/shims/docker)
lives in test_cli_compose.py.
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from agent import compose, isolation_drift  # noqa: E402

OMES_ROOT = Path(_pathfix.OMES_ROOT)
FIXTURES = OMES_ROOT / "contracts" / "agent" / "v1" / "fixtures" / "agent-deployment"


def _manifest(egress: str = "open") -> dict:
    data = json.loads((FIXTURES / "valid-compose-generic.json").read_text(encoding="utf-8"))
    if egress == "none":
        del data["spec"]["compose"]["ports"]
        data["spec"]["compose"]["egress"] = "none"
    return data


def _plan(egress: str = "open") -> dict:
    return compose.build_plan(_manifest(egress))


def _network_name(plan: dict) -> str:
    return f"{plan['project']}_{plan['network']}"


def _healthy_inspect(plan: dict) -> dict:
    """What `docker inspect` reports for a container that matches `plan`."""
    mem = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[plan["resources"]["memory"][-1]] * int(plan["resources"]["memory"][:-1])
    return {
        "State": {"Running": True},
        "Config": {"Image": plan["image"], "User": plan["user"]},
        "HostConfig": {
            "CapDrop": ["ALL"],
            "ReadonlyRootfs": True,
            "SecurityOpt": ["no-new-privileges:true"],
            "Memory": mem,
            "NanoCpus": int(round(float(plan["resources"]["cpu"]) * 1e9)),
            "PidsLimit": plan["resources"]["pids"],
            "Privileged": False,
            "NetworkMode": _network_name(plan),
            "PidMode": "",
        },
        "Mounts": [
            {"Type": "bind", "Source": v["hostPath"], "Destination": v["containerPath"], "RW": not v.get("readOnly", False)}
            for v in plan["volumes"]
        ],
        "NetworkSettings": {"Networks": {_network_name(plan): {}}},
    }


class FakeDocker:
    """Records every argv it is given and answers from canned data."""

    def __init__(self, inspect=None, network_internal=False, inspect_rc=0, network_rc=0, inspect_stdout=None, network_stdout=None):
        self.calls = []
        self.inspect_rc = inspect_rc
        self.network_rc = network_rc
        self.inspect_stdout = inspect_stdout if inspect_stdout is not None else json.dumps([inspect])
        self.network_stdout = network_stdout if network_stdout is not None else json.dumps([{"Internal": network_internal}])

    def __call__(self, cmd, timeout):
        self.calls.append(list(cmd))
        if cmd[:2] == ["docker", "inspect"]:
            return self.inspect_rc, self.inspect_stdout if self.inspect_rc == 0 else "[]", "" if self.inspect_rc == 0 else "Error: No such container"
        if cmd[:3] == ["docker", "network", "inspect"]:
            return self.network_rc, self.network_stdout if self.network_rc == 0 else "[]", "" if self.network_rc == 0 else "Error: No such network"
        return -1, "", "unexpected command"


def _fields(result):
    return {f["field"]: f for f in result["findings"]}


class TestOk(unittest.TestCase):
    def test_matching_container_is_ok(self):
        plan = _plan()
        fake = FakeDocker(inspect=_healthy_inspect(plan), network_internal=False)
        result = isolation_drift.check(plan, runner=fake)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["unverified"], [])
        self.assertTrue(result["readOnly"])

    def test_egress_none_internal_network_is_ok(self):
        plan = _plan("none")
        fake = FakeDocker(inspect=_healthy_inspect(plan), network_internal=True)
        self.assertEqual(isolation_drift.check(plan, runner=fake)["status"], "ok")

    def test_only_fixed_read_only_argv_is_used(self):
        plan = _plan()
        fake = FakeDocker(inspect=_healthy_inspect(plan))
        isolation_drift.check(plan, runner=fake)
        self.assertEqual(
            fake.calls,
            [
                ["docker", "inspect", "--type", "container", plan["containerName"]],
                ["docker", "network", "inspect", _network_name(plan)],
            ],
        )

    def test_cap_drop_prefix_and_case_are_normalised(self):
        plan = _plan()
        inspect = _healthy_inspect(plan)
        inspect["HostConfig"]["CapDrop"] = ["CAP_ALL"]
        inspect["HostConfig"]["SecurityOpt"] = ["no-new-privileges"]
        self.assertEqual(isolation_drift.check(plan, runner=FakeDocker(inspect=inspect))["status"], "ok")


class TestDriftPerField(unittest.TestCase):
    def _drift(self, mutate, egress="open", network_internal=None):
        plan = _plan(egress)
        inspect = _healthy_inspect(plan)
        mutate(inspect, plan)
        if network_internal is None:
            network_internal = egress == "none"
        result = isolation_drift.check(plan, runner=FakeDocker(inspect=inspect, network_internal=network_internal))
        self.assertEqual(result["status"], "drift", result)
        return _fields(result)

    def test_cap_drop_missing(self):
        findings = self._drift(lambda i, p: i["HostConfig"].update(CapDrop=None))
        self.assertEqual(findings["capDrop"]["declared"], ["ALL"])
        self.assertEqual(findings["capDrop"]["observed"], [])
        self.assertEqual(findings["capDrop"]["severity"], "high")

    def test_read_only_false(self):
        findings = self._drift(lambda i, p: i["HostConfig"].update(ReadonlyRootfs=False))
        self.assertEqual((findings["readOnlyRootfs"]["declared"], findings["readOnlyRootfs"]["observed"]), (True, False))

    def test_user_root(self):
        findings = self._drift(lambda i, p: i["Config"].update(User=""))
        self.assertEqual(findings["user"]["declared"], "1000:1000")
        self.assertEqual(findings["user"]["severity"], "critical")

    def test_user_other_non_root_is_high(self):
        findings = self._drift(lambda i, p: i["Config"].update(User="2000:2000"))
        self.assertEqual(findings["user"]["severity"], "high")

    def test_docker_socket_mount_present(self):
        def mutate(inspect, plan):
            inspect["Mounts"].append(
                {"Type": "bind", "Source": "/run/user/1000/docker.sock", "Destination": "/var/run/docker.sock", "RW": True}
            )

        findings = self._drift(mutate)
        self.assertEqual(findings["mounts.dockerSocket"]["severity"], "critical")

    def test_network_internal_mismatch_when_none_declared(self):
        findings = self._drift(lambda i, p: None, egress="none", network_internal=False)
        self.assertEqual(findings["network.internal"]["declared"], True)
        self.assertEqual(findings["network.internal"]["observed"], False)
        self.assertEqual(findings["network.internal"]["severity"], "high")

    def test_network_stricter_than_declared_is_low(self):
        findings = self._drift(lambda i, p: None, egress="open", network_internal=True)
        self.assertEqual(findings["network.internal"]["severity"], "low")

    def test_no_new_privileges_missing(self):
        findings = self._drift(lambda i, p: i["HostConfig"].update(SecurityOpt=None))
        self.assertEqual(findings["noNewPrivileges"]["observed"], False)

    def test_image_mismatch(self):
        findings = self._drift(lambda i, p: i["Config"].update(Image="registry.example.com/other:latest"))
        self.assertEqual(findings["image"]["severity"], "high")

    def test_resource_limits(self):
        def mutate(inspect, plan):
            inspect["HostConfig"].update(Memory=0, NanoCpus=0, PidsLimit=None)

        findings = self._drift(mutate)
        for field in ("memory", "cpus", "pidsLimit"):
            self.assertEqual(findings[field]["severity"], "medium", field)

    def test_privileged_and_host_namespaces(self):
        findings = self._drift(lambda i, p: i["HostConfig"].update(Privileged=True, NetworkMode="host", PidMode="host"))
        for field in ("privileged", "networkMode", "pidMode"):
            self.assertEqual(findings[field]["severity"], "critical", field)

    def test_undeclared_bind_mount(self):
        def mutate(inspect, plan):
            inspect["Mounts"].append({"Type": "bind", "Source": "/etc", "Destination": "/host-etc", "RW": False})

        self.assertIn("mounts.undeclared", self._drift(mutate))

    def test_missing_declared_mount(self):
        findings = self._drift(lambda i, p: i["Mounts"].clear())
        self.assertIn("mounts.missing", findings)

    def test_unexpected_network_attachment(self):
        def mutate(inspect, plan):
            inspect["NetworkSettings"]["Networks"]["bridge"] = {}

        findings = self._drift(mutate)
        self.assertEqual(findings["network.attached"]["observed"], sorted([_network_name(_plan()), "bridge"]))

    def test_findings_have_required_shape(self):
        findings = self._drift(lambda i, p: i["HostConfig"].update(ReadonlyRootfs=False))
        for finding in findings.values():
            self.assertEqual(sorted(finding), ["declared", "field", "observed", "severity"])

    def test_drift_wins_over_unverified_fields(self):
        plan = _plan()
        inspect = _healthy_inspect(plan)
        inspect["HostConfig"]["ReadonlyRootfs"] = False
        result = isolation_drift.check(plan, runner=FakeDocker(inspect=inspect, network_rc=1))
        self.assertEqual(result["status"], "drift")
        self.assertIn("network.internal", result["unverified"])


class TestUnknown(unittest.TestCase):
    def test_docker_not_installed(self):
        def runner(cmd, timeout):
            return -1, "", "docker not found on PATH"

        result = isolation_drift.check(_plan(), runner=runner)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("docker not found", result["reason"])

    def test_inspect_failure_container_missing(self):
        result = isolation_drift.check(_plan(), runner=FakeDocker(inspect_rc=1))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("No such container", result["reason"])

    def test_inspect_timeout(self):
        def runner(cmd, timeout):
            return -2, "", "timed out"

        self.assertEqual(isolation_drift.check(_plan(), runner=runner)["status"], "unknown")

    def test_malformed_inspect_json(self):
        result = isolation_drift.check(_plan(), runner=FakeDocker(inspect_stdout="{not json"))
        self.assertEqual(result["status"], "unknown")

    def test_inspect_wrong_shape(self):
        for body in ("[]", "{}", "[1]", json.dumps([{}, {}]), ""):
            with self.subTest(body=body):
                self.assertEqual(isolation_drift.check(_plan(), runner=FakeDocker(inspect_stdout=body))["status"], "unknown")

    def test_empty_object_is_never_ok(self):
        result = isolation_drift.check(_plan(), runner=FakeDocker(inspect_stdout="[{}]"))
        self.assertNotEqual(result["status"], "ok")

    def test_network_inspect_failure_without_drift_is_unknown(self):
        plan = _plan()
        result = isolation_drift.check(plan, runner=FakeDocker(inspect=_healthy_inspect(plan), network_rc=1))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["unverified"], ["network.internal"])

    def test_network_inspect_malformed_is_unknown(self):
        plan = _plan()
        for body in ("nope", "[]", json.dumps([{"Name": "x"}]), json.dumps([{"Internal": "yes"}])):
            with self.subTest(body=body):
                result = isolation_drift.check(plan, runner=FakeDocker(inspect=_healthy_inspect(plan), network_stdout=body))
                self.assertEqual(result["status"], "unknown")

    def test_stopped_container_is_unknown(self):
        plan = _plan()
        inspect = _healthy_inspect(plan)
        inspect["State"]["Running"] = False
        result = isolation_drift.check(plan, runner=FakeDocker(inspect=inspect))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("not running", result["reason"])

    def test_missing_host_config_is_unknown_not_ok(self):
        plan = _plan()
        inspect = _healthy_inspect(plan)
        del inspect["HostConfig"]
        result = isolation_drift.check(plan, runner=FakeDocker(inspect=inspect))
        self.assertEqual(result["status"], "unknown")
        self.assertIn("capDrop", result["unverified"])


class TestUndeclaredUser(unittest.TestCase):
    def test_undeclared_user_is_skipped_not_a_finding(self):
        data = _manifest()
        del data["spec"]["compose"]["user"]
        plan = compose.build_plan(data)
        inspect = _healthy_inspect(plan)
        inspect["Config"]["User"] = ""
        result = isolation_drift.check(plan, runner=FakeDocker(inspect=inspect))
        self.assertEqual(result["status"], "ok")
        self.assertTrue(any(s.startswith("user:") for s in result["skipped"]))


if __name__ == "__main__":
    unittest.main()
