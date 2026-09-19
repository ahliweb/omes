"""Tests for lib/omes/py/jobs/store.py (issue #90): idempotent submission,
cross-tenant rejection, approval policy, state machine, TTL expiry."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401

from jobs import schema, store  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT_SCHEMA_PATH = REPO_ROOT / "contracts" / "control-center" / "v1" / "deployment.request.schema.json"


def make_request(**overrides) -> dict:
    request = {
        "tenant_id": "tenant-acme",
        "correlation_id": "corr-1",
        "idempotency_key": "idem-0001",
        "actor": {"type": "user", "id": "op-1"},
        "operation": "status",
        "target": {"server_id": "srv-1"},
    }
    request.update(overrides)
    return request


class JobsStoreTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="omes-jobs-test-")
        self.root = Path(self._tmp) / "state"
        store.paths.ensure_layout(self.root)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestOperationAllowlistParity(unittest.TestCase):
    def test_store_operations_match_contract_enum(self):
        schema_doc = schema.load_json(DEPLOYMENT_SCHEMA_PATH)
        contract_ops = set(schema_doc["properties"]["operation"]["enum"])
        self.assertEqual(set(store.OPERATIONS), contract_ops)


class TestSubmitIdempotency(JobsStoreTestBase):
    def test_duplicate_idempotency_key_returns_original_job_and_does_not_create_a_second(self):
        request = make_request()
        record1, replayed1 = store.submit(request, root=self.root)
        record2, replayed2 = store.submit(request, root=self.root)
        self.assertFalse(replayed1)
        self.assertTrue(replayed2)
        self.assertEqual(record1["job_id"], record2["job_id"])
        self.assertEqual(len(store.list_jobs(self.root)), 1)

    def test_replay_is_audited(self):
        request = make_request()
        store.submit(request, root=self.root)
        store.submit(request, root=self.root)
        audit_path = store.paths.audit_log_path(self.root)
        lines = audit_path.read_text().splitlines()
        events = [json.loads(l)["event"] for l in lines]
        self.assertIn("replayed", events)


class TestCrossTenantRejection(JobsStoreTestBase):
    def test_mismatched_tenant_is_rejected(self):
        import os

        os.environ["OMES_JOBS_TENANT_ID"] = "tenant-other"
        try:
            request = make_request(tenant_id="tenant-acme")
            with self.assertRaises(store.CrossTenantError):
                store.submit(request, root=self.root)
        finally:
            del os.environ["OMES_JOBS_TENANT_ID"]

    def test_matching_tenant_is_accepted(self):
        import os

        os.environ["OMES_JOBS_TENANT_ID"] = "tenant-acme"
        try:
            request = make_request(tenant_id="tenant-acme")
            record, replayed = store.submit(request, root=self.root)
            self.assertFalse(replayed)
        finally:
            del os.environ["OMES_JOBS_TENANT_ID"]

    def test_mismatched_server_id_is_rejected(self):
        import os

        os.environ["OMES_JOBS_SERVER_ID"] = "srv-local"
        try:
            request = make_request(target={"server_id": "srv-remote"})
            with self.assertRaises(store.CrossTenantError):
                store.submit(request, root=self.root)
        finally:
            del os.environ["OMES_JOBS_SERVER_ID"]


class TestApprovalPolicy(JobsStoreTestBase):
    def test_status_is_auto_approvable_by_default(self):
        self.assertTrue(store.can_auto_approve("status"))

    def test_restore_is_never_auto_approvable(self):
        self.assertFalse(store.can_auto_approve("restore"))
        self.assertTrue(store.is_destructive("restore"))

    def test_approve_requires_backup_reference_for_restore(self):
        request = make_request(operation="restore", target={"server_id": "srv-1"})
        record, _ = store.submit(request, root=self.root)
        with self.assertRaises(store.ApprovalRequiredError):
            store.approve(record, actor="op-1", root=self.root)

    def test_approve_succeeds_with_backup_id(self):
        request = make_request(operation="restore", target={"server_id": "srv-1"}, backup_id="backup-1")
        record, _ = store.submit(request, root=self.root)
        store.approve(record, actor="op-1", root=self.root)
        self.assertEqual(record["state"], "approved")

    def test_approve_is_audited_with_actor(self):
        request = make_request(operation="restore", target={"server_id": "srv-1"}, backup_id="backup-1")
        record, _ = store.submit(request, root=self.root)
        store.approve(record, actor="approver-9", root=self.root)
        lines = store.paths.audit_log_path(self.root).read_text().splitlines()
        approved_entries = [json.loads(l) for l in lines if json.loads(l)["event"] == "approved"]
        self.assertEqual(approved_entries[0]["actor"], "approver-9")


class TestStateMachine(JobsStoreTestBase):
    def test_every_valid_transition_is_accepted(self):
        record = store.new_job_record(make_request())
        store.apply_transition(record, "approved", "system")
        store.apply_transition(record, "running", "system")
        store.apply_transition(record, "succeeded", "system")
        self.assertEqual(record["state"], "succeeded")

    def test_terminal_states_reject_further_transitions(self):
        record = store.new_job_record(make_request())
        store.apply_transition(record, "cancelled", "system")
        with self.assertRaises(store.InvalidTransitionError):
            store.apply_transition(record, "approved", "system")

    def test_queued_cannot_jump_straight_to_running(self):
        record = store.new_job_record(make_request())
        with self.assertRaises(store.InvalidTransitionError):
            store.apply_transition(record, "running", "system")


class TestCancelAndExpire(JobsStoreTestBase):
    def test_cancel_from_queued(self):
        request = make_request()
        record, _ = store.submit(request, root=self.root)
        store.cancel(record, actor="op-1", root=self.root)
        self.assertEqual(record["state"], "cancelled")

    def test_cancel_from_running_is_rejected(self):
        record = store.new_job_record(make_request())
        store.apply_transition(record, "approved", "system")
        store.apply_transition(record, "running", "system")
        with self.assertRaises(store.InvalidTransitionError):
            store.cancel(record, actor="op-1", root=self.root)

    def test_expire_stale_jobs(self):
        request = make_request()
        record, _ = store.submit(request, root=self.root)
        record["created_at"] = "2000-01-01T00:00:00Z"
        store.save_job(record, self.root)
        expired = store.expire_stale_jobs(root=self.root, ttl=1)
        self.assertIn(record["job_id"], expired)
        reloaded = store.load_job(record["job_id"], self.root)
        self.assertEqual(reloaded["state"], "expired")

    def test_expire_leaves_recent_jobs_alone(self):
        request = make_request()
        record, _ = store.submit(request, root=self.root)
        expired = store.expire_stale_jobs(root=self.root, ttl=3600)
        self.assertNotIn(record["job_id"], expired)
