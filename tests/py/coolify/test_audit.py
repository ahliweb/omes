"""Tests for lib/omes/py/coolify/audit.py (issue #97): hash-chain
integrity and secret redaction, mirroring lib/omes/py/jobs/test_audit.py's
coverage of the same shape for the jobs package."""
import shutil
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from coolify import audit  # noqa: E402


class TestRedaction(unittest.TestCase):
    def test_redacts_secret_like_key(self):
        redacted = audit.redact_structure({"api_key": "some-value", "note": "fine"})
        self.assertEqual(redacted["api_key"], "[REDACTED]")
        self.assertEqual(redacted["note"], "fine")

    def test_redacts_bearer_token_in_text(self):
        text = "request failed: Authorization: Bearer abc123def456"
        redacted = audit.redact_text(text)
        self.assertNotIn("abc123def456", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_capped_redacted_tail_truncates_long_output(self):
        long_text = "x" * (audit.MAX_OUTPUT_TAIL_CHARS * 2)
        result = audit.capped_redacted_tail(long_text)
        self.assertLess(len(result), len(long_text))
        self.assertIn("[truncated]", result)


class TestAuditChain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-coolify-audit-test-")
        self.root = Path(self._tmp)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_verify_chain_passes_on_untouched_log(self):
        audit.append(self.root, actor="test", job_id="corr-1", event="coolify.apply")
        audit.append(self.root, actor="test", job_id="corr-1", event="coolify.status")
        audit.verify_chain(self.root)  # must not raise

    def test_verify_chain_detects_tampering(self):
        audit.append(self.root, actor="test", job_id="corr-1", event="coolify.apply")
        log_path = self.root / "coolify" / "audit.jsonl"
        tampered = log_path.read_text(encoding="utf-8").replace("coolify.apply", "coolify.rollback")
        log_path.write_text(tampered, encoding="utf-8")
        with self.assertRaises(audit.AuditTamperedError):
            audit.verify_chain(self.root)

    def test_verify_chain_on_missing_log_is_a_no_op(self):
        audit.verify_chain(self.root)  # must not raise even though nothing was appended


if __name__ == "__main__":
    unittest.main()
