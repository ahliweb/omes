"""tests/py/agent/test_compose.py - rootless Docker Compose isolation
backend validation and rendering (issue #96)."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from agent import compose, manifest  # noqa: E402

OMES_ROOT = Path(_pathfix.OMES_ROOT)
FIXTURES = OMES_ROOT / "contracts" / "agent" / "v1" / "fixtures"


def _load_fixture(name: str) -> dict:
    with open(FIXTURES / name, "r", encoding="utf-8") as fh:
        return json.load(fh)


VALID_DIGEST = "sha256:" + "a" * 64


class TestImageValidation(unittest.TestCase):
    def test_digest_pinned_image_accepted(self):
        errors = compose.validate_compose_spec(
            {"image": f"registry.example.com/x@{VALID_DIGEST}", "user": "1000:1000"}, "worker"
        )
        self.assertEqual(errors, [])

    def test_tag_only_image_rejected(self):
        errors = compose.validate_compose_spec({"image": "registry.example.com/x:latest"}, "worker")
        self.assertTrue(any("spec.compose.image" in e for e in errors))

    def test_missing_image_rejected(self):
        errors = compose.validate_compose_spec({}, "worker")
        self.assertTrue(any("spec.compose.image" in e for e in errors))

    def test_short_digest_rejected(self):
        errors = compose.validate_compose_spec(
            {"image": "registry.example.com/x@sha256:abc123"}, "worker"
        )
        self.assertTrue(errors)


class TestUserValidation(unittest.TestCase):
    def _spec(self, user):
        return {"image": f"registry.example.com/x@{VALID_DIGEST}", "user": user}

    def test_root_uid_rejected(self):
        errors = compose.validate_compose_spec(self._spec("0:1000"), "worker")
        self.assertTrue(any("uid and gid" in e for e in errors))

    def test_root_gid_rejected(self):
        errors = compose.validate_compose_spec(self._spec("1000:0"), "worker")
        self.assertTrue(any("uid and gid" in e for e in errors))

    def test_non_root_user_accepted(self):
        errors = compose.validate_compose_spec(self._spec("1000:1000"), "worker")
        self.assertEqual(errors, [])

    def test_malformed_user_rejected(self):
        errors = compose.validate_compose_spec(self._spec("not-a-uid"), "worker")
        self.assertTrue(errors)


class TestCapDropValidation(unittest.TestCase):
    def _spec(self, cap_drop):
        return {"image": f"registry.example.com/x@{VALID_DIGEST}", "user": "1000:1000", "capDrop": cap_drop}

    def test_default_omitted_is_fine(self):
        errors = compose.validate_compose_spec({"image": f"registry.example.com/x@{VALID_DIGEST}", "user": "1000:1000"}, "worker")
        self.assertEqual(errors, [])

    def test_all_accepted(self):
        errors = compose.validate_compose_spec(self._spec(["ALL"]), "worker")
        self.assertEqual(errors, [])

    def test_partial_cap_drop_rejected(self):
        errors = compose.validate_compose_spec(self._spec(["NET_ADMIN"]), "worker")
        self.assertTrue(errors)

    def test_empty_cap_drop_rejected(self):
        errors = compose.validate_compose_spec(self._spec([]), "worker")
        self.assertTrue(errors)


class TestNetworkValidation(unittest.TestCase):
    def _spec(self, network):
        return {"image": f"registry.example.com/x@{VALID_DIGEST}", "user": "1000:1000", "network": network}

    def test_host_network_rejected(self):
        errors = compose.validate_compose_spec(self._spec("host"), "worker")
        self.assertTrue(any("host/none" in e for e in errors))

    def test_none_network_rejected(self):
        errors = compose.validate_compose_spec(self._spec("none"), "worker")
        self.assertTrue(errors)

    def test_custom_network_accepted(self):
        errors = compose.validate_compose_spec(self._spec("omes-agent-worker-net"), "worker")
        self.assertEqual(errors, [])


class TestPortValidation(unittest.TestCase):
    def _spec(self, ports):
        return {"image": f"registry.example.com/x@{VALID_DIGEST}", "user": "1000:1000", "ports": ports}

    def test_loopback_bind_accepted(self):
        errors = compose.validate_compose_spec(self._spec(["127.0.0.1:8080:8080"]), "worker")
        self.assertEqual(errors, [])

    def test_wildcard_bind_rejected(self):
        errors = compose.validate_compose_spec(self._spec(["0.0.0.0:8080:8080"]), "worker")
        self.assertTrue(errors)

    def test_no_host_binding_rejected(self):
        errors = compose.validate_compose_spec(self._spec(["8080:8080"]), "worker")
        self.assertTrue(errors)


class TestVolumeValidation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["OMES_STATE_DIR"] = self._tmp.name
        self.addCleanup(lambda: os.environ.pop("OMES_STATE_DIR", None))
        self.agent_root = str(Path(self._tmp.name) / "agents" / "worker")

    def _spec(self, volumes):
        return {
            "image": f"registry.example.com/x@{VALID_DIGEST}",
            "user": "1000:1000",
            "volumes": volumes,
        }

    def test_volume_under_agent_state_dir_accepted(self):
        errors = compose.validate_compose_spec(
            self._spec([{"hostPath": f"{self.agent_root}/data", "containerPath": "/data"}]), "worker"
        )
        self.assertEqual(errors, [])

    def test_volume_outside_agent_state_dir_rejected(self):
        errors = compose.validate_compose_spec(
            self._spec([{"hostPath": "/home/someone/other", "containerPath": "/data"}]), "worker"
        )
        self.assertTrue(any("own state directory" in e for e in errors))

    def test_another_agents_state_dir_rejected(self):
        other_root = str(Path(self._tmp.name) / "agents" / "other-agent")
        errors = compose.validate_compose_spec(
            self._spec([{"hostPath": f"{other_root}/data", "containerPath": "/data"}]), "worker"
        )
        self.assertTrue(errors)

    def test_docker_socket_rejected(self):
        errors = compose.validate_compose_spec(
            self._spec([{"hostPath": "/var/run/docker.sock", "containerPath": "/var/run/docker.sock"}]),
            "worker",
        )
        self.assertTrue(any("Docker socket" in e for e in errors))

    def test_traversal_rejected(self):
        errors = compose.validate_compose_spec(
            self._spec([{"hostPath": f"{self.agent_root}/../other", "containerPath": "/data"}]), "worker"
        )
        self.assertTrue(errors)

    def test_colon_in_host_path_rejected(self):
        errors = compose.validate_compose_spec(
            self._spec([{"hostPath": f"{self.agent_root}/data:Z", "containerPath": "/data"}]), "worker"
        )
        self.assertTrue(any("must not contain ':'" in e for e in errors))

    def test_colon_in_container_path_rejected(self):
        errors = compose.validate_compose_spec(
            self._spec([{"hostPath": f"{self.agent_root}/data", "containerPath": "/data:Z"}]), "worker"
        )
        self.assertTrue(errors)

    def test_relative_host_path_rejected(self):
        errors = compose.validate_compose_spec(
            self._spec([{"hostPath": "relative/path", "containerPath": "/data"}]), "worker"
        )
        self.assertTrue(errors)


class TestManifestIntegration(unittest.TestCase):
    def setUp(self):
        self.base = _load_fixture("valid-compose-generic.json")

    def test_valid_compose_manifest_passes(self):
        errors = manifest.validate(copy.deepcopy(self.base), OMES_ROOT)
        self.assertEqual(errors, [])

    def test_compose_backend_without_compose_object_rejected(self):
        data = copy.deepcopy(self.base)
        del data["spec"]["compose"]
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(any("spec.compose" in e for e in errors))

    def test_systemd_backend_with_compose_object_rejected(self):
        data = copy.deepcopy(self.base)
        data["spec"]["backend"] = "systemd"
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(any("spec.compose" in e for e in errors))

    def test_tag_only_image_rejected_through_manifest(self):
        data = copy.deepcopy(self.base)
        data["spec"]["compose"]["image"] = "registry.example.com/agents/compose-worker:latest"
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(any("spec.compose.image" in e for e in errors))


class TestPlanAndRendering(unittest.TestCase):
    def setUp(self):
        self.manifest = _load_fixture("valid-compose-generic.json")

    def test_plan_defaults_project_and_network(self):
        data = copy.deepcopy(self.manifest)
        del data["spec"]["compose"]["project"]
        del data["spec"]["compose"]["network"]
        plan = compose.build_plan(data)
        self.assertEqual(plan["project"], "omes-agent-compose-worker")
        self.assertEqual(plan["network"], "omes-agent-compose-worker-net")

    def test_plan_never_contains_secret_value(self):
        plan = compose.build_plan(self.manifest)
        rendered = json.dumps(plan)
        self.assertNotIn("provider-primary-value", rendered)
        self.assertIn("environmentFileReference", plan)
        self.assertTrue(plan["environmentFileReference"].endswith(".env"))

    def test_rendering_is_deterministic(self):
        plan = compose.build_plan(self.manifest)
        first = compose.render_compose_yaml(plan)
        second = compose.render_compose_yaml(plan)
        self.assertEqual(first, second)

    def test_rendering_contains_expected_fields(self):
        plan = compose.build_plan(self.manifest)
        rendered = compose.render_compose_yaml(plan)
        self.assertIn("image:", rendered)
        self.assertIn("sha256:aaaa", rendered)
        self.assertIn("read_only: true", rendered)
        self.assertIn("cap_drop:", rendered)
        self.assertIn("- \"ALL\"", rendered)
        self.assertIn("no-new-privileges:true", rendered)
        self.assertIn("mem_limit:", rendered)
        self.assertIn("pids_limit: 64", rendered)
        self.assertIn("127.0.0.1:8081:8080", rendered)
        self.assertNotIn("environment:", rendered)

    def test_rendering_never_inlines_secret_values(self):
        plan = compose.build_plan(self.manifest)
        rendered = compose.render_compose_yaml(plan)
        self.assertIn("env_file:", rendered)
        self.assertNotIn("provider-primary", rendered)

    def test_memory_conversion(self):
        self.assertEqual(compose._memory_to_compose("512M"), "512m")
        self.assertEqual(compose._memory_to_compose("1G"), "1g")
        self.assertEqual(compose._memory_to_compose("2048K"), "2048k")


if __name__ == "__main__":
    unittest.main()
