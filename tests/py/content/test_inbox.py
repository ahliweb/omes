import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401  (sets sys.path)

from content import inbox, jobs, paths  # noqa: E402


class InboxTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-content-test-")
        self.root = Path(self._tmp) / "content"
        paths.ensure_layout(self.root)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _drop_file(self, name: str, data: bytes = b"hello world") -> Path:
        p = paths.inbox_dir(self.root) / name
        p.write_bytes(data)
        return p


class TestScanBasic(InboxTestBase):
    def test_settled_file_becomes_a_job(self):
        self._drop_file("a.mp4")
        result = inbox.scan(self.root, settle_seconds=0)
        self.assertEqual(len(result["created"]), 1)
        job_id = result["created"][0]
        record = jobs.load_job(job_id, self.root)
        self.assertEqual(record["state"], "queued")
        self.assertEqual(record["source"]["mime_guess"], "video/mp4")
        # File moved out of inbox into processing/<job-id>/source.mp4
        self.assertFalse((paths.inbox_dir(self.root) / "a.mp4").exists())
        moved = paths.job_processing_dir(job_id, self.root) / "source.mp4"
        self.assertTrue(moved.is_file())

    def test_settle_window_defers_unstable_file(self):
        self._drop_file("b.mp4")
        result = inbox.scan(self.root, settle_seconds=3600)
        self.assertEqual(result["created"], [])
        self.assertEqual(len(result["pending"]), 1)
        # File is still in the inbox - untouched.
        self.assertTrue((paths.inbox_dir(self.root) / "b.mp4").exists())

    def test_two_scans_settle_even_with_long_window(self):
        self._drop_file("c.mp4")
        first = inbox.scan(self.root, settle_seconds=3600)
        self.assertEqual(first["created"], [])
        # Second scan with identical size/mtime settles it (two-scan rule).
        second = inbox.scan(self.root, settle_seconds=3600)
        self.assertEqual(len(second["created"]), 1)

    def test_duplicate_hash_is_skipped_with_duplicate_of(self):
        self._drop_file("d1.mp4", data=b"same-bytes")
        first = inbox.scan(self.root, settle_seconds=0)
        self.assertEqual(len(first["created"]), 1)
        original_id = first["created"][0]

        self._drop_file("d2.mp4", data=b"same-bytes")
        second = inbox.scan(self.root, settle_seconds=0)
        self.assertEqual(second["created"], [])
        self.assertEqual(len(second["duplicates"]), 1)
        dup_record = jobs.load_job(second["duplicates"][0], self.root)
        self.assertEqual(dup_record["source"]["duplicate_of"], original_id)
        self.assertEqual(dup_record["state"], "cancelled")
        # Duplicate no longer sits in inbox (no infinite rescanning).
        self.assertFalse((paths.inbox_dir(self.root) / "d2.mp4").exists())

    def test_symlink_is_never_followed(self):
        target = Path(self._tmp) / "outside.mp4"
        target.write_bytes(b"secret-outside-inbox")
        link = paths.inbox_dir(self.root) / "link.mp4"
        os.symlink(target, link)
        result = inbox.scan(self.root, settle_seconds=0)
        self.assertEqual(result["created"], [])
        self.assertEqual(result["duplicates"], [])
        # The symlink itself is left alone, not consumed or moved.
        self.assertTrue(link.is_symlink())

    def test_generated_dirs_are_ignored(self):
        # A file placed directly under reports/ (not inbox/) must never be
        # picked up even if someone scans the parent by mistake; scan()
        # only ever walks inbox_dir(), so this is mostly a sanity check
        # that ignoring dotfiles/generated dirs inside inbox/ works too.
        hidden = paths.inbox_dir(self.root) / ".hidden.mp4"
        hidden.write_bytes(b"x")
        result = inbox.scan(self.root, settle_seconds=0)
        self.assertEqual(result["created"], [])
        self.assertTrue(hidden.exists())


class TestAtomicity(InboxTestBase):
    def test_crash_between_temp_write_and_rename_leaves_no_partial_final_file(self):
        self._drop_file("e.mp4")
        # Monkeypatch os.replace to simulate a crash: raise right before
        # the rename would happen, after the temp copy was already made.
        original_replace = os.replace
        calls = {"n": 0}

        def flaky_replace(src, dst):
            calls["n"] += 1
            raise OSError("simulated crash before rename")

        os.replace = flaky_replace
        try:
            with self.assertRaises(OSError):
                inbox.scan(self.root, settle_seconds=0)
        finally:
            os.replace = original_replace

        self.assertEqual(calls["n"], 1)
        # No job record should exist (save_job happens after the move).
        self.assertEqual(jobs.list_jobs(self.root), [])
        # No file visible under its final name anywhere in processing/.
        for p in paths.processing_dir(self.root).rglob("source.*"):
            self.fail(f"unexpected partially-renamed file: {p}")
        # A rescan afterwards does not choke on the leftover temp file.
        result = inbox.rescan(self.root)
        self.assertEqual(result["checked"], [])


class TestLock(InboxTestBase):
    def test_concurrent_scan_is_rejected(self):
        with inbox.scan_lock(self.root):
            with self.assertRaises(inbox.LockContentionError):
                with inbox.scan_lock(self.root):
                    pass

    def test_stale_lock_is_reclaimed(self):
        lock_path = paths.scan_lock_path(self.root)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # A pid that (almost certainly) does not exist.
        lock_path.write_text("999999999")
        with inbox.scan_lock(self.root):
            pass  # No exception: stale lock was reclaimed.


class TestRescan(InboxTestBase):
    def test_rescan_detects_tampered_processing_file(self):
        self._drop_file("f.mp4")
        result = inbox.scan(self.root, settle_seconds=0)
        job_id = result["created"][0]
        moved = paths.job_processing_dir(job_id, self.root) / "source.mp4"
        moved.write_bytes(b"tampered contents")

        rescan_result = inbox.rescan(self.root)
        self.assertIn(job_id, rescan_result["mismatches"])

    def test_rescan_clean_when_untouched(self):
        self._drop_file("g.mp4")
        result = inbox.scan(self.root, settle_seconds=0)
        self.assertEqual(len(result["created"]), 1)
        rescan_result = inbox.rescan(self.root)
        self.assertEqual(rescan_result["mismatches"], [])


if __name__ == "__main__":
    unittest.main()
