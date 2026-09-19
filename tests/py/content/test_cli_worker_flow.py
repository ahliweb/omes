"""End-to-end `omes content` CLI flow using the generic_browser worker's
default manual_stub driver (issue #66) - no real browser, no network.
Exercises: scan -> plan -> approve -> publish (needs_login ->
manual-review) -> session login -> retry -> publish (ok -> succeeded ->
archived), plus the security assertions from #66's acceptance criteria
(session data mode 0700, never in reports/exports/audit)."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from content import cli, paths, reports  # noqa: E402


def _run_cli(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


class TestCliWorkerFlow(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-content-cli-worker-")
        self.root = Path(self._tmp) / "content"
        self._old_env = os.environ.get("OMES_CONTENT_ROOT")
        os.environ["OMES_CONTENT_ROOT"] = str(self.root)
        inbox = paths.inbox_dir(self.root)
        inbox.mkdir(parents=True)
        (inbox / "clip.mp4").write_bytes(b"fake media bytes")

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("OMES_CONTENT_ROOT", None)
        else:
            os.environ["OMES_CONTENT_ROOT"] = self._old_env
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _scan_job_id(self):
        code, out = _run_cli(["scan", "--json", "--settle-seconds", "0"])
        self.assertEqual(code, 0)
        return json.loads(out)["created"][0]

    def test_publish_without_session_goes_to_manual_review_needs_login(self):
        job_id = self._scan_job_id()
        _run_cli(["plan", job_id, "--actor", "alice", "--target", "generic_browser"])
        _run_cli(["approve", job_id, "--actor", "alice"])
        code, out = _run_cli(["publish", job_id, "--platform", "generic_browser", "--json"])
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["state"], "manual-review")

    def test_session_login_then_retry_then_publish_succeeds(self):
        job_id = self._scan_job_id()
        _run_cli(["plan", job_id, "--actor", "alice", "--target", "generic_browser"])
        _run_cli(["approve", job_id, "--actor", "alice"])
        _run_cli(["publish", job_id, "--platform", "generic_browser", "--json"])

        code, _ = _run_cli(["session", "login", "generic_browser", "--actor", "alice", "--json"])
        self.assertEqual(code, 0)

        code, out = _run_cli(["retry", job_id, "--actor", "alice", "--yes", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["state"], "publishing")

        code, out = _run_cli(["publish", job_id, "--platform", "generic_browser", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["state"], "archived")
        self.assertTrue(payload["resulting_url"].startswith("https://"))

    def test_publish_rejects_platform_not_in_planned_targets(self):
        job_id = self._scan_job_id()
        _run_cli(["plan", job_id, "--actor", "alice", "--target", "generic_browser"])
        _run_cli(["approve", job_id, "--actor", "alice"])
        code, _ = _run_cli(["publish", job_id, "--platform", "some-other-platform", "--json"])
        self.assertEqual(code, 1)

    def test_publish_rejects_unknown_platform(self):
        job_id = self._scan_job_id()
        _run_cli(["plan", job_id, "--actor", "alice"])
        _run_cli(["approve", job_id, "--actor", "alice"])
        code, _ = _run_cli(["publish", job_id, "--platform", "does-not-exist", "--json"])
        self.assertEqual(code, 1)

    def test_session_dir_is_mode_0700_after_login(self):
        _run_cli(["session", "login", "generic_browser", "--actor", "alice", "--json"])
        session_dir = paths.session_platform_dir("generic_browser", self.root)
        mode = oct(session_dir.stat().st_mode)[-3:]
        self.assertEqual(mode, "700")

    def test_sessions_never_appear_in_report_export_or_audit(self):
        job_id = self._scan_job_id()
        _run_cli(["plan", job_id, "--actor", "alice", "--target", "generic_browser"])
        _run_cli(["approve", job_id, "--actor", "alice"])
        _run_cli(["publish", job_id, "--platform", "generic_browser", "--json"])
        _run_cli(["session", "login", "generic_browser", "--actor", "alice", "--json"])
        _run_cli(["retry", job_id, "--actor", "alice", "--yes", "--json"])
        _run_cli(["publish", job_id, "--platform", "generic_browser", "--json"])

        report_dir = paths.reports_dir(self.root) / job_id
        report_text = (report_dir / "report.json").read_text(encoding="utf-8")
        self.assertNotIn("sessions/", report_text)

        export_dir = Path(self._tmp) / "export"
        result = reports.export_reports(self.root, "2020-01-01", export_dir)
        self.assertFalse((export_dir / "sessions").exists())
        audit_text = (export_dir / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("sessions/", audit_text)

        raw_audit = paths.audit_log_path(self.root).read_text(encoding="utf-8")
        self.assertNotIn("/sessions/generic_browser", raw_audit)

    def test_session_revoke_requires_confirmation(self):
        _run_cli(["session", "login", "generic_browser", "--actor", "alice"])
        code, _ = _run_cli(["session", "revoke", "generic_browser", "--actor", "alice"])
        self.assertEqual(code, 1)
        code, _ = _run_cli(["session", "revoke", "generic_browser", "--actor", "alice", "--yes", "--json"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
