"""End-to-end `omes content plan/edit/publish` validation flow (#69):
plan shows a validation preview per target platform, publish blocks only
the platform whose caption/cover fails validation, and per-platform
caption edits are versioned and never lose the original plan.caption."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from content import cli, jobs, paths  # noqa: E402


def _run_cli(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


class TestCliValidationFlow(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-content-cli-validation-")
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

    def test_plan_shows_a_validation_preview_per_target(self):
        job_id = self._scan_job_id()
        code, out = _run_cli(
            ["plan", job_id, "--actor", "alice", "--caption", "guaranteed results!", "--target", "youtube", "--json"]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("youtube", payload["validation"])
        self.assertTrue(any(i["rule"] == "unsupported_claim" for i in payload["validation"]["youtube"]))

    def test_publish_is_blocked_by_a_validation_error_and_moves_to_manual_review(self):
        job_id = self._scan_job_id()
        _run_cli(["plan", job_id, "--actor", "alice", "--caption", "guaranteed results!", "--target", "generic_browser"])
        _run_cli(["approve", job_id, "--actor", "alice"])
        code, out = _run_cli(["publish", job_id, "--platform", "generic_browser", "--json"])
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["state"], "manual-review")
        self.assertTrue(any(i["rule"] == "unsupported_claim" for i in payload["validation"]))

    def test_force_validation_overrides_a_blocking_error(self):
        job_id = self._scan_job_id()
        _run_cli(["plan", job_id, "--actor", "alice", "--caption", "guaranteed results!", "--target", "generic_browser"])
        _run_cli(["approve", job_id, "--actor", "alice"])
        code, out = _run_cli(["publish", job_id, "--platform", "generic_browser", "--force-validation", "--json"])
        # Validation no longer blocks; the job proceeds all the way to
        # the worker, which then reports needs_login (no session yet) -
        # a DIFFERENT failure_state than the validation-blocked case,
        # proving validation was bypassed rather than silently
        # re-blocking under a different name.
        payload = json.loads(out)
        self.assertEqual(payload["state"], "manual-review")
        self.assertEqual(payload["publish_result"]["failure_state"], "needs_login")

    def test_edit_with_platform_and_caption_file_creates_a_versioned_variant(self):
        job_id = self._scan_job_id()
        _run_cli(["plan", job_id, "--actor", "alice", "--caption", "original caption", "--target", "generic_browser"])

        caption_file = Path(self._tmp) / "caption.txt"
        caption_file.write_text("a totally clean, edited caption", encoding="utf-8")
        code, _out = _run_cli(
            ["edit", job_id, "--actor", "alice", "--platform", "generic_browser", "--caption-file", str(caption_file)]
        )
        self.assertEqual(code, 0)
        # human-mode call above created caption.v1; re-run with --json to
        # get a structured result for the second (caption.v2) call.
        code, out = _run_cli(
            [
                "edit",
                job_id,
                "--actor",
                "alice",
                "--platform",
                "generic_browser",
                "--caption-file",
                str(caption_file),
                "--json",
            ]
        )
        payload = json.loads(out)
        self.assertEqual(payload["variant"], "caption.v2")

        record = jobs.load_job(job_id, self.root)
        self.assertEqual(record["plan"]["caption"], "original caption")

        variant_path = paths.job_variants_dir(job_id, "generic_browser", self.root) / "caption.v1"
        self.assertEqual(variant_path.read_text(encoding="utf-8"), "a totally clean, edited caption")

    def test_publish_uses_the_latest_caption_variant_not_the_generic_plan_caption(self):
        job_id = self._scan_job_id()
        _run_cli(["plan", job_id, "--actor", "alice", "--caption", "guaranteed results!", "--target", "generic_browser"])

        caption_file = Path(self._tmp) / "caption.txt"
        caption_file.write_text("a totally clean, edited caption", encoding="utf-8")
        _run_cli(["edit", job_id, "--actor", "alice", "--platform", "generic_browser", "--caption-file", str(caption_file)])

        _run_cli(["approve", job_id, "--actor", "alice"])
        code, out = _run_cli(["publish", job_id, "--platform", "generic_browser", "--json"])
        payload = json.loads(out)
        # The clean edited variant passed validation, so the job reaches
        # the worker (needs_login, no session yet) instead of being
        # blocked by the original caption's "guaranteed" claim.
        self.assertEqual(payload["state"], "manual-review")
        self.assertEqual(payload["publish_result"]["failure_state"], "needs_login")


if __name__ == "__main__":
    unittest.main()
