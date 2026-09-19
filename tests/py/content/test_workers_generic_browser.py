"""Exercises the generic_browser worker (issue #66) exactly the way the
manager does: as a subprocess, JSON on stdin, JSON on stdout - using the
default manual_stub driver (no browser dependency)."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from content.workers import registry  # noqa: E402

WORKER = str(registry.worker_executable_for_platform("generic_browser"))


def _run(operation: str, payload: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, WORKER, operation],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


class TestGenericBrowserWorkerLifecycle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-content-generic-browser-")
        root = Path(self._tmp)
        self.processing_dir = root / "processing" / "job1"
        self.processing_dir.mkdir(parents=True)
        self.source_path = self.processing_dir / "source.mp4"
        self.source_path.write_bytes(b"fake media bytes")
        self.session_dir = root / "sessions" / "generic_browser"
        self.evidence_dir = root / "reports" / "job1" / "evidence"
        self.allowed_paths = {
            "processing_dir": str(self.processing_dir),
            "session_dir": str(self.session_dir),
            "evidence_dir": str(self.evidence_dir),
        }

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _payload(self, **extra):
        payload = {
            "job_id": "job1",
            "platform": "generic_browser",
            "allowed_paths": self.allowed_paths,
            "session_dir": str(self.session_dir),
        }
        payload.update(extra)
        return payload

    def test_prepare_creates_session_dir_mode_0700(self):
        result = _run("prepare", self._payload(source_path=str(self.source_path)))
        self.assertEqual(result, {"status": "ok", "ready": True})
        self.assertTrue(self.session_dir.is_dir())
        mode = oct(self.session_dir.stat().st_mode)[-3:]
        self.assertEqual(mode, "700")

    def test_publish_before_bootstrap_returns_needs_login(self):
        _run("prepare", self._payload(source_path=str(self.source_path)))
        result = _run("publish", self._payload(source_path=str(self.source_path), caption="hi"))
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["failure_state"], "needs_login")
        self.assertIsNone(result["url"])

    def test_bootstrap_session_never_publishes_and_marks_session(self):
        _run("prepare", self._payload(source_path=str(self.source_path)))
        result = _run("bootstrap-session", self._payload())
        self.assertEqual(result["status"], "ok")
        marker = self.session_dir / "manual_stub_logged_in.json"
        self.assertTrue(marker.is_file())
        # bootstrap must never write publish evidence.
        self.assertFalse(self.evidence_dir.exists())

    def test_publish_after_bootstrap_succeeds_with_url_and_screenshot_ref(self):
        _run("prepare", self._payload(source_path=str(self.source_path)))
        _run("bootstrap-session", self._payload())
        result = _run("publish", self._payload(source_path=str(self.source_path), caption="hi"))
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["url"].startswith("https://"))
        self.assertIsNotNone(result["screenshot_ref"])
        self.assertIsInstance(result["screenshot_ref"], str)
        # never inline bytes - it's a path reference.
        self.assertNotIn("\x00", result["screenshot_ref"])

    def test_verify_confirms_published_url(self):
        _run("prepare", self._payload(source_path=str(self.source_path)))
        _run("bootstrap-session", self._payload())
        published = _run("publish", self._payload(source_path=str(self.source_path), caption="hi"))
        verified = _run("verify", self._payload(url=published["url"]))
        self.assertEqual(verified["status"], "ok")
        self.assertEqual(verified["url"], published["url"])

    def test_verify_unrecognized_url_is_uncertain(self):
        result = _run("verify", self._payload(url="https://not-ours.example/x"))
        self.assertEqual(result["status"], "uncertain")

    def test_collect_evidence_lists_only_evidence_dir_files_never_session_dir(self):
        _run("prepare", self._payload(source_path=str(self.source_path)))
        _run("bootstrap-session", self._payload())
        _run("publish", self._payload(source_path=str(self.source_path), caption="hi"))
        result = _run("collect-evidence", self._payload())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["evidence_paths"]), 1)
        for p in result["evidence_paths"]:
            self.assertIn(str(self.evidence_dir), p)
            self.assertNotIn(str(self.session_dir), p)

    def test_revoke_session_clears_the_profile_directory(self):
        _run("prepare", self._payload(source_path=str(self.source_path)))
        _run("bootstrap-session", self._payload())
        self.assertTrue(any(self.session_dir.iterdir()))
        result = _run("revoke-session", self._payload())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(list(self.session_dir.iterdir()), [])

    def test_publish_rejects_source_path_outside_allowed_roots(self):
        outside = Path(self._tmp) / "outside.mp4"
        outside.write_bytes(b"x")
        _run("prepare", self._payload(source_path=str(self.source_path)))
        _run("bootstrap-session", self._payload())
        result = _run("publish", self._payload(source_path=str(outside), caption="hi"))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["failure_state"], "nonretryable")
        self.assertIn("path boundary violation", result["note"])

    def test_worker_result_never_contains_a_planted_secret_key(self):
        # Sanity check on the redaction path: even if a driver accidentally
        # returned a secret-shaped field, the transport must strip it.
        _run("prepare", self._payload(source_path=str(self.source_path)))
        _run("bootstrap-session", self._payload())
        result = _run("publish", self._payload(source_path=str(self.source_path), caption="hi"))
        dumped = json.dumps(result)
        self.assertNotIn("COOKIE_VALUE_SHOULD_NEVER_APPEAR", dumped)


class TestGenericBrowserWorkerMissingSessionDir(unittest.TestCase):
    def test_prepare_without_allowed_session_dir_is_a_typed_failure(self):
        proc = subprocess.run(
            [sys.executable, WORKER, "prepare"],
            input=json.dumps({"job_id": "job1", "allowed_paths": {}}),
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["failure_state"], "nonretryable")


if __name__ == "__main__":
    unittest.main()
