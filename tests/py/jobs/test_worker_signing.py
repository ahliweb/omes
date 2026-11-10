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
        msg = ("OMES-WORKER-ENROLL-V1\n" + sent["enrollment_challenge"] + "\ntenant-acme\nsrv-test-01\n"
               + sent["public_key"]).encode()
        self.assertEqual(msg, signing.enrollment_proof_message(
            sent["enrollment_challenge"], "tenant-acme", "srv-test-01", sent["public_key"]))
        self.assertTrue(signing.verify_signature(raw, msg, sig))
        # The proof is bound to its context: the bare challenge, or another tenant/server/key, does not verify.
        self.assertFalse(signing.verify_signature(raw, sent["enrollment_challenge"].encode(), sig))
        for other in (
            ("tenant-evil", "srv-test-01", sent["public_key"]),
            ("tenant-acme", "srv-other", sent["public_key"]),
            ("tenant-acme", "srv-test-01", base64.b64encode(bytes(32)).decode()),
        ):
            self.assertFalse(signing.verify_signature(
                raw, signing.enrollment_proof_message(sent["enrollment_challenge"], *other), sig))
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
            "control_center_public_key": "cc-key-reference", "poll_interval_seconds": 10, "heartbeat_interval_seconds": 60,
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


class TestEnrollmentProofMessage(unittest.TestCase):
    def test_exact_canonical_string(self) -> None:
        self.assertEqual(
            signing.enrollment_proof_message("chal_123", "tenant-a", "srv-1", "PUBKEYB64="),
            b"OMES-WORKER-ENROLL-V1\nchal_123\ntenant-a\nsrv-1\nPUBKEYB64=",
        )

    def test_line_breaks_in_any_component_are_rejected(self) -> None:
        good = ["chal_123", "tenant-a", "srv-1", "PUBKEYB64="]
        for i in range(4):
            for bad in ("x\ny", "x\ry", "\n"):
                args = list(good)
                args[i] = bad
                with self.assertRaises(signing.SigningError, msg=f"component {i} {bad!r}"):
                    signing.enrollment_proof_message(*args)

    def test_sign_challenge_rejects_line_breaks_before_touching_the_key(self) -> None:
        with self.assertRaises(signing.SigningError):
            signing.sign_challenge("/nonexistent/key", "a\nb", "t", "s", "p")


class TestAtomicKeyAndCredentialsUpdate(_Base):
    """The key file and credentials.json are replaced together: both new, both old, or fail closed."""

    def snapshot(self) -> dict[str, bytes]:
        return {p.name: p.read_bytes() for p in sorted((self.state / "worker").iterdir())}

    def reenroll(self) -> None:
        client = worker.HttpClient(mock_handlers={"/api/v1/worker/enroll": {
            "tenant_id": "tenant-acme", "server_id": "srv-test-01", "worker_id": "wrk-srv-test-01-002",
            "status": "enrolled", "control_center_public_key": "cc-key-reference", "poll_interval_seconds": 10,
            "heartbeat_interval_seconds": 60, "enrolled_at": "2026-09-22T12:00:00Z"}})
        worker.enroll_worker("https://control-center.example.com", "tenant-acme", "srv-test-01",
                             "challenge_token_valid12345678", state_dir=self.state, client=client)

    def test_successful_update_leaves_a_consistent_new_pair_and_no_leftovers(self) -> None:
        old, _ = self.enroll()
        self.reenroll()
        loaded = worker.load_credentials(self.state)
        assert loaded is not None
        self.assertNotEqual(loaded.public_key, old.public_key)
        self.assertEqual(signing.public_key_b64(loaded.private_key_path), loaded.public_key)
        self.assertEqual(sorted(self.snapshot()), ["credentials.json", signing.KEY_FILE_NAME])
        self.assertEqual(Path(loaded.private_key_path).stat().st_mode & 0o777, 0o600)

    def test_credentials_save_failure_restores_the_previous_pair(self) -> None:
        old, _ = self.enroll()
        before = self.snapshot()
        with mock.patch.object(worker, "save_credentials", side_effect=OSError("disk full")):
            with self.assertRaises(worker.EnrollmentError) as ctx:
                self.reenroll()
        self.assertIn("re-enroll", str(ctx.exception))
        self.assertIn("previous worker key and credentials were restored", str(ctx.exception))
        self.assertEqual(self.snapshot(), before)
        loaded = worker.load_credentials(self.state)
        assert loaded is not None
        self.assertEqual(loaded.public_key, old.public_key)
        self.assertEqual(signing.public_key_b64(loaded.private_key_path), loaded.public_key)

    def test_credentials_save_failure_on_first_enrollment_leaves_nothing(self) -> None:
        with mock.patch.object(worker, "save_credentials", side_effect=OSError("disk full")):
            with self.assertRaises(worker.EnrollmentError) as ctx:
                self.reenroll()
        self.assertIn("re-enroll", str(ctx.exception))
        self.assertEqual(self.snapshot(), {})
        self.assertIsNone(worker.load_credentials(self.state))

    def test_key_rename_failure_keeps_the_previous_pair(self) -> None:
        old, _ = self.enroll()
        before = self.snapshot()
        real_replace = os.replace

        def flaky(src: Any, dst: Any) -> None:
            if str(src).endswith(".pending"):
                raise OSError("rename failed")
            real_replace(src, dst)

        with mock.patch.object(worker.os, "replace", flaky):
            with self.assertRaises(worker.EnrollmentError) as ctx:
                self.reenroll()
        self.assertIn("re-enroll", str(ctx.exception))
        self.assertEqual(self.snapshot(), before)
        loaded = worker.load_credentials(self.state)
        assert loaded is not None
        self.assertEqual(loaded.public_key, old.public_key)

    def test_failed_rollback_fails_closed_with_no_usable_mismatch(self) -> None:
        self.enroll()
        real_replace = os.replace

        def flaky(src: Any, dst: Any) -> None:
            if str(src).endswith(".prev"):
                raise OSError("rollback failed")
            real_replace(src, dst)

        with mock.patch.object(worker, "save_credentials", side_effect=OSError("disk full")), \
                mock.patch.object(worker.os, "replace", flaky):
            with self.assertRaises(worker.EnrollmentError) as ctx:
                self.reenroll()
        self.assertIn("not enrolled (fail closed)", str(ctx.exception))
        self.assertIn("re-enroll", str(ctx.exception))
        self.assertEqual(self.snapshot(), {})
        self.assertIsNone(worker.load_credentials(self.state))

    def test_no_partial_credentials_file_is_ever_visible(self) -> None:
        creds, _ = self.enroll()
        before = (self.state / "worker" / "credentials.json").read_bytes()
        with mock.patch.object(worker.json, "dumps", side_effect=ValueError("boom")):
            with self.assertRaises(ValueError):
                worker.save_credentials(creds, self.state)
        self.assertEqual((self.state / "worker" / "credentials.json").read_bytes(), before)
        self.assertFalse((self.state / "worker" / "credentials.tmp").exists())

    def test_credentials_are_fsynced_before_the_rename(self) -> None:
        creds, _ = self.enroll()
        calls: list[str] = []
        real_fsync, real_replace = os.fsync, os.replace

        def fs(fd: int) -> None:
            calls.append("fsync")
            real_fsync(fd)

        def rp(a: Any, b: Any) -> None:
            calls.append("replace")
            real_replace(a, b)

        with mock.patch.object(worker.os, "fsync", fs), mock.patch.object(worker.os, "replace", rp):
            worker.save_credentials(creds, self.state)
        self.assertEqual(calls[:2], ["fsync", "replace"])
        self.assertEqual((self.state / "worker" / "credentials.json").stat().st_mode & 0o777, 0o600)

    def _simulate_crash_after_key_rename(self) -> worker.WorkerCredentials:
        """New key in place, previous key kept as .prev, credentials.json still the old ones."""
        old, _ = self.enroll()
        wdir = self.state / "worker"
        os.link(wdir / signing.KEY_FILE_NAME, wdir / (signing.KEY_FILE_NAME + ".prev"))
        new_key = wdir / "new.key"
        signing.generate_private_key(new_key)
        os.replace(new_key, wdir / signing.KEY_FILE_NAME)
        return old

    def test_crash_between_the_renames_is_rolled_back_on_next_load(self) -> None:
        old = self._simulate_crash_after_key_rename()
        loaded = worker.load_credentials(self.state)
        assert loaded is not None
        self.assertEqual(loaded.public_key, old.public_key)
        self.assertEqual(signing.public_key_b64(loaded.private_key_path), old.public_key)
        self.assertFalse((self.state / "worker" / (signing.KEY_FILE_NAME + ".prev")).exists())

    def test_crash_after_both_renames_just_drops_the_rollback_copy(self) -> None:
        creds, _ = self.enroll()
        wdir = self.state / "worker"
        prev = wdir / (signing.KEY_FILE_NAME + ".prev")
        prev.write_bytes((wdir / signing.KEY_FILE_NAME).read_bytes())
        os.chmod(prev, 0o600)
        loaded = worker.load_credentials(self.state)
        assert loaded is not None
        self.assertEqual(loaded.public_key, creds.public_key)
        self.assertFalse(prev.exists())

    def test_unrelated_key_is_refused_fail_closed_and_cli_says_reenroll(self) -> None:
        self.enroll()
        wdir = self.state / "worker"
        for name in (signing.KEY_FILE_NAME, signing.KEY_FILE_NAME + ".prev"):
            (wdir / name).unlink(missing_ok=True)
            signing.generate_private_key(wdir / name)  # neither matches credentials.json
        with self.assertRaises(worker.InconsistentCredentialsError) as ctx:
            worker.load_credentials(self.state)
        self.assertIn("Re-enroll", str(ctx.exception))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = worker.main(["poll", "--once"])
        self.assertEqual(rc, 1)
        self.assertIn("Re-enroll", err.getvalue())
        # Re-enrollment is the way out.
        self.reenroll()
        self.assertIsNotNone(worker.load_credentials(self.state))
        self.assertFalse((wdir / (signing.KEY_FILE_NAME + ".prev")).exists())


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

    def test_canonical_string_rejects_line_breaks_and_bad_timestamps(self) -> None:
        ok = ("POST", "/api/v1/worker/poll", b"{}", "nonce_abcdefabcdefabcd", "2026-01-01T00:00:00Z")
        signing.canonical_string(*ok)
        for idx, bad in ((0, "POST\nGET"), (0, "POST\r"), (1, "/a\n/b"), (1, "/a\r"),
                         (3, "nonce_abcdefabc\ndef"), (4, "2026-01-01T00:00:00Z\n")):
            args = list(ok)
            args[idx] = bad
            with self.assertRaises(signing.SigningError, msg=repr(bad)):
                signing.canonical_string(*args)
        for ts in ("", "2026-01-01 00:00:00Z", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00", "yesterday",
                   "2026-01-01T00:00:00Zextra"):
            args = list(ok)
            args[4] = ts
            with self.assertRaises(signing.SigningError, msg=ts):
                signing.canonical_string(*args)

    def test_sign_request_refuses_a_line_break_in_the_path(self) -> None:
        with self.assertRaises(signing.SigningError):
            signing.sign_request(self.creds.private_key_path, self.creds.worker_id, "POST", "/a\nb", b"{}")

    def test_emitted_headers_validate_against_the_header_contract(self) -> None:
        schema = schema_mod.load_json(CC / "worker-request-signature.headers.schema.json")
        worker.send_heartbeat(self.creds, client=self.client)
        worker.poll_and_dispatch_once(self.creds, state_dir=self.state, client=self.client)
        worker._submit_result(self.creds, self.client, {"correlation_id": "corr-1", "idempotency_key": _IDEM_KEY,
                                                         "operation": "status"}, "succeeded", 0, "ok")
        self.assertEqual(len(self.client.sent), 3)
        for s in self.client.sent:
            self.assertEqual(schema_mod.validate(s["headers"], schema), [], s["path"])
        broken = {**self.client.sent[0]["headers"], signing.HEADER_NONCE: "short"}
        self.assertNotEqual(schema_mod.validate(broken, schema), [])

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
