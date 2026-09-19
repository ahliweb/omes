import itertools
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from content import jobs, paths  # noqa: E402

FAKE_WORKER = str(
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "content" / "fake-worker.py"
)


def _fresh_record(root):
    record = jobs.new_job_record(
        job_id="aaaaaaaaaaaa-20260101T000000Z",
        original_path="inbox/x.mp4",
        processing_path="processing/aaaaaaaaaaaa-20260101T000000Z/source.mp4",
        sha256_hex="a" * 64,
        size_bytes=1,
        mime_guess="video/mp4",
    )
    return record


class TestTransitionValidation(unittest.TestCase):
    def test_every_valid_transition_is_accepted(self):
        for from_state, targets in jobs.TRANSITIONS.items():
            for to_state in targets:
                record = {"state": from_state, "history": [{"ts": jobs.now_iso(), "from": None, "to": from_state, "actor": "t", "note": ""}]}
                jobs.apply_transition(record, to_state, actor="test")
                self.assertEqual(record["state"], to_state)

    def test_every_non_listed_pair_is_rejected(self):
        all_pairs = set(itertools.product(jobs.STATES, jobs.STATES))
        valid_pairs = {(f, t) for f, targets in jobs.TRANSITIONS.items() for t in targets}
        invalid_pairs = all_pairs - valid_pairs
        self.assertGreater(len(invalid_pairs), 0)
        for from_state, to_state in invalid_pairs:
            record = {"state": from_state, "history": []}
            with self.assertRaises(jobs.InvalidTransitionError):
                jobs.apply_transition(record, to_state, actor="test")

    def test_archived_is_a_dead_end(self):
        self.assertEqual(jobs.TRANSITIONS["archived"], frozenset())


class TestBackoff(unittest.TestCase):
    def test_backoff_doubles_each_attempt(self):
        self.assertEqual(jobs.compute_backoff_seconds(0, base_seconds=30, max_seconds=1800), 30)
        self.assertEqual(jobs.compute_backoff_seconds(1, base_seconds=30, max_seconds=1800), 60)
        self.assertEqual(jobs.compute_backoff_seconds(2, base_seconds=30, max_seconds=1800), 120)

    def test_backoff_is_capped(self):
        self.assertEqual(jobs.compute_backoff_seconds(20, base_seconds=30, max_seconds=1800), 1800)

    def test_negative_attempt_treated_as_zero(self):
        self.assertEqual(
            jobs.compute_backoff_seconds(-5, base_seconds=30, max_seconds=1800),
            jobs.compute_backoff_seconds(0, base_seconds=30, max_seconds=1800),
        )


class TestClassification(unittest.TestCase):
    def test_ok_with_url_goes_to_verifying_from_publish(self):
        self.assertEqual(jobs.next_state_after_publish({"status": "ok", "url": "https://x"}), "verifying")

    def test_uncertain_goes_to_manual_review(self):
        self.assertEqual(jobs.next_state_after_publish({"status": "uncertain"}), "manual-review")
        self.assertEqual(jobs.next_state_after_verify({"status": "uncertain"}), "manual-review")

    def test_confirmed_retryable_error_goes_to_retryable_failure(self):
        self.assertEqual(
            jobs.next_state_after_publish({"status": "error", "retryable": True}), "retryable-failure"
        )

    def test_confirmed_nonretryable_error_goes_to_failed(self):
        self.assertEqual(
            jobs.next_state_after_publish({"status": "error", "retryable": False}), "failed"
        )

    def test_missing_or_garbled_status_is_uncertain_not_success(self):
        self.assertEqual(jobs.next_state_after_publish({}), "manual-review")
        self.assertEqual(jobs.next_state_after_publish({"status": "banana"}), "manual-review")

    def test_ok_verify_succeeds(self):
        self.assertEqual(jobs.next_state_after_verify({"status": "ok", "url": "https://x"}), "succeeded")


class TestApprovals(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-content-approvals-")
        self.root = Path(self._tmp) / "content"
        paths.ensure_layout(self.root)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_approve_binds_artifact_hash_and_sets_state(self):
        record = _fresh_record(self.root)
        jobs.plan_job(record)
        jobs.approve_job(record, actor="alice")
        self.assertEqual(record["state"], "approved")
        approval = jobs.latest_approval(record)
        self.assertEqual(approval["artifact_hash"], record["source"]["sha256"])
        self.assertEqual(approval["actor"], "alice")
        valid, _ = jobs.is_approval_valid(record)
        self.assertTrue(valid)

    def test_expired_approval_is_invalid(self):
        record = _fresh_record(self.root)
        jobs.plan_job(record)
        jobs.approve_job(record, actor="alice", ttl_seconds=-1)
        valid, reason = jobs.is_approval_valid(record)
        self.assertFalse(valid)
        self.assertIn("expired", reason)

    def test_hash_mismatch_invalidates_approval(self):
        record = _fresh_record(self.root)
        jobs.plan_job(record)
        jobs.approve_job(record, actor="alice")
        record["source"]["sha256"] = "tampered" * 8
        valid, reason = jobs.is_approval_valid(record)
        self.assertFalse(valid)
        self.assertIn("hash", reason)

    def test_reject_cancels_the_job(self):
        record = _fresh_record(self.root)
        jobs.plan_job(record)
        jobs.reject_job(record, actor="bob")
        self.assertEqual(record["state"], "cancelled")
        self.assertEqual(jobs.latest_approval(record)["decision"], "rejected")


class TestRetryAndCancelAuth(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-content-retry-")
        self.root = Path(self._tmp) / "content"
        paths.ensure_layout(self.root)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _to_retryable_failure(self):
        record = _fresh_record(self.root)
        jobs.plan_job(record)
        jobs.approve_job(record, actor="alice")
        jobs.apply_transition(record, "publishing", actor="system")
        jobs.apply_transition(record, "retryable-failure", actor="system", note="error")
        return record

    def test_retry_too_soon_without_force(self):
        record = self._to_retryable_failure()
        with self.assertRaises(jobs.RetryTooSoonError):
            jobs.retry_job(record, actor="carol", force=False)

    def test_force_retry_records_actor(self):
        record = self._to_retryable_failure()
        jobs.retry_job(record, actor="carol", force=True)
        self.assertEqual(record["state"], "publishing")
        self.assertEqual(record["history"][-1]["actor"], "carol")

    def test_retry_exhausted_after_max_attempts(self):
        record = self._to_retryable_failure()
        record["publish"]["attempts"] = record["publish"]["max_attempts"]
        with self.assertRaises(jobs.ContentJobsError):
            jobs.retry_job(record, actor="carol", force=True)

    def test_cancel_records_actor_and_state(self):
        record = _fresh_record(self.root)
        jobs.cancel_job(record, actor="dave")
        self.assertEqual(record["state"], "cancelled")
        self.assertEqual(record["history"][-1]["actor"], "dave")

    def test_cancel_from_terminal_state_rejected(self):
        record = _fresh_record(self.root)
        record["state"] = "archived"
        record["history"] = [{"ts": jobs.now_iso(), "from": None, "to": "archived", "actor": "t", "note": ""}]
        with self.assertRaises(jobs.InvalidTransitionError):
            jobs.cancel_job(record, actor="dave")


class TestPublishVerifyWithFakeWorker(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-content-publish-")
        self.root = Path(self._tmp) / "content"
        paths.ensure_layout(self.root)
        self._env_backup = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _approved_record(self):
        record = _fresh_record(self.root)
        jobs.plan_job(record)
        jobs.approve_job(record, actor="alice")
        return record

    def test_publish_without_approval_is_refused(self):
        record = _fresh_record(self.root)
        jobs.plan_job(record)
        with self.assertRaises(jobs.InvalidTransitionError):
            jobs.publish_job(record, FAKE_WORKER)

    def test_successful_publish_then_verify_reaches_succeeded(self):
        os.environ["FAKE_WORKER_PUBLISH_STATUS"] = "ok"
        os.environ["FAKE_WORKER_VERIFY_STATUS"] = "ok"
        record = self._approved_record()
        jobs.publish_job(record, FAKE_WORKER)
        self.assertEqual(record["state"], "verifying")
        jobs.verify_job(record, FAKE_WORKER)
        self.assertEqual(record["state"], "succeeded")
        self.assertTrue(record["publish"]["resulting_url"])

    def test_resume_never_republishes_only_reverifies(self):
        counter_file = Path(self._tmp) / "publish-counter.txt"
        os.environ["FAKE_WORKER_COUNTER_FILE"] = str(counter_file)
        os.environ["FAKE_WORKER_PUBLISH_STATUS"] = "ok"
        os.environ["FAKE_WORKER_VERIFY_STATUS"] = "ok"

        record = self._approved_record()
        jobs.publish_job(record, FAKE_WORKER)
        self.assertEqual(record["state"], "verifying")
        jobs.save_job(record, self.root)

        publish_calls_before_resume = counter_file.read_text().count("publish")
        self.assertEqual(publish_calls_before_resume, 1)

        # Simulate a restart: cli.cmd_resume-equivalent logic re-verifies
        # jobs stuck in verifying, without ever calling publish again.
        from content import cli as content_cli

        os.environ["OMES_CONTENT_ROOT"] = str(self.root)
        args = content_cli.build_parser().parse_args(["resume", "--json"])
        content_cli.cmd_resume(args)

        publish_calls_after_resume = counter_file.read_text().count("publish")
        self.assertEqual(publish_calls_after_resume, 1, "resume must never re-publish")

        resumed_record = jobs.load_job(record["job_id"], self.root)
        # cli.cmd_resume archives a job as soon as it reaches a terminal
        # state (issue #68), so "succeeded" immediately becomes "archived".
        self.assertEqual(resumed_record["state"], "archived")

    def test_publishing_survives_crash_as_manual_review_not_a_second_publish(self):
        counter_file = Path(self._tmp) / "publish-counter.txt"
        os.environ["FAKE_WORKER_COUNTER_FILE"] = str(counter_file)
        record = self._approved_record()
        # Force the job into "publishing" without completing (simulating a
        # crash mid-worker-call), then persist it as resume would find it.
        jobs.apply_transition(record, "publishing", actor="system", note="publish started")
        jobs.save_job(record, self.root)

        from content import cli as content_cli

        os.environ["OMES_CONTENT_ROOT"] = str(self.root)
        args = content_cli.build_parser().parse_args(["resume", "--json"])
        content_cli.cmd_resume(args)

        resumed_record = jobs.load_job(record["job_id"], self.root)
        self.assertEqual(resumed_record["state"], "manual-review")
        self.assertEqual(counter_file.exists() and counter_file.read_text().count("publish") or 0, 0)


if __name__ == "__main__":
    unittest.main()
