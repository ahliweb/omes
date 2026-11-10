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
FIXTURES = OMES_ROOT / "contracts" / "agent" / "v1" / "fixtures" / "agent-deployment"


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

    def test_plan_mounts_opt_data_and_tmpfs(self):
        plan = compose.build_plan(self.manifest)
        self.assertEqual(plan["topology"], "dedicated")
        self.assertTrue(any(v["containerPath"] == "/opt/data" for v in plan["volumes"]))
        rendered = compose.render_compose_yaml(plan)
        self.assertIn("/opt/data:rw", rendered)
        self.assertIn("tmpfs:", rendered)
        self.assertIn("/run:rw,noexec,nosuid,size=64k", rendered)
        self.assertIn("/tmp:rw,noexec,nosuid,size=64k", rendered)

    def test_plan_shared_topology(self):
        data = copy.deepcopy(self.manifest)
        data["spec"]["compose"]["topology"] = "shared"
        del data["spec"]["compose"]["project"]
        plan = compose.build_plan(data)
        self.assertEqual(plan["topology"], "shared")
        self.assertEqual(plan["project"], "omes-shared-hermes")
        self.assertEqual(plan["serviceName"], "hermes")
        self.assertEqual(plan["containerName"], "omes-shared-hermes-hermes")
        self.assertTrue(any(v["containerPath"] == "/opt/data" and "shared-hermes" in v["hostPath"] for v in plan["volumes"]))


class TestTopologyValidation(unittest.TestCase):
    def _spec(self, topology):
        return {
            "image": f"registry.example.com/x@{VALID_DIGEST}",
            "user": "1000:1000",
            "topology": topology,
        }

    def test_topology_dedicated_accepted(self):
        errors = compose.validate_compose_spec(self._spec("dedicated"), "worker")
        self.assertEqual(errors, [])

    def test_topology_shared_accepted(self):
        errors = compose.validate_compose_spec(self._spec("shared"), "worker")
        self.assertEqual(errors, [])

    def test_topology_invalid_rejected(self):
        errors = compose.validate_compose_spec(self._spec("clustered"), "worker")
        self.assertTrue(any("topology" in e for e in errors))


class TestEgressControl(unittest.TestCase):
    """Per-deployment egress mode (issue #276)."""

    def setUp(self):
        self.manifest = _load_fixture("valid-compose-generic.json")
        # The generic fixture publishes a loopback port, which is
        # incompatible with egress "none"; tests that need "none" drop it.
        self.no_ports = copy.deepcopy(self.manifest)
        del self.no_ports["spec"]["compose"]["ports"]

    def test_default_renders_open_network_unchanged(self):
        plan = compose.build_plan(self.manifest)
        self.assertEqual(plan["egress"], "open")
        rendered = compose.render_compose_yaml(plan)
        self.assertIn("    driver: bridge\n    internal: false\n", rendered)
        self.assertNotIn("internal: true", rendered)

    def test_explicit_open_renders_identically_to_default(self):
        explicit = copy.deepcopy(self.manifest)
        explicit["spec"]["compose"]["egress"] = "open"
        self.assertEqual(
            compose.render_compose_yaml(compose.build_plan(explicit)),
            compose.render_compose_yaml(compose.build_plan(self.manifest)),
        )

    def test_none_renders_internal_network(self):
        data = copy.deepcopy(self.no_ports)
        data["spec"]["compose"]["egress"] = "none"
        plan = compose.build_plan(data)
        self.assertEqual(plan["egress"], "none")
        rendered = compose.render_compose_yaml(plan)
        self.assertIn("    driver: bridge\n    internal: true\n", rendered)
        self.assertNotIn("internal: false", rendered)
        self.assertEqual(compose.plan_summary(plan)["egress"], "none")

    def test_none_changes_only_the_internal_flag(self):
        data = copy.deepcopy(self.no_ports)
        open_yaml = compose.render_compose_yaml(compose.build_plan(data))
        data["spec"]["compose"]["egress"] = "none"
        none_yaml = compose.render_compose_yaml(compose.build_plan(data))
        self.assertEqual(open_yaml.replace("internal: false", "internal: true"), none_yaml)

    def test_invalid_egress_rejected_by_schema(self):
        data = copy.deepcopy(self.manifest)
        data["spec"]["compose"]["egress"] = "bogus"
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(any("egress" in e for e in errors), errors)

    def test_invalid_egress_rejected_by_semantic_validation(self):
        errors = compose.validate_compose_spec(
            {"image": f"registry.example.com/x@{VALID_DIGEST}", "user": "1000:1000", "egress": "bogus"}, "worker"
        )
        self.assertTrue(any("spec.compose.egress" in e for e in errors), errors)

    def test_valid_egress_values_accepted(self):
        for value in ("open", "none"):
            errors = compose.validate_compose_spec(
                {"image": f"registry.example.com/x@{VALID_DIGEST}", "user": "1000:1000", "egress": value}, "worker"
            )
            self.assertEqual(errors, [], value)

    def test_none_with_ports_rejected(self):
        data = copy.deepcopy(self.manifest)  # fixture publishes 127.0.0.1:8081:8080
        data["spec"]["compose"]["egress"] = "none"
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(any("cannot be combined with spec.compose.ports" in e for e in errors), errors)

    def test_none_manifest_validates_without_ports(self):
        data = copy.deepcopy(self.no_ports)
        data["spec"]["compose"]["egress"] = "none"
        self.assertEqual(manifest.validate(data, OMES_ROOT), [])

    def test_v2_manifest_egress_none(self):
        v2 = OMES_ROOT / "contracts" / "agent" / "v2" / "fixtures" / "runtime-deployment"
        data = json.loads((v2 / "valid-compose-egress-none.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest.validate(data, OMES_ROOT), [])
        plan = compose.build_plan(data)
        self.assertEqual(plan["egress"], "none")
        self.assertIn("internal: true", compose.render_compose_yaml(plan))

    def test_v2_manifest_unknown_egress_rejected(self):
        v2 = OMES_ROOT / "contracts" / "agent" / "v2" / "fixtures" / "runtime-deployment"
        data = json.loads((v2 / "invalid-compose-egress-unknown.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest.validate(data, OMES_ROOT))

    def test_existing_refusals_unchanged_with_egress_none(self):
        data = copy.deepcopy(self.no_ports)
        data["spec"]["compose"]["egress"] = "none"
        data["spec"]["compose"]["volumes"] = [
            {"hostPath": "/var/run/docker.sock", "containerPath": "/var/run/docker.sock"}
        ]
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(any("Docker socket" in e for e in errors), errors)


PROXY_IMAGE = "registry.example.com/proxy/tinyproxy@sha256:" + "b" * 64


def _allowlist_manifest(hosts=("models.example.org", "api.example.com")) -> dict:
    data = _load_fixture("valid-compose-generic.json")
    c = data["spec"]["compose"]
    c.pop("ports", None)
    c["egress"] = "allowlist"
    c["egressAllowlist"] = list(hosts)
    c["egressProxyImage"] = PROXY_IMAGE
    return data


class TestEgressAllowlist(unittest.TestCase):
    """Opt-in `egress: "allowlist"` rendering and validation (issue #276)."""

    def _errors(self, mutate):
        data = _allowlist_manifest()
        mutate(data["spec"]["compose"])
        return manifest.validate(data, OMES_ROOT)

    def test_valid_manifest(self):
        self.assertEqual(manifest.validate(_allowlist_manifest(), OMES_ROOT), [])

    def test_valid_fixtures_v1_and_v2(self):
        v2 = OMES_ROOT / "contracts" / "agent" / "v2" / "fixtures" / "runtime-deployment"
        for path in (FIXTURES / "valid-compose-egress-allowlist.json", v2 / "valid-compose-egress-allowlist.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(manifest.validate(data, OMES_ROOT), [], path.name)
            rendered = compose.render_compose_yaml(compose.build_plan(data))
            self.assertIn("FilterDefaultDeny Yes", rendered)

    def test_negative_validation(self):
        cases = {
            "wildcard": (lambda c: c.__setitem__("egressAllowlist", ["*.example.com"]), "wildcards"),
            "ipv4": (lambda c: c.__setitem__("egressAllowlist", ["203.0.113.10"]), "IP literals"),
            "ipv6": (lambda c: c.__setitem__("egressAllowlist", ["[::1]"]), "IP literals"),
            "localhost": (lambda c: c.__setitem__("egressAllowlist", ["localhost"]), "single-label"),
            "single-label": (lambda c: c.__setitem__("egressAllowlist", ["intranet"]), "single-label"),
            "dot-local": (lambda c: c.__setitem__("egressAllowlist", ["printer.local"]), ".local"),
            "dot-internal": (lambda c: c.__setitem__("egressAllowlist", ["db.internal"]), ".internal"),
            "uppercase": (lambda c: c.__setitem__("egressAllowlist", ["API.example.com"]), "lowercase"),
            "trailing-dot": (lambda c: c.__setitem__("egressAllowlist", ["api.example.com."]), "RFC 1123"),
            "url": (lambda c: c.__setitem__("egressAllowlist", ["https://api.example.com"]), "hostnames only"),
            "too-long": (lambda c: c.__setitem__("egressAllowlist", [".".join(["a" * 60] * 5) + ".com"]), "253"),
            "empty": (lambda c: c.__setitem__("egressAllowlist", []), "non-empty"),
            "missing-list": (lambda c: c.pop("egressAllowlist"), "non-empty"),
            "duplicate": (lambda c: c.__setitem__("egressAllowlist", ["a.example.com", "a.example.com"]), "duplicate"),
            "ports": (lambda c: c.__setitem__("ports", ["127.0.0.1:8081:8080"]), "cannot be combined with spec.compose.ports"),
            "shared": (lambda c: c.__setitem__("topology", "shared"), "dedicated topology"),
            "missing-image": (lambda c: c.pop("egressProxyImage"), "egressProxyImage"),
            "tag-only-image": (lambda c: c.__setitem__("egressProxyImage", "registry.example.com/p:latest"), "egressProxyImage"),
            "list-without-mode": (lambda c: c.__setitem__("egress", "open"), "only valid when"),
        }
        for label, (mutate, needle) in cases.items():
            with self.subTest(case=label):
                errors = self._errors(mutate)
                self.assertTrue(any(needle in e for e in errors), (label, errors))

    def test_v2_implicit_shared_topology_rejected(self):
        v2 = OMES_ROOT / "contracts" / "agent" / "v2" / "fixtures" / "runtime-deployment"
        data = json.loads((v2 / "valid-compose-egress-allowlist.json").read_text(encoding="utf-8"))
        data["security"] = {"isolationClass": "standard"}
        errors = manifest.validate(data, OMES_ROOT)
        self.assertTrue(any("dedicated topology" in e for e in errors), errors)

    def test_render_proxy_config_pure_and_deterministic(self):
        a = compose.render_proxy_config(["b.example.com", "a-1.example.com"])
        b = compose.render_proxy_config(["a-1.example.com", "b.example.com"])
        self.assertEqual(a, b)
        self.assertIn("FilterDefaultDeny Yes\n", a["tinyproxy.conf"])
        self.assertIn("ConnectPort 443\n", a["tinyproxy.conf"])
        self.assertEqual(a["allowlist.filter"], "^a-1\\.example\\.com$\n^b\\.example\\.com$\n")

    def test_render_proxy_config_fails_closed(self):
        for bad in ([], ["*.example.com"], ["localhost"], ["1.2.3.4"], ["a.example.com\nb.com"]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                compose.render_proxy_config(bad)

    def test_rendered_topology(self):
        plan = compose.build_plan(_allowlist_manifest())
        rendered = compose.render_compose_yaml(plan)
        net = plan["network"]
        egress_net = net + "-egress"
        proxy = plan["egressProxyService"]
        self.assertEqual(plan["egressAllowlist"], ["api.example.com", "models.example.org"])
        # agent service: only the internal network, proxy env, depends_on
        agent_block = rendered.split(f"  {proxy}:")[0]
        self.assertIn(f'    networks:\n      - "{net}"\n', agent_block)
        self.assertNotIn(egress_net, agent_block)
        self.assertIn(f'      HTTPS_PROXY: "http://{proxy}:8888"\n', agent_block)
        self.assertIn(f'      HTTP_PROXY: "http://{proxy}:8888"\n', agent_block)
        self.assertIn('      NO_PROXY: "localhost,127.0.0.1,::1"\n', agent_block)
        self.assertIn(f'    depends_on:\n      - "{proxy}"\n', agent_block)
        # sidecar: both networks, hardened, pinned image, no ports/volumes/env_file
        sidecar = rendered.split(f"  {proxy}:")[1].split("\nnetworks:")[0]
        self.assertIn(f'    image: "{PROXY_IMAGE}"', sidecar)
        self.assertIn(f'      - "{net}"\n      - "{egress_net}"\n', sidecar)
        self.assertIn("    read_only: true\n", sidecar)
        self.assertIn('    cap_drop:\n      - "ALL"\n', sidecar)
        self.assertIn('"no-new-privileges:true"', sidecar)
        for forbidden in ("ports:", "volumes:", "env_file:", "privileged", "docker.sock"):
            self.assertNotIn(forbidden, sidecar)
        # networks: agent network internal, egress network not
        nets = rendered.split("\nnetworks:\n")[1]
        self.assertIn(f'  {net}:\n    driver: bridge\n    internal: true\n', nets)
        self.assertIn(f'  {egress_net}:\n    driver: bridge\n    internal: false\n', nets)
        # inline proxy config
        self.assertIn("    content: |\n      ^api\\.example\\.com$\n      ^models\\.example\\.org$\n", rendered)
        self.assertTrue(compose.network_is_internal(plan))

    def test_sidecar_and_networks_golden(self):
        plan = compose.build_plan(_allowlist_manifest(("api.example.com", "models.example.org")))
        rendered = compose.render_compose_yaml(plan)
        part = rendered[rendered.index(f"  {plan['egressProxyService']}:"):]
        golden = (Path(__file__).parent / "golden" / "compose-egress-allowlist-sidecar.golden").read_text(encoding="utf-8")
        self.assertEqual(part, golden)

    def test_render_is_deterministic_and_order_insensitive(self):
        a = compose.render_compose_yaml(compose.build_plan(_allowlist_manifest(("a.example.com", "b.example.com"))))
        b = compose.render_compose_yaml(compose.build_plan(_allowlist_manifest(("b.example.com", "a.example.com"))))
        self.assertEqual(a, b)
        self.assertEqual(a, compose.render_compose_yaml(compose.build_plan(_allowlist_manifest(("a.example.com", "b.example.com")))))

    def test_no_secret_in_render(self):
        data = _allowlist_manifest()
        plan = compose.build_plan(data)
        rendered = compose.render_compose_yaml(plan)
        # secrets stay name-only, behind env_file on the agent; never in the proxy part
        self.assertIn("env_file:", rendered)
        proxy_part = rendered.split(f"  {plan['egressProxyService']}:")[1]
        for needle in ("provider-primary", ".env", "KEY", "TOKEN", "PASSWORD", "SECRET"):
            self.assertNotIn(needle, proxy_part)
        summary = compose.plan_summary(plan)
        self.assertEqual(summary["egress"], "allowlist")
        self.assertEqual(summary["egressProxyImage"], PROXY_IMAGE)

    def test_open_and_none_render_unchanged(self):
        base = _load_fixture("valid-compose-generic.json")
        plan = compose.build_plan(base)
        rendered = compose.render_compose_yaml(plan)
        for marker in ("-egress", "configs:", "depends_on", "environment:", "tinyproxy"):
            self.assertNotIn(marker, rendered)
        self.assertNotIn("egressAllowlist", compose.plan_summary(plan))
        self.assertNotIn("egressProxyService", plan)


if __name__ == "__main__":
    unittest.main()
