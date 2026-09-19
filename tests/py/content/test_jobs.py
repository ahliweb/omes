import shutil
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from content import jobs, paths  # noqa: E402


class TestJobRecord(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-content-jobs-test-")
        self.root = Path(self._tmp) / "content"
        paths.ensure_layout(self.root)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_new_job_record_has_schema_version_and_queued_state(self):
        record = jobs.new_job_record(
            job_id="abc123-20260101T000000Z",
            original_path="inbox/x.mp4",
            processing_path="processing/abc123-20260101T000000Z/source.mp4",
            sha256_hex="abc123" * 10,
            size_bytes=42,
            mime_guess="video/mp4",
        )
        self.assertEqual(record["schema_version"], jobs.SCHEMA_VERSION)
        self.assertEqual(record["state"], "queued")
        self.assertEqual(len(record["history"]), 1)

    def test_save_and_load_roundtrip(self):
        record = jobs.new_job_record(
            job_id="deadbeefdead-20260101T000000Z",
            original_path="inbox/x.mp4",
            processing_path="processing/deadbeefdead-20260101T000000Z/source.mp4",
            sha256_hex="d" * 64,
            size_bytes=1,
            mime_guess=None,
        )
        path = jobs.save_job(record, self.root)
        self.assertTrue(path.is_file())
        # Job files hold no secrets but are still kept private (0600).
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        loaded = jobs.load_job(record["job_id"], self.root)
        self.assertEqual(loaded, record)

    def test_unknown_job_raises(self):
        with self.assertRaises(jobs.UnknownJobError):
            jobs.load_job("does-not-exist", self.root)

    def test_unknown_schema_version_rejected(self):
        record = jobs.new_job_record(
            job_id="cafef00dcafe-20260101T000000Z",
            original_path="inbox/x.mp4",
            processing_path="p",
            sha256_hex="c" * 64,
            size_bytes=1,
            mime_guess=None,
        )
        record["schema_version"] = 999
        jobs.save_job(record, self.root)
        with self.assertRaises(jobs.UnknownSchemaVersionError):
            jobs.load_job(record["job_id"], self.root)

    def test_record_history_appends_and_updates_state(self):
        record = jobs.new_job_record(
            job_id="0011223344ff-20260101T000000Z",
            original_path="inbox/x.mp4",
            processing_path="p",
            sha256_hex="0" * 64,
            size_bytes=1,
            mime_guess=None,
        )
        jobs.record_history(record, "planning", actor="system", note="plan started")
        self.assertEqual(record["state"], "planning")
        self.assertEqual(len(record["history"]), 2)
        self.assertEqual(record["history"][-1]["from"], "queued")
        self.assertEqual(record["history"][-1]["to"], "planning")


class TestPathsLayout(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-content-paths-test-")
        self.root = Path(self._tmp) / "content"

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_ensure_layout_creates_all_dirs_with_expected_modes(self):
        paths.ensure_layout(self.root)
        for name in paths.LAYOUT_DIRS:
            d = self.root / name
            self.assertTrue(d.is_dir(), f"missing {d}")
        sessions_mode = (self.root / "sessions").stat().st_mode & 0o777
        state_mode = (self.root / "state").stat().st_mode & 0o777
        self.assertEqual(sessions_mode, 0o700)
        self.assertEqual(state_mode, 0o700)

    def test_default_content_root_uses_xdg_data_home(self):
        import os

        old_root = os.environ.pop("OMES_CONTENT_ROOT", None)
        old_xdg = os.environ.get("XDG_DATA_HOME")
        try:
            os.environ["XDG_DATA_HOME"] = "/tmp/xdg-data-home-test"
            resolved = paths.content_root()
            self.assertEqual(str(resolved), "/tmp/xdg-data-home-test/omes/content")
        finally:
            if old_root is not None:
                os.environ["OMES_CONTENT_ROOT"] = old_root
            if old_xdg is not None:
                os.environ["XDG_DATA_HOME"] = old_xdg
            else:
                os.environ.pop("XDG_DATA_HOME", None)


if __name__ == "__main__":
    unittest.main()
