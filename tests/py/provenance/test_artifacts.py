"""tests/py/provenance/test_artifacts.py - unit tests for
lib/omes/py/provenance/artifacts.py (issue #236, threat AI-06: model
and runtime artifact provenance/integrity evidence for local inference).

Covers the positive path (a declared artifact with a matching pinned
digest verifies and is recorded), and the negative paths required by
the issue: a checksum mismatch and a missing artifact must both be
FAIL/fail-closed, never silently downgraded to a warning or ignored.
Also covers the re-hash cache (`_needs_rehash`) that keeps repeated
`--verify-artifacts` runs cheap for large, rarely-changing model files,
and `summarize()`, the hashing-free read path `omes health ai-privacy`
uses on every invocation.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PROVENANCE_DIR = os.path.join(ROOT, "lib", "omes", "py", "provenance")


def _load(name):
    spec = importlib.util.spec_from_file_location(f"omes_provenance_{name}", os.path.join(PROVENANCE_DIR, f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"omes_provenance_{name}"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


record = _load("record")
artifacts = _load("artifacts")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestVerifyOne(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp()
        self.work_dir = tempfile.mkdtemp()

    def _write(self, name: str, content: bytes) -> str:
        path = os.path.join(self.work_dir, name)
        with open(path, "wb") as fh:
            fh.write(content)
        return path

    def test_matching_pin_is_verified(self):
        content = b"fake model weights, deterministic for the test"
        path = self._write("model.bin", content)
        digest = _sha256(content)

        result = artifacts.verify_one(
            {"component": "main-model", "path": path, "expected_sha256": digest}, self.state_dir, force=False
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["checksum_status"], "verified")
        self.assertTrue(result["hashed"])

    def test_no_pin_supplied_is_unverified_not_a_failure(self):
        path = self._write("model.bin", b"content")
        result = artifacts.verify_one({"component": "m", "path": path, "expected_sha256": None}, self.state_dir, force=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["checksum_status"], "unverified")

    def test_mismatched_pin_is_fail_closed(self):
        path = self._write("model.bin", b"actual content")
        wrong_digest = _sha256(b"different content entirely")
        result = artifacts.verify_one(
            {"component": "m", "path": path, "expected_sha256": wrong_digest}, self.state_dir, force=False
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["checksum_status"], "mismatch")

    def test_missing_artifact_is_fail_closed(self):
        missing_path = os.path.join(self.work_dir, "does-not-exist.bin")
        result = artifacts.verify_one(
            {"component": "m", "path": missing_path, "expected_sha256": _sha256(b"anything")}, self.state_dir, force=False
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["checksum_status"], "missing")

    def test_invalid_component_name_is_rejected(self):
        result = artifacts.verify_one(
            {"component": "../../etc/passwd", "path": self._write("m.bin", b"x"), "expected_sha256": None},
            self.state_dir,
            force=False,
        )
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_relative_path_is_rejected(self):
        result = artifacts.verify_one(
            {"component": "m", "path": "relative/path.bin", "expected_sha256": None}, self.state_dir, force=False
        )
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_malformed_expected_sha256_is_rejected_not_silently_ignored(self):
        path = self._write("m.bin", b"content")
        result = artifacts.verify_one(
            {"component": "m", "path": path, "expected_sha256": "not-a-hex-digest"}, self.state_dir, force=False
        )
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_record_is_written_and_readable_by_the_existing_provenance_audit_module(self):
        content = b"weights"
        path = self._write("model.bin", content)
        digest = _sha256(content)
        artifacts.verify_one({"component": "m", "path": path, "expected_sha256": digest}, self.state_dir, force=False)

        audit = _load("audit")
        report = audit.audit(self.state_dir, None, None)
        components = [c["component"] for c in report["components"]]
        self.assertIn("model-artifact:m", components)
        self.assertEqual(report["findings"], [])
        self.assertTrue(report["ok"])

    def test_mismatch_record_surfaces_as_fail_through_the_existing_audit_module(self):
        path = self._write("model.bin", b"actual")
        wrong = _sha256(b"expected-something-else")
        artifacts.verify_one({"component": "m", "path": path, "expected_sha256": wrong}, self.state_dir, force=False)

        audit = _load("audit")
        report = audit.audit(self.state_dir, None, None)
        severities = {f["severity"] for f in report["findings"]}
        self.assertIn("FAIL", severities)
        self.assertFalse(report["ok"])

    def test_missing_artifact_record_surfaces_as_fail_through_the_existing_audit_module(self):
        missing_path = os.path.join(self.work_dir, "gone.bin")
        artifacts.verify_one({"component": "m", "path": missing_path, "expected_sha256": None}, self.state_dir, force=False)

        audit = _load("audit")
        report = audit.audit(self.state_dir, None, None)
        kinds = {f["kind"] for f in report["findings"]}
        self.assertIn("artifact_missing", kinds)
        self.assertFalse(report["ok"])


class TestRehashCache(unittest.TestCase):
    """Hashing a multi-GB model file on every verification run would be
    prohibitively slow (see module docstring / docs/provenance.md). These
    tests prove the cache actually skips re-hashing an unchanged file and
    still re-hashes when the file changes or --force is passed."""

    def setUp(self):
        self.state_dir = tempfile.mkdtemp()
        self.work_dir = tempfile.mkdtemp()
        self.path = os.path.join(self.work_dir, "model.bin")
        with open(self.path, "wb") as fh:
            fh.write(b"version one")
        self.digest = _sha256(b"version one")

    def test_second_run_with_unchanged_file_skips_rehash(self):
        entry = {"component": "m", "path": self.path, "expected_sha256": self.digest}
        first = artifacts.verify_one(entry, self.state_dir, force=False)
        self.assertTrue(first["hashed"])

        second = artifacts.verify_one(entry, self.state_dir, force=False)
        self.assertFalse(second["hashed"])
        self.assertEqual(second["checksum_status"], "verified")

    def test_force_always_rehashes(self):
        entry = {"component": "m", "path": self.path, "expected_sha256": self.digest}
        artifacts.verify_one(entry, self.state_dir, force=False)
        forced = artifacts.verify_one(entry, self.state_dir, force=True)
        self.assertTrue(forced["hashed"])

    def test_changed_file_is_rehashed_even_without_force(self):
        entry = {"component": "m", "path": self.path, "expected_sha256": self.digest}
        artifacts.verify_one(entry, self.state_dir, force=False)

        with open(self.path, "wb") as fh:
            fh.write(b"version two, a different length so mtime/size both change")

        second = artifacts.verify_one(entry, self.state_dir, force=False)
        self.assertTrue(second["hashed"])
        self.assertEqual(second["checksum_status"], "mismatch")

    def test_content_swap_with_restored_mtime_is_still_rehashed_and_flagged(self):
        """Security regression: an attacker who can replace the artifact
        can also restore its mtime with `touch -d`/`os.utime`. A cache
        keyed on (size, mtime) alone would then keep reporting the
        tampered artifact as verified forever - turning an integrity
        control into an accident detector. The cache must also key on
        ctime (not attacker-settable via utime) plus inode/device, so a
        same-size content swap with a restored mtime is still detected."""
        entry = {"component": "m", "path": self.path, "expected_sha256": self.digest}
        first = artifacts.verify_one(entry, self.state_dir, force=False)
        self.assertTrue(first["hashed"])
        self.assertEqual(first["checksum_status"], "verified")

        original_stat = os.stat(self.path)

        # Same size as b"version one" (11 bytes), so a (size, mtime)-only
        # cache would see this as byte-for-byte unchanged once mtime is
        # restored below.
        tampered = b"version TWO"
        self.assertEqual(len(tampered), len(b"version one"))
        with open(self.path, "wb") as fh:
            fh.write(tampered)
        os.utime(self.path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

        # Sanity check: the attack scenario actually restored size+mtime.
        tampered_stat = os.stat(self.path)
        self.assertEqual(tampered_stat.st_size, original_stat.st_size)
        self.assertEqual(tampered_stat.st_mtime_ns, original_stat.st_mtime_ns)

        second = artifacts.verify_one(entry, self.state_dir, force=False)
        self.assertTrue(second["hashed"], "a content swap with a restored mtime must still be rehashed")
        self.assertEqual(second["checksum_status"], "mismatch")

    def test_legacy_stat_snapshot_shape_forces_rehash(self):
        """A provenance record written by older code only ever recorded
        {"size", "mtime"} - not the new ctime/ino/dev keys. Such a
        snapshot must never be trusted: it fails closed to "needs
        rehash" rather than silently treating an incomplete/legacy
        snapshot as a match."""
        entry = {"component": "m", "path": self.path, "expected_sha256": self.digest}
        artifacts.verify_one(entry, self.state_dir, force=False)

        record_path = artifacts._provenance_path(self.state_dir, "m")
        with open(record_path, "r", encoding="utf-8") as fh:
            record = json.load(fh)
        st = os.stat(self.path)
        record["artifact_stat"] = {"size": st.st_size, "mtime": st.st_mtime}
        with open(record_path, "w", encoding="utf-8") as fh:
            json.dump(record, fh)

        second = artifacts.verify_one(entry, self.state_dir, force=False)
        self.assertTrue(second["hashed"], "a legacy (size, mtime)-only snapshot must force a rehash")


class TestSummarize(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp()
        self.work_dir = tempfile.mkdtemp()

    def _write(self, name: str, content: bytes) -> str:
        path = os.path.join(self.work_dir, name)
        with open(path, "wb") as fh:
            fh.write(content)
        return path

    def test_no_declared_artifacts_is_unavailable_not_a_healthy_default(self):
        summary = artifacts.summarize(self.state_dir)
        self.assertFalse(summary["available"])
        self.assertEqual(summary["status"], "unknown")
        self.assertEqual(summary["declared_count"], 0)

    def test_all_verified_is_pass(self):
        content = b"weights"
        path = self._write("m.bin", content)
        artifacts.verify_one({"component": "m", "path": path, "expected_sha256": _sha256(content)}, self.state_dir, force=False)

        summary = artifacts.summarize(self.state_dir)
        self.assertTrue(summary["available"])
        self.assertEqual(summary["status"], "pass")
        self.assertEqual(summary["reason"], "consistent")
        self.assertEqual(summary["declared_count"], 1)
        self.assertEqual(summary["verified_count"], 1)

    def test_any_mismatch_makes_the_whole_summary_fail(self):
        path = self._write("m.bin", b"actual")
        artifacts.verify_one(
            {"component": "m", "path": path, "expected_sha256": _sha256(b"something-else")}, self.state_dir, force=False
        )
        summary = artifacts.summarize(self.state_dir)
        self.assertEqual(summary["status"], "fail")
        self.assertEqual(summary["reason"], "checksum_mismatch")

    def test_any_missing_artifact_makes_the_whole_summary_fail(self):
        missing_path = os.path.join(self.work_dir, "gone.bin")
        artifacts.verify_one({"component": "m", "path": missing_path, "expected_sha256": None}, self.state_dir, force=False)
        summary = artifacts.summarize(self.state_dir)
        self.assertEqual(summary["status"], "fail")
        self.assertEqual(summary["reason"], "missing_artifact")

    def test_unpinned_artifact_is_warn_not_pass(self):
        path = self._write("m.bin", b"content")
        artifacts.verify_one({"component": "m", "path": path, "expected_sha256": None}, self.state_dir, force=False)
        summary = artifacts.summarize(self.state_dir)
        self.assertEqual(summary["status"], "warn")
        self.assertEqual(summary["reason"], "unverified_no_pin")

    def test_stale_evidence_is_warn_even_if_previously_verified(self):
        content = b"weights"
        path = self._write("m.bin", content)
        artifacts.verify_one({"component": "m", "path": path, "expected_sha256": _sha256(content)}, self.state_dir, force=False)

        import datetime

        far_future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
        summary = artifacts.summarize(self.state_dir, now=far_future, max_age_seconds=artifacts.DEFAULT_MAX_ARTIFACT_AGE_SECONDS)
        self.assertEqual(summary["status"], "warn")
        self.assertEqual(summary["reason"], "stale")

    def test_never_hashes_artifact_bytes(self):
        """summarize() must be cheap even for a multi-GB artifact: it only
        reads the small recorded JSON, never the artifact path itself."""
        content = b"weights"
        path = self._write("m.bin", content)
        artifacts.verify_one({"component": "m", "path": path, "expected_sha256": _sha256(content)}, self.state_dir, force=False)

        original_open = open

        def _guarded_open(file, *args, **kwargs):
            if isinstance(file, str) and os.path.abspath(file) == os.path.abspath(path):
                raise AssertionError("summarize() must never open the artifact file itself")
            return original_open(file, *args, **kwargs)

        import builtins

        builtins.open = _guarded_open
        try:
            artifacts.summarize(self.state_dir)
        finally:
            builtins.open = original_open


class TestVerifyBatch(unittest.TestCase):
    def test_empty_declared_list_is_ok(self):
        state_dir = tempfile.mkdtemp()
        result = artifacts.verify([], state_dir, force=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["artifacts"], [])

    def test_non_list_declared_is_treated_as_empty_not_an_error(self):
        state_dir = tempfile.mkdtemp()
        result = artifacts.verify("not-a-list", state_dir, force=False)  # type: ignore[arg-type]
        self.assertTrue(result["ok"])
        self.assertEqual(result["artifacts"], [])


if __name__ == "__main__":
    unittest.main()
