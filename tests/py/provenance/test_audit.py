"""tests/py/provenance/test_audit.py - unit tests for
lib/omes/py/provenance/audit.py (issue #84).
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PROVENANCE_DIR = os.path.join(ROOT, "lib", "omes", "py", "provenance")

spec = importlib.util.spec_from_file_location("omes_provenance_audit", os.path.join(PROVENANCE_DIR, "audit.py"))
audit = importlib.util.module_from_spec(spec)
sys.modules["omes_provenance_audit"] = audit
spec.loader.exec_module(audit)  # type: ignore[union-attr]


def _write(state_dir: str, component: str, data: dict) -> None:
    prov_dir = os.path.join(state_dir, "provenance")
    os.makedirs(prov_dir, exist_ok=True)
    path = os.path.join(prov_dir, f"{component}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


VALID_RECORD = {
    "component": "hermes",
    "profile": "hermes",
    "installer_source_url": "https://hermes-agent.nousresearch.com/install.sh",
    "resolved_version": "hermes 1.2.3",
    "install_time": "2026-09-19T00:00:00Z",
    "checksum": {"algorithm": "sha256", "expected": "abc", "actual": "abc", "status": "verified"},
}


class TestEvaluateRecord(unittest.TestCase):
    def test_clean_record_has_no_findings(self):
        findings = audit.evaluate_record("hermes", VALID_RECORD, None)
        self.assertEqual(findings, [])

    def test_checksum_mismatch_is_fail_closed(self):
        data = dict(VALID_RECORD)
        data["checksum"] = {"algorithm": "sha256", "expected": "aaa", "actual": "bbb", "status": "unknown"}
        findings = audit.evaluate_record("x", data, None)
        kinds = {f["kind"]: f for f in findings}
        self.assertIn("checksum_mismatch", kinds)
        self.assertEqual(kinds["checksum_mismatch"]["severity"], "FAIL")

    def test_unverified_status_is_warn_not_fail(self):
        data = dict(VALID_RECORD)
        data["checksum"] = {"algorithm": "sha256", "expected": None, "actual": None, "status": "unverified"}
        findings = audit.evaluate_record("x", data, None)
        kinds = {f["kind"]: f for f in findings}
        self.assertIn("checksum_unverified", kinds)
        self.assertEqual(kinds["checksum_unverified"]["severity"], "WARN")

    def test_verified_pinned_locally_built_produce_no_checksum_warning(self):
        for status in ("verified", "pinned", "locally-built", "package_manager_verified"):
            data = dict(VALID_RECORD)
            data["checksum"] = {"algorithm": "sha256", "expected": None, "actual": None, "status": status}
            findings = audit.evaluate_record("x", data, None)
            kinds = [f["kind"] for f in findings]
            self.assertNotIn("checksum_unverified", kinds, status)

    def test_mutable_url_variants_warn(self):
        for url in (
            "https://example.com/repo/main/install.sh",
            "https://example.com/repo/master/install.sh",
            "https://example.com/latest/install.sh",
            "https://example.com/tool@latest",
            "https://example.com/repo/HEAD/install.sh",
        ):
            data = dict(VALID_RECORD)
            data["installer_source_url"] = url
            findings = audit.evaluate_record("x", data, None)
            kinds = [f["kind"] for f in findings]
            self.assertIn("mutable_url", kinds, url)

    def test_pinned_release_url_does_not_warn(self):
        data = dict(VALID_RECORD)
        data["installer_source_url"] = "https://example.com/releases/download/v1.2.3/install.sh"
        findings = audit.evaluate_record("x", data, None)
        kinds = [f["kind"] for f in findings]
        self.assertNotIn("mutable_url", kinds)

    def test_missing_required_field_warns(self):
        data = dict(VALID_RECORD)
        del data["resolved_version"]
        findings = audit.evaluate_record("x", data, None)
        kinds = {f["kind"]: f for f in findings}
        self.assertIn("missing_metadata", kinds)
        self.assertEqual(kinds["missing_metadata"]["severity"], "WARN")

    def test_load_error_is_a_fail(self):
        findings = audit.evaluate_record("x", None, "could not parse")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "FAIL")
        self.assertEqual(findings[0]["kind"], "missing_metadata")


class TestReviewManagedExecutables(unittest.TestCase):
    def test_lists_but_never_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills_dir = os.path.join(tmp, "skills", "evil")
            os.makedirs(skills_dir)
            canary = os.path.join(tmp, "canary")
            script_path = os.path.join(skills_dir, "run.sh")
            with open(script_path, "w", encoding="utf-8") as fh:
                fh.write(f"#!/usr/bin/env bash\ntouch {canary}\n")
            os.chmod(script_path, 0o755)

            review = audit.review_managed_executables(tmp)
            self.assertEqual(len(review), 1)
            self.assertEqual(review[0]["path"], script_path)
            self.assertIn(review[0]["mode"], ("0o755",))
            self.assertIsNotNone(review[0]["sha256"])
            self.assertFalse(os.path.exists(canary), "audit must never execute a discovered file")

    def test_non_executable_files_are_not_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills_dir = os.path.join(tmp, "skills", "harmless")
            os.makedirs(skills_dir)
            path = os.path.join(skills_dir, "README.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("hello")
            os.chmod(path, 0o644)

            review = audit.review_managed_executables(tmp)
            self.assertEqual(review, [])

    def test_missing_hermes_home_returns_empty(self):
        self.assertEqual(audit.review_managed_executables(None), [])
        self.assertEqual(audit.review_managed_executables("/no/such/dir"), [])


class TestAuditEndToEnd(unittest.TestCase):
    def test_clean_state_dir_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = audit.audit(tmp, None, None)
            self.assertTrue(result["ok"])
            self.assertEqual(result["findings"], [])

    def test_one_bad_record_makes_ok_false_and_exit_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = dict(VALID_RECORD)
            bad["checksum"] = {"expected": "aaa", "actual": "bbb", "status": "unknown"}
            _write(tmp, "broken", bad)
            result = audit.audit(tmp, None, None)
            self.assertFalse(result["ok"])
            self.assertTrue(any(f["kind"] == "checksum_mismatch" for f in result["findings"]))

    def test_profile_filter_excludes_other_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = dict(VALID_RECORD)
            a["profile"] = "hermes"
            _write(tmp, "a", a)
            b = dict(VALID_RECORD)
            b["profile"] = "other"
            _write(tmp, "b", b)

            result = audit.audit(tmp, "hermes", None)
            names = [c["component"] for c in result["components"]]
            self.assertIn("a", names)
            self.assertNotIn("b", names)

    def test_package_manager_field_is_surfaced_in_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = dict(VALID_RECORD)
            rec["checksum"] = {"algorithm": None, "expected": None, "actual": None, "status": "package_manager_verified"}
            rec["package_manager"] = {"name": "apt", "package": "curl", "version": "8.5.0-2", "origin": "http://archive.ubuntu.com/ubuntu"}
            _write(tmp, "curl", rec)
            result = audit.audit(tmp, None, None)
            self.assertTrue(result["ok"])
            curl = next(c for c in result["components"] if c["component"] == "curl")
            self.assertEqual(curl["package_manager"]["name"], "apt")
            self.assertEqual(curl["checksum_status"], "package_manager_verified")

    def test_main_exit_code_reflects_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = dict(VALID_RECORD)
            bad["checksum"] = {"expected": "aaa", "actual": "bbb", "status": "unknown"}
            _write(tmp, "broken", bad)
            rc = audit.main(["--state-dir", tmp])
            self.assertEqual(rc, audit.EXIT_FINDINGS)

    def test_main_exit_code_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = audit.main(["--state-dir", tmp])
            self.assertEqual(rc, audit.EXIT_OK)

    def test_output_never_contains_environ_dump(self):
        os.environ["OMES_TEST_CANARY_SECRET_AUDIT"] = "must-not-leak"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = audit.audit(tmp, None, None)
                self.assertNotIn("must-not-leak", json.dumps(result))
        finally:
            del os.environ["OMES_TEST_CANARY_SECRET_AUDIT"]


if __name__ == "__main__":
    unittest.main()
