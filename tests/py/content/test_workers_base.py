import shutil
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from content.workers import base  # noqa: E402


class TestPathBoundary(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-content-workers-base-")
        self.allowed = Path(self._tmp) / "allowed"
        self.allowed.mkdir()
        self.outside = Path(self._tmp) / "outside"
        self.outside.mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_path_inside_allowed_root_is_accepted(self):
        target = self.allowed / "evidence.json"
        resolved = base.enforce_path_boundary(target, [self.allowed])
        self.assertEqual(resolved, target.resolve())

    def test_path_outside_allowed_roots_is_rejected(self):
        target = self.outside / "evil.json"
        with self.assertRaises(base.PathBoundaryError):
            base.enforce_path_boundary(target, [self.allowed])

    def test_parent_traversal_is_rejected(self):
        target = self.allowed / ".." / "outside" / "evil.json"
        with self.assertRaises(base.PathBoundaryError):
            base.enforce_path_boundary(target, [self.allowed])

    def test_symlink_escape_is_rejected(self):
        link = self.allowed / "escape"
        link.symlink_to(self.outside)
        target = link / "evil.json"
        with self.assertRaises(base.PathBoundaryError):
            base.enforce_path_boundary(target, [self.allowed])

    def test_multiple_allowed_roots(self):
        second = Path(self._tmp) / "second-allowed"
        second.mkdir()
        target = second / "x.json"
        resolved = base.enforce_path_boundary(target, [self.allowed, second])
        self.assertEqual(resolved, target.resolve())

    def test_empty_allowed_roots_rejects_everything(self):
        with self.assertRaises(base.PathBoundaryError):
            base.enforce_path_boundary(self.allowed / "x", [])


class TestRedaction(unittest.TestCase):
    def test_redacts_secret_shaped_keys(self):
        out = base.redact({"api_token": "sekret", "note": "hello"})
        self.assertEqual(out["api_token"], "[REDACTED]")
        self.assertEqual(out["note"], "hello")

    def test_redacts_nested_structures(self):
        out = base.redact({"result": {"cookie_jar": "abc", "list": [{"password": "x"}, "plain"]}})
        self.assertEqual(out["result"]["cookie_jar"], "[REDACTED]")
        self.assertEqual(out["result"]["list"][0]["password"], "[REDACTED]")
        self.assertEqual(out["result"]["list"][1], "plain")


class TestTypedFailures(unittest.TestCase):
    def test_retryable_failure_shape(self):
        result = base.typed_failure("retryable", "transient")
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["retryable"])
        self.assertEqual(result["failure_state"], "retryable")

    def test_nonretryable_failure_shape(self):
        result = base.typed_failure("nonretryable", "permanent")
        self.assertEqual(result["status"], "error")
        self.assertFalse(result["retryable"])
        self.assertEqual(result["failure_state"], "nonretryable")

    def test_uncertain_failure_shape(self):
        result = base.typed_failure("uncertain", "ambiguous")
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["failure_state"], "uncertain")

    def test_needs_login_failure_shape(self):
        result = base.typed_failure("needs_login", "not logged in")
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["failure_state"], "needs_login")

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            base.typed_failure("bogus", "x")

    def test_ok_result_shape(self):
        result = base.ok_result(url="https://example.invalid/x", screenshot_ref="evidence/x.json")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["url"], "https://example.invalid/x")
        self.assertIsNone(result["failure_state"])


class TestSessionAndEvidenceDirs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-content-workers-dirs-")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_ensure_session_dir_creates_mode_0700(self):
        session_dir = Path(self._tmp) / "sessions" / "generic_browser"
        base.ensure_session_dir(session_dir)
        self.assertTrue(session_dir.is_dir())
        mode = oct(session_dir.stat().st_mode)[-3:]
        self.assertEqual(mode, "700")

    def test_ensure_evidence_dir_creates_directory(self):
        evidence_dir = Path(self._tmp) / "reports" / "job1" / "evidence"
        base.ensure_evidence_dir(evidence_dir)
        self.assertTrue(evidence_dir.is_dir())


class TestDispatch(unittest.TestCase):
    def test_dispatch_unknown_operation_is_typed_nonretryable(self):
        result = base.dispatch({}, "bogus-op", {})
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["failure_state"], "nonretryable")

    def test_dispatch_converts_path_boundary_error(self):
        def handler(_payload):
            raise base.PathBoundaryError("nope")

        result = base.dispatch({"op": handler}, "op", {})
        self.assertEqual(result["failure_state"], "nonretryable")
        self.assertIn("path boundary violation", result["note"])

    def test_dispatch_never_raises_on_unexpected_exception(self):
        def handler(_payload):
            raise RuntimeError("boom")

        result = base.dispatch({"op": handler}, "op", {})
        self.assertEqual(result["failure_state"], "nonretryable")

    def test_dispatch_passes_through_successful_result(self):
        def handler(_payload):
            return base.ok_result(url="u")

        result = base.dispatch({"op": handler}, "op", {})
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
