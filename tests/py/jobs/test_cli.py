"""Tests for lib/omes/py/jobs/cli.py (issue #90): schema-gated submission,
including the "no free-form command execution" requirement."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from jobs import cli, store  # noqa: E402


class CliTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-jobs-cli-test-")
        # cli.py calls store.submit()/store.list_jobs() with the default
        # root (None), which resolves via paths.state_root() honoring
        # OMES_STATE_DIR - point it at a fresh temp dir for isolation.
        os.environ["OMES_STATE_DIR"] = str(Path(self._tmp) / "state-dir")

    def tearDown(self):
        del os.environ["OMES_STATE_DIR"]
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_request(self, request: dict) -> str:
        path = Path(self._tmp) / "request.json"
        path.write_text(json.dumps(request))
        return str(path)


class TestSubmitRejectsFreeFormCommand(CliTestBase):
    def test_extra_command_field_is_rejected_by_schema(self):
        request = {
            "tenant_id": "tenant-acme",
            "correlation_id": "corr-1",
            "idempotency_key": "idem-0001",
            "actor": {"type": "user", "id": "op-1"},
            "operation": "backup",
            "target": {"server_id": "srv-1"},
            "command": "rm -rf /",
        }
        path = self._write_request(request)
        args = cli.build_parser().parse_args(["submit", "--file", path, "--json"])
        exit_code = cli.cmd_submit(args)
        self.assertEqual(exit_code, cli.EX_ERROR)
        # No job should have been created for a rejected request.
        self.assertEqual(store.list_jobs(), [])

    def test_operation_not_in_allowlist_is_rejected(self):
        request = {
            "tenant_id": "tenant-acme",
            "correlation_id": "corr-1",
            "idempotency_key": "idem-0002",
            "actor": {"type": "user", "id": "op-1"},
            "operation": "shell_exec",
            "target": {"server_id": "srv-1"},
        }
        path = self._write_request(request)
        args = cli.build_parser().parse_args(["submit", "--file", path, "--json"])
        exit_code = cli.cmd_submit(args)
        self.assertEqual(exit_code, cli.EX_ERROR)


class TestSubmitAcceptsValidRequest(CliTestBase):
    def test_valid_request_is_accepted(self):
        request = {
            "tenant_id": "tenant-acme",
            "correlation_id": "corr-1",
            "idempotency_key": "idem-0003",
            "actor": {"type": "user", "id": "op-1"},
            "operation": "status",
            "target": {"server_id": "srv-1"},
        }
        path = self._write_request(request)
        args = cli.build_parser().parse_args(["submit", "--file", path, "--json"])
        exit_code = cli.cmd_submit(args)
        self.assertEqual(exit_code, cli.EX_OK)
        self.assertEqual(len(store.list_jobs()), 1)
