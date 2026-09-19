import json
import shutil
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from content import jobs, paths, reports  # noqa: E402


def _new_record(job_id="bbbbbbbbbbbb-20260101T000000Z"):
    return jobs.new_job_record(
        job_id=job_id,
        original_path="inbox/x.mp4",
        processing_path=f"processing/{job_id}/source.mp4",
        sha256_hex="b" * 64,
        size_bytes=1,
        mime_guess="video/mp4",
    )


class ReportsTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-content-reports-")
        self.root = Path(self._tmp) / "content"
        paths.ensure_layout(self.root)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestRedaction(unittest.TestCase):
    def test_secret_shaped_key_is_redacted(self):
        out = reports.redact_structure({"SESSION_COOKIE": "abc123", "note": "fine"})
        self.assertEqual(out["SESSION_COOKIE"], "[REDACTED]")
        self.assertEqual(out["note"], "fine")

    def test_secret_shaped_value_pattern_is_redacted(self):
        out = reports.redact_value("API_TOKEN=super-secret-value")
        self.assertNotIn("super-secret-value", out)
        self.assertIn("[REDACTED]", out)

    def test_nested_structures_are_redacted(self):
        out = reports.redact_structure({"result": {"AUTH_KEY": "xyz", "url": "https://x"}})
        self.assertEqual(out["result"]["AUTH_KEY"], "[REDACTED]")
        self.assertEqual(out["result"]["url"], "https://x")

    def test_non_secret_fields_pass_through(self):
        out = reports.redact_structure({"job_id": "abc", "state": "queued"})
        self.assertEqual(out, {"job_id": "abc", "state": "queued"})


class TestAuditLog(ReportsTestBase):
    def test_append_creates_hash_chained_lines(self):
        reports.audit_append(self.root, actor="system", job_id="j1", from_state=None, to_state="queued")
        reports.audit_append(self.root, actor="alice", job_id="j1", from_state="queued", to_state="planning")
        ok, bad_line = reports.audit_verify(self.root)
        self.assertTrue(ok)
        self.assertIsNone(bad_line)

    def test_audit_log_is_0600_under_0700_state_dir(self):
        reports.audit_append(self.root, actor="system", job_id="j1", from_state=None, to_state="queued")
        audit_path = paths.audit_log_path(self.root)
        self.assertEqual(audit_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(paths.state_dir(self.root).stat().st_mode & 0o777, 0o700)

    def test_rewriting_a_line_is_detected(self):
        reports.audit_append(self.root, actor="system", job_id="j1", from_state=None, to_state="queued")
        reports.audit_append(self.root, actor="alice", job_id="j1", from_state="queued", to_state="planning")
        reports.audit_append(self.root, actor="bob", job_id="j1", from_state="planning", to_state="approval-required")

        audit_path = paths.audit_log_path(self.root)
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(lines[1])
        tampered["actor"] = "mallory"  # rewrite without recomputing the hash chain
        lines[1] = json.dumps(tampered, sort_keys=True)
        audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ok, bad_line = reports.audit_verify(self.root)
        self.assertFalse(ok)
        self.assertEqual(bad_line, 2)

    def test_inserting_a_line_is_detected(self):
        reports.audit_append(self.root, actor="system", job_id="j1", from_state=None, to_state="queued")
        reports.audit_append(self.root, actor="alice", job_id="j1", from_state="queued", to_state="planning")
        audit_path = paths.audit_log_path(self.root)
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        forged = {
            "ts": jobs.now_iso(),
            "actor": "mallory",
            "job": "j1",
            "from": "planning",
            "to": "approval-required",
            "platform": None,
            "artifact_hash": None,
            "url": None,
            "worker_version": None,
            "note": "forged, wrong prev_hash",
            "prev_hash": "0" * 64,
            "line_hash": "1" * 64,
        }
        lines.append(json.dumps(forged, sort_keys=True))
        audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, bad_line = reports.audit_verify(self.root)
        self.assertFalse(ok)

    def test_audit_entry_with_secret_shaped_note_is_redacted(self):
        reports.audit_append(
            self.root,
            actor="system",
            job_id="j1",
            from_state="publishing",
            to_state="failed",
            note="worker returned AUTH_TOKEN=leaked-value in logs",
        )
        audit_path = paths.audit_log_path(self.root)
        content = audit_path.read_text(encoding="utf-8")
        self.assertNotIn("leaked-value", content)

    def test_empty_audit_log_verifies_true(self):
        ok, bad_line = reports.audit_verify(self.root)
        self.assertTrue(ok)


class TestReportGeneration(ReportsTestBase):
    def test_first_report_is_report_json_and_md(self):
        record = _new_record()
        written = reports.generate_report(record, self.root)
        self.assertTrue(Path(written["json"]).name == "report.json")
        self.assertTrue(Path(written["md"]).name == "report.md")
        self.assertTrue(Path(written["json"]).is_file())
        self.assertTrue(Path(written["md"]).is_file())

    def test_regeneration_never_overwrites_report_json(self):
        record = _new_record()
        first = reports.generate_report(record, self.root)
        original_content = Path(first["json"]).read_text(encoding="utf-8")

        record["state"] = "manual-review"  # simulate a later change worth re-reporting
        second = reports.generate_report(record, self.root)

        self.assertNotEqual(first["json"], second["json"])
        self.assertEqual(Path(second["json"]).name, "report-2.json")
        # report.json (the first version) is untouched.
        self.assertEqual(Path(first["json"]).read_text(encoding="utf-8"), original_content)

    def test_third_generation_uses_report_3(self):
        record = _new_record()
        reports.generate_report(record, self.root)
        reports.generate_report(record, self.root)
        third = reports.generate_report(record, self.root)
        self.assertEqual(Path(third["json"]).name, "report-3.json")

    def test_report_json_is_valid_and_redacted(self):
        record = _new_record()
        record["publish"]["last_result"] = {"status": "ok", "SESSION_COOKIE": "shhh", "url": "https://x"}
        written = reports.generate_report(record, self.root)
        data = json.loads(Path(written["json"]).read_text(encoding="utf-8"))
        self.assertEqual(data["publish"]["last_result"]["SESSION_COOKIE"], "[REDACTED]")

    def test_report_files_are_0600(self):
        record = _new_record()
        written = reports.generate_report(record, self.root)
        self.assertEqual(Path(written["json"]).stat().st_mode & 0o777, 0o600)


class TestArchive(ReportsTestBase):
    def _terminal_record(self, state="succeeded"):
        record = _new_record()
        proc_dir = paths.job_processing_dir(record["job_id"], self.root)
        proc_dir.mkdir(parents=True, exist_ok=True)
        (proc_dir / "source.mp4").write_bytes(b"evidence")
        record["state"] = state
        record["history"] = [{"ts": jobs.now_iso(), "from": "verifying", "to": state, "actor": "system", "note": ""}]
        return record

    def test_succeeded_archives_to_uploaded(self):
        record = self._terminal_record("succeeded")
        dest = reports.archive_job(record, self.root)
        self.assertEqual(record["state"], "archived")
        self.assertTrue((self.root / "uploaded" / record["job_id"] / "source.mp4").is_file())
        self.assertEqual(dest, self.root / "uploaded" / record["job_id"])

    def test_failed_archives_to_failed(self):
        record = self._terminal_record("failed")
        reports.archive_job(record, self.root)
        self.assertTrue((self.root / "failed" / record["job_id"] / "source.mp4").is_file())

    def test_archive_does_not_overwrite_existing_evidence(self):
        record = self._terminal_record("succeeded")
        dest_dir = self.root / "uploaded" / record["job_id"]
        dest_dir.mkdir(parents=True)
        (dest_dir / "preexisting.txt").write_text("keep me", encoding="utf-8")
        reports.archive_job(record, self.root)
        self.assertTrue((dest_dir / "preexisting.txt").is_file())


class TestExport(ReportsTestBase):
    def test_export_copies_reports_and_recent_audit_only(self):
        record = _new_record()
        reports.generate_report(record, self.root)
        reports.audit_append(self.root, actor="system", job_id=record["job_id"], from_state=None, to_state="queued")

        out_dir = Path(self._tmp) / "export-out"
        result = reports.export_reports(self.root, since="2020-01-01", out_dir=out_dir)
        self.assertIn(record["job_id"], result["exported_jobs"])
        self.assertTrue((out_dir / "reports" / record["job_id"] / "report.json").is_file())
        self.assertTrue((out_dir / "audit.jsonl").is_file())

    def test_export_never_touches_sessions(self):
        (paths.sessions_dir(self.root) / "cookie-blob").write_text("do-not-export", encoding="utf-8")
        out_dir = Path(self._tmp) / "export-out2"
        reports.export_reports(self.root, since="2020-01-01", out_dir=out_dir)
        # No "sessions" directory should appear anywhere under the export.
        for p in out_dir.rglob("*"):
            self.assertNotIn("sessions", p.parts)
        self.assertFalse((out_dir / "sessions").exists())

    def test_export_filters_audit_by_since_date(self):
        record = _new_record()
        reports.audit_append(self.root, actor="system", job_id=record["job_id"], from_state=None, to_state="queued")
        out_dir = Path(self._tmp) / "export-out3"
        # A since-date far in the future excludes everything.
        result = reports.export_reports(self.root, since="2099-01-01", out_dir=out_dir)
        self.assertEqual(result["audit_lines"], 0)


class TestPrune(ReportsTestBase):
    def _archived_record(self, days_old=100):
        from datetime import datetime, timedelta, timezone

        record = _new_record()
        old_ts = (datetime.now(timezone.utc) - timedelta(days=days_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
        record["state"] = "archived"
        record["updated_at"] = old_ts
        record["history"] = [{"ts": old_ts, "from": "succeeded", "to": "archived", "actor": "system", "note": ""}]
        dest_dir = self.root / "uploaded" / record["job_id"]
        dest_dir.mkdir(parents=True)
        (dest_dir / "source.mp4").write_bytes(b"evidence")
        job_reports_dir = paths.reports_dir(self.root) / record["job_id"]
        job_reports_dir.mkdir(parents=True)
        (job_reports_dir / "report.json").write_text("{}", encoding="utf-8")
        jobs.save_job(record, self.root)
        return record

    def test_dry_run_reports_without_deleting(self):
        record = self._archived_record(days_old=100)
        result = reports.prune(self.root, older_than_days=30, dry_run=True)
        self.assertIn(record["job_id"], result["job_ids"])
        self.assertEqual(result["deleted_paths"], [])
        self.assertTrue((self.root / "uploaded" / record["job_id"]).exists())

    def test_prune_deletes_media_and_reports_but_not_sessions_or_audit(self):
        record = self._archived_record(days_old=100)
        (paths.sessions_dir(self.root) / "keepme").write_text("secret", encoding="utf-8")
        reports.audit_append(self.root, actor="system", job_id=record["job_id"], from_state=None, to_state="queued")
        audit_before = paths.audit_log_path(self.root).read_text(encoding="utf-8")

        result = reports.prune(self.root, older_than_days=30, dry_run=False)

        self.assertIn(record["job_id"], result["job_ids"])
        self.assertFalse((self.root / "uploaded" / record["job_id"]).exists())
        self.assertFalse((paths.reports_dir(self.root) / record["job_id"]).exists())
        # sessions/ and audit.jsonl are never touched by prune.
        self.assertTrue((paths.sessions_dir(self.root) / "keepme").is_file())
        self.assertEqual(paths.audit_log_path(self.root).read_text(encoding="utf-8"), audit_before)

    def test_prune_ignores_recent_jobs(self):
        record = self._archived_record(days_old=1)
        result = reports.prune(self.root, older_than_days=30, dry_run=False)
        self.assertNotIn(record["job_id"], result["job_ids"])
        self.assertTrue((self.root / "uploaded" / record["job_id"]).exists())

    def test_prune_ignores_non_archived_jobs(self):
        record = _new_record()
        jobs.save_job(record, self.root)
        result = reports.prune(self.root, older_than_days=0, dry_run=False)
        self.assertNotIn(record["job_id"], result["job_ids"])


if __name__ == "__main__":
    unittest.main()
