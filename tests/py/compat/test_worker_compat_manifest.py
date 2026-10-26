"""tests/py/compat/test_worker_compat_manifest.py - worker compatibility
manifest producer, reference consumer decision and synthetic worker-flow
fixtures (issue #352, ADR-0036 section 4). Stdlib only, no network."""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import logging
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from jobs import schema as schema_mod  # noqa: E402
from jobs import worker  # noqa: E402

_spec = importlib.util.spec_from_file_location("gen_compat", REPO_ROOT / "scripts" / "generate-worker-compat-manifest.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

CONTRACT_V1 = REPO_ROOT / "contracts" / "compatibility" / "v1"
FIXTURES = CONTRACT_V1 / "fixtures" / "worker-compatibility-manifest"
CC_FIXTURES = REPO_ROOT / "contracts" / "control-center" / "v1" / "fixtures"
SECRET_SHAPES = re.compile(r"(sk_live_|ghp_[A-Za-z0-9]|AKIA[0-9A-Z]{8}|xox[bp]-|Bearer\s+[A-Za-z0-9._-]{10,})")


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# Assembled at runtime so the secret scanner never sees a key-shaped literal;
# the tests only assert that this value never appears in payloads or logs.
_KEY_SENTINEL = "-".join(["PRIVATE", "KEY", "SENTINEL", "0123456789"])

class TestProducer(unittest.TestCase):
    def test_unreleased_manifest_validates_and_is_deterministic(self):
        a = gen.build_manifest()
        self.assertEqual(gen.validate_manifest(a), [])
        self.assertEqual(gen.render(a), gen.render(gen.build_manifest()))
        self.assertEqual(a["channel"], "unreleased")
        self.assertEqual(a["product_version"], (REPO_ROOT / "VERSION").read_text().strip())
        for key in ("release_tag", "release_time", "commit_sha"):
            self.assertNotIn(key, a)

    def test_check_mode_passes_on_repository(self):
        self.assertEqual(gen.check(), [])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gen.main(["--check"]), 0)

    def test_nothing_is_invented(self):
        m = gen.build_manifest()
        self.assertEqual(m["installer"], {"status": "not_published"})
        self.assertEqual(m["awcms_omes_control"]["verified_builds"], [])
        self.assertEqual(m["integrity"]["signature_status"], "unsigned")

    def test_contract_digest_recomputes_from_files(self):
        m = gen.build_manifest()
        files = m["control_contract"]["files"]
        self.assertEqual(gen.digest_of(files), m["control_contract"]["digest"])
        for f in files:
            self.assertEqual(f["sha256"], gen._sha256_file(REPO_ROOT / f["path"]))
        self.assertEqual([f["path"] for f in files], sorted(f["path"] for f in files))

    def test_contract_version_matches_what_the_worker_reports(self):
        src = (PY_ROOT / "jobs" / "worker.py").read_text(encoding="utf-8")
        self.assertIn(f'"contract_version": "{gen.CONTRACT_VERSION}"', src)

    def test_capability_digest_and_capabilities_match_heartbeat(self):
        sent: list[dict[str, Any]] = []

        def hb(data):
            sent.append(data)
            return {"status": "acknowledged", "server_id": data["server_id"], "received_at": "2026-01-01T00:00:00Z", "next_heartbeat_seconds": 60}

        creds = worker.WorkerCredentials(
            tenant_id="tenant-x", server_id="srv-x", worker_id="wrk-x",
            control_center_endpoint="https://cc.example.invalid", public_key="pub", private_key="priv",
        )
        worker.send_heartbeat(creds, client=worker.HttpClient(mock_handlers={"/api/v1/worker/heartbeat": hb}))
        m = gen.build_manifest()
        self.assertEqual(sent[0]["capability_registry_digest"], m["capability_posture"]["capability_registry_digest"])
        self.assertEqual(sent[0]["contract_version"], m["control_contract"]["contract_version"])
        self.assertEqual(m["capability_posture"]["worker_capabilities"], list(worker.SUPPORTED_CAPABILITIES))

    def test_request_signing_claim_is_honest(self):
        # ADR-0027 targets signed requests; the shipped worker has none.
        # If signing lands this fails, forcing REQUEST_SIGNING to be updated.
        src = (PY_ROOT / "jobs" / "worker.py").read_text(encoding="utf-8").lower()
        self.assertEqual(gen.REQUEST_SIGNING, "not_implemented")
        for marker in ("hmac", "\"authorization\"", "'authorization'", "x-omes-signature"):
            self.assertNotIn(marker, src)

    def test_platform_versions_match_detect_sh(self):
        detect = (REPO_ROOT / "lib" / "omes" / "detect.sh").read_text(encoding="utf-8")
        for entry in gen.PLATFORM_OS:
            for v in entry["versions"]:
                self.assertIn(v, detect)

    def test_stable_requires_release_identity_and_matching_tag(self):
        with self.assertRaises(ValueError):
            gen.build_manifest(channel="stable")
        version = (REPO_ROOT / "VERSION").read_text().strip()
        with self.assertRaises(ValueError):
            gen.build_manifest(channel="stable", tag="v0.0.1-nope", release_time="2026-01-01T00:00:00Z", commit="a" * 40)
        m = gen.build_manifest(channel="stable", tag=f"v{version}", release_time="2026-01-01T00:00:00Z", commit="a" * 40)
        self.assertEqual(gen.validate_manifest(m), [])
        with self.assertRaises(ValueError):
            gen.build_manifest(channel="unreleased", tag=f"v{version}")

    def test_no_secret_shapes_in_output_or_fixtures(self):
        self.assertFalse(SECRET_SHAPES.search(gen.render(gen.build_manifest())))
        for path in list(FIXTURES.glob("*.json")) + [CONTRACT_V1 / "consumer-scenarios.json"]:
            self.assertFalse(SECRET_SHAPES.search(path.read_text(encoding="utf-8")), path.name)

    def test_cli_stdout_and_out_file_are_identical(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(gen.main([]), 0)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "m.json"
            self.assertEqual(gen.main(["--out", str(out)]), 0)
            self.assertEqual(out.read_text(encoding="utf-8"), buf.getvalue())


class TestConsumerDecision(unittest.TestCase):
    def test_scenario_table(self):
        table = json.loads((CONTRACT_V1 / "consumer-scenarios.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(table["scenarios"]), 8)
        for sc in table["scenarios"]:
            manifest = fixture(sc["manifest"])
            consumer = dict(sc["consumer"])
            if consumer.get("contract_digest") == "$FIXTURE_DIGEST":
                consumer["contract_digest"] = manifest["control_contract"]["digest"]
            with self.subTest(sc["name"]):
                self.assertEqual(gen.evaluate_compatibility(manifest, consumer), sc["expected"])

    def test_tampered_file_hash_is_detected(self):
        m = fixture("valid-stable.json")
        m["control_contract"]["files"][0]["sha256"] = "0" * 64
        self.assertEqual(gen.evaluate_compatibility(m, {})["reason_codes"], ["manifest_digest_inconsistent"])

    def test_tag_must_match_product_version(self):
        m = fixture("valid-stable.json")
        m["release_tag"] = "v9.9.9"
        self.assertIn("release_tag_version_mismatch", gen.evaluate_compatibility(m, {})["reason_codes"])

    def test_stable_without_release_identity_fails_closed(self):
        m = fixture("valid-stable.json")
        del m["commit_sha"]
        self.assertIn("stable_release_identity_missing", gen.evaluate_compatibility(m, {})["reason_codes"])

    def test_published_installer_needs_uri_and_sha256(self):
        m = fixture("valid-stable.json")
        m["installer"] = {"status": "published"}
        self.assertIn("installer_incomplete", gen.evaluate_compatibility(m, {})["reason_codes"])

    def test_unparseable_inputs_fail_closed(self):
        for bad in (None, [], "x", 3, {}):
            self.assertFalse(gen.evaluate_compatibility(bad, {"contract_version": "1.0.0"})["compatible"])

    def test_verify_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            ok = Path(tmp) / "ok.json"
            ok.write_text(json.dumps(fixture("valid-stable.json")))
            self.assertEqual(gen.main(["--verify", str(ok), "--consumer-contract-version", "1.0.0"]), 0)
            self.assertEqual(gen.main(["--verify", str(ok), "--consumer-contract-version", "2.0.0"]), 1)
            garbage = Path(tmp) / "bad.json"
            garbage.write_text("{not json")
            self.assertEqual(gen.main(["--verify", str(garbage)]), 1)
            self.assertEqual(gen.main(["--verify", str(Path(tmp) / "missing.json")]), 1)

    def test_every_valid_fixture_is_decided_without_crash(self):
        for path in FIXTURES.glob("valid-*.json"):
            self.assertIn("compatible", gen.evaluate_compatibility(json.loads(path.read_text()), {}))


class TestWorkerFlowFixtures(unittest.TestCase):
    """Synthetic enroll/poll/result/heartbeat flow against the mock client:
    outbound only, nonce/idempotency, mismatch and schema-break handling, and
    no secret in payloads or logs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.creds = worker.WorkerCredentials(
            tenant_id="tenant-acme", server_id="srv-prod-01", worker_id="wrk-001",
            control_center_endpoint="https://control-center.example.invalid",
            public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPubKeyFixture",
            private_key=_KEY_SENTINEL,
        )

    def _recording(self, path: str, response: Any):
        def handler(data):
            self.calls.append((path, data))
            return response
        return handler

    def _poll_job(self, **overrides):
        resp = json.loads((CC_FIXTURES / "worker-poll.response" / "valid-02-job-available.json").read_text())
        resp["job"].update(overrides)
        return resp

    def _client(self, poll_response):
        result_ack = json.loads((CC_FIXTURES / "worker-result.response" / "valid-01.json").read_text())
        return worker.HttpClient(mock_handlers={
            "/api/v1/worker/poll": self._recording("/api/v1/worker/poll", poll_response),
            "/api/v1/worker/result": self._recording("/api/v1/worker/result", result_ack),
        })

    def test_enroll_poll_result_heartbeat_use_only_outbound_worker_paths(self):
        enroll = json.loads((CC_FIXTURES / "worker-enrollment.response" / "valid-01.json").read_text())
        hb = json.loads((CC_FIXTURES / "worker-heartbeat.response" / "valid-01.json").read_text())
        idle = json.loads((CC_FIXTURES / "worker-poll.response" / "valid-01.json").read_text())
        client = worker.HttpClient(mock_handlers={
            "/api/v1/worker/enroll": self._recording("/api/v1/worker/enroll", enroll),
            "/api/v1/worker/heartbeat": self._recording("/api/v1/worker/heartbeat", hb),
            "/api/v1/worker/poll": self._recording("/api/v1/worker/poll", idle),
            "/api/v1/worker/result": self._recording("/api/v1/worker/result", {}),
        })
        creds = worker.enroll_worker(
            "https://control-center.example.invalid", enroll["tenant_id"], enroll["server_id"],
            "challenge_token_valid12345678", state_dir=self.state, client=client,
        )
        worker.send_heartbeat(creds, client=client)
        worker.poll_and_dispatch_once(creds, state_dir=self.state, client=client)
        self.assertEqual([p for p, _ in self.calls], ["/api/v1/worker/enroll", "/api/v1/worker/heartbeat", "/api/v1/worker/poll"])
        # The worker module opens no listening socket and exposes no server entry point.
        src = (PY_ROOT / "jobs" / "worker.py").read_text(encoding="utf-8")
        for marker in ("socket.bind", ".listen(", "HTTPServer", "websocket"):
            self.assertNotIn(marker, src)

    def test_poll_nonce_is_fresh_and_contract_shaped(self):
        client = self._client(json.loads((CC_FIXTURES / "worker-poll.response" / "valid-01.json").read_text()))
        for _ in range(3):
            worker.poll_and_dispatch_once(self.creds, state_dir=self.state, client=client)
        nonces = [d["nonce"] for _, d in self.calls]
        self.assertEqual(len(set(nonces)), 3)
        schema = schema_mod.load_json(CC_FIXTURES.parent / "worker-poll.request.schema.json")
        for _, payload in self.calls:
            self.assertEqual(schema_mod.validate(payload, schema), [])

    def test_short_nonce_fixture_stays_invalid(self):
        schema = schema_mod.load_json(CC_FIXTURES.parent / "worker-poll.request.schema.json")
        bad = json.loads((CC_FIXTURES / "worker-poll.request" / "invalid-nonce-too-short.json").read_text())
        self.assertNotEqual(schema_mod.validate(bad, schema), [])

    def test_result_carries_the_jobs_idempotency_key_and_correlation(self):
        client = self._client(self._poll_job(target={"server_id": "srv-other"}))
        res = worker.poll_and_dispatch_once(self.creds, state_dir=self.state, client=client)
        results = [d for p, d in self.calls if p.endswith("/result")]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["idempotency_key"], "idem-job-status-00000001")
        self.assertEqual(results[0]["correlation_id"], "corr-job-0001")
        self.assertNotIn("job_id", results[0])
        self.assertEqual(res.get("state", res.get("status")), "rejected")

    def test_server_mismatch_is_rejected_without_execution(self):
        client = self._client(self._poll_job(target={"server_id": "srv-other"}))
        worker.poll_and_dispatch_once(self.creds, state_dir=self.state, client=client)
        (result,) = [d for p, d in self.calls if p.endswith("/result")]
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(result["error"]["code"], "scope_mismatch")

    def test_non_allowlisted_operation_never_reaches_execution(self):
        # The poll response contract enumerates operations, so an unknown
        # one is a schema break: fail closed, nothing executed or reported.
        client = self._client(self._poll_job(operation="enable_ssh_root"))
        with self.assertRaises(worker.WorkerError):
            worker.poll_and_dispatch_once(self.creds, state_dir=self.state, client=client)
        self.assertEqual([p for p, _ in self.calls], ["/api/v1/worker/poll"])

    def test_poll_response_schema_break_fails_closed(self):
        client = self._client({"status": "definitely-not-a-status"})
        with self.assertRaises(worker.WorkerError):
            worker.poll_and_dispatch_once(self.creds, state_dir=self.state, client=client)
        self.assertEqual([p for p, _ in self.calls], ["/api/v1/worker/poll"])

    def test_no_secret_in_payloads_or_logs(self):
        out, err = io.StringIO(), io.StringIO()
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        root.addHandler(handler)
        old_level = root.level
        root.setLevel(logging.DEBUG)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                client = self._client(self._poll_job(target={"server_id": "srv-other"}))
                worker.poll_and_dispatch_once(self.creds, state_dir=self.state, client=client)
                hb = json.loads((CC_FIXTURES / "worker-heartbeat.response" / "valid-01.json").read_text())
                worker.send_heartbeat(self.creds, client=worker.HttpClient(
                    mock_handlers={"/api/v1/worker/heartbeat": self._recording("/api/v1/worker/heartbeat", hb)}))
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)
        blob = json.dumps([d for _, d in self.calls]) + out.getvalue() + err.getvalue() + stream.getvalue()
        self.assertNotIn(self.creds.private_key, blob)
        self.assertFalse(SECRET_SHAPES.search(blob))
        for _, payload in self.calls:
            self.assertEqual(schema_mod.scan_for_raw_secrets(payload), [])


class TestSchemaContract(unittest.TestCase):
    def test_schema_loads_and_fixture_sets_are_complete(self):
        schema = schema_mod.load_json(CONTRACT_V1 / "worker-compatibility-manifest.schema.json")
        schema_mod.validate_schema(schema)
        valid = sorted(p.name for p in FIXTURES.glob("valid-*.json"))
        invalid = sorted(p.name for p in FIXTURES.glob("invalid-*.json"))
        self.assertGreaterEqual(len(valid), 3)
        self.assertGreaterEqual(len(invalid), 6)
        for name in invalid:
            self.assertTrue((FIXTURES / name.replace(".json", ".reason.txt")).is_file(), name)

    def test_compatibility_contract_is_stdlib_validator_clean(self):
        # A fixture mutation must be rejected by the schema, not only by the consumer.
        m = copy.deepcopy(fixture("valid-stable.json"))
        m["worker_protocol"]["transport"] = "inbound_websocket"
        schema = schema_mod.load_json(CONTRACT_V1 / "worker-compatibility-manifest.schema.json")
        self.assertNotEqual(schema_mod.validate(m, schema), [])


if __name__ == "__main__":
    unittest.main()
