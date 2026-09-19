"""tests/py/provenance/test_record.py - unit tests for
lib/omes/py/provenance/record.py (issue #84).
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PROVENANCE_DIR = os.path.join(ROOT, "lib", "omes", "py", "provenance")

spec = importlib.util.spec_from_file_location("omes_provenance_record", os.path.join(PROVENANCE_DIR, "record.py"))
record = importlib.util.module_from_spec(spec)
sys.modules["omes_provenance_record"] = record
spec.loader.exec_module(record)  # type: ignore[union-attr]


class TestBuildRecord(unittest.TestCase):
    def test_defaults_for_empty_payload(self):
        rec = record.build_record("hermes", "hermes", {})
        self.assertEqual(rec["component"], "hermes")
        self.assertEqual(rec["profile"], "hermes")
        self.assertIsNone(rec["installer_source_url"])
        self.assertEqual(rec["checksum"]["status"], "unknown")

    def test_invalid_checksum_status_falls_back_to_unknown(self):
        rec = record.build_record("hermes", "", {"checksum": {"status": "not-a-real-status"}})
        self.assertEqual(rec["checksum"]["status"], "unknown")

    def test_valid_checksum_status_preserved(self):
        for status in record.VALID_CHECKSUM_STATUSES:
            rec = record.build_record("hermes", "", {"checksum": {"status": status}})
            self.assertEqual(rec["checksum"]["status"], status)

    def test_never_includes_a_credential_looking_field(self):
        payload = {"installer_source_url": "https://x", "token": "should-not-be-copied", "api_key": "nope"}
        rec = record.build_record("hermes", "", payload)
        self.assertNotIn("token", rec)
        self.assertNotIn("api_key", rec)


class TestWriteRecord(unittest.TestCase):
    def test_write_record_mode_and_dir_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = record.build_record("hermes", "hermes", {"installer_source_url": "https://x"})
            path = record.write_record(tmp, "hermes", rec)
            self.assertTrue(os.path.exists(path))
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(mode, 0o600)
            dir_mode = stat.S_IMODE(os.stat(os.path.join(tmp, "provenance")).st_mode)
            self.assertEqual(dir_mode, 0o700)

            with open(path) as fh:
                on_disk = json.load(fh)
            self.assertEqual(on_disk["component"], "hermes")

    def test_write_record_overwrites_previous_record_for_same_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec1 = record.build_record("hermes", "", {"resolved_version": "1.0"})
            record.write_record(tmp, "hermes", rec1)
            rec2 = record.build_record("hermes", "", {"resolved_version": "2.0"})
            path = record.write_record(tmp, "hermes", rec2)
            with open(path) as fh:
                on_disk = json.load(fh)
            self.assertEqual(on_disk["resolved_version"], "2.0")


class TestMain(unittest.TestCase):
    def test_main_writes_and_prints_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            stdin = io.StringIO(json.dumps({"installer_source_url": "https://x", "resolved_version": "1.0"}))
            buf = io.StringIO()
            import unittest.mock as mock

            with mock.patch.object(sys, "stdin", stdin), redirect_stdout(buf):
                rc = record.main(["--state-dir", tmp, "--component", "hermes", "--profile", "hermes"])
            self.assertEqual(rc, record.EXIT_OK)
            out = json.loads(buf.getvalue())
            self.assertTrue(out["ok"])
            self.assertTrue(os.path.exists(out["path"]))

    def test_main_with_invalid_json_exits_nonzero(self):
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as tmp:
            stdin = io.StringIO("not json")
            buf = io.StringIO()
            with mock.patch.object(sys, "stdin", stdin), redirect_stdout(buf):
                rc = record.main(["--state-dir", tmp, "--component", "hermes"])
            self.assertEqual(rc, record.EXIT_ERROR)

    def test_main_with_non_object_json_exits_nonzero(self):
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as tmp:
            stdin = io.StringIO("[1, 2, 3]")
            buf = io.StringIO()
            with mock.patch.object(sys, "stdin", stdin), redirect_stdout(buf):
                rc = record.main(["--state-dir", tmp, "--component", "hermes"])
            self.assertEqual(rc, record.EXIT_ERROR)


if __name__ == "__main__":
    unittest.main()
