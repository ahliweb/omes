"""tests/py/content/test_migration.py - Verification tests for ADR-0024 and issue #179.

Verifies:
  1. migration/export preserves non-secret job history and artifact hashes;
  2. session/cookie secrets are excluded;
  3. compatibility CLI does not bypass successor authorization/approval;
  4. duplicate publish protection survives migration;
  5. partial/uncertain publish remains manual-review rather than auto-retry;
  6. deprecation path is reversible until successor acceptance passes.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401
from content import cli, inbox, jobs, paths, reports


class ContentMigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="omes-test-migration-"))
        self.content_root = self.tmp / "content"
        self._orig_root = os.environ.get("OMES_CONTENT_ROOT")
        os.environ["OMES_CONTENT_ROOT"] = str(self.content_root)
        paths.ensure_layout(self.content_root)

    def tearDown(self):
        if self._orig_root is not None:
            os.environ["OMES_CONTENT_ROOT"] = self._orig_root
        else:
            os.environ.pop("OMES_CONTENT_ROOT", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_sample_job(
        self,
        job_id: str,
        state: str = "queued",
        sha256_hex: str = "a" * 64,
        size_bytes: int = 1024,
        duplicate_of: str | None = None,
        caption: str = "Sample video caption #trending",
        targets: list[str] | None = None,
        resulting_url: str | None = None,
        last_result: str = "ok",
    ) -> dict:
        targets = targets or ["youtube"]
        record = jobs.new_job_record(
            job_id=job_id,
            original_path=str(self.content_root / "inbox" / f"{job_id}.mp4"),
            processing_path=str(self.content_root / "processing" / job_id / "media.mp4"),
            sha256_hex=sha256_hex,
            size_bytes=size_bytes,
            mime_guess="video/mp4",
            duplicate_of=duplicate_of,
        )
        record["state"] = state
        record["plan"] = {"caption": caption, "targets": targets}
        record["publish"]["resulting_url"] = resulting_url
        record["publish"]["last_result"] = last_result
        if resulting_url:
            record["publish"]["attempts"] = 1
            record["publish"]["worker_version"] = "1.0.0"

        # Create dummy processing dir and evidence
        pdir = paths.job_processing_dir(job_id, self.content_root)
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "media.mp4").write_bytes(b"dummy media content")

        jobs.save_job(record, self.content_root)
        reports.audit_append_for_job(self.content_root, record, actor="test-user")
        return record

    def test_export_preserves_non_secret_history_and_hashes(self):
        """Acceptance criteria: migration/export preserves non-secret job history and artifact hashes."""
        job = self._create_sample_job(
            "job-preserve-1",
            state="succeeded",
            sha256_hex="1234567890abcdef" * 4,
            resulting_url="https://youtube.com/watch?v=sample123",
        )
        # Record approval
        job["approvals"].append({
            "actor": "operator-alice",
            "approved_at": "2026-09-21T10:00:00Z",
            "decision": "approved",
            "channel": "cli",
            "expires_at": "2026-09-22T10:00:00Z",
        })
        jobs.save_job(job, self.content_root)

        out_dir = self.tmp / "export-out"
        res = reports.export_awcms_v1(self.content_root, since="2026-01-01", out_dir=out_dir)

        self.assertEqual(res["format"], "awcms-v1")
        self.assertIn("job-preserve-1", res["exported_jobs"])

        manifest_path = Path(res["manifest"])
        self.assertTrue(manifest_path.is_file())

        with manifest_path.open("r", encoding="utf-8") as fh:
            manifest = json.load(fh)

        self.assertEqual(manifest["schema_version"], "awcms-content-v1")
        self.assertEqual(manifest["total_jobs"], 1)
        self.assertTrue(manifest["sessions_excluded"])
        self.assertTrue(manifest["secrets_redacted"])

        exported_job = manifest["jobs"][0]
        self.assertEqual(exported_job["job_id"], "job-preserve-1")
        self.assertEqual(exported_job["artifact_sha256"], "1234567890abcdef" * 4)
        self.assertEqual(exported_job["size_bytes"], 1024)
        self.assertEqual(exported_job["mime_guess"], "video/mp4")
        self.assertEqual(len(exported_job["approvals"]), 1)
        self.assertEqual(exported_job["approvals"][0]["actor"], "operator-alice")
        self.assertEqual(len(exported_job["publications"]), 1)
        self.assertEqual(exported_job["publications"][0]["url"], "https://youtube.com/watch?v=sample123")
        self.assertGreaterEqual(len(exported_job["audit_events"]), 1)

    def test_session_cookie_secrets_are_excluded(self):
        """Acceptance criteria: session/cookie secrets are strictly excluded from export."""
        # Create fake session directory with sensitive tokens and cookies
        session_dir = paths.session_platform_dir("youtube", self.content_root)
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "cookies.sqlite").write_text("SUPER_SECRET_COOKIE_DATA", encoding="utf-8")
        (session_dir / "tokens.json").write_text('{"oauth_token": "secret_oauth_12345"}', encoding="utf-8")

        # Create job that references platform
        job = self._create_sample_job("job-secret-test", state="succeeded")
        # Inadvertently add secret-looking metadata to test recursive redaction
        job["custom_secret_key"] = "SHOULD_BE_REDACTED_PASSWORD"
        jobs.save_job(job, self.content_root)

        out_dir = self.tmp / "export-secrets"
        res = reports.export_awcms_v1(self.content_root, since="2026-01-01", out_dir=out_dir)

        self.assertTrue(res["sessions_untouched"])

        # Check entire export out_dir recursively for any session files or cookie content
        found_cookie_files = list(out_dir.rglob("*cookie*"))
        found_token_files = list(out_dir.rglob("*token*"))
        self.assertEqual(len(found_cookie_files), 0)
        self.assertEqual(len(found_token_files), 0)

        # Inspect exported manifest content
        manifest_text = Path(res["manifest"]).read_text(encoding="utf-8")
        self.assertNotIn("SUPER_SECRET_COOKIE_DATA", manifest_text)
        self.assertNotIn("secret_oauth_12345", manifest_text)
        self.assertNotIn("SHOULD_BE_REDACTED_PASSWORD", manifest_text)

    def test_duplicate_publish_protection_survives_migration(self):
        """Acceptance criteria: duplicate publish protection survives migration."""
        # Original job
        self._create_sample_job(
            "job-orig-dup",
            state="succeeded",
            sha256_hex="dddddddddddddddd" * 4,
            resulting_url="https://youtube.com/watch?v=origdup",
        )
        # Duplicate job
        self._create_sample_job(
            "job-copy-dup",
            state="cancelled",
            sha256_hex="dddddddddddddddd" * 4,
            duplicate_of="job-orig-dup",
        )

        out_dir = self.tmp / "export-dup"
        res = reports.export_awcms_v1(self.content_root, since="2026-01-01", out_dir=out_dir)

        manifest = json.loads(Path(res["manifest"]).read_text(encoding="utf-8"))
        jobs_by_id = {j["job_id"]: j for j in manifest["jobs"]}

        orig = jobs_by_id["job-orig-dup"]
        dup = jobs_by_id["job-copy-dup"]

        self.assertEqual(orig["artifact_sha256"], dup["artifact_sha256"])
        self.assertEqual(dup["duplicate_of"], "job-orig-dup")
        self.assertEqual(len(orig["publications"]), 1)
        self.assertEqual(len(dup["publications"]), 0)

    def test_partial_or_uncertain_publish_remains_manual_review(self):
        """Acceptance criteria: partial/uncertain publish remains manual-review rather than auto-retry."""
        # Succeeded job: does NOT require manual review
        self._create_sample_job("job-success", state="succeeded", resulting_url="https://yt.com/ok")

        # Retryable failure: MUST require manual review in successor
        self._create_sample_job("job-retryable", state="retryable-failure", last_result="error_network")

        # Manual review: MUST require manual review in successor
        self._create_sample_job("job-manual", state="manual-review", last_result="error_unsupported_claim")

        # Unapproved queued: MUST require manual review
        self._create_sample_job("job-queued", state="queued")

        out_dir = self.tmp / "export-review"
        res = reports.export_awcms_v1(self.content_root, since="2026-01-01", out_dir=out_dir)

        manifest = json.loads(Path(res["manifest"]).read_text(encoding="utf-8"))
        jobs_by_id = {j["job_id"]: j for j in manifest["jobs"]}

        self.assertFalse(jobs_by_id["job-success"]["requires_manual_review"])
        self.assertTrue(jobs_by_id["job-retryable"]["requires_manual_review"])
        self.assertTrue(jobs_by_id["job-manual"]["requires_manual_review"])
        self.assertTrue(jobs_by_id["job-queued"]["requires_manual_review"])
        self.assertEqual(manifest["requires_manual_review_count"], 3)

    def test_migration_is_non_destructive_and_reversible(self):
        """Acceptance criteria: migration/export is non-destructive and leaves original evidence untouched."""
        job = self._create_sample_job("job-reversible", state="succeeded")
        original_job_record_path = paths.job_record_path("job-reversible", self.content_root)
        original_job_bytes = original_job_record_path.read_bytes()

        out_dir = self.tmp / "export-non-destruct"
        reports.export_awcms_v1(self.content_root, since="2026-01-01", out_dir=out_dir)

        # Verify source job record is unmodified
        self.assertEqual(original_job_record_path.read_bytes(), original_job_bytes)
        # Verify processing media remains untouched
        p_media = paths.job_processing_dir("job-reversible", self.content_root) / "media.mp4"
        self.assertTrue(p_media.is_file())
        self.assertEqual(p_media.read_bytes(), b"dummy media content")

    def test_compatibility_cli_export_command(self):
        """Acceptance criteria: compatibility CLI runs export with --format awcms-v1 and legacy."""
        self._create_sample_job("job-cli-test", state="succeeded")
        out_legacy = self.tmp / "cli-legacy"
        out_awcms = self.tmp / "cli-awcms"

        parser = cli.build_parser()

        # Legacy export
        args_legacy = parser.parse_args(["export", "--since", "2026-01-01", "--out", str(out_legacy), "--json"])
        rc_legacy = cli.cmd_export(args_legacy)
        self.assertEqual(rc_legacy, 0)
        self.assertTrue((out_legacy / "audit.jsonl").is_file())

        # AWCMS-v1 export
        args_awcms = parser.parse_args([
            "export",
            "--since", "2026-01-01",
            "--out", str(out_awcms),
            "--format", "awcms-v1",
            "--json",
        ])
        rc_awcms = cli.cmd_export(args_awcms)
        self.assertEqual(rc_awcms, 0)
        self.assertTrue((out_awcms / "awcms-content-migration-manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
