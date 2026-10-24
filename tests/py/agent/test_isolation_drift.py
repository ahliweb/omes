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
            "CapAdd": None,
            "ReadonlyRootfs": True,
            "SecurityOpt": ["no-new-privileges:true"],
            "Devices": None,
            "IpcMode": "private",
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


class TestHardeningFields(unittest.TestCase):
    """CapAdd, SecurityOpt (seccomp/apparmor/other), Devices and IpcMode
    (issue #276). The renderer declares none of them, so each is compared
    with Docker's default and only an undeclared override is drift."""

    def _check(self, mutate):
        plan = _plan()
        inspect = _healthy_inspect(plan)
        mutate(inspect["HostConfig"])
        return isolation_drift.check(plan, runner=FakeDocker(inspect=inspect))

    def _drift(self, mutate):
        result = self._check(mutate)
        self.assertEqual(result["status"], "drift", result)
        return _fields(result)

    def test_renderer_declares_none_of_these_fields(self):
        # Pins the premise of the comparison: if the renderer ever starts
        # declaring one of these, this check must be extended with it.
        rendered = compose.render_compose_yaml(_plan())
        for key in ("cap_add", "devices:", "ipc:", "privileged", "seccomp", "apparmor", "pid:"):
            self.assertNotIn(key, rendered)
        self.assertIn("no-new-privileges:true", rendered)

    def test_cap_add_empty_list_and_null_are_ok(self):
        for value in (None, []):
            with self.subTest(value=value):
                self.assertEqual(self._check(lambda h: h.update(CapAdd=value))["status"], "ok")

    def test_cap_add_any_capability_is_drift(self):
        findings = self._drift(lambda h: h.update(CapAdd=["CAP_NET_RAW", "chown"]))
        self.assertEqual(findings["capAdd"]["declared"], [])
        self.assertEqual(findings["capAdd"]["observed"], ["CHOWN", "NET_RAW"])
        self.assertEqual(findings["capAdd"]["severity"], "high")

    def test_cap_add_sys_admin_and_all_are_critical(self):
        for caps in (["SYS_ADMIN"], ["ALL"], ["CAP_SYS_ADMIN"]):
            with self.subTest(caps=caps):
                self.assertEqual(self._drift(lambda h: h.update(CapAdd=caps))["capAdd"]["severity"], "critical")

    def test_seccomp_unconfined_is_high(self):
        for opt in ("seccomp=unconfined", "seccomp:unconfined"):
            with self.subTest(opt=opt):
                findings = self._drift(lambda h: h.update(SecurityOpt=["no-new-privileges:true", opt]))
                self.assertEqual(findings["securityOpt.seccomp"]["observed"], "unconfined")
                self.assertEqual(findings["securityOpt.seccomp"]["severity"], "high")

    def test_seccomp_custom_profile_is_medium_and_content_is_not_echoed(self):
        profile = 'seccomp={"defaultAction":"SCMP_ACT_ALLOW","syscalls":[{"names":["x=y"]}]}'
        result = self._check(lambda h: h.update(SecurityOpt=["no-new-privileges:true", profile]))
        self.assertEqual(result["status"], "drift")
        finding = _fields(result)["securityOpt.seccomp"]
        self.assertEqual(finding["observed"], "custom profile")
        self.assertEqual(finding["severity"], "medium")
        self.assertNotIn("SCMP_ACT_ALLOW", json.dumps(result))

    def test_apparmor_unconfined_and_custom(self):
        findings = self._drift(lambda h: h.update(SecurityOpt=["no-new-privileges:true", "apparmor=unconfined"]))
        self.assertEqual((findings["securityOpt.apparmor"]["observed"], findings["securityOpt.apparmor"]["severity"]), ("unconfined", "high"))
        findings = self._drift(lambda h: h.update(SecurityOpt=["no-new-privileges:true", "apparmor=my-profile"]))
        self.assertEqual((findings["securityOpt.apparmor"]["observed"], findings["securityOpt.apparmor"]["severity"]), ("custom profile", "medium"))

    def test_top_level_apparmor_profile_unconfined_is_drift(self):
        plan = _plan()
        inspect = _healthy_inspect(plan)
        inspect["AppArmorProfile"] = "unconfined"
        result = isolation_drift.check(plan, runner=FakeDocker(inspect=inspect))
        self.assertEqual(result["status"], "drift")
        self.assertIn("securityOpt.apparmor", _fields(result))

    def test_default_apparmor_profile_is_not_drift(self):
        plan = _plan()
        inspect = _healthy_inspect(plan)
        inspect["AppArmorProfile"] = "docker-default"
        self.assertEqual(isolation_drift.check(plan, runner=FakeDocker(inspect=inspect))["status"], "ok")

    def test_other_security_options_are_undeclared_drift(self):
        findings = self._drift(lambda h: h.update(SecurityOpt=["no-new-privileges:true", "label=disable", "systempaths=unconfined"]))
        self.assertEqual(findings["securityOpt.undeclared"]["observed"], ["label=disable", "systempaths=unconfined"])
        self.assertEqual(findings["securityOpt.undeclared"]["severity"], "high")

    def test_no_new_privileges_spellings_are_not_undeclared(self):
        for opt in ("no-new-privileges", "no-new-privileges:true", "no-new-privileges=true"):
            with self.subTest(opt=opt):
                self.assertEqual(self._check(lambda h: h.update(SecurityOpt=[opt]))["status"], "ok")

    def test_no_new_privileges_false_is_still_drift(self):
        findings = self._drift(lambda h: h.update(SecurityOpt=["no-new-privileges=false"]))
        self.assertIn("noNewPrivileges", findings)
        self.assertNotIn("securityOpt.undeclared", findings)

    def test_seccomp_and_apparmor_are_reported_as_not_declared(self):
        result = self._check(lambda h: None)
        self.assertEqual(result["status"], "ok")
        notes = result["skipped"]
        self.assertTrue(any(n.startswith("seccomp: not declared") for n in notes), notes)
        self.assertTrue(any(n.startswith("apparmor: not declared") for n in notes), notes)

    def test_devices_empty_and_null_are_ok(self):
        for value in (None, []):
            with self.subTest(value=value):
                self.assertEqual(self._check(lambda h: h.update(Devices=value))["status"], "ok")

    def test_device_mapping_is_drift(self):
        device = {"PathOnHost": "/dev/fuse", "PathInContainer": "/dev/fuse", "CgroupPermissions": "rwm"}
        findings = self._drift(lambda h: h.update(Devices=[device]))
        self.assertEqual(findings["devices"]["declared"], [])
        self.assertEqual(findings["devices"]["observed"], ["/dev/fuse:/dev/fuse:rwm"])
        self.assertEqual(findings["devices"]["severity"], "high")

    def test_ipc_default_spellings_are_ok(self):
        for value in ("private", ""):
            with self.subTest(value=value):
                self.assertEqual(self._check(lambda h: h.update(IpcMode=value))["status"], "ok")

    def test_ipc_modes_by_severity(self):
        cases = {"host": "critical", "container:abc123": "high", "shareable": "low", "none": "low"}
        for mode, severity in cases.items():
            with self.subTest(mode=mode):
                finding = self._drift(lambda h: h.update(IpcMode=mode))["ipcMode"]
                self.assertEqual((finding["declared"], finding["observed"], finding["severity"]), ("private", mode, severity))

    def test_privileged_true_is_critical(self):
        self.assertEqual(self._drift(lambda h: h.update(Privileged=True))["privileged"]["severity"], "critical")

    def test_missing_or_wrongly_typed_fields_are_unknown_never_ok(self):
        cases = {
            "capAdd": [("CapAdd", "SYS_ADMIN"), ("CapAdd", [1])],
            "securityOpt": [("SecurityOpt", "seccomp=unconfined"), ("SecurityOpt", [None])],
            "devices": [("Devices", "/dev/fuse"), ("Devices", [{"PathInContainer": "/x"}]), ("Devices", ["/dev/fuse"])],
            "ipcMode": [("IpcMode", None), ("IpcMode", 7)],
            "privileged": [("Privileged", None), ("Privileged", "false")],
            "networkMode": [("NetworkMode", None), ("NetworkMode", 3)],
            "pidMode": [("PidMode", None), ("PidMode", False)],
            "capDrop": [("CapDrop", "ALL")],
        }
        for field, variants in cases.items():
            for key, value in variants:
                with self.subTest(field=field, key=key, value=value):
                    result = self._check(lambda h: h.update({key: value}))
                    self.assertNotEqual(result["status"], "ok", result)
                    self.assertIn(field, result["unverified"])

    def test_absent_keys_are_unknown_never_ok(self):
        for key, field in (
            ("CapAdd", "capAdd"), ("CapDrop", "capDrop"), ("SecurityOpt", "securityOpt"), ("Devices", "devices"),
            ("IpcMode", "ipcMode"), ("Privileged", "privileged"), ("NetworkMode", "networkMode"), ("PidMode", "pidMode"),
        ):
            with self.subTest(key=key):
                result = self._check(lambda h: h.pop(key))
                self.assertEqual(result["status"], "unknown", result)
                self.assertIn(field, result["unverified"])

    def test_drift_still_wins_over_unverified_new_fields(self):
        result = self._check(lambda h: (h.pop("Devices"), h.update(CapAdd=["NET_ADMIN"])))
        self.assertEqual(result["status"], "drift")
        self.assertIn("devices", result["unverified"])

    def test_unparseable_device_entry_does_not_hide_a_valid_one(self):
        device = {"PathOnHost": "/dev/kvm", "PathInContainer": "/dev/kvm", "CgroupPermissions": "rwm"}
        result = self._check(lambda h: h.update(Devices=[device, "junk"]))
        self.assertEqual(result["status"], "drift")
        self.assertIn("devices", result["unverified"])
        self.assertIn("devices", _fields(result))

    def test_missing_host_config_lists_new_fields_as_unverified(self):
        plan = _plan()
        inspect = _healthy_inspect(plan)
        del inspect["HostConfig"]
        result = isolation_drift.check(plan, runner=FakeDocker(inspect=inspect))
        for field in ("capAdd", "securityOpt", "devices", "ipcMode", "privileged"):
            self.assertIn(field, result["unverified"])

    def test_still_only_two_read_only_commands(self):
        plan = _plan()
        inspect = _healthy_inspect(plan)
        inspect["HostConfig"].update(CapAdd=["NET_RAW"], IpcMode="host")
        fake = FakeDocker(inspect=inspect)
        isolation_drift.check(plan, runner=fake)
        self.assertEqual([c[:2] for c in fake.calls], [["docker", "inspect"], ["docker", "network"]])


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
