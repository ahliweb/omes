"""tests/py/jobs/test_worker.py - Unit tests for Control Center pull-worker transport (ADR-0027, issue #192)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from jobs import runner, worker
from jobs import worker_signing as signing


class TestWorkerTransport(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmp_dir.name)
        os.environ["OMES_STATE_DIR"] = str(self.state_dir)
        os.environ["OMES_ROOT"] = str(REPO_ROOT)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()
        os.environ.pop("OMES_STATE_DIR", None)

    def key_kwargs(self) -> dict[str, str]:
        """A throwaway Ed25519 key minted at runtime inside the temp state dir (never committed)."""
        key = self.state_dir / "test_worker.key"
        if not key.exists():
            signing.generate_private_key(key)
        return {"public_key": signing.public_key_b64(key), "private_key_path": str(key)}

    def test_enrollment_success(self) -> None:
        """Scenario: Worker enrolls via valid challenge, persists mode 0600 credentials."""
        mock_handlers = {
            "/api/v1/worker/enroll": {
                "tenant_id": "tenant-acme",
                "server_id": "srv-test-01",
                "worker_id": "wrk-srv-test-01-001",
                "status": "enrolled",
                "control_center_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIControlCenterPub12345",
                "poll_interval_seconds": 10,
                "heartbeat_interval_seconds": 60,
                "enrolled_at": "2026-09-21T12:00:00Z",
            }
        }
        client = worker.HttpClient(mock_handlers=mock_handlers)
        creds = worker.enroll_worker(
            endpoint="https://control-center.example.com",
            tenant_id="tenant-acme",
            server_id="srv-test-01",
            enrollment_challenge="challenge_token_valid12345678",
            state_dir=self.state_dir,
            client=client,
        )

        self.assertEqual(creds.worker_id, "wrk-srv-test-01-001")
        self.assertEqual(creds.server_id, "srv-test-01")

        # Verify saved credentials file
        loaded = worker.load_credentials(self.state_dir)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.worker_id, "wrk-srv-test-01-001")

        cred_file = worker._credentials_path(self.state_dir)
        mode = oct(cred_file.stat().st_mode & 0o777)
        self.assertEqual(mode, "0o600")
        key_file = cred_file.parent / signing.KEY_FILE_NAME
        self.assertEqual(oct(key_file.stat().st_mode & 0o777), "0o600")
        self.assertNotIn("PRIVATE KEY", cred_file.read_text())

    def test_enrollment_rejected(self) -> None:
        """Scenario: Control Center rejects enrollment challenge -> raises EnrollmentError."""
        mock_handlers = {
            "/api/v1/worker/enroll": {
                "tenant_id": "tenant-acme",
                "server_id": "srv-test-01",
                "worker_id": "wrk-srv-test-01-001",
                "status": "token_expired",
                "control_center_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIControlCenterPub12345",
                "poll_interval_seconds": 10,
                "heartbeat_interval_seconds": 60,
                "enrolled_at": "2026-09-21T12:00:00Z",
            }
        }
        client = worker.HttpClient(mock_handlers=mock_handlers)
        with self.assertRaises(worker.EnrollmentError) as ctx:
            worker.enroll_worker(
                endpoint="https://control-center.example.com",
                tenant_id="tenant-acme",
                server_id="srv-test-01",
                enrollment_challenge="challenge_token_expired123456",
                state_dir=self.state_dir,
                client=client,
            )
        self.assertIn("token_expired", str(ctx.exception))

    def test_heartbeat_transmission(self) -> None:
        """Scenario: Worker transmits telemetry heartbeat and receives acknowledgement."""
        creds = worker.WorkerCredentials(
            tenant_id="tenant-acme",
            server_id="srv-test-01",
            worker_id="wrk-001",
            control_center_endpoint="https://control-center.example.com",
            **self.key_kwargs(),
        )

        mock_handlers = {
            "/api/v1/worker/heartbeat": {
                "status": "acknowledged",
                "server_id": "srv-test-01",
                "received_at": "2026-09-21T12:05:00Z",
                "next_heartbeat_seconds": 60,
            }
        }
        client = worker.HttpClient(mock_handlers=mock_handlers)
        resp = worker.send_heartbeat(creds, state_dir=self.state_dir, client=client)
        self.assertEqual(resp["status"], "acknowledged")

    def test_poll_idle(self) -> None:
        """Scenario: Worker polls when no jobs queued -> returns idle."""
        creds = worker.WorkerCredentials(
            tenant_id="tenant-acme",
            server_id="srv-test-01",
            worker_id="wrk-001",
            control_center_endpoint="https://control-center.example.com",
            **self.key_kwargs(),
        )
        mock_handlers = {
            "/api/v1/worker/poll": {
                "status": "idle",
                "server_id": "srv-test-01",
                "tenant_id": "tenant-acme",
                "poll_interval_seconds": 10,
            }
        }
        client = worker.HttpClient(mock_handlers=mock_handlers)
        res = worker.poll_and_dispatch_once(creds, state_dir=self.state_dir, client=client)
        self.assertEqual(res["status"], "idle")
        self.assertIsNone(res["job_id"])

    def test_poll_and_execute_status_job(self) -> None:
        """Scenario: Worker polls, receives valid status job, dispatches via runner, submits result."""
        creds = worker.WorkerCredentials(
            tenant_id="tenant-acme",
            server_id="srv-test-01",
            worker_id="wrk-001",
            control_center_endpoint="https://control-center.example.com",
            **self.key_kwargs(),
        )

        job_payload = {
            "tenant_id": "tenant-acme",
            "correlation_id": "corr-job-001",
            "idempotency_key": "idem-job-status-12345678",
            "actor": {"type": "service", "id": "control-center"},
            "operation": "status",
            "target": {"server_id": "srv-test-01"},
            "permission": {
                "granted": True,
                "policy_id": "pol-allow-all",
                "requires_approval": False,
            },
        }

        submitted_result = {}

        def handle_result(data: dict[str, Any]) -> dict[str, Any]:
            submitted_result.update(data)
            # issue #221: worker-result.request no longer carries job_id;
            # the Control Center's own ack still has one, but it is a
            # server-minted id, never an echo of anything the worker sent.
            return {
                "job_id": "cc-job-0001",
                "status": "recorded",
                "reconciled": True,
                "recorded_at": "2026-09-21T12:06:00Z",
            }

        mock_handlers = {
            "/api/v1/worker/poll": {
                "status": "job_available",
                "server_id": "srv-test-01",
                "tenant_id": "tenant-acme",
                "job": job_payload,
                "poll_interval_seconds": 10,
            },
            "/api/v1/worker/result": handle_result,
        }
        client = worker.HttpClient(mock_handlers=mock_handlers)

        # Set OMES test mode to mock runner execution
        os.environ["OMES_JOBS_TEST_MODE"] = "1"
        os.environ["OMES_JOBS_TEST_ARGV_OVERRIDE"] = json.dumps(["true"])

        try:
            res = worker.poll_and_dispatch_once(creds, state_dir=self.state_dir, client=client)
            self.assertEqual(res["status"], "executed")
            self.assertEqual(res["state"], "succeeded")
            self.assertEqual(submitted_result.get("operation"), "status")
            self.assertEqual(submitted_result.get("tenant_id"), "tenant-acme")
            self.assertEqual(submitted_result.get("server_id"), "srv-test-01")
            self.assertEqual(submitted_result["evidence"]["returncode"], 0)
            self.assertNotIn("job_id", submitted_result)
            self.assertEqual(submitted_result.get("idempotency_key"), "idem-job-status-12345678")
        finally:
            os.environ.pop("OMES_JOBS_TEST_MODE", None)
            os.environ.pop("OMES_JOBS_TEST_ARGV_OVERRIDE", None)

    def test_worker_uses_enrolled_tenant_without_env_var(self) -> None:
        """Issue #279: the worker passes its enrolled tenant to store.submit
        and does not depend on OMES_JOBS_TENANT_ID."""
        saved = os.environ.pop("OMES_JOBS_TENANT_ID", None)
        creds = worker.WorkerCredentials(
            tenant_id="tenant-acme",
            server_id="srv-test-01",
            worker_id="wrk-001",
            control_center_endpoint="https://control-center.example.com",
            **self.key_kwargs(),
        )
        job_payload = {
            "tenant_id": "tenant-acme",
            "correlation_id": "corr-job-279",
            "idempotency_key": "idem-job-status-27927927",
            "actor": {"type": "service", "id": "control-center"},
            "operation": "status",
            "target": {"server_id": "srv-test-01"},
            "permission": {"granted": True, "policy_id": "pol-allow-all", "requires_approval": False},
        }
        mock_handlers = {
            "/api/v1/worker/poll": {
                "status": "job_available",
                "server_id": "srv-test-01",
                "tenant_id": "tenant-acme",
                "job": job_payload,
                "poll_interval_seconds": 10,
            },
            "/api/v1/worker/result": {
                "job_id": "cc-job-0279",
                "status": "recorded",
                "reconciled": True,
                "recorded_at": "2026-09-21T12:06:00Z",
            },
        }
        client = worker.HttpClient(mock_handlers=mock_handlers)
        os.environ["OMES_JOBS_TEST_MODE"] = "1"
        os.environ["OMES_JOBS_TEST_ARGV_OVERRIDE"] = json.dumps(["true"])
        try:
            with mock.patch.object(worker.store, "submit", wraps=worker.store.submit) as submit_spy:
                res = worker.poll_and_dispatch_once(creds, state_dir=self.state_dir, client=client)
            self.assertEqual(res["status"], "executed")
            self.assertEqual(res["state"], "succeeded")
            self.assertEqual(submit_spy.call_args.kwargs.get("enrolled_tenant_id"), "tenant-acme")
        finally:
            os.environ.pop("OMES_JOBS_TEST_MODE", None)
            os.environ.pop("OMES_JOBS_TEST_ARGV_OVERRIDE", None)
            if saved is not None:
                os.environ["OMES_JOBS_TENANT_ID"] = saved

    def test_scope_mismatch_rejection(self) -> None:
        """Scenario: Job targeting another server_id is rejected without local execution."""
        creds = worker.WorkerCredentials(
            tenant_id="tenant-acme",
            server_id="srv-test-01",
            worker_id="wrk-001",
            control_center_endpoint="https://control-center.example.com",
            **self.key_kwargs(),
        )

        job_payload = {
            "tenant_id": "tenant-acme",
            "correlation_id": "corr-job-002",
            "idempotency_key": "idem-job-status-87654321",
            "actor": {"type": "service", "id": "control-center"},
            "operation": "status",
            "target": {"server_id": "srv-DIFFERENT-99"},  # Mismatch!
            "permission": {
                "granted": True,
                "policy_id": "pol-allow-all",
                "requires_approval": False,
            },
        }

        submitted_result = {}

        def handle_result(data: dict[str, Any]) -> dict[str, Any]:
            submitted_result.update(data)
            return {
                "job_id": "cc-job-0002",
                "status": "recorded",
                "reconciled": False,
                "recorded_at": "2026-09-21T12:06:00Z",
            }

        mock_handlers = {
            "/api/v1/worker/poll": {
                "status": "job_available",
                "server_id": "srv-test-01",
                "tenant_id": "tenant-acme",
                "job": job_payload,
                "poll_interval_seconds": 10,
            },
            "/api/v1/worker/result": handle_result,
        }
        client = worker.HttpClient(mock_handlers=mock_handlers)

        res = worker.poll_and_dispatch_once(creds, state_dir=self.state_dir, client=client)
        self.assertEqual(res["status"], "executed")
        self.assertEqual(res["state"], "rejected")
        self.assertEqual(submitted_result.get("state"), "rejected")
        self.assertEqual(submitted_result.get("error", {}).get("code"), "scope_mismatch")
        self.assertNotIn("job_id", submitted_result)


if __name__ == "__main__":
    unittest.main()
