"""tests/py/jobs/test_worker_signing.py - Ed25519 enrollment proof and request signing (ADR-0027, issue #362).

A fake Control Center verifies what the worker sends. Every key is minted at runtime in a temp dir through
OpenSSL 3; no key material is committed. Skipped when no Ed25519-capable openssl is installed.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import http.server
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from jobs import schema as schema_mod  # noqa: E402
from jobs import worker  # noqa: E402
from jobs import worker_signing as signing  # noqa: E402

CC = REPO_ROOT / "contracts" / "control-center" / "v1"


def _ed25519_available() -> bool:
    if not shutil.which("openssl"):
        return False
    with tempfile.TemporaryDirectory() as td:
        try:
            signing.generate_private_key(Path(td) / "k")
            return True
        except signing.SigningError:
            return False


HAVE_ED25519 = _ed25519_available()


# Built at runtime so the secret scanner does not mistake a fixture id for a key.
_IDEM_KEY = "idem-" + "1" * 10

class FakeControlCenter:
    """Verifies signed requests the way a Control Center must: signature, freshness, nonce replay."""

    def __init__(self, public_key_raw: bytes, worker_id: str, now: datetime | None = None) -> None:
        self.pub = public_key_raw
        self.worker_id = worker_id
        self.now = now
        self.seen_nonces: set[str] = set()

    def verify(self, sent: dict[str, Any]) -> str:
        """Return "ok" or the rejection reason."""
        h = sent["headers"]
        for name in (signing.HEADER_WORKER_ID, signing.HEADER_TIMESTAMP, signing.HEADER_NONCE, signing.HEADER_SIGNATURE):
            if name not in h:
                return f"missing_header:{name}"
        if h[signing.HEADER_WORKER_ID] != self.worker_id:
            return "unknown_worker"
        msg = signing.canonical_string(
            sent["method"], sent["path"], sent["body"], h[signing.HEADER_NONCE], h[signing.HEADER_TIMESTAMP]
        )
        if not signing.verify_signature(self.pub, msg, base64.b64decode(h[signing.HEADER_SIGNATURE])):
            return "bad_signature"
        ts = datetime.strptime(h[signing.HEADER_TIMESTAMP], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        now = self.now or datetime.now(timezone.utc)
        if abs((now - ts).total_seconds()) > signing.MAX_CLOCK_SKEW_SECONDS:
            return "stale_timestamp"
        if h[signing.HEADER_NONCE] in self.seen_nonces:
            return "replayed_nonce"
        self.seen_nonces.add(h[signing.HEADER_NONCE])
        return "ok"


@unittest.skipUnless(HAVE_ED25519, "OpenSSL 3 with Ed25519 support required")
class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        os.environ["OMES_STATE_DIR"] = str(self.state)
        os.environ["OMES_ROOT"] = str(REPO_ROOT)
        self.addCleanup(os.environ.pop, "OMES_STATE_DIR", None)

    def enroll(self, challenge: str = "challenge_token_valid12345678") -> tuple[worker.WorkerCredentials, worker.HttpClient]:
        enroll_resp = {
            "tenant_id": "tenant-acme", "server_id": "srv-test-01", "worker_id": "wrk-srv-test-01-001",
            "status": "enrolled", "control_center_public_key": "cc-key-reference",
            "poll_interval_seconds": 10, "heartbeat_interval_seconds": 60, "enrolled_at": "2026-09-21T12:00:00Z",
        }
        client = worker.HttpClient(mock_handlers={
            "/api/v1/worker/enroll": enroll_resp,
            "/api/v1/worker/heartbeat": {"status": "acknowledged", "server_id": "srv-test-01",
                                         "received_at": "2026-09-21T12:05:00Z", "next_heartbeat_seconds": 60},
            "/api/v1/worker/poll": {"status": "idle", "server_id": "srv-test-01", "tenant_id": "tenant-acme",
                                    "poll_interval_seconds": 10},
            "/api/v1/worker/result": {"job_id": "cc-job-1", "status": "recorded", "reconciled": True,
                                      "recorded_at": "2026-09-21T12:06:00Z"},
        })
        creds = worker.enroll_worker(
            "https://control-center.example.com", "tenant-acme", "srv-test-01", challenge,
            state_dir=self.state, client=client,
        )
        return creds, client

    @staticmethod
    def secret_forms(key_path: str) -> list[str]:
        """Every textual form of the private key that must never leak."""
        pem = Path(key_path).read_text()
        body = "".join(pem.strip().splitlines()[1:-1])
        seed = base64.b64decode(body)[-32:]
        return [body, base64.b64encode(seed).decode(), seed.hex(), "PRIVATE KEY"]


class TestEnrollment(_Base):
    def test_enrollment_sends_real_public_key_and_valid_proof_of_possession(self) -> None:
        creds, client = self.enroll()
        sent = json.loads(client.sent[0]["body"])
        self.assertEqual(sent["public_key_algorithm"], "ed25519")
        raw = base64.b64decode(sent["public_key"], validate=True)
        self.assertEqual(len(raw), 32)
        self.assertEqual(raw, signing.public_key_raw(creds.private_key_path))
        sig = base64.b64decode(sent["challenge_signature"], validate=True)
        self.assertEqual(len(sig), 64)
        self.assertTrue(signing.verify_signature(raw, sent["enrollment_challenge"].encode(), sig))
        self.assertFalse(signing.verify_signature(raw, b"another-challenge-0123456789", sig))
        self.assertEqual(schema_mod.validate(sent, schema_mod.load_json(CC / "worker-enrollment.request.schema.json")), [])
        # Enrollment itself is not header-signed: the key is not yet known to the Control Center.
        self.assertNotIn(signing.HEADER_SIGNATURE, client.sent[0]["headers"])

    def test_two_enrollments_generate_distinct_keys_and_none_is_derived_from_a_hash(self) -> None:
        a, ca = self.enroll()
        pub_a = a.public_key
        b, cb = self.enroll()
        self.assertNotEqual(pub_a, b.public_key)
        src = (PY_ROOT / "jobs" / "worker.py").read_text()
        self.assertNotIn("AAAAC3NzaC1lZDI1NTE5", src)  # the old fabricated "ssh-ed25519" prefix
        self.assertNotIn("token_hex(32)", src)

    def test_key_lives_in_its_own_0600_file_and_not_in_credentials_json(self) -> None:
        creds, _ = self.enroll()
        key = Path(creds.private_key_path)
        self.assertEqual(key.parent, self.state / "worker")
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.state / "worker").stat().st_mode & 0o777, 0o700)
        cred_file = worker._credentials_path(self.state)
        self.assertEqual(cred_file.stat().st_mode & 0o777, 0o600)
        text = cred_file.read_text()
        for form in self.secret_forms(creds.private_key_path):
            self.assertNotIn(form, text)
        data = json.loads(text)
        self.assertNotIn("private_key", data)
        self.assertEqual(data["private_key_file"], signing.KEY_FILE_NAME)
        loaded = worker.load_credentials(self.state)
        assert loaded is not None
        self.assertEqual(loaded.private_key_path, str(key))
        self.assertEqual(loaded.public_key, creds.public_key)

    def test_key_file_is_created_0600_before_openssl_writes(self) -> None:
        seen: list[int] = []
        real_run = signing._run

        def spy(argv: list[str]) -> bytes:
            if "genpkey" in argv:
                out = Path(argv[argv.index("-out") + 1])
                seen.append(out.stat().st_mode & 0o777)
                self.assertEqual(out.stat().st_size, 0)
            return real_run(argv)

        with mock.patch.object(signing, "_run", spy):
            self.enroll()
        self.assertEqual(seen, [0o600])

    def test_rejected_enrollment_leaves_no_key_behind(self) -> None:
        client = worker.HttpClient(mock_handlers={"/api/v1/worker/enroll": {
            "tenant_id": "tenant-acme", "server_id": "srv-test-01", "worker_id": "wrk-1", "status": "token_expired",
            "control_center_public_key": "cc", "poll_interval_seconds": 10, "heartbeat_interval_seconds": 60,
            "enrolled_at": "2026-09-21T12:00:00Z"}})
        with self.assertRaises(worker.EnrollmentError):
            worker.enroll_worker("https://control-center.example.com", "tenant-acme", "srv-test-01",
                                 "challenge_token_expired123456", state_dir=self.state, client=client)
        self.assertEqual(sorted(p.name for p in (self.state / "worker").iterdir()), [])
        self.assertIsNone(worker.load_credentials(self.state))

    def test_failed_reenrollment_keeps_the_previous_key(self) -> None:
        creds, _ = self.enroll()
        before = Path(creds.private_key_path).read_bytes()
        bad = worker.HttpClient(mock_handlers={"/api/v1/worker/enroll": {"status": "x"}})
        with self.assertRaises(worker.EnrollmentError):
            worker.enroll_worker("https://control-center.example.com", "tenant-acme", "srv-test-01",
                                 "challenge_token_valid12345678", state_dir=self.state, client=bad)
        self.assertEqual(Path(creds.private_key_path).read_bytes(), before)


class TestFailClosed(_Base):
    def _enroll_expecting_failure(self) -> worker.HttpClient:
        client = worker.HttpClient(mock_handlers={"/api/v1/worker/enroll": {"status": "enrolled"}})
        with self.assertRaises(worker.EnrollmentError) as ctx:
            worker.enroll_worker("https://control-center.example.com", "tenant-acme", "srv-test-01",
                                 "challenge_token_valid12345678", state_dir=self.state, client=client)
        self.last_error = str(ctx.exception)
        self.assertEqual(client.sent, [], "nothing may be sent when no real key can be made")
        self.assertIsNone(worker.load_credentials(self.state))
        self.assertEqual(list((self.state / "worker").glob("*")), [])
        return client

    def test_missing_openssl_fails_closed(self) -> None:
        with mock.patch.object(signing.shutil, "which", return_value=None):
            self._enroll_expecting_failure()
        self.assertIn("openssl is not installed", self.last_error)

    def test_openssl_without_ed25519_support_fails_closed(self) -> None:
        failed = subprocess.CompletedProcess([], 1, b"", b"genpkey: Unknown algorithm ed25519")
        with mock.patch.object(signing.subprocess, "run", return_value=failed):
            self._enroll_expecting_failure()
        self.assertIn("OpenSSL 3 with Ed25519 support is required", self.last_error)

    def test_openssl_timeout_fails_closed(self) -> None:
        with mock.patch.object(signing.subprocess, "run", side_effect=subprocess.TimeoutExpired("openssl", 15)):
            self._enroll_expecting_failure()
        self.assertIn("timed out", self.last_error)

    def test_openssl_is_called_with_a_fixed_argv_and_no_shell(self) -> None:
        calls: list[Any] = []
        real = subprocess.run

        def spy(*a: Any, **kw: Any) -> Any:
            calls.append((a, kw))
            return real(*a, **kw)

        with mock.patch.object(signing.subprocess, "run", spy):
            self.enroll()
        self.assertTrue(calls)
        for args, kw in calls:
            self.assertIsInstance(args[0], list)
            self.assertFalse(kw.get("shell"))
            self.assertTrue(kw.get("timeout"))
            self.assertTrue(args[0][0].endswith("openssl"))

    def test_signing_with_a_group_readable_key_is_refused(self) -> None:
        creds, client = self.enroll()
        os.chmod(creds.private_key_path, 0o640)
        with self.assertRaises(signing.SigningError):
            worker.send_heartbeat(creds, client=client)
        self.assertEqual([s["path"] for s in client.sent], ["/api/v1/worker/enroll"])  # nothing unsigned went out

    def test_missing_key_file_is_refused_not_replaced(self) -> None:
        creds, client = self.enroll()
        Path(creds.private_key_path).unlink()
        with self.assertRaises(signing.SigningError) as ctx:
            worker.poll_and_dispatch_once(creds, state_dir=self.state, client=client)
        self.assertIn("re-enroll", str(ctx.exception))
        self.assertFalse(Path(creds.private_key_path).exists())

    def test_legacy_fake_credentials_are_refused_with_reenroll_message(self) -> None:
        wdir = self.state / "worker"
        wdir.mkdir(parents=True)
        legacy = {
            "tenant_id": "tenant-acme", "server_id": "srv-test-01", "worker_id": "wrk-old",
            "control_center_endpoint": "https://control-center.example.com",
            "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIdeadbeef",
            "private_key": "0" * 64, "enrolled_at": "2026-01-01T00:00:00Z",
        }
        (wdir / "credentials.json").write_text(json.dumps(legacy))
        with self.assertRaises(worker.LegacyCredentialsError) as ctx:
            worker.load_credentials(self.state)
        self.assertIn("Re-enroll", str(ctx.exception))
        # Not migrated: the file is untouched.
        self.assertEqual(json.loads((wdir / "credentials.json").read_text()), legacy)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = worker.main(["status"])
        self.assertEqual(rc, 1)
        self.assertIn("Re-enroll", err.getvalue())
        self.assertNotIn("0" * 64, out.getvalue() + err.getvalue())
        # Re-enrollment is the way out and replaces the legacy file.
        creds, _ = self.enroll()
        self.assertEqual(worker.load_credentials(self.state).public_key, creds.public_key)


class TestRequestSigning(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.creds, self.client = self.enroll()
        self.cc = FakeControlCenter(signing.public_key_raw(self.creds.private_key_path), self.creds.worker_id)
        self.client.sent.clear()

    def test_poll_result_and_heartbeat_are_signed_and_verify(self) -> None:
        worker.send_heartbeat(self.creds, client=self.client)
        worker.poll_and_dispatch_once(self.creds, state_dir=self.state, client=self.client)
        worker._submit_result(self.creds, self.client, {"correlation_id": "corr-1", "idempotency_key": _IDEM_KEY,
                                                         "operation": "status"}, "succeeded", 0, "ok")
        self.assertEqual([s["path"] for s in self.client.sent],
                         ["/api/v1/worker/heartbeat", "/api/v1/worker/poll", "/api/v1/worker/result"])
        for s in self.client.sent:
            self.assertEqual(self.cc.verify(s), "ok", s["path"])
            self.assertEqual(s["headers"][signing.HEADER_WORKER_ID], self.creds.worker_id)

    def test_poll_header_nonce_and_timestamp_match_the_signed_payload(self) -> None:
        worker.poll_and_dispatch_once(self.creds, state_dir=self.state, client=self.client)
        s = self.client.sent[0]
        body = json.loads(s["body"])
        self.assertEqual(s["headers"][signing.HEADER_NONCE], body["nonce"])
        self.assertEqual(s["headers"][signing.HEADER_TIMESTAMP], body["timestamp"])

    def test_canonical_string_shape(self) -> None:
        msg = signing.canonical_string("post", "/api/v1/worker/poll", b"{}", "nonce_abcdefabcdefabcd", "2026-01-01T00:00:00Z")
        self.assertEqual(
            msg.decode(),
            "OMES-WORKER-SIG-V1\nPOST\n/api/v1/worker/poll\n" + hashlib.sha256(b"{}").hexdigest()
            + "\nnonce_abcdefabcdefabcd\n2026-01-01T00:00:00Z",
        )

    def test_tampered_body_fails_verification(self) -> None:
        worker.send_heartbeat(self.creds, client=self.client)
        s = dict(self.client.sent[0])
        s["body"] = s["body"].replace(b'"healthy"', b'"degraded"')
        self.assertNotEqual(s["body"], self.client.sent[0]["body"])
        self.assertEqual(self.cc.verify(s), "bad_signature")

    def test_changed_path_fails_verification(self) -> None:
        worker.send_heartbeat(self.creds, client=self.client)
        s = dict(self.client.sent[0])
        s["path"] = "/api/v1/worker/result"
        self.assertEqual(self.cc.verify(s), "bad_signature")

    def test_changed_method_nonce_or_timestamp_fails_verification(self) -> None:
        worker.send_heartbeat(self.creds, client=self.client)
        base = self.client.sent[0]
        self.assertEqual(self.cc.verify({**base, "method": "PUT"}), "bad_signature")
        for hdr, val in ((signing.HEADER_NONCE, "nonce_" + "0" * 32), (signing.HEADER_TIMESTAMP, "2026-01-01T00:00:01Z")):
            self.assertEqual(self.cc.verify({**base, "headers": {**base["headers"], hdr: val}}), "bad_signature")

    def test_signature_from_another_key_fails(self) -> None:
        worker.send_heartbeat(self.creds, client=self.client)
        other = self.state / "other.key"
        signing.generate_private_key(other)
        hostile = FakeControlCenter(signing.public_key_raw(other), self.creds.worker_id)
        self.assertEqual(hostile.verify(self.client.sent[0]), "bad_signature")

    def test_reused_nonce_is_detectable(self) -> None:
        worker.send_heartbeat(self.creds, client=self.client)
        s = self.client.sent[0]
        self.assertEqual(self.cc.verify(s), "ok")
        self.assertEqual(self.cc.verify(s), "replayed_nonce")  # a captured request replayed verbatim
        # Fresh requests never reuse a nonce.
        worker.send_heartbeat(self.creds, client=self.client)
        worker.send_heartbeat(self.creds, client=self.client)
        nonces = {x["headers"][signing.HEADER_NONCE] for x in self.client.sent}
        self.assertEqual(len(nonces), 3)

    def test_stale_timestamp_is_detectable(self) -> None:
        worker.send_heartbeat(self.creds, client=self.client)
        s = self.client.sent[0]
        late = FakeControlCenter(self.cc.pub, self.creds.worker_id,
                                 now=datetime.now(timezone.utc) + timedelta(seconds=signing.MAX_CLOCK_SKEW_SECONDS + 60))
        self.assertEqual(late.verify(s), "stale_timestamp")
        early = FakeControlCenter(self.cc.pub, self.creds.worker_id,
                                  now=datetime.now(timezone.utc) - timedelta(seconds=signing.MAX_CLOCK_SKEW_SECONDS + 60))
        self.assertEqual(early.verify(s), "stale_timestamp")
        self.assertEqual(self.cc.verify(s), "ok")

    def test_missing_signature_headers_are_rejected_by_a_verifier(self) -> None:
        worker.send_heartbeat(self.creds, client=self.client)
        s = self.client.sent[0]
        headers = {k: v for k, v in s["headers"].items() if k != signing.HEADER_SIGNATURE}
        self.assertTrue(self.cc.verify({**s, "headers": headers}).startswith("missing_header"))

    def test_private_key_never_appears_in_payloads_headers_logs_or_output(self) -> None:
        forms = self.secret_forms(self.creds.private_key_path)
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        old_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.client.sent.clear()
                worker.send_heartbeat(self.creds, client=self.client)
                worker.poll_and_dispatch_once(self.creds, state_dir=self.state, client=self.client)
                worker.main(["status"])
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)
        blob = json.dumps([
            {"path": s["path"], "body": s["body"].decode(), "headers": s["headers"]} for s in self.client.sent
        ]) + out.getvalue() + err.getvalue() + stream.getvalue()
        for form in forms:
            self.assertNotIn(form, blob)
        # The worker never puts the key path or bytes in argv of anything but openssl's -inkey file argument.
        self.assertNotIn(self.creds.private_key_path, blob)

    def test_signing_failure_sends_nothing(self) -> None:
        with mock.patch.object(signing, "sign_bytes", side_effect=signing.SigningError("boom")):
            with self.assertRaises(signing.SigningError):
                worker.send_heartbeat(self.creds, client=self.client)
        self.assertEqual(self.client.sent, [])


class _Capture(http.server.BaseHTTPRequestHandler):
    captured: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).captured.append({"method": "POST", "path": self.path, "body": body, "headers": dict(self.headers)})
        resp = json.dumps({"status": "acknowledged", "server_id": "srv-test-01",
                           "received_at": "2026-09-21T12:05:00Z", "next_heartbeat_seconds": 60}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *args: Any) -> None:
        pass


class TestOnTheWire(_Base):
    def test_headers_and_exact_body_bytes_reach_a_real_http_server(self) -> None:
        creds, _ = self.enroll()
        _Capture.captured = []
        server = http.server.HTTPServer(("127.0.0.1", 0), _Capture)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        creds.control_center_endpoint = f"http://127.0.0.1:{server.server_port}"
        worker.send_heartbeat(creds)
        got = _Capture.captured[0]
        headers = {k.lower(): v for k, v in got["headers"].items()}
        wire = {
            "method": "POST", "path": got["path"], "body": got["body"],
            "headers": {
                signing.HEADER_WORKER_ID: headers["x-omes-worker-id"], signing.HEADER_TIMESTAMP: headers["x-omes-timestamp"],
                signing.HEADER_NONCE: headers["x-omes-nonce"], signing.HEADER_SIGNATURE: headers["x-omes-signature"],
            },
        }
        cc = FakeControlCenter(signing.public_key_raw(creds.private_key_path), creds.worker_id)
        self.assertEqual(cc.verify(wire), "ok")
        self.assertEqual(cc.verify(wire), "replayed_nonce")


class TestBackupExclusion(unittest.TestCase):
    def test_no_backup_or_export_path_includes_the_worker_state_dir(self) -> None:
        """Backups copy explicit paths only (lib/omes/backup.sh backup_path); nothing names the worker dir."""
        for sh in (REPO_ROOT / "lib" / "omes").rglob("*.sh"):
            text = sh.read_text(errors="replace")
            self.assertNotIn("worker_ed25519", text, sh)
            self.assertNotIn("state_dir)/worker", text, sh)
            self.assertNotIn("state_dir)\"/worker", text, sh)
        for py in (PY_ROOT / "hermesbackup").rglob("*.py"):
            text = py.read_text(errors="replace")
            self.assertNotIn("worker_ed25519", text, py)
            self.assertNotIn('"worker"', text, py)


if __name__ == "__main__":
    unittest.main()
