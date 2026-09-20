"""Tests for lib/omes/py/jobs/runner.py (issue #90): execution mapping,
timeout handling, retry classification, read-back reconciliation, and
secret redaction."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from . import _pathfix  # noqa: F401

from jobs import audit, runner, store  # noqa: E402


def make_request(**overrides) -> dict:
    request = {
        "tenant_id": "tenant-acme",
        "correlation_id": "corr-1",
        "idempotency_key": "idem-0001",
        "actor": {"type": "user", "id": "op-1"},
        "operation": "status",
        "target": {"server_id": "srv-1"},
    }
    request.update(overrides)
    return request


class RunnerTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-jobs-runner-test-")
        self.root = Path(self._tmp) / "state"
        store.paths.ensure_layout(self.root)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestNotImplementedOperations(RunnerTestBase):
    def test_install_is_not_implemented(self):
        # "install" is neither destructive nor in the default
        # auto-approve allowlist, so it requires an explicit approval
        # before `run` - same as any other operation not on that list.
        record, _ = store.submit(make_request(operation="install"), root=self.root)
        store.approve(record, actor="approver-1", root=self.root)
        updated = runner.run(record, actor="op-1", root=self.root)
        self.assertEqual(updated["state"], "failed")
        self.assertEqual(updated["error"]["code"], "not_implemented")
        self.assertFalse(updated["error"]["retryable"])

    def test_configure_requires_approval_and_is_not_implemented(self):
        record, _ = store.submit(make_request(operation="configure"), root=self.root)
        with self.assertRaises(store.ApprovalRequiredError):
            runner.run(record, actor="op-1", root=self.root)
        store.approve(record, actor="approver-1", root=self.root)
        updated = runner.run(record, actor="op-1", root=self.root)
        self.assertEqual(updated["state"], "failed")
        self.assertEqual(updated["error"]["code"], "not_implemented")


class TestApprovalGate(RunnerTestBase):
    def test_run_on_queued_non_destructive_auto_approves(self):
        record, _ = store.submit(make_request(operation="status"), root=self.root)
        self.assertEqual(record["state"], "queued")
        updated = runner.run(record, actor="op-1", root=self.root)
        self.assertIn(updated["state"], ("succeeded", "failed"))
        auto_approved = [h for h in updated["history"] if h["note"] == "auto-approved (policy)"]
        self.assertEqual(len(auto_approved), 1)

    def test_run_on_queued_destructive_raises_authorization_error(self):
        record, _ = store.submit(
            make_request(operation="stop", target={"server_id": "srv-1"}), root=self.root
        )
        with self.assertRaises(store.ApprovalRequiredError):
            runner.run(record, actor="op-1", root=self.root)
        self.assertEqual(record["state"], "queued")


class TestDefensiveArgvValidation(RunnerTestBase):
    """The #89 contract already rejects an option-like backup_id/
    rollback_ref at `omes job submit` time (tests/py/jobs/test_cli.py).
    These tests cover the SECOND, independent gate: even a job record
    that somehow reached the store with such a value (e.g. hand-edited
    on disk, or created before the schema was tightened) must never
    have it placed into a real bin/omes argv."""

    def _stored_record_bypassing_schema(self, **overrides) -> dict:
        # Builds a job record directly via store.new_job_record/save_job,
        # deliberately bypassing store.submit()'s schema validation, to
        # simulate a record that reached the store some other way.
        request = make_request(operation="restore", target={"server_id": "srv-1"})
        request.update(overrides)
        record = store.new_job_record(request)
        store.save_job(record, self.root)
        return record

    def test_option_like_backup_id_never_reaches_a_real_subprocess_call(self):
        record = self._stored_record_bypassing_schema(backup_id="--yes")
        store.approve(record, actor="approver-1", root=self.root)
        with mock.patch("subprocess.run") as subprocess_run_mock:
            updated = runner.run(record, actor="op-1", root=self.root)
        subprocess_run_mock.assert_not_called()
        self.assertEqual(updated["state"], "failed")
        self.assertEqual(updated["error"]["code"], "invalid_argument")

    def test_option_like_rollback_ref_never_reaches_a_real_subprocess_call(self):
        record = self._stored_record_bypassing_schema(operation="rollback", rollback_ref="-rf")
        store.approve(record, actor="approver-1", root=self.root)
        with mock.patch("subprocess.run") as subprocess_run_mock:
            updated = runner.run(record, actor="op-1", root=self.root)
        subprocess_run_mock.assert_not_called()
        self.assertEqual(updated["state"], "failed")
        self.assertEqual(updated["error"]["code"], "invalid_argument")

    def test_blanket_run_argv_check_catches_an_option_like_value_directly(self):
        # Exercises _run_argv()'s own second-layer check independent of
        # build_argv(), in case a future code path ever constructs argv
        # without going through store.require_safe_argv_value().
        with self.assertRaises(runner.UnsafeArgvError):
            runner._run_argv(["restore", "--json", "--yes", "--from", "--not-a-real-id"])

    def test_safe_backup_id_is_unaffected(self):
        record = self._stored_record_bypassing_schema(backup_id="backup-0001")
        store.approve(record, actor="approver-1", root=self.root)
        ok = {"ok": True, "timed_out": False, "duration_seconds": 0.01, "parsed": {"ok": True}, "output_tail": ""}
        with mock.patch.object(runner, "_run_argv", side_effect=[ok, ok]):
            updated = runner.run(record, actor="op-1", root=self.root)
        self.assertEqual(updated["state"], "succeeded")


class TestReadbackReconciliation(RunnerTestBase):
    def test_readback_mismatch_reports_failed_with_evidence_never_success(self):
        record, _ = store.submit(
            make_request(operation="backup", target={"server_id": "srv-1"}), root=self.root
        )
        execute_ok = {"ok": True, "timed_out": False, "duration_seconds": 0.01, "parsed": {"ok": True}, "output_tail": ""}
        readback_bad = {"ok": True, "timed_out": False, "duration_seconds": 0.01, "parsed": {"ok": False}, "output_tail": ""}
        with mock.patch.object(runner, "_run_argv", side_effect=[execute_ok, readback_bad]):
            updated = runner.run(record, actor="op-1", root=self.root)
        self.assertEqual(updated["state"], "failed")
        self.assertEqual(updated["error"]["code"], "reconciliation_mismatch")
        self.assertIn("readback", updated["evidence"])

    def test_readback_timeout_is_never_treated_as_success(self):
        record, _ = store.submit(
            make_request(operation="backup", target={"server_id": "srv-1"}), root=self.root
        )
        execute_ok = {"ok": True, "timed_out": False, "duration_seconds": 0.01, "parsed": {"ok": True}, "output_tail": ""}
        readback_timeout = {"ok": False, "timed_out": True, "duration_seconds": 5, "error": {"code": "timeout", "message": "x", "retryable": True}, "output_tail": ""}
        with mock.patch.object(runner, "_run_argv", side_effect=[execute_ok, readback_timeout]):
            updated = runner.run(record, actor="op-1", root=self.root)
        self.assertEqual(updated["state"], "failed")

    def test_matching_readback_succeeds(self):
        record, _ = store.submit(
            make_request(operation="backup", target={"server_id": "srv-1"}), root=self.root
        )
        ok = {"ok": True, "timed_out": False, "duration_seconds": 0.01, "parsed": {"ok": True}, "output_tail": ""}
        with mock.patch.object(runner, "_run_argv", side_effect=[ok, ok]):
            updated = runner.run(record, actor="op-1", root=self.root)
        self.assertEqual(updated["state"], "succeeded")

    def test_partial_readback_does_not_report_success(self):
        record, _ = store.submit(
            make_request(operation="backup", target={"server_id": "srv-1"}), root=self.root
        )
        execute = {
            "ok": True,
            "timed_out": False,
            "duration_seconds": 0.01,
            "parsed": {"ok": True, "desired": {"state": "complete"}},
            "output_tail": "",
        }
        readback = {
            "ok": True,
            "timed_out": False,
            "duration_seconds": 0.01,
            "parsed": {"ok": True, "observed": {"state": "partial"}},
            "output_tail": "",
        }
        with mock.patch.object(runner, "_run_argv", side_effect=[execute, readback]):
            updated = runner.run(record, actor="op-1", root=self.root)
        self.assertEqual(updated["state"], "failed")
        self.assertEqual(updated["error"]["code"], "reconciliation_mismatch")

    def test_rollback_success_reaches_rolled_back_state(self):
        record, _ = store.submit(
            make_request(
                operation="rollback",
                target={"server_id": "srv-1"},
                rollback_ref="20260101T000000Z",
            ),
            root=self.root,
        )
        store.approve(record, actor="approver-1", root=self.root)
        ok = {"ok": True, "timed_out": False, "duration_seconds": 0.01, "parsed": {"ok": True}, "output_tail": ""}
        with mock.patch.object(runner, "_run_argv", side_effect=[ok, ok]):
            updated = runner.run(record, actor="op-1", root=self.root)
        self.assertEqual(updated["state"], "rolled_back")


class TestRetryClassification(RunnerTestBase):
    def test_retryable_failure_can_run_again(self):
        record, _ = store.submit(make_request(operation="status"), root=self.root)
        transport_failure = {
            "ok": False,
            "timed_out": False,
            "duration_seconds": 0.01,
            "error": {"code": "transport_error", "message": "exit 8", "retryable": True},
            "output_tail": "",
        }
        with mock.patch.object(runner, "_run_argv", side_effect=[transport_failure]):
            updated = runner.run(record, actor="op-1", root=self.root)
        self.assertEqual(updated["state"], "failed")
        self.assertTrue(updated["error"]["retryable"])
        self.assertEqual(updated["attempts"], 1)

        ok = {"ok": True, "timed_out": False, "duration_seconds": 0.01, "parsed": {"ok": True}, "output_tail": ""}
        with mock.patch.object(runner, "_run_argv", side_effect=[ok]):
            updated2 = runner.run(updated, actor="op-1", root=self.root)
        self.assertEqual(updated2["state"], "succeeded")
        self.assertEqual(updated2["attempts"], 2)

    def test_nonretryable_failure_cannot_run_again(self):
        record, _ = store.submit(make_request(operation="status"), root=self.root)
        validation_failure = {
            "ok": False,
            "timed_out": False,
            "duration_seconds": 0.01,
            "error": {"code": "operation_failed", "message": "exit 2", "retryable": False},
            "output_tail": "",
        }
        with mock.patch.object(runner, "_run_argv", side_effect=[validation_failure]):
            updated = runner.run(record, actor="op-1", root=self.root)
        self.assertEqual(updated["state"], "failed")
        with self.assertRaises(store.InvalidTransitionError):
            runner.run(updated, actor="op-1", root=self.root)

    def test_exhausted_retries_refuses_to_run_again(self):
        record, _ = store.submit(make_request(operation="status"), root=self.root)
        record["attempts"] = record["max_attempts"]
        record["state"] = "failed"
        record["error"] = {"code": "transport_error", "message": "exit 8", "retryable": True}
        store.save_job(record, self.root)
        with self.assertRaises(store.JobsError):
            runner.run(record, actor="op-1", root=self.root)


class TestTimeout(RunnerTestBase):
    def test_real_subprocess_timeout_is_classified_retryable(self):
        os.environ["OMES_JOBS_TEST_MODE"] = "1"
        os.environ["OMES_JOBS_TEST_ARGV_OVERRIDE"] = json.dumps(["sleep", "5"])
        os.environ["OMES_JOBS_TIMEOUT_SECONDS"] = "0.2"
        try:
            record, _ = store.submit(make_request(operation="status"), root=self.root)
            updated = runner.run(record, actor="op-1", root=self.root)
        finally:
            del os.environ["OMES_JOBS_TEST_MODE"]
            del os.environ["OMES_JOBS_TEST_ARGV_OVERRIDE"]
            del os.environ["OMES_JOBS_TIMEOUT_SECONDS"]
        self.assertEqual(updated["state"], "failed")
        self.assertEqual(updated["error"]["code"], "timeout")
        self.assertTrue(updated["error"]["retryable"])

    def test_argv_override_is_ignored_without_test_mode(self):
        # Defense in depth: OMES_JOBS_TEST_ARGV_OVERRIDE alone (without
        # OMES_JOBS_TEST_MODE=1) must never redirect real execution.
        os.environ["OMES_JOBS_TEST_ARGV_OVERRIDE"] = json.dumps(["sleep", "5"])
        try:
            self.assertIsNone(runner._test_argv_override())
        finally:
            del os.environ["OMES_JOBS_TEST_ARGV_OVERRIDE"]


class TestSecretRedaction(RunnerTestBase):
    def test_output_tail_redacts_secret_like_content(self):
        # Built at runtime rather than as a literal so no secret-shaped
        # string is ever committed to the repository (gitleaks scans
        # tracked file content, not values assembled at test time).
        fake_secret_value = "sk_" + "live_" + ("a" * 20)
        text = f"line1\nAPI_TOKEN={fake_secret_value}\nline3"
        redacted = audit.capped_redacted_tail(text)
        self.assertNotIn(fake_secret_value, redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_output_tail_is_capped(self):
        text = "x" * 100000
        redacted = audit.capped_redacted_tail(text, limit=100)
        self.assertLessEqual(len(redacted), 100 + len("...[truncated]...\n"))

    def test_audit_entries_redact_nested_secret_fields(self):
        entry = audit.append(
            self.root,
            actor="system",
            job_id="job-x",
            event="failed",
            detail={"error": {"code": "auth_failed", "password": "hunter2"}},
        )
        self.assertEqual(entry["detail"]["error"]["password"], "[REDACTED]")


class TestAuditChainIntegrity(RunnerTestBase):
    def test_chain_verifies_clean_after_normal_operations(self):
        record, _ = store.submit(make_request(operation="status"), root=self.root)
        store.approve  # not used for status (auto-approved by runner)
        ok = {"ok": True, "timed_out": False, "duration_seconds": 0.01, "parsed": {"ok": True}, "output_tail": ""}
        with mock.patch.object(runner, "_run_argv", side_effect=[ok]):
            runner.run(record, actor="op-1", root=self.root)
        audit.verify_chain(self.root)  # must not raise

    def test_tampered_line_is_detected(self):
        record, _ = store.submit(make_request(operation="status"), root=self.root)
        audit_path = store.paths.audit_log_path(self.root)
        lines = audit_path.read_text().splitlines()
        tampered = json.loads(lines[0])
        tampered["actor"] = "attacker"
        lines[0] = json.dumps(tampered, sort_keys=True)
        audit_path.write_text("\n".join(lines) + "\n")
        with self.assertRaises(audit.AuditTamperedError):
            audit.verify_chain(self.root)
