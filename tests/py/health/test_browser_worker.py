"""tests/py/health/test_browser_worker.py - unit tests for
lib/omes/py/health/browser_worker.py (issue #298): read-only host-readiness
evidence for an optional browser worker, validated against
contracts/browser-transfer/v1/browser-worker-readiness.schema.json (#296)
with the repository's own stdlib validator.

No real browser, Ollama, `ss`, `ps`, Hermes or network is used: every side
effect goes through the module's `Deps` bundle, and the two filesystem-
shaped probes (profile root, heartbeat) run against a throw-away temp tree.
The only sockets opened are the loopback stdlib fake-Ollama servers the #71
suite already uses.
"""

from __future__ import annotations

import argparse
import builtins
import contextlib
import datetime
import importlib.util
import json
import os
import pathlib
import re
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
HEALTH_DIR = os.path.join(ROOT, "lib", "omes", "py", "health")
SCHEMA_PATH = os.path.join(ROOT, "contracts", "browser-transfer", "v1", "browser-worker-readiness.schema.json")
DOC_PATH = os.path.join(ROOT, "docs", "browser-worker-readiness.md")


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HEALTH_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


bw = _load("omes_health_browser_worker_test", "browser_worker.py")
exposure = bw.exposure
ollama = bw.ollama
schema_mod = bw.schema_mod

NOW = datetime.datetime(2026, 10, 14, 12, 0, 0, tzinfo=datetime.timezone.utc)
NOW_ISO = "2026-10-14T12:00:00Z"
SS_WILDCARD_CDP = 'tcp   LISTEN 0      128    0.0.0.0:9222          0.0.0.0:*     users:(("chrome",pid=3,fd=4))\n'
SS_LAN_CDP = 'tcp   LISTEN 0      128    192.168.1.5:9222      0.0.0.0:*     users:(("chrome",pid=3,fd=4))\n'
SS_V6_WILDCARD_CDP = 'tcp   LISTEN 0      128    [::]:9223             [::]:*        users:(("node",pid=4,fd=5))\n'
SS_WILDCARD_MCP = 'tcp   LISTEN 0      128    0.0.0.0:8931          0.0.0.0:*     users:(("node",pid=5,fd=6))\n'
SS_WILDCARD_OLLAMA = 'tcp   LISTEN 0      128    0.0.0.0:11434         0.0.0.0:*     users:(("ollama",pid=6,fd=3))\n'
SS_LOOPBACK_ALL = (
    'tcp   LISTEN 0      128    127.0.0.1:9222        0.0.0.0:*     users:(("chrome",pid=3,fd=4))\n'
    'tcp   LISTEN 0      128    127.0.0.1:8931        0.0.0.0:*     users:(("node",pid=5,fd=6))\n'
    'tcp   LISTEN 0      128    127.0.0.1:11434       0.0.0.0:*     users:(("ollama",pid=6,fd=3))\n'
)
SS_HERMES_GATEWAY_WILDCARD = 'tcp   LISTEN 0      128    0.0.0.0:8642          0.0.0.0:*     users:(("hermes",pid=7,fd=10))\n'


def listeners_from(text, collected=True):
    return {"listeners": exposure.parse_ss(text), "collected": collected, "tool_missing": False}


def schema_errors(document):
    schema = schema_mod.load_json(pathlib.Path(SCHEMA_PATH))
    return list(schema_mod.validate(document, schema))


def set_mtime(path, when):
    ts = when.timestamp()
    os.utime(path, (ts, ts), follow_symlinks=False)


class World:
    """A fully configured, healthy worker host. Tests mutate one aspect and
    assert the resulting reason code / state."""

    def __init__(self, test: unittest.TestCase):
        self.test = test
        self.tmp = tempfile.mkdtemp(prefix="omes-bw-test.")
        test.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.profile_root = os.path.join(self.tmp, "profiles")
        os.mkdir(self.profile_root, 0o700)
        os.chmod(self.profile_root, 0o700)
        self.heartbeat = os.path.join(self.tmp, "heartbeat")
        pathlib.Path(self.heartbeat).write_text("", encoding="utf-8")
        os.chmod(self.heartbeat, 0o600)
        set_mtime(self.heartbeat, NOW - datetime.timedelta(seconds=10))
        self.browser = os.path.join(self.tmp, "bin-chromium")
        pathlib.Path(self.browser).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        os.chmod(self.browser, 0o755)

        self.env = {
            "HOME": self.home,
            "OMES_JOBS_TENANT_ID": "tenant-fixture",
            "OMES_JOBS_SERVER_ID": "server-fixture",
            "OMES_BROWSER_WORKER_ID": "worker-fixture",
            "OMES_BROWSER_WORKER_CAPABILITY_PROFILE": "ai_assisted",
            "OMES_OLLAMA_MODEL": "qwen3.5:9b",
            "OMES_BROWSER_WORKER_ADAPTER_VERSION": "adapter-v1",
            "OMES_BROWSER_WORKER_BROWSER_BIN": self.browser,
            "OMES_BROWSER_WORKER_BROWSER_PIN": "chromium-122.0.6261.94",
            "OMES_BROWSER_WORKER_PROFILE_ROOT": self.profile_root,
            "OMES_BROWSER_WORKER_HEARTBEAT_FILE": self.heartbeat,
        }
        self.facts = {"platform": {"supported": True, "tier": "tier1"}}
        self.args = argparse.Namespace(worker_id=None, capability_profile=None, model=None)

        deps = bw.Deps()
        deps.now = lambda: NOW
        deps.listeners = lambda: listeners_from("")
        deps.processes = lambda: {"collected": True, "rows": []}
        deps.resources = lambda cfg: {"disk_free_mb": 50000, "mem_available_mb": 16000, "cpus": 8}
        deps.browser_version = lambda binary: "Chromium 122.0.6261.94 unofficial"
        deps.which = lambda name: None
        deps.ollama_probe = lambda model: {"ready": True}
        deps.destination_class = lambda: "local"
        deps.hermes_version = lambda: "Hermes Agent v0.21.5 (2026.9.24)"
        # The default isolation provider is `unknown` (nothing integrated);
        # a healthy world injects the future #276 evidence explicitly.
        deps.isolation = lambda cfg: {"session_isolation": "per_job", "codes": []}
        deps.uid = lambda: os.geteuid()
        self.deps = deps

    def collect(self):
        cfg = bw.load_config(self.args, self.env)
        return bw.collect(cfg, self.deps, self.facts)


class TestContractShape(unittest.TestCase):
    def test_patterns_match_the_published_schema(self):
        schema = schema_mod.load_json(pathlib.Path(SCHEMA_PATH))
        props = schema["properties"]
        self.assertEqual(bw.ID_PATTERN.pattern, props["tenant_id"]["pattern"])
        self.assertEqual(bw.ID_PATTERN.pattern, props["server_id"]["pattern"])
        self.assertEqual(bw.ID_PATTERN.pattern, props["worker_id"]["pattern"])
        self.assertEqual(bw.VERSION_PATTERN.pattern, props["adapter_version"]["pattern"])
        self.assertEqual(bw.VERSION_PATTERN.pattern, props["browser_version"]["pattern"])
        self.assertEqual(bw.VERSION_PATTERN.pattern, props["hermes_version"]["pattern"])
        self.assertEqual(bw.MODEL_ID_PATTERN.pattern, props["model_id"]["pattern"])
        self.assertEqual(bw.SCHEMA_VERSION, props["schema_version"]["const"])

    def test_every_reason_code_matches_the_schema_pattern_and_is_registered(self):
        pattern = re.compile(schema_mod.load_json(pathlib.Path(SCHEMA_PATH))["properties"]["reason_codes"]["items"]["pattern"])
        for code in list(bw.REASONS) + [bw.READY_CODE]:
            self.assertRegex(code, pattern)
        for code, (severity, meaning, remediation) in bw.REASONS.items():
            self.assertIn(severity, ("unsupported", "blocked", "unknown", "degraded"), code)
            self.assertTrue(meaning and remediation, code)

    def test_document_emits_only_contract_fields(self):
        world = World(self)
        document, _ = world.collect()
        allowed = set(schema_mod.load_json(pathlib.Path(SCHEMA_PATH))["properties"])
        self.assertLessEqual(set(document), allowed)


class TestReadyPath(unittest.TestCase):
    def test_fully_healthy_worker_is_ready_and_valid(self):
        world = World(self)
        document, _ = world.collect()
        self.assertEqual(schema_errors(document), [])
        self.assertEqual(document["state"], "ready")
        self.assertEqual(document["reason_codes"], [bw.READY_CODE])
        self.assertEqual(document["observed_at"], NOW_ISO)
        self.assertEqual(document["tenant_id"], "tenant-fixture")
        self.assertEqual(document["server_id"], "server-fixture")
        self.assertEqual(document["worker_id"], "worker-fixture")
        self.assertEqual(document["browser_engine"], "chromium")
        self.assertEqual(document["browser_version"], "chromium-122.0.6261.94")
        self.assertEqual(document["adapter_version"], "adapter-v1")
        self.assertEqual(document["model_provider"], "ollama")
        self.assertEqual(document["model_id"], "qwen3.5:9b")
        self.assertEqual(document["model_ready"], "ready")
        self.assertEqual(document["session_isolation"], "per_job")
        self.assertEqual(document["exposure"], "loopback")
        self.assertEqual(document["capability_profile"], "ai_assisted")
        self.assertEqual(document["hermes_version"], "0.21.5")

    def test_deterministic_profile_needs_no_model(self):
        world = World(self)
        world.env["OMES_BROWSER_WORKER_CAPABILITY_PROFILE"] = "deterministic"
        del world.env["OMES_OLLAMA_MODEL"]
        world.deps.ollama_probe = mock.Mock(side_effect=AssertionError("the model must not be probed"))
        world.deps.destination_class = mock.Mock(side_effect=AssertionError("the destination must not be probed"))
        document, _ = world.collect()
        self.assertEqual(schema_errors(document), [])
        self.assertEqual(document["state"], "ready")
        self.assertEqual(document["model_provider"], "none")
        self.assertEqual(document["model_ready"], "not_required")
        self.assertNotIn("model_id", document)

    def test_default_isolation_provider_can_never_produce_ready(self):
        """The production isolation source is `unknown` until #276 is
        integrated: even a fully healthy host must not reach `ready`."""
        world = World(self)
        world.deps.isolation = bw.default_isolation
        document, _ = world.collect()
        self.assertEqual(schema_errors(document), [])
        self.assertEqual(document["state"], "unknown")
        self.assertEqual(document["session_isolation"], "unknown")
        self.assertEqual(document["reason_codes"], ["ISOLATION_EVIDENCE_NOT_INTEGRATED"])
        self.assertNotEqual(document["state"], "ready")


class TestStatePrecedence(unittest.TestCase):
    """unsupported > blocked > unknown > degraded > ready."""

    BLOCKED = "BROWSER_PIN_MISMATCH"
    UNKNOWN = "HEARTBEAT_NOT_CONFIGURED"
    DEGRADED = "RESOURCE_DISK_LOW"

    def test_no_codes_is_ready(self):
        self.assertEqual(bw.decide_state([]), "ready")

    def test_degraded_alone(self):
        self.assertEqual(bw.decide_state([self.DEGRADED]), "degraded")

    def test_unknown_outranks_degraded(self):
        self.assertEqual(bw.decide_state([self.DEGRADED, self.UNKNOWN]), "unknown")

    def test_blocked_outranks_unknown_and_degraded(self):
        self.assertEqual(bw.decide_state([self.DEGRADED, self.UNKNOWN, self.BLOCKED]), "blocked")

    def test_unsupported_outranks_everything(self):
        self.assertEqual(bw.decide_state([self.BLOCKED, "PLATFORM_UNSUPPORTED", self.UNKNOWN]), "unsupported")

    def test_every_severity_pair_resolves_to_the_higher_rank(self):
        rank = ["unsupported", "blocked", "unknown", "degraded"]
        by_severity = {}
        for code, (severity, _m, _r) in bw.REASONS.items():
            by_severity.setdefault(severity, code)
        for i, high in enumerate(rank):
            for low in rank[i + 1:]:
                self.assertEqual(bw.decide_state([by_severity[low], by_severity[high]]), high)

    def test_unregistered_code_is_an_error_not_a_pass(self):
        with self.assertRaises(ValueError):
            bw.decide_state(["MADE_UP_CODE"])

    def test_documents_report_the_precedence_winner(self):
        world = World(self)
        world.deps.resources = lambda cfg: {"disk_free_mb": 1, "mem_available_mb": 16000, "cpus": 8}  # degraded
        document, _ = world.collect()
        self.assertEqual(document["state"], "degraded")
        del world.env["OMES_BROWSER_WORKER_HEARTBEAT_FILE"]  # + unknown
        document, _ = world.collect()
        self.assertEqual(document["state"], "unknown")
        world.deps.listeners = lambda: listeners_from(SS_WILDCARD_CDP)  # + blocked
        document, _ = world.collect()
        self.assertEqual(document["state"], "blocked")
        self.assertEqual(schema_errors(document), [])

    def test_reason_codes_are_sorted_most_severe_first_and_capped(self):
        ordered = bw._ordered_codes(["RESOURCE_DISK_LOW", "HEARTBEAT_NOT_CONFIGURED", "BROWSER_PIN_MISMATCH", "RESOURCE_CPU_LOW"])
        self.assertEqual(ordered, ["BROWSER_PIN_MISMATCH", "HEARTBEAT_NOT_CONFIGURED", "RESOURCE_CPU_LOW", "RESOURCE_DISK_LOW"])
        every = bw._ordered_codes(list(bw.REASONS))
        self.assertLessEqual(len(every), 20)
        # A blocked code can never be the one truncated away.
        severities = [bw.REASONS[c][0] for c in every]
        self.assertEqual(severities, sorted(severities, key=lambda s: bw._SEVERITY_RANK[s]))


def _s_platform_unsupported(w, stack):
    w.facts = {"platform": {"supported": False, "tier": "unsupported"}}
    for name in ("listeners", "processes", "ollama_probe", "destination_class", "hermes_version"):
        setattr(w.deps, name, mock.Mock(side_effect=AssertionError(f"{name} must not run on an unsupported platform")))


def _s_host_facts_unavailable(w, stack):
    w.facts = {}


def _drop_env(name):
    def mutate(w, stack):
        del w.env[name]
    return mutate


def _set_env(**kv):
    def mutate(w, stack):
        w.env.update(kv)
    return mutate


def _s_browser_missing(w, stack):
    w.env["OMES_BROWSER_WORKER_BROWSER_BIN"] = os.path.join(w.tmp, "no-such-browser")
    # An explicit declaration is authoritative: PATH must not rescue it.
    w.deps.which = lambda name: "/usr/bin/chromium"


def _s_browser_version_unknown(w, stack):
    w.deps.browser_version = lambda binary: None


def _s_listeners(text, collected=True):
    def mutate(w, stack):
        w.deps.listeners = lambda: listeners_from(text, collected)
    return mutate


def _s_model_unavailable(w, stack):
    w.deps.ollama_probe = lambda model: {"ready": False}


def _s_model_probe_error(w, stack):
    w.deps.ollama_probe = mock.Mock(side_effect=RuntimeError("boom"))


def _s_destination(value):
    def mutate(w, stack):
        w.deps.destination_class = lambda: value
    return mutate


def _s_isolation_default(w, stack):
    w.deps.isolation = bw.default_isolation


def _s_isolation_not_configured(w, stack):
    w.deps.isolation = lambda cfg: {"session_isolation": "not_configured", "codes": []}


def _s_resources(**kv):
    def mutate(w, stack):
        base = {"disk_free_mb": 50000, "mem_available_mb": 16000, "cpus": 8}
        base.update(kv)
        w.deps.resources = lambda cfg: base
    return mutate


def _s_root_missing(w, stack):
    w.env["OMES_BROWSER_WORKER_PROFILE_ROOT"] = os.path.join(w.tmp, "no-such-profiles")


def _s_root_unsafe(w, stack):
    os.chmod(w.profile_root, 0o755)


def _s_orphan(w, stack):
    argv = ["/opt/pw/chrome-linux/chrome", "--headless=new", "--remote-debugging-pipe", f"--user-data-dir={w.profile_root}/job-1"]
    w.deps.processes = lambda: {"collected": True, "rows": [{"pid": 42, "age": 99999, "argv": argv}]}


def _s_stale_profile(w, stack):
    stale = os.path.join(w.profile_root, "job-old")
    os.mkdir(stale, 0o700)
    set_mtime(stale, NOW - datetime.timedelta(hours=5))


def _s_process_table_unavailable(w, stack):
    w.deps.processes = lambda: {"collected": False, "rows": []}


def _s_heartbeat_missing(w, stack):
    w.env["OMES_BROWSER_WORKER_HEARTBEAT_FILE"] = os.path.join(w.tmp, "no-heartbeat")


def _s_heartbeat_stale(w, stack):
    set_mtime(w.heartbeat, NOW - datetime.timedelta(seconds=600))


def _s_heartbeat_invalid(w, stack):
    os.chmod(w.heartbeat, 0o666)


def _s_invariant_violated(w, stack):
    stack.enter_context(
        mock.patch.object(bw, "probe_browser", return_value=bw._result("browser", [], {"browser_engine": "unknown", "browser_version": "x"}))
    )


#: code -> (mutator, expected overall state). Adding a code to bw.REASONS
#: without a scenario here fails test_every_reason_code_has_a_scenario.
SCENARIOS = {
    "PLATFORM_UNSUPPORTED": (_s_platform_unsupported, "unsupported"),
    "HOST_FACTS_UNAVAILABLE": (_s_host_facts_unavailable, "unknown"),
    "CAPABILITY_PROFILE_UNDECLARED": (_drop_env("OMES_BROWSER_WORKER_CAPABILITY_PROFILE"), "unknown"),
    "ADAPTER_VERSION_UNDECLARED": (_drop_env("OMES_BROWSER_WORKER_ADAPTER_VERSION"), "unknown"),
    "BROWSER_PIN_UNDECLARED": (_drop_env("OMES_BROWSER_WORKER_BROWSER_PIN"), "unknown"),
    "BROWSER_BINARY_MISSING": (_s_browser_missing, "blocked"),
    "BROWSER_VERSION_UNKNOWN": (_s_browser_version_unknown, "unknown"),
    "BROWSER_PIN_MISMATCH": (_set_env(OMES_BROWSER_WORKER_BROWSER_PIN="chromium-1.0.0"), "blocked"),
    "CDP_LISTENER_NONLOOPBACK": (_s_listeners(SS_WILDCARD_CDP), "blocked"),
    "MCP_LISTENER_NONLOOPBACK": (_s_listeners(SS_WILDCARD_MCP), "blocked"),
    "OLLAMA_LISTENER_NONLOOPBACK": (_s_listeners(SS_WILDCARD_OLLAMA), "blocked"),
    "EXPOSURE_AUDIT_UNAVAILABLE": (_s_listeners("", collected=False), "unknown"),
    "MODEL_NOT_CONFIGURED": (_drop_env("OMES_OLLAMA_MODEL"), "unknown"),
    "MODEL_UNAVAILABLE": (_s_model_unavailable, "degraded"),
    "MODEL_PROBE_ERROR": (_s_model_probe_error, "unknown"),
    "EGRESS_MODEL_DESTINATION_CLOUD": (_s_destination("cloud"), "blocked"),
    "EGRESS_MODEL_DESTINATION_UNKNOWN": (_s_destination("unknown"), "unknown"),
    "ISOLATION_EVIDENCE_NOT_INTEGRATED": (_s_isolation_default, "unknown"),
    "ISOLATION_NOT_CONFIGURED": (_s_isolation_not_configured, "blocked"),
    "RESOURCE_DISK_LOW": (_s_resources(disk_free_mb=10), "degraded"),
    "RESOURCE_MEMORY_LOW": (_s_resources(mem_available_mb=10), "degraded"),
    "RESOURCE_CPU_LOW": (_s_resources(cpus=1), "degraded"),
    "RESOURCE_FACTS_UNAVAILABLE": (_s_resources(mem_available_mb=None), "unknown"),
    "PROFILE_ROOT_NOT_CONFIGURED": (_drop_env("OMES_BROWSER_WORKER_PROFILE_ROOT"), "unknown"),
    "PROFILE_ROOT_MISSING": (_s_root_missing, "degraded"),
    "PROFILE_ROOT_UNSAFE": (_s_root_unsafe, "blocked"),
    "ORPHAN_BROWSER_PROCESS": (_s_orphan, "blocked"),
    "STALE_PROFILE_DIRECTORY": (_s_stale_profile, "blocked"),
    "PROCESS_TABLE_UNAVAILABLE": (_s_process_table_unavailable, "unknown"),
    "HEARTBEAT_NOT_CONFIGURED": (_drop_env("OMES_BROWSER_WORKER_HEARTBEAT_FILE"), "unknown"),
    "HEARTBEAT_MISSING": (_s_heartbeat_missing, "degraded"),
    "HEARTBEAT_STALE": (_s_heartbeat_stale, "degraded"),
    "HEARTBEAT_INVALID": (_s_heartbeat_invalid, "unknown"),
    "READINESS_INVARIANT_VIOLATED": (_s_invariant_violated, "unknown"),
}


class TestReasonCodes(unittest.TestCase):
    def test_every_reason_code_has_a_scenario(self):
        self.assertEqual(set(SCENARIOS), set(bw.REASONS))

    def test_scenario_state_matches_the_registered_severity(self):
        for code, (_mutator, expected_state) in SCENARIOS.items():
            severity = bw.REASONS[code][0]
            if code == "READINESS_INVARIANT_VIOLATED":
                self.assertEqual(expected_state, severity)
            else:
                self.assertEqual(expected_state, severity, code)

    def test_each_reason_code_is_produced_and_every_document_validates(self):
        for code, (mutator, expected_state) in SCENARIOS.items():
            with self.subTest(code=code):
                world = World(self)
                with contextlib.ExitStack() as stack:
                    mutator(world, stack)
                    document, _results = world.collect()
                self.assertIn(code, document["reason_codes"])
                self.assertEqual(document["state"], expected_state)
                self.assertNotEqual(document["state"], "ready")
                self.assertNotIn(bw.READY_CODE, document["reason_codes"])
                self.assertEqual(schema_errors(document), [])


class TestNotConfigured(unittest.TestCase):
    def _run(self, env, argv=None):
        return bw.run(argv or [], env=env, deps=World(self).deps, facts={"platform": {"supported": True}})

    def test_missing_identity_is_not_configured_with_no_stdout_and_no_fabricated_id(self):
        base = World(self).env
        for missing in ("OMES_JOBS_TENANT_ID", "OMES_JOBS_SERVER_ID", "OMES_BROWSER_WORKER_ID"):
            with self.subTest(missing=missing):
                env = dict(base)
                del env[missing]
                code, out, err = self._run(env)
                self.assertEqual(code, bw.EXIT_NOT_CONFIGURED)
                self.assertEqual(out, "")
                self.assertIn("not configured", err)
                self.assertIn(missing, err)

    def test_empty_values_count_as_missing(self):
        env = dict(World(self).env)
        env["OMES_JOBS_TENANT_ID"] = "   "
        code, out, _err = self._run(env)
        self.assertEqual((code, out), (bw.EXIT_NOT_CONFIGURED, ""))

    def test_all_three_missing_are_all_named(self):
        code, out, err = self._run({"HOME": "/nonexistent"})
        self.assertEqual((code, out), (bw.EXIT_NOT_CONFIGURED, ""))
        for name in ("OMES_JOBS_TENANT_ID", "OMES_JOBS_SERVER_ID", "OMES_BROWSER_WORKER_ID"):
            self.assertIn(name, err)

    def test_worker_id_flag_satisfies_the_worker_id(self):
        env = dict(World(self).env)
        del env["OMES_BROWSER_WORKER_ID"]
        code, out, _err = self._run(env, ["--worker-id", "worker-from-flag"])
        self.assertIn(code, (bw.EXIT_READY, bw.EXIT_NOT_READY))
        self.assertEqual(json.loads(out)["worker_id"], "worker-from-flag")

    def test_invalid_identity_is_a_usage_error_not_a_sanitised_guess(self):
        env = dict(World(self).env)
        env["OMES_JOBS_TENANT_ID"] = "tenant with spaces"
        code, out, err = self._run(env)
        self.assertEqual((code, out), (bw.EXIT_USAGE, ""))
        self.assertIn("OMES_JOBS_TENANT_ID", err)

    def test_invalid_configuration_values_are_usage_errors(self):
        bad = {
            "OMES_BROWSER_WORKER_CAPABILITY_PROFILE": "autonomous",
            "OMES_BROWSER_WORKER_ADAPTER_VERSION": "bad version!",
            "OMES_BROWSER_WORKER_BROWSER_PIN": "unknown",
            "OMES_BROWSER_WORKER_BROWSER_BIN": "relative/chromium",
            "OMES_BROWSER_WORKER_PROFILE_ROOT": "relative/profiles",
            "OMES_BROWSER_WORKER_HEARTBEAT_FILE": "relative/heartbeat",
            "OMES_BROWSER_WORKER_HEARTBEAT_MAX_AGE_SECONDS": "abc",
            "OMES_BROWSER_WORKER_SESSION_MAX_AGE_SECONDS": "0",
            "OMES_BROWSER_WORKER_DISK_MIN_MB": "-5",
            "OMES_BROWSER_WORKER_MEM_MIN_MB": "1.5",
            "OMES_BROWSER_WORKER_CPU_MIN": "",
            "OMES_BROWSER_WORKER_CDP_PORTS": "9222,notaport",
            "OMES_BROWSER_WORKER_MCP_PORTS": "70000",
            "OMES_OLLAMA_MODEL": "-h",
        }
        for name, value in bad.items():
            if name == "OMES_BROWSER_WORKER_CPU_MIN":
                continue  # empty means "unset", not invalid
            with self.subTest(name=name):
                env = dict(World(self).env)
                env[name] = value
                code, out, err = self._run(env)
                self.assertEqual((code, out), (bw.EXIT_USAGE, ""))
                self.assertTrue(err)

    def test_unknown_cli_flag_is_a_usage_error(self):
        with contextlib.redirect_stderr(__import__("io").StringIO()) as captured:  # argparse prints its usage error
            code, out, _err = bw.run(["--no-such-flag"], env=World(self).env, deps=World(self).deps, facts={})
        self.assertEqual((code, out), (bw.EXIT_USAGE, ""))
        self.assertIn("unrecognized arguments", captured.getvalue())


class TestBrowserRuntime(unittest.TestCase):
    def _playwright_cache(self, world, entries, base=None):
        base = base or os.path.join(world.home, ".cache", "ms-playwright")
        os.makedirs(base, exist_ok=True)
        for name, marker, exe in entries:
            d = os.path.join(base, name)
            os.makedirs(os.path.join(d, "chrome-linux"), exist_ok=True)
            if marker:
                pathlib.Path(d, "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")
            if exe:
                p = pathlib.Path(d, "chrome-linux", exe)
                p.write_text("#!/bin/sh\n", encoding="utf-8")
                os.chmod(p, 0o755)
        return base

    def _bundled_world(self, entries, **env):
        world = World(self)
        del world.env["OMES_BROWSER_WORKER_BROWSER_BIN"]
        world.deps.browser_version = mock.Mock(side_effect=AssertionError("the bundled browser must never be executed"))
        self._playwright_cache(world, entries)
        world.env.update(env)
        return world

    def test_hermes_bundled_playwright_chromium_is_found_without_executing_it(self):
        world = self._bundled_world([("chromium-1194", True, "chrome")], OMES_BROWSER_WORKER_BROWSER_PIN="playwright-chromium-1194")
        document, results = world.collect()
        self.assertEqual(document["browser_engine"], "chromium")
        self.assertEqual(document["browser_version"], "playwright-chromium-1194")
        self.assertEqual(document["state"], "ready")
        browser = next(r for r in results if r["probe"] == "browser")
        self.assertEqual(browser["detail"]["browser_source"], "playwright_cache")

    def test_highest_complete_revision_wins_and_incomplete_installs_are_ignored(self):
        world = self._bundled_world(
            [
                ("chromium-1100", True, "chrome"),
                ("chromium-1194", True, "chrome"),
                ("chromium-1300", False, "chrome"),  # no INSTALLATION_COMPLETE marker
                ("chromium_headless_shell-1250", True, None),  # marker but no executable
                ("ffmpeg-1011", True, "chrome"),  # not a browser directory
            ]
        )
        document, _ = world.collect()
        self.assertEqual(document["browser_version"], "playwright-chromium-1194")

    def test_headless_shell_counts_as_chromium(self):
        world = self._bundled_world([("chromium_headless_shell-1194", True, "headless_shell")])
        document, _ = world.collect()
        self.assertEqual(document["browser_engine"], "chromium")
        self.assertEqual(document["browser_version"], "playwright-chromium-1194")

    def test_playwright_browsers_path_override_is_honoured_and_zero_is_ignored(self):
        world = self._bundled_world([])
        custom = os.path.join(world.tmp, "pw-cache")
        self._playwright_cache(world, [("chromium-1500", True, "chrome")], base=custom)
        world.env["PLAYWRIGHT_BROWSERS_PATH"] = custom
        document, _ = world.collect()
        self.assertEqual(document["browser_version"], "playwright-chromium-1500")
        world.env["PLAYWRIGHT_BROWSERS_PATH"] = "0"
        document, _ = world.collect()
        self.assertEqual(document["browser_engine"], "not_installed")

    def test_symlinked_cache_directories_are_not_followed(self):
        world = self._bundled_world([])
        real = os.path.join(world.tmp, "elsewhere", "chromium-1999")
        os.makedirs(os.path.join(real, "chrome-linux"))
        pathlib.Path(real, "INSTALLATION_COMPLETE").write_text("", encoding="utf-8")
        cache = os.path.join(world.home, ".cache", "ms-playwright")
        os.makedirs(cache, exist_ok=True)
        os.symlink(real, os.path.join(cache, "chromium-1999"))
        document, _ = world.collect()
        self.assertEqual(document["browser_engine"], "not_installed")

    def test_path_discovery_runs_only_the_version_probe(self):
        world = World(self)
        del world.env["OMES_BROWSER_WORKER_BROWSER_BIN"]
        world.deps.which = lambda name: "/usr/bin/chromium" if name == "chromium" else None
        seen = []
        world.deps.browser_version = lambda binary: seen.append(binary) or "Chromium 122.0.6261.94 unofficial"
        document, _ = world.collect()
        self.assertEqual(seen, ["/usr/bin/chromium"])
        self.assertEqual(document["browser_version"], "chromium-122.0.6261.94")
        self.assertEqual(document["state"], "ready")

    def test_google_chrome_is_labelled_chrome_but_engine_is_chromium(self):
        world = World(self)
        world.deps.browser_version = lambda binary: "Google Chrome 120.0.6099.109 "
        world.env["OMES_BROWSER_WORKER_BROWSER_PIN"] = "chrome-120.0.6099.109"
        document, _ = world.collect()
        self.assertEqual((document["browser_engine"], document["browser_version"], document["state"]), ("chromium", "chrome-120.0.6099.109", "ready"))

    def test_no_browser_anywhere_is_not_installed(self):
        world = World(self)
        del world.env["OMES_BROWSER_WORKER_BROWSER_BIN"]
        document, _ = world.collect()
        self.assertEqual((document["browser_engine"], document["browser_version"]), ("not_installed", "none"))
        self.assertIn("BROWSER_BINARY_MISSING", document["reason_codes"])
        self.assertEqual(schema_errors(document), [])

    def test_unrecognised_version_output_is_never_echoed(self):
        world = World(self)
        world.deps.browser_version = lambda binary: "totally-not-a-browser TOKEN_CANARY_123"
        document, _ = world.collect()
        self.assertEqual((document["browser_engine"], document["browser_version"]), ("unknown", "unknown"))
        self.assertNotIn("TOKEN_CANARY_123", json.dumps(document))

    def test_a_pin_is_required_and_must_match_exactly(self):
        world = World(self)
        document, _ = world.collect()
        self.assertEqual(document["state"], "ready")
        world.env["OMES_BROWSER_WORKER_BROWSER_PIN"] = "chromium-122.0.6261.95"
        document, _ = world.collect()
        self.assertIn("BROWSER_PIN_MISMATCH", document["reason_codes"])
        self.assertEqual(document["state"], "blocked")

    def test_default_version_probe_runs_a_fixed_argv_against_a_script(self):
        world = World(self)
        script = os.path.join(world.tmp, "fake-chromium")
        pathlib.Path(script).write_text('#!/bin/sh\n[ "$1" = "--version" ] || exit 9\necho "Chromium 122.0.6261.94 test"\n', encoding="utf-8")
        os.chmod(script, 0o755)
        self.assertEqual(bw.default_browser_version(script), "Chromium 122.0.6261.94 test")
        failing = os.path.join(world.tmp, "failing-chromium")
        pathlib.Path(failing).write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
        os.chmod(failing, 0o755)
        self.assertIsNone(bw.default_browser_version(failing))
        self.assertIsNone(bw.default_browser_version(os.path.join(world.tmp, "missing")))


class TestExposure(unittest.TestCase):
    def _doc(self, text, collected=True, **env):
        world = World(self)
        world.env.update(env)
        world.deps.listeners = lambda: listeners_from(text, collected)
        document, _ = world.collect()
        return document

    def test_loopback_listeners_are_fine(self):
        document = self._doc(SS_LOOPBACK_ALL)
        self.assertEqual(document["exposure"], "loopback")
        self.assertEqual(document["state"], "ready")

    def test_no_listeners_at_all_is_loopback(self):
        self.assertEqual(self._doc("")["exposure"], "loopback")

    def test_wildcard_cdp_is_public_blocked(self):
        document = self._doc(SS_WILDCARD_CDP)
        self.assertEqual((document["exposure"], document["state"]), ("public_blocked", "blocked"))
        self.assertIn("CDP_LISTENER_NONLOOPBACK", document["reason_codes"])

    def test_private_lan_cdp_is_private_boundary_but_still_blocked(self):
        document = self._doc(SS_LAN_CDP)
        self.assertEqual((document["exposure"], document["state"]), ("private_boundary", "blocked"))
        self.assertIn("CDP_LISTENER_NONLOOPBACK", document["reason_codes"])

    def test_public_ip_bind_is_public_blocked(self):
        text = 'tcp   LISTEN 0      128    1.1.1.1:9222          0.0.0.0:*     users:(("chrome",pid=3,fd=4))\n'
        self.assertEqual(self._doc(text)["exposure"], "public_blocked")

    def test_ipv6_wildcard_on_a_declared_port_is_blocked(self):
        document = self._doc(SS_V6_WILDCARD_CDP)  # port 9223 is inside the default CDP range
        self.assertEqual((document["exposure"], document["state"]), ("public_blocked", "blocked"))
        self.assertIn("CDP_LISTENER_NONLOOPBACK", document["reason_codes"])

    def test_mcp_and_ollama_listeners_are_blocked(self):
        mcp = self._doc(SS_WILDCARD_MCP)
        self.assertIn("MCP_LISTENER_NONLOOPBACK", mcp["reason_codes"])
        self.assertEqual(mcp["state"], "blocked")
        ol = self._doc(SS_WILDCARD_OLLAMA)
        self.assertIn("OLLAMA_LISTENER_NONLOOPBACK", ol["reason_codes"])
        self.assertEqual(ol["state"], "blocked")

    def test_operator_declared_ports_extend_the_watch_list(self):
        text = 'tcp   LISTEN 0      128    0.0.0.0:7777          0.0.0.0:*     users:(("python3",pid=9,fd=4))\n'
        self.assertEqual(self._doc(text)["exposure"], "loopback")  # not watched by default
        document = self._doc(text, OMES_BROWSER_WORKER_CDP_PORTS="7777")
        self.assertIn("CDP_LISTENER_NONLOOPBACK", document["reason_codes"])
        document = self._doc(text, OMES_BROWSER_WORKER_MCP_PORTS="7777")
        self.assertIn("MCP_LISTENER_NONLOOPBACK", document["reason_codes"])

    def test_a_desktop_browsers_wildcard_udp_mdns_socket_is_not_a_cdp_listener(self):
        text = 'udp   UNCONN 0      0      0.0.0.0:5353          0.0.0.0:*     users:(("chrome",pid=3,fd=60))\n'
        document = self._doc(text)
        self.assertEqual((document["exposure"], document["state"]), ("loopback", "ready"))

    def test_unrelated_exposed_listeners_are_not_this_checks_business(self):
        document = self._doc(SS_HERMES_GATEWAY_WILDCARD)
        self.assertEqual((document["exposure"], document["state"]), ("loopback", "ready"))

    def test_exposure_allowlist_never_approves_browser_control(self):
        with mock.patch.dict(os.environ, {"OMES_EXPOSURE_ALLOW": "0.0.0.0:9222"}):
            document = self._doc(SS_WILDCARD_CDP, OMES_EXPOSURE_ALLOW="0.0.0.0:9222")
        self.assertEqual(document["state"], "blocked")
        self.assertIn("CDP_LISTENER_NONLOOPBACK", document["reason_codes"])

    def test_unreadable_socket_table_is_unknown_not_loopback(self):
        document = self._doc("", collected=False)
        self.assertEqual((document["exposure"], document["state"]), ("unknown", "unknown"))
        self.assertIn("EXPOSURE_AUDIT_UNAVAILABLE", document["reason_codes"])

    def test_partial_table_still_reports_a_visible_exposed_listener(self):
        document = self._doc(SS_WILDCARD_CDP, collected=False)
        self.assertEqual(document["state"], "blocked")
        self.assertEqual(document["exposure"], "public_blocked")
        self.assertIn("CDP_LISTENER_NONLOOPBACK", document["reason_codes"])
        self.assertIn("EXPOSURE_AUDIT_UNAVAILABLE", document["reason_codes"])

    def test_custom_ollama_endpoint_port_is_watched(self):
        text = 'tcp   LISTEN 0      128    0.0.0.0:12345         0.0.0.0:*     users:(("python3",pid=9,fd=4))\n'
        with mock.patch.dict(os.environ, {"OLLAMA_HOST": "127.0.0.1:12345"}):
            document = self._doc(text)
        self.assertIn("OLLAMA_LISTENER_NONLOOPBACK", document["reason_codes"])

    def test_default_listener_source_reuses_the_80_audit_via_ss_shim(self):
        world = World(self)
        shim = os.path.join(world.tmp, "ss-shim")
        pathlib.Path(shim).write_text("#!/bin/sh\ncat <<'EOF'\n" + SS_WILDCARD_CDP + "EOF\n", encoding="utf-8")
        os.chmod(shim, 0o755)
        world.deps.listeners = bw.default_listeners
        with mock.patch.dict(os.environ, {"OMES_SS_BIN": shim}):
            document, _ = world.collect()
        self.assertIn("CDP_LISTENER_NONLOOPBACK", document["reason_codes"])
        failing = os.path.join(world.tmp, "ss-fail")
        pathlib.Path(failing).write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        os.chmod(failing, 0o755)
        with mock.patch.dict(os.environ, {"OMES_SS_BIN": failing}):
            document, _ = world.collect()
        self.assertIn("EXPOSURE_AUDIT_UNAVAILABLE", document["reason_codes"])
        with mock.patch.dict(os.environ, {"OMES_SS_BIN": os.path.join(world.tmp, "no-ss")}):
            document, _ = world.collect()
        self.assertIn("EXPOSURE_AUDIT_UNAVAILABLE", document["reason_codes"])


class TestModel(unittest.TestCase):
    def test_ai_assisted_ready(self):
        world = World(self)
        document, _ = world.collect()
        self.assertEqual((document["model_provider"], document["model_ready"], document["model_id"]), ("ollama", "ready", "qwen3.5:9b"))

    def test_undeclared_profile_does_not_claim_not_required(self):
        world = World(self)
        del world.env["OMES_BROWSER_WORKER_CAPABILITY_PROFILE"]
        document, _ = world.collect()
        self.assertEqual((document["model_provider"], document["model_ready"], document["capability_profile"]), ("unknown", "unknown", "unknown"))

    def test_cloud_destination_does_not_report_a_healthy_ollama(self):
        world = World(self)
        world.deps.destination_class = lambda: "cloud"
        world.deps.ollama_probe = mock.Mock(side_effect=AssertionError("no need to probe a model that will not be used"))
        document, _ = world.collect()
        self.assertEqual((document["model_provider"], document["model_ready"]), ("unknown", "unknown"))
        self.assertNotIn("model_id", document)
        self.assertEqual(document["state"], "blocked")

    def test_a_destination_probe_that_raises_is_unknown_not_a_crash_or_a_pass(self):
        world = World(self)
        world.deps.destination_class = mock.Mock(side_effect=RuntimeError("boom"))
        document, _ = world.collect()
        self.assertIn("EGRESS_MODEL_DESTINATION_UNKNOWN", document["reason_codes"])
        self.assertEqual(document["state"], "unknown")

    def test_unavailable_model_is_degraded_with_provider_still_ollama(self):
        world = World(self)
        world.deps.ollama_probe = lambda model: {"ready": False, "exit_code": 7}
        document, _ = world.collect()
        self.assertEqual((document["model_provider"], document["model_ready"], document["state"]), ("ollama", "unavailable", "degraded"))

    def test_a_probe_that_exits_the_interpreter_is_contained(self):
        world = World(self)
        world.deps.ollama_probe = mock.Mock(side_effect=SystemExit(2))
        document, _ = world.collect()
        self.assertIn("MODEL_PROBE_ERROR", document["reason_codes"])

    def test_model_flag_overrides_env_and_is_validated(self):
        world = World(self)
        world.args.model = "llama3.1:8b"
        document, _ = world.collect()
        self.assertEqual(document["model_id"], "llama3.1:8b")

    def test_default_probe_reuses_the_71_check_unchanged_against_a_fake_endpoint(self):
        sys.path.insert(0, os.path.join(ROOT))
        from tests.py.health import test_ollama as t71  # noqa: PLC0415

        config = {"tags": ["llama3"], "generate_response": {"response": "pong"}, "ps_response": {"models": [{"name": "llama3", "processor": "100% CPU"}]}}
        with t71.FakeOllama(config) as fake, mock.patch.object(ollama.shutil, "which", return_value="/usr/bin/ollama"):
            env = {"OLLAMA_HOST": fake.endpoint, "OMES_HEALTH_TIMEOUT": "2", "OMES_OLLAMA_LOAD_TIMEOUT": "2"}
            with mock.patch.dict(os.environ, env):
                os.environ.pop("OMES_OLLAMA_PROFILE", None)
                self.assertTrue(bw.default_ollama_probe("llama3")["ready"])
                self.assertFalse(bw.default_ollama_probe("not-pulled")["ready"])

    def test_default_probe_against_an_unreachable_endpoint_is_unavailable_not_a_crash(self):
        world = World(self)
        world.deps.ollama_probe = bw.default_ollama_probe
        with mock.patch.dict(os.environ, {"OLLAMA_HOST": "127.0.0.1:1", "OMES_HEALTH_TIMEOUT": "2"}):
            document, _ = world.collect()
        self.assertEqual(document["model_ready"], "unavailable")
        self.assertEqual(document["state"], "degraded")


class TestOllamaOutputIsUnchanged(unittest.TestCase):
    """Issue #298: the #71 probe is reused as a library and its CLI/JSON
    output must not change. Golden key sets pin the public shape."""

    READY_KEYS = {"provider", "profile", "service", "model", "capabilities", "checks", "ready"}

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT))
        self._env = dict(os.environ)
        for key in ("OMES_OLLAMA_PROFILE", "OMES_OLLAMA_PROFILE_FILE", "OMES_OLLAMA_MODEL", "OMES_OLLAMA_EXPECT_PLACEMENT", "OMES_OLLAMA_ALLOW_REMOTE", "OLLAMA_HOST"):
            os.environ.pop(key, None)
        os.environ["OMES_HEALTH_TIMEOUT"] = "2"
        os.environ["OMES_OLLAMA_LOAD_TIMEOUT"] = "2"
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env)))

    def test_ready_json_shape(self):
        from tests.py.health import test_ollama as t71  # noqa: PLC0415

        config = {"tags": ["llama3"], "generate_response": {"response": "pong"}, "ps_response": {"models": [{"name": "llama3", "processor": "100% CPU"}]}}
        with t71.FakeOllama(config) as fake, mock.patch.object(ollama.shutil, "which", return_value="/usr/bin/ollama"):
            os.environ["OLLAMA_HOST"] = fake.endpoint
            os.environ["OMES_OLLAMA_MODEL"] = "llama3"
            result = ollama.run([])
            self.assertEqual(set(result), self.READY_KEYS | {"exit_code"})
            self.assertEqual(result["provider"], "ollama-local")
            self.assertEqual(set(result["capabilities"]), {"chat", "structured_output", "tool_calling", "embeddings", "vision"})
            self.assertEqual(set(result["model"]), {"id", "status", "processor"})
            self.assertEqual(set(result["service"]), {"status"})
            for check in result["checks"]:
                self.assertEqual(set(check), {"name", "status", "detail", "remediation"})
            with mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
                rc = ollama.main([])
            printed = json.loads(out.getvalue())
            self.assertEqual(rc, ollama.EXIT_READY)
            self.assertEqual(set(printed), self.READY_KEYS)  # exit_code is stripped before printing

    def test_service_missing_json_shape(self):
        with mock.patch.object(ollama.shutil, "which", return_value="/usr/bin/ollama"):
            os.environ["OLLAMA_HOST"] = "127.0.0.1:1"
            os.environ["OMES_OLLAMA_MODEL"] = "llama3"
            result = ollama.run([])
        self.assertEqual(set(result), self.READY_KEYS | {"exit_code"})
        self.assertEqual(result["exit_code"], ollama.EXIT_SERVICE_MISSING)
        self.assertEqual(set(result["service"]), {"status", "reason"})

    def test_browser_worker_does_not_mutate_the_ollama_profile_environment(self):
        os.environ["OMES_OLLAMA_PROFILE"] = "tools"
        with mock.patch.object(ollama.shutil, "which", return_value="/usr/bin/ollama"):
            os.environ["OLLAMA_HOST"] = "127.0.0.1:1"
            bw.default_ollama_probe("llama3")
        self.assertEqual(os.environ["OMES_OLLAMA_PROFILE"], "tools")


class TestSessions(unittest.TestCase):
    def _proc(self, world, *rows):
        world.deps.processes = lambda: {"collected": True, "rows": list(rows)}

    def _row(self, age, *argv, pid=100):
        return {"pid": pid, "age": age, "argv": list(argv)}

    def test_young_automation_browser_is_not_an_orphan(self):
        world = World(self)
        self._proc(world, self._row(60, "/opt/chrome", "--headless=new", "--remote-debugging-pipe"))
        document, _ = world.collect()
        self.assertEqual(document["state"], "ready")

    def test_old_headless_browser_is_an_orphan_and_blocks(self):
        world = World(self)
        self._proc(world, self._row(7200, "/opt/chrome", "--headless=new", "--remote-debugging-pipe"))
        document, results = world.collect()
        self.assertEqual(document["state"], "blocked")
        self.assertIn("ORPHAN_BROWSER_PROCESS", document["reason_codes"])
        sessions = next(r for r in results if r["probe"] == "sessions")
        self.assertEqual(sessions["detail"]["orphan_processes"], 1)

    def test_old_browser_with_a_remote_debugging_port_is_an_orphan(self):
        world = World(self)
        self._proc(world, self._row(7200, "/usr/bin/chromium", "--remote-debugging-port=9222"))
        document, _ = world.collect()
        self.assertIn("ORPHAN_BROWSER_PROCESS", document["reason_codes"])

    def test_ordinary_desktop_browser_is_not_a_worker_session(self):
        world = World(self)
        self._proc(world, self._row(999999, "/usr/bin/google-chrome", "--profile-directory=Default"))
        document, _ = world.collect()
        self.assertEqual(document["state"], "ready")

    def test_old_browser_inside_the_profile_root_counts_even_without_automation_flags(self):
        world = World(self)
        self._proc(world, self._row(7200, "/usr/bin/chromium", f"--user-data-dir={world.profile_root}/job-9"))
        document, _ = world.collect()
        self.assertIn("ORPHAN_BROWSER_PROCESS", document["reason_codes"])

    def test_child_processes_are_not_counted(self):
        world = World(self)
        self._proc(world, self._row(7200, "/opt/chrome", "--type=renderer", "--headless", "--remote-debugging-pipe"))
        document, _ = world.collect()
        self.assertEqual(document["state"], "ready")

    def test_non_browser_processes_with_similar_flags_are_ignored(self):
        world = World(self)
        self._proc(world, self._row(7200, "/usr/bin/python3", "--headless", "--remote-debugging-port=1"))
        document, _ = world.collect()
        self.assertEqual(document["state"], "ready")

    def test_stale_profile_dir_without_a_live_browser_blocks(self):
        world = World(self)
        stale = os.path.join(world.profile_root, "job-old")
        os.mkdir(stale, 0o700)
        set_mtime(stale, NOW - datetime.timedelta(hours=5))
        document, results = world.collect()
        self.assertEqual(document["state"], "blocked")
        self.assertIn("STALE_PROFILE_DIRECTORY", document["reason_codes"])
        sessions = next(r for r in results if r["probe"] == "sessions")
        self.assertEqual(sessions["detail"]["stale_profile_dirs"], 1)

    def test_old_profile_dir_in_use_by_a_live_browser_is_not_stale(self):
        world = World(self)
        live = os.path.join(world.profile_root, "job-live")
        os.mkdir(live, 0o700)
        set_mtime(live, NOW - datetime.timedelta(hours=5))
        self._proc(world, self._row(60, "/opt/chrome", "--headless", f"--user-data-dir={live}"))
        document, _ = world.collect()
        self.assertEqual(document["state"], "ready")

    def test_young_profile_dir_is_fine(self):
        world = World(self)
        young = os.path.join(world.profile_root, "job-young")
        os.mkdir(young, 0o700)
        set_mtime(young, NOW - datetime.timedelta(seconds=30))
        document, _ = world.collect()
        self.assertEqual(document["state"], "ready")

    def test_symlink_entry_in_the_root_is_unsafe_and_never_followed(self):
        world = World(self)
        outside = os.path.join(world.tmp, "outside")
        os.mkdir(outside)
        os.symlink(outside, os.path.join(world.profile_root, "job-link"))
        document, _ = world.collect()
        self.assertEqual(document["state"], "blocked")
        self.assertIn("PROFILE_ROOT_UNSAFE", document["reason_codes"])

    def test_root_that_is_a_symlink_or_a_file_or_foreign_owned_is_unsafe(self):
        world = World(self)
        link = os.path.join(world.tmp, "root-link")
        os.symlink(world.profile_root, link)
        world.env["OMES_BROWSER_WORKER_PROFILE_ROOT"] = link
        self.assertIn("PROFILE_ROOT_UNSAFE", world.collect()[0]["reason_codes"])
        plain = os.path.join(world.tmp, "root-file")
        pathlib.Path(plain).write_text("", encoding="utf-8")
        world.env["OMES_BROWSER_WORKER_PROFILE_ROOT"] = plain
        self.assertIn("PROFILE_ROOT_UNSAFE", world.collect()[0]["reason_codes"])
        world.env["OMES_BROWSER_WORKER_PROFILE_ROOT"] = world.profile_root
        world.deps.uid = lambda: os.geteuid() + 4242
        # Owned by the real uid, which is neither the pretend euid nor root
        # unless the suite itself runs as root; only assert when it can fail.
        if os.stat(world.profile_root).st_uid != 0:
            self.assertIn("PROFILE_ROOT_UNSAFE", world.collect()[0]["reason_codes"])

    def test_group_or_world_accessible_root_is_unsafe(self):
        for mode in (0o750, 0o705, 0o777):
            with self.subTest(mode=oct(mode)):
                world = World(self)
                os.chmod(world.profile_root, mode)
                self.assertIn("PROFILE_ROOT_UNSAFE", world.collect()[0]["reason_codes"])

    def test_stale_check_is_skipped_when_the_process_table_is_unreadable(self):
        world = World(self)
        stale = os.path.join(world.profile_root, "job-old")
        os.mkdir(stale, 0o700)
        set_mtime(stale, NOW - datetime.timedelta(hours=5))
        world.deps.processes = lambda: {"collected": False, "rows": []}
        document, _ = world.collect()
        self.assertEqual(document["state"], "unknown")
        self.assertNotIn("STALE_PROFILE_DIRECTORY", document["reason_codes"])

    def test_session_ceiling_is_configurable(self):
        world = World(self)
        world.env["OMES_BROWSER_WORKER_SESSION_MAX_AGE_SECONDS"] = "30"
        self._proc(world, self._row(60, "/opt/chrome", "--headless"))
        self.assertIn("ORPHAN_BROWSER_PROCESS", world.collect()[0]["reason_codes"])

    def test_default_process_table_reader_parses_ps_output_and_flags_failure(self):
        world = World(self)
        ok = os.path.join(world.tmp, "ps-ok")
        pathlib.Path(ok).write_text(
            "#!/bin/sh\ncat <<'EOF'\n"
            "    1       500 /sbin/init\n"
            " 4242      7300 /opt/pw/chrome-linux/chrome --headless=new --user-data-dir=/tmp/p1 --proxy-server=http://u:S3CRET@h:1\n"
            "garbage line\n"
            "EOF\n",
            encoding="utf-8",
        )
        os.chmod(ok, 0o755)
        with mock.patch.dict(os.environ, {"OMES_PS_BIN": ok}):
            table = bw.default_processes()
        self.assertTrue(table["collected"])
        self.assertEqual([r["pid"] for r in table["rows"]], [1, 4242])
        self.assertEqual(table["rows"][1]["age"], 7300)
        bad = os.path.join(world.tmp, "ps-bad")
        pathlib.Path(bad).write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        os.chmod(bad, 0o755)
        with mock.patch.dict(os.environ, {"OMES_PS_BIN": bad}):
            self.assertFalse(bw.default_processes()["collected"])
        with mock.patch.dict(os.environ, {"OMES_PS_BIN": os.path.join(world.tmp, "nope")}):
            self.assertFalse(bw.default_processes()["collected"])

    def test_process_arguments_never_reach_any_output(self):
        world = World(self)
        self._proc(
            world,
            self._row(7200, "/opt/chrome", "--headless", "--proxy-server=http://user:PROXY_CANARY_77@host:1", f"--user-data-dir={world.profile_root}/job-1"),
        )
        document, results = world.collect()
        human = bw.render_human(document, results)
        for text in (json.dumps(document), human):
            self.assertNotIn("PROXY_CANARY_77", text)
            self.assertNotIn("--proxy-server", text)
            self.assertNotIn(world.profile_root, text)


class TestHeartbeat(unittest.TestCase):
    def test_fresh_heartbeat_passes(self):
        self.assertEqual(World(self).collect()[0]["state"], "ready")

    def test_freshness_window_is_configurable(self):
        world = World(self)
        world.env["OMES_BROWSER_WORKER_HEARTBEAT_MAX_AGE_SECONDS"] = "5"
        self.assertIn("HEARTBEAT_STALE", world.collect()[0]["reason_codes"])  # file is 10s old
        world.env["OMES_BROWSER_WORKER_HEARTBEAT_MAX_AGE_SECONDS"] = "3600"
        self.assertEqual(world.collect()[0]["state"], "ready")

    def test_future_dated_heartbeat_is_invalid_not_fresh(self):
        world = World(self)
        set_mtime(world.heartbeat, NOW + datetime.timedelta(hours=1))
        self.assertIn("HEARTBEAT_INVALID", world.collect()[0]["reason_codes"])

    def test_small_clock_skew_is_tolerated(self):
        world = World(self)
        set_mtime(world.heartbeat, NOW + datetime.timedelta(seconds=2))
        self.assertEqual(world.collect()[0]["state"], "ready")

    def test_symlink_and_directory_heartbeats_are_invalid(self):
        world = World(self)
        link = os.path.join(world.tmp, "hb-link")
        os.symlink(world.heartbeat, link)
        world.env["OMES_BROWSER_WORKER_HEARTBEAT_FILE"] = link
        self.assertIn("HEARTBEAT_INVALID", world.collect()[0]["reason_codes"])
        world.env["OMES_BROWSER_WORKER_HEARTBEAT_FILE"] = world.tmp
        self.assertIn("HEARTBEAT_INVALID", world.collect()[0]["reason_codes"])

    def test_heartbeat_content_is_never_read(self):
        world = World(self)
        pathlib.Path(world.heartbeat).write_text("HEARTBEAT_CANARY_VALUE", encoding="utf-8")
        set_mtime(world.heartbeat, NOW - datetime.timedelta(seconds=10))
        opened = []
        real_open = builtins.open

        def spy(file, *args, **kwargs):
            opened.append(os.fspath(file) if isinstance(file, (str, bytes, os.PathLike)) else file)
            return real_open(file, *args, **kwargs)

        with mock.patch.object(builtins, "open", spy):
            document, results = world.collect()
        self.assertNotIn(world.heartbeat, opened)
        self.assertNotIn("HEARTBEAT_CANARY_VALUE", json.dumps(document) + bw.render_human(document, results))


class TestResources(unittest.TestCase):
    def test_thresholds_are_configurable(self):
        world = World(self)
        world.deps.resources = lambda cfg: {"disk_free_mb": 3000, "mem_available_mb": 3000, "cpus": 3}
        self.assertEqual(world.collect()[0]["state"], "ready")
        world.env.update({"OMES_BROWSER_WORKER_DISK_MIN_MB": "4000", "OMES_BROWSER_WORKER_MEM_MIN_MB": "4000", "OMES_BROWSER_WORKER_CPU_MIN": "4"})
        codes = world.collect()[0]["reason_codes"]
        for code in ("RESOURCE_DISK_LOW", "RESOURCE_MEMORY_LOW", "RESOURCE_CPU_LOW"):
            self.assertIn(code, codes)

    def test_missing_fact_is_unknown_not_a_pass(self):
        world = World(self)
        world.deps.resources = lambda cfg: {"disk_free_mb": None, "mem_available_mb": None, "cpus": None}
        document, _ = world.collect()
        self.assertEqual(document["state"], "unknown")
        self.assertIn("RESOURCE_FACTS_UNAVAILABLE", document["reason_codes"])

    def test_default_resource_reader_returns_plausible_host_facts(self):
        world = World(self)
        cfg = bw.load_config(world.args, world.env)
        facts = bw.default_resources(cfg)
        self.assertGreater(facts["disk_free_mb"], 0)
        self.assertGreater(facts["cpus"], 0)
        self.assertTrue(facts["mem_available_mb"] is None or facts["mem_available_mb"] > 0)


class TestIsolationIntegrationPoint(unittest.TestCase):
    """The browser job/profile isolation test is owned by #276 (open PR #282),
    not duplicated here: #298 only defines the seam (`Deps.isolation`, whose
    production default reports `unknown`) and the rules for what a provider's
    answer means."""

    def test_seam_defaults_to_unknown_and_a_provider_answer_is_mapped_not_trusted_blindly(self):
        self.assertEqual(bw.default_isolation(None), {"session_isolation": "unknown", "codes": ["ISOLATION_EVIDENCE_NOT_INTEGRATED"]})
        for answer, expect_isolation, expect_code in (
            ({"session_isolation": "per_job", "codes": []}, "per_job", None),
            ({"session_isolation": "not_configured", "codes": []}, "not_configured", "ISOLATION_NOT_CONFIGURED"),
            ({"session_isolation": "unknown", "codes": []}, "unknown", "ISOLATION_EVIDENCE_NOT_INTEGRATED"),
            ({"session_isolation": "bogus", "codes": []}, "unknown", "ISOLATION_EVIDENCE_NOT_INTEGRATED"),
            ({"session_isolation": "unknown", "codes": ["MADE_UP_CODE"]}, "unknown", "ISOLATION_EVIDENCE_NOT_INTEGRATED"),
            ({"session_isolation": "unknown", "codes": ["RESOURCE_DISK_LOW"]}, "unknown", "ISOLATION_EVIDENCE_NOT_INTEGRATED"),
            ({}, "unknown", "ISOLATION_EVIDENCE_NOT_INTEGRATED"),
        ):
            with self.subTest(answer=answer):
                world = World(self)
                world.deps.isolation = lambda cfg, answer=answer: answer
                document, _ = world.collect()
                self.assertEqual(document["session_isolation"], expect_isolation)
                if expect_code:
                    self.assertIn(expect_code, document["reason_codes"])
                    self.assertNotEqual(document["state"], "ready")
                else:
                    self.assertEqual(document["state"], "ready")

    @unittest.skip("Not implemented yet (tracked in #298, depends on #276 / PR #282): wire the #276 isolation drift check into Deps.isolation")
    def test_276_isolation_drift_check_feeds_session_isolation(self):
        raise AssertionError("placeholder: replaced when #276 lands and is integrated")


class TestPlatform(unittest.TestCase):
    def test_unsupported_platform_short_circuits_and_validates(self):
        world = World(self)
        _s_platform_unsupported(world, None)
        document, results = world.collect()
        self.assertEqual(document["state"], "unsupported")
        self.assertEqual(document["reason_codes"], ["PLATFORM_UNSUPPORTED"])
        self.assertEqual([r["probe"] for r in results], ["platform"])
        self.assertEqual(schema_errors(document), [])
        self.assertEqual(
            (document["browser_engine"], document["model_ready"], document["session_isolation"], document["exposure"]),
            ("unknown", "unknown", "unknown", "unknown"),
        )

    def test_missing_host_facts_is_unknown_never_assumed_supported(self):
        world = World(self)
        world.facts = {"platform": {"supported": "yes"}}
        self.assertIn("HOST_FACTS_UNAVAILABLE", world.collect()[0]["reason_codes"])


class TestHermesVersion(unittest.TestCase):
    def test_version_is_extracted_not_echoed(self):
        world = World(self)
        world.deps.hermes_version = lambda: "hermes 0.21.5 /home/u/.hermes/secret-path"
        document, _ = world.collect()
        self.assertEqual(document["hermes_version"], "0.21.5")
        self.assertNotIn("secret-path", json.dumps(document))

    def test_absent_or_unparseable_hermes_omits_the_optional_field(self):
        for value in (None, "", "no digits here"):
            world = World(self)
            world.deps.hermes_version = lambda value=value: value
            self.assertNotIn("hermes_version", world.collect()[0])


class TestRunContract(unittest.TestCase):
    def _run(self, world, argv=None):
        return bw.run(argv or [], env=world.env, deps=world.deps, facts=world.facts)

    def test_ready_exits_0_with_exactly_one_json_object(self):
        code, out, err = self._run(World(self))
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.count("\n"), 1)
        self.assertEqual(json.loads(out)["state"], "ready")

    def test_every_not_ready_state_exits_7(self):
        for mutator in (_s_listeners(SS_WILDCARD_CDP), _s_resources(cpus=1), _drop_env("OMES_BROWSER_WORKER_HEARTBEAT_FILE"), _s_platform_unsupported):
            world = World(self)
            mutator(world, contextlib.ExitStack())
            code, out, _err = self._run(world)
            self.assertEqual(code, bw.EXIT_NOT_READY)
            self.assertEqual(schema_errors(json.loads(out)), [])

    def test_human_format_names_state_and_remediation_but_json_flag_wins(self):
        world = World(self)
        _s_resources(disk_free_mb=1)(world, None)
        code, out, _err = self._run(world, ["--format", "human"])
        self.assertEqual(code, bw.EXIT_NOT_READY)
        self.assertIn("state=degraded", out)
        self.assertIn("RESOURCE_DISK_LOW", out)
        self.assertIn("remediation:", out)
        code, out, _err = self._run(world, ["--format", "human", "--json"])
        self.assertEqual(json.loads(out)["state"], "degraded")

    def test_a_document_that_fails_the_contract_is_never_emitted(self):
        world = World(self)
        with mock.patch.object(bw, "validate_document", return_value=["$.state: bad"]):
            code, out, err = self._run(world)
        self.assertEqual((code, out), (bw.EXIT_ERROR, ""))
        self.assertIn("self-check", err)

    def test_facts_are_read_from_stdin_only_when_asked(self):
        world = World(self)
        with mock.patch.object(bw.sys, "stdin", __import__("io").StringIO('{"platform": {"supported": false}}')):
            code, out, _ = bw.run(["--facts-stdin"], env=world.env, deps=world.deps)
        self.assertEqual(json.loads(out)["state"], "unsupported")
        code, out, _ = bw.run([], env=world.env, deps=world.deps)  # no stdin read, no facts
        self.assertIn("HOST_FACTS_UNAVAILABLE", json.loads(out)["reason_codes"])

    def test_module_has_no_mutating_or_launching_calls(self):
        """Static guard for the issue's non-goals: the producer never
        mutates the host, never signals a process, never launches a browser
        and never imports an RPA/automation library."""
        import ast  # noqa: PLC0415

        tree = ast.parse(pathlib.Path(HEALTH_DIR, "browser_worker.py").read_text(encoding="utf-8"))

        def dotted(node):
            parts = []
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.append(node.id)
            return ".".join(reversed(parts))

        forbidden_calls = {
            "os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.rename", "os.replace", "os.chmod", "os.chown",
            "os.makedirs", "os.mkdir", "os.symlink", "os.link", "os.utime", "os.kill", "os.killpg", "os.system", "os.execv",
            "os.execvp", "os.fork", "shutil.rmtree", "shutil.move", "shutil.copy", "shutil.copy2", "shutil.copyfile",
            "shutil.copytree", "subprocess.Popen", "subprocess.call", "subprocess.check_call", "subprocess.check_output",
        }
        subprocess_runs = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for name in names:
                    top = name.split(".")[0]
                    self.assertNotIn(top, {"playwright", "selenium", "pyppeteer", "socket", "requests", "urllib3", "signal"}, name)
            if isinstance(node, ast.Call):
                name = dotted(node.func)
                self.assertNotIn(name, forbidden_calls, name)
                if name == "subprocess.run":
                    subprocess_runs += 1
                    self.assertFalse(any(k.arg == "shell" for k in node.keywords), "no shell=")
                if name == "open" and len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    self.assertFalse(set(str(node.args[1].value)) & set("wax+"), "no write-mode open()")
        self.assertEqual(subprocess_runs, 1, "all subprocess use goes through the single fixed-argv _run()")


class TestReadOnlyAndNoSecrets(unittest.TestCase):
    def _snapshot(self, root):
        entries = {}
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                p = os.path.join(dirpath, name)
                st = os.lstat(p)
                entries[p] = (stat.S_IMODE(st.st_mode), st.st_mtime_ns, st.st_size, st.st_ino)
        return entries

    def test_collect_does_not_change_the_filesystem(self):
        world = World(self)
        job = os.path.join(world.profile_root, "job-1", "Default")
        os.makedirs(job)
        pathlib.Path(job, "Cookies").write_text("COOKIE_CANARY_VALUE", encoding="utf-8")
        stale = os.path.join(world.profile_root, "job-old")
        os.mkdir(stale)
        set_mtime(stale, NOW - datetime.timedelta(hours=5))
        before = self._snapshot(world.tmp)
        world.collect()
        world.collect()
        self.assertEqual(before, self._snapshot(world.tmp))

    def test_profile_contents_are_never_opened_or_echoed(self):
        world = World(self)
        job = os.path.join(world.profile_root, "job-1", "Default")
        os.makedirs(job)
        cookie = os.path.join(job, "Cookies")
        pathlib.Path(cookie).write_text("COOKIE_CANARY_VALUE", encoding="utf-8")
        pathlib.Path(world.profile_root, "job-1", "Session Storage").write_text("SESSION_CANARY_VALUE", encoding="utf-8")
        opened = []
        real_open = builtins.open

        def spy(file, *args, **kwargs):
            opened.append(os.fspath(file) if isinstance(file, (str, bytes, os.PathLike)) else file)
            return real_open(file, *args, **kwargs)

        with mock.patch.object(builtins, "open", spy):
            document, results = world.collect()
        self.assertEqual([p for p in opened if isinstance(p, str) and p.startswith(world.profile_root)], [])
        blob = json.dumps(document) + bw.render_human(document, results)
        for canary in ("COOKIE_CANARY_VALUE", "SESSION_CANARY_VALUE", "Cookies", "Session Storage", world.profile_root):
            self.assertNotIn(canary, blob)

    def test_output_contains_no_paths_hosts_or_environment(self):
        world = World(self)
        document, results = world.collect()
        blob = json.dumps(document) + bw.render_human(document, results)
        for needle in (world.tmp, world.home, world.heartbeat, world.browser, "/proc", "OMES_JOBS"):
            self.assertNotIn(needle, blob)


class TestRegistryAndDocs(unittest.TestCase):
    def test_every_reason_code_is_documented(self):
        text = pathlib.Path(DOC_PATH).read_text(encoding="utf-8")
        for code in list(bw.REASONS) + [bw.READY_CODE]:
            self.assertIn(f"`{code}`", text, f"{code} is missing from docs/browser-worker-readiness.md")

    def test_every_documented_code_is_registered(self):
        text = pathlib.Path(DOC_PATH).read_text(encoding="utf-8")
        documented = set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \|", text, flags=re.M))
        self.assertEqual(documented, set(bw.REASONS) | {bw.READY_CODE})

    def test_documented_defaults_match_the_code(self):
        text = pathlib.Path(os.path.join(ROOT, "docs", "configuration.md")).read_text(encoding="utf-8")
        for name, default in (
            ("OMES_BROWSER_WORKER_HEARTBEAT_MAX_AGE_SECONDS", bw.DEFAULT_HEARTBEAT_MAX_AGE_SECONDS),
            ("OMES_BROWSER_WORKER_SESSION_MAX_AGE_SECONDS", bw.DEFAULT_SESSION_MAX_AGE_SECONDS),
            ("OMES_BROWSER_WORKER_DISK_MIN_MB", bw.DEFAULT_DISK_MIN_MB),
            ("OMES_BROWSER_WORKER_MEM_MIN_MB", bw.DEFAULT_MEM_MIN_MB),
            ("OMES_BROWSER_WORKER_CPU_MIN", bw.DEFAULT_CPU_MIN),
        ):
            row = next((line for line in text.splitlines() if line.startswith(f"| `{name}`")), None)
            self.assertIsNotNone(row, name)
            self.assertIn(f"`{default}`", row, name)


if __name__ == "__main__":
    unittest.main()
