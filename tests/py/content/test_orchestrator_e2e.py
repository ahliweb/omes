"""End-to-end orchestrator tests with mocked workers (issue #70).

Exercises the manager (`cli.py`/`jobs.py`/`reports.py`) as a black box
through the real CLI entry point, using only synthetic fixtures:
`tests/fixtures/content/fake-worker.py` (controllable outcomes via env
vars) and the `generic_browser` worker's default `manual_stub` driver.
No real browser, no real Telegram bot, no real platform account, and no
network access anywhere in this file.

Real-platform tests are opt-in only, gated on `OMES_TEST_REAL_PLATFORM=1`
(see docs/content-threat-model.md section 4) - there is exactly one
placeholder for that gate at the bottom of this file, skipped by default.
"""
import contextlib
import io
import json
import os
import shutil
import stat
import tempfile
import time
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from content import cli, jobs, paths  # noqa: E402

_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "content"
FAKE_WORKER = str(_FIXTURES_DIR / "fake-worker.py")

_FAKE_WORKER_ENV_KEYS = (
    "FAKE_WORKER_PUBLISH_STATUS",
    "FAKE_WORKER_PUBLISH_URL",
    "FAKE_WORKER_PUBLISH_RETRYABLE",
    "FAKE_WORKER_VERIFY_STATUS",
    "FAKE_WORKER_VERIFY_URL",
    "FAKE_WORKER_VERIFY_RETRYABLE",
    "FAKE_WORKER_COUNTER_FILE",
)


def _run_cli(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


class OrchestratorTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-content-orchestrator-")
        self.root = Path(self._tmp) / "content"
        self._old_content_root = os.environ.get("OMES_CONTENT_ROOT")
        os.environ["OMES_CONTENT_ROOT"] = str(self.root)
        self._old_fake_worker_env = {k: os.environ.get(k) for k in _FAKE_WORKER_ENV_KEYS}
        for k in _FAKE_WORKER_ENV_KEYS:
            os.environ.pop(k, None)
        paths.ensure_layout(self.root)

    def tearDown(self):
        if self._old_content_root is None:
            os.environ.pop("OMES_CONTENT_ROOT", None)
        else:
            os.environ["OMES_CONTENT_ROOT"] = self._old_content_root
        for k, v in self._old_fake_worker_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _drop_inbox_file(self, name="clip.mp4", content=b"fake media bytes"):
        inbox = paths.inbox_dir(self.root)
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / name).write_bytes(content)

    def _scan_job_ids(self):
        code, out = _run_cli(["scan", "--json", "--settle-seconds", "0"])
        self.assertEqual(code, 0)
        return json.loads(out)

    def _plan_approve(self, job_id, caption="a perfectly normal caption", target="generic_browser"):
        _run_cli(["plan", job_id, "--actor", "alice", "--caption", caption, "--target", target])
        _run_cli(["approve", job_id, "--actor", "alice"])

    def _publish_fake_worker(self, job_id, platform="generic_browser"):
        return _run_cli(["publish", job_id, "--platform", platform, "--worker-executable", FAKE_WORKER, "--json"])


class TestDuplicateDetection(OrchestratorTestBase):
    def test_duplicate_inbox_file_is_recorded_not_republished(self):
        self._drop_inbox_file("clip.mp4", b"identical bytes")
        first = self._scan_job_ids()
        self.assertEqual(len(first["created"]), 1)
        original_job = first["created"][0]

        # Job ids are `<sha256-prefix>-<second-resolution UTC stamp>`
        # (docs/content-distribution.md section 4); a real sleep here
        # guarantees the duplicate gets its own distinct id rather than
        # colliding with the original's, exactly as two real, separately
        # timed inbox drops would.
        time.sleep(1.1)
        self._drop_inbox_file("clip-copy.mp4", b"identical bytes")
        second = self._scan_job_ids()
        self.assertEqual(second["created"], [])
        self.assertEqual(len(second["duplicates"]), 1)

        duplicate_job = second["duplicates"][0]
        self.assertNotEqual(original_job, duplicate_job)

        record = jobs.load_job(duplicate_job, self.root)
        self.assertEqual(record["state"], "cancelled")
        self.assertEqual(record["source"]["duplicate_of"], original_job)

        original_record = jobs.load_job(original_job, self.root)
        self.assertEqual(original_record["state"], "queued")

        # The duplicate can never independently reach publishing - it is
        # cancelled (and thus archived) from the moment it is detected.
        self._plan_approve(original_job)
        code, out = self._publish_fake_worker(original_job)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["state"], "archived")


class TestRestartResume(OrchestratorTestBase):
    def test_restart_resume_never_republishes_a_job_stuck_in_publishing(self):
        self._drop_inbox_file()
        job_id = self._scan_job_ids()["created"][0]
        self._plan_approve(job_id)

        counter_file = Path(self._tmp) / "publish-counter.txt"
        os.environ["FAKE_WORKER_COUNTER_FILE"] = str(counter_file)

        # Simulate a crash mid-publish: force the job straight to
        # `publishing` with a recorded worker, bypassing a real publish
        # call, exactly like a process killed between "worker invoked"
        # and "manager recorded the outcome."
        record = jobs.load_job(job_id, self.root)
        jobs.apply_transition(record, "publishing", actor="system", note="publish started")
        record["publish"]["worker_executable"] = FAKE_WORKER
        jobs.save_job(record, self.root)

        code, out = _run_cli(["resume", "--json"])
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result["resumed"], [])
        self.assertEqual(result["skipped_to_manual_review"], [job_id])

        reloaded = jobs.load_job(job_id, self.root)
        self.assertEqual(reloaded["state"], "manual-review")
        self.assertFalse(counter_file.exists(), "resume must never invoke the worker's publish operation")


class TestPartialPlatformFailure(OrchestratorTestBase):
    def test_one_platforms_failure_does_not_affect_another_jobs_success(self):
        self._drop_inbox_file("ok.mp4", b"ok bytes")
        ok_job = self._scan_job_ids()["created"][0]
        self._plan_approve(ok_job)

        self._drop_inbox_file("fails.mp4", b"fails bytes")
        fail_job = self._scan_job_ids()["created"][0]
        self._plan_approve(fail_job)

        os.environ["FAKE_WORKER_PUBLISH_STATUS"] = "error"
        os.environ["FAKE_WORKER_PUBLISH_RETRYABLE"] = "false"
        code, out = self._publish_fake_worker(fail_job)
        self.assertEqual(code, 1)
        # The non-retryable failure is immediately archived (issue #68's
        # auto-archive-on-terminal-state), but the exit code still
        # reflects the underlying failure outcome (see cli.py's
        # `outcome_state` capture, taken before archiving).
        self.assertEqual(json.loads(out)["state"], "archived")

        os.environ["FAKE_WORKER_PUBLISH_STATUS"] = "ok"
        os.environ["FAKE_WORKER_PUBLISH_URL"] = "https://example.invalid/ok"
        os.environ["FAKE_WORKER_VERIFY_URL"] = "https://example.invalid/ok"
        code, out = self._publish_fake_worker(ok_job)
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["state"], "archived")
        self.assertEqual(payload["resulting_url"], "https://example.invalid/ok")

        # Failure evidence for one job never lands under the other job's
        # directories.
        self.assertTrue((self.root / "failed" / fail_job).is_dir())
        self.assertTrue((self.root / "uploaded" / ok_job).is_dir())
        self.assertFalse((self.root / "failed" / ok_job).exists())
        self.assertFalse((self.root / "uploaded" / fail_job).exists())


class TestWorkerTimeout(OrchestratorTestBase):
    def test_worker_timeout_is_treated_as_uncertain_never_success(self):
        from content import worker as worker_mod

        slow_worker = Path(self._tmp) / "slow-worker.py"
        slow_worker.write_text(
            "import sys, time, json\n"
            "time.sleep(2)\n"
            'print(json.dumps({"status": "ok", "url": "https://example.invalid/x"}))\n',
            encoding="utf-8",
        )

        result = worker_mod.run_worker(str(slow_worker), "publish", {"job_id": "x"}, timeout=0.3)
        self.assertEqual(result["status"], "uncertain")
        self.assertIsNone(result.get("url"))

        # Feeding that result into the classifier proves the orchestration
        # consequence: a timeout routes to manual-review, never success.
        self.assertEqual(jobs.next_state_after_publish(result), "manual-review")


class TestRateLimitBackoff(OrchestratorTestBase):
    def test_rate_limited_publish_enforces_backoff_before_retry(self):
        self._drop_inbox_file()
        job_id = self._scan_job_ids()["created"][0]
        self._plan_approve(job_id)

        os.environ["FAKE_WORKER_PUBLISH_STATUS"] = "error"
        os.environ["FAKE_WORKER_PUBLISH_RETRYABLE"] = "true"
        code, out = self._publish_fake_worker(job_id)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["state"], "retryable-failure")

        code, _out = _run_cli(["retry", job_id, "--actor", "alice", "--yes", "--json"])
        self.assertEqual(code, 1)  # backoff has not elapsed; no --force given

        code, out = _run_cli(["retry", job_id, "--actor", "alice", "--yes", "--force", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["state"], "publishing")

        os.environ["FAKE_WORKER_PUBLISH_STATUS"] = "ok"
        os.environ["FAKE_WORKER_PUBLISH_URL"] = "https://example.invalid/after-retry"
        code, out = self._publish_fake_worker(job_id)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["state"], "archived")


class TestManualReviewNeverAutoRetried(OrchestratorTestBase):
    def test_uncertain_outcome_reaches_manual_review_and_stays_there(self):
        self._drop_inbox_file()
        job_id = self._scan_job_ids()["created"][0]
        self._plan_approve(job_id)

        os.environ["FAKE_WORKER_PUBLISH_STATUS"] = "uncertain"
        code, out = self._publish_fake_worker(job_id)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["state"], "manual-review")

        # `reconcile` must never suggest an automatic retry for an
        # uncertain outcome the way it does for an elapsed backoff.
        code, out = _run_cli(["reconcile", "--json"])
        entry = next(j for j in json.loads(out)["jobs"] if j["job_id"] == job_id)
        self.assertNotIn("backoff elapsed", " ".join(entry["reasons"]))


class TestApprovalAndCancellation(OrchestratorTestBase):
    def test_stale_approval_blocks_publish(self):
        self._drop_inbox_file()
        job_id = self._scan_job_ids()["created"][0]
        _run_cli(["plan", job_id, "--actor", "alice", "--caption", "hi", "--target", "generic_browser"])
        _run_cli(["approve", job_id, "--actor", "alice", "--ttl-seconds", "0"])
        time.sleep(1.1)

        code, out = self._publish_fake_worker(job_id)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["state"], "manual-review")

    def test_artifact_tampering_between_approval_and_publish_is_blocked(self):
        self._drop_inbox_file()
        job_id = self._scan_job_ids()["created"][0]
        self._plan_approve(job_id)

        record = jobs.load_job(job_id, self.root)
        record["source"]["sha256"] = "0" * 64  # simulated tamper
        jobs.save_job(record, self.root)

        code, out = self._publish_fake_worker(job_id)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["state"], "manual-review")

    def test_cancellation_moves_a_non_terminal_job_to_archived(self):
        self._drop_inbox_file()
        job_id = self._scan_job_ids()["created"][0]
        _run_cli(["plan", job_id, "--actor", "alice", "--caption", "hi", "--target", "generic_browser"])

        code, out = _run_cli(["cancel", job_id, "--actor", "bob", "--yes", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["state"], "archived")
        self.assertTrue((self.root / "review" / job_id).is_dir())


class TestSessionPermissionsAndSecrets(OrchestratorTestBase):
    def test_session_profile_directory_is_mode_0700(self):
        code, _out = _run_cli(["session", "login", "generic_browser", "--actor", "alice", "--json"])
        self.assertEqual(code, 0)
        session_dir = paths.session_platform_dir("generic_browser", self.root)
        mode = stat.S_IMODE(session_dir.stat().st_mode)
        self.assertEqual(mode, 0o700)

    def test_canary_secret_never_appears_anywhere(self):
        canary = "CANARY_SECRET_TOKEN_SHOULD_NEVER_LEAK_9f3a"
        hermes_home = Path(self._tmp) / "hermes-home"
        hermes_home.mkdir()
        env_file = hermes_home / ".env"
        env_file.write_text(f"TELEGRAM_BOT_TOKEN={canary}\nTELEGRAM_ALLOWED_USERS=111\n", encoding="utf-8")
        os.chmod(env_file, 0o600)

        old_hermes_home = os.environ.get("HERMES_HOME")
        old_approvers = os.environ.get("OMES_CONTENT_APPROVERS")
        old_api_base = os.environ.get("OMES_CONTENT_TELEGRAM_API_BASE")
        os.environ["HERMES_HOME"] = str(hermes_home)
        os.environ["OMES_CONTENT_APPROVERS"] = "111"
        # Point the Telegram client at a closed port so a `notify` call
        # fails fast (ConnectionRefused) rather than hanging on a real
        # network call - the assertion under test is about what never
        # gets *printed/stored*, not about a successful send.
        os.environ["OMES_CONTENT_TELEGRAM_API_BASE"] = "http://127.0.0.1:1"

        try:
            self._drop_inbox_file()
            job_id = self._scan_job_ids()["created"][0]
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                cli.main(["plan", job_id, "--actor", "alice", "--caption", "hi", "--target", "generic_browser"])
                cli.main(["edit", job_id, "--actor", "alice", "--caption", "hi again"])
                cli.main(["approve", job_id, "--actor", "111", "--channel", "telegram", "--json"])
                cli.main(["notify", job_id, "--chat-id", "111", "--json"])
                cli.main(["status", job_id, "--telegram", "--chat-id", "111", "--json"])
                cli.main(["publish", job_id, "--platform", "generic_browser", "--worker-executable", FAKE_WORKER, "--json"])
                cli.main(["report", job_id, "--json"])
                cli.main(["export", "--since", "2020-01-01", "--out", str(Path(self._tmp) / "export"), "--json"])
            stdout_text = captured.getvalue()
        finally:
            if old_hermes_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = old_hermes_home
            if old_approvers is None:
                os.environ.pop("OMES_CONTENT_APPROVERS", None)
            else:
                os.environ["OMES_CONTENT_APPROVERS"] = old_approvers
            if old_api_base is None:
                os.environ.pop("OMES_CONTENT_TELEGRAM_API_BASE", None)
            else:
                os.environ["OMES_CONTENT_TELEGRAM_API_BASE"] = old_api_base

        self.assertNotIn(canary, stdout_text)

        for path in self.root.rglob("*"):
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8", errors="strict")
                except (UnicodeDecodeError, OSError):
                    continue
                self.assertNotIn(canary, text, f"canary secret leaked into {path}")


@unittest.skipUnless(os.environ.get("OMES_TEST_REAL_PLATFORM") == "1", "opt-in real-platform test, disabled by default")
class TestRealPlatformOptIn(unittest.TestCase):
    def test_placeholder_for_a_real_platform_test(self):  # pragma: no cover - opt-in only
        self.skipTest("no real-platform worker is configured in this repository (docs/content-threat-model.md section 4)")


if __name__ == "__main__":
    unittest.main()
