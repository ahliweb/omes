"""tests/py/agent/test_orchestration.py - Unit tests for Hermes orchestration and subagent tree projection (ADR-0028, issue #183)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from agent import orchestration


class TestHermesOrchestration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.state_root = Path(self.tmp_dir.name)
        os.environ["OMES_STATE_DIR"] = str(self.state_root)
        os.environ["OMES_ROOT"] = str(REPO_ROOT)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()
        os.environ.pop("OMES_STATE_DIR", None)

    def test_ingest_and_build_tree_single_agent(self) -> None:
        """Scenario: Single subagent starts, runs a tool, and completes."""
        start_event = {
            "schema_version": "1.0.0",
            "event_type": "subagent_start",
            "tenant_id": "tenant-acme",
            "server_id": "srv-01",
            "session_id": "sess-single-01",
            "turn_id": "turn-1",
            "subagent_id": "sub-1",
            "parent_subagent_id": None,
            "role": "coder",
            "goal": "Write unit tests",
            "state": "RUNNING",
            "step_number": 1,
            "active_tool": "view_file",
            "timestamp": "2026-09-21T12:00:00Z",
            "summary": "Viewing file contents",
            "hermes_version": "v2026.9.14",
        }
        orchestration.ingest_event(start_event, state_root=self.state_root)

        # Build intermediate tree while running
        tree = orchestration.build_tree(
            session_id="sess-single-01",
            tenant_id="tenant-acme",
            server_id="srv-01",
            state_root=self.state_root,
            now=datetime(2026, 9, 21, 12, 1, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(tree["active_count"], 1)
        self.assertEqual(tree["completed_count"], 0)
        self.assertEqual(tree["freshness"], "live")
        self.assertEqual(len(tree["nodes"]), 1)
        self.assertEqual(tree["nodes"][0]["state"], "RUNNING")

        # Stop event
        stop_event = {
            "schema_version": "1.0.0",
            "event_type": "subagent_stop",
            "tenant_id": "tenant-acme",
            "server_id": "srv-01",
            "session_id": "sess-single-01",
            "turn_id": "turn-1",
            "subagent_id": "sub-1",
            "parent_subagent_id": None,
            "role": "coder",
            "goal": "Write unit tests",
            "state": "SUCCEEDED",
            "step_number": 3,
            "active_tool": "view_file",
            "timestamp": "2026-09-21T12:02:00Z",
            "summary": "Completed unit tests successfully",
            "hermes_version": "v2026.9.14",
        }
        orchestration.ingest_event(stop_event, state_root=self.state_root)

        final_tree = orchestration.build_tree(
            session_id="sess-single-01",
            tenant_id="tenant-acme",
            server_id="srv-01",
            state_root=self.state_root,
        )
        self.assertEqual(final_tree["active_count"], 0)
        self.assertEqual(final_tree["completed_count"], 1)
        self.assertEqual(final_tree["failed_count"], 0)
        self.assertEqual(final_tree["nodes"][0]["state"], "SUCCEEDED")
        self.assertEqual(final_tree["nodes"][0]["duration_seconds"], 120.0)

    def test_parallel_batch_delegation(self) -> None:
        """Scenario: 1 lead planner delegates 10 concurrent child subagents (Hermes default concurrency)."""
        session_id = "sess-parallel-batch"
        # Ingest lead planner
        orchestration.ingest_event({
            "schema_version": "1.0.0",
            "event_type": "subagent_start",
            "tenant_id": "tenant-acme",
            "server_id": "srv-01",
            "session_id": session_id,
            "subagent_id": "sub-lead",
            "parent_subagent_id": None,
            "role": "lead_planner",
            "goal": "Dispatch batch of 10 tasks",
            "state": "RUNNING",
            "timestamp": "2026-09-21T12:00:00Z",
            "hermes_version": "v2026.9.14",
        }, state_root=self.state_root)

        # Ingest 10 parallel children
        for i in range(1, 11):
            orchestration.ingest_event({
                "schema_version": "1.0.0",
                "event_type": "subagent_start",
                "tenant_id": "tenant-acme",
                "server_id": "srv-01",
                "session_id": session_id,
                "subagent_id": f"sub-child-{i:02d}",
                "parent_subagent_id": "sub-lead",
                "role": f"worker_{i}",
                "goal": f"Execute chunk {i}",
                "state": "RUNNING",
                "timestamp": "2026-09-21T12:00:10Z",
                "hermes_version": "v2026.9.14",
            }, state_root=self.state_root)

        tree = orchestration.build_tree(
            session_id=session_id,
            tenant_id="tenant-acme",
            server_id="srv-01",
            state_root=self.state_root,
        )
        self.assertEqual(tree["root_subagent_id"], "sub-lead")
        self.assertEqual(len(tree["nodes"]), 11)
        self.assertEqual(tree["active_count"], 11)

        lead_node = next(n for n in tree["nodes"] if n["subagent_id"] == "sub-lead")
        self.assertEqual(len(lead_node["children"]), 10)

    def test_nested_delegation_hierarchy(self) -> None:
        """Scenario: Nested delegation (Lead -> Orchestrator Child -> 2 Grandchildren)."""
        session_id = "sess-nested"
        orchestration.ingest_event({
            "schema_version": "1.0.0",
            "event_type": "subagent_start",
            "tenant_id": "tenant-acme",
            "server_id": "srv-01",
            "session_id": session_id,
            "subagent_id": "sub-root",
            "parent_subagent_id": None,
            "role": "root_planner",
            "state": "RUNNING",
            "timestamp": "2026-09-21T12:00:00Z",
            "hermes_version": "v2026.9.14",
        }, state_root=self.state_root)

        orchestration.ingest_event({
            "schema_version": "1.0.0",
            "event_type": "subagent_start",
            "tenant_id": "tenant-acme",
            "server_id": "srv-01",
            "session_id": session_id,
            "subagent_id": "sub-mid",
            "parent_subagent_id": "sub-root",
            "role": "middle_orchestrator",
            "state": "RUNNING",
            "timestamp": "2026-09-21T12:00:05Z",
            "hermes_version": "v2026.9.14",
        }, state_root=self.state_root)

        orchestration.ingest_event({
            "schema_version": "1.0.0",
            "event_type": "subagent_start",
            "tenant_id": "tenant-acme",
            "server_id": "srv-01",
            "session_id": session_id,
            "subagent_id": "sub-leaf-1",
            "parent_subagent_id": "sub-mid",
            "role": "leaf_worker",
            "state": "RUNNING",
            "timestamp": "2026-09-21T12:00:10Z",
            "hermes_version": "v2026.9.14",
        }, state_root=self.state_root)

        orchestration.ingest_event({
            "schema_version": "1.0.0",
            "event_type": "subagent_start",
            "tenant_id": "tenant-acme",
            "server_id": "srv-01",
            "session_id": session_id,
            "subagent_id": "sub-leaf-2",
            "parent_subagent_id": "sub-mid",
            "role": "leaf_worker",
            "state": "SUCCEEDED",
            "timestamp": "2026-09-21T12:00:20Z",
            "hermes_version": "v2026.9.14",
        }, state_root=self.state_root)

        tree = orchestration.build_tree(
            session_id=session_id,
            tenant_id="tenant-acme",
            server_id="srv-01",
            state_root=self.state_root,
        )
        self.assertEqual(len(tree["nodes"]), 4)
        root = next(n for n in tree["nodes"] if n["subagent_id"] == "sub-root")
        mid = next(n for n in tree["nodes"] if n["subagent_id"] == "sub-mid")
        self.assertEqual(root["children"], ["sub-mid"])
        self.assertEqual(sorted(mid["children"]), ["sub-leaf-1", "sub-leaf-2"])

    def test_out_of_order_event_delivery(self) -> None:
        """Scenario: Stop event arrives before start event -> state does not regress to running."""
        session_id = "sess-ooo"
        # Stop event arrives first
        orchestration.ingest_event({
            "schema_version": "1.0.0",
            "event_type": "subagent_stop",
            "tenant_id": "tenant-acme",
            "server_id": "srv-01",
            "session_id": session_id,
            "subagent_id": "sub-early-stop",
            "parent_subagent_id": None,
            "state": "SUCCEEDED",
            "timestamp": "2026-09-21T12:01:00Z",
            "hermes_version": "v2026.9.14",
        }, state_root=self.state_root)

        # Late-arriving start event
        orchestration.ingest_event({
            "schema_version": "1.0.0",
            "event_type": "subagent_start",
            "tenant_id": "tenant-acme",
            "server_id": "srv-01",
            "session_id": session_id,
            "subagent_id": "sub-early-stop",
            "parent_subagent_id": None,
            "role": "fast_worker",
            "goal": "Fast execution",
            "state": "RUNNING",
            "timestamp": "2026-09-21T12:00:00Z",
            "hermes_version": "v2026.9.14",
        }, state_root=self.state_root)

        tree = orchestration.build_tree(
            session_id=session_id,
            tenant_id="tenant-acme",
            server_id="srv-01",
            state_root=self.state_root,
        )
        node = tree["nodes"][0]
        self.assertEqual(node["state"], "SUCCEEDED")
        self.assertEqual(node["role"], "fast_worker")
        self.assertEqual(node["goal"], "Fast execution")
        self.assertEqual(node["duration_seconds"], 60.0)

    def test_secret_redaction_and_xss_sanitization(self) -> None:
        """Scenario: Goal contains potential XSS and secret tokens -> sanitized and redacted."""
        # Unit test sanitize_text helper
        raw_text = "<script>alert('xss')</script> token ghp_1234567890123456789012345678901234 and SSH ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI12345678901234567890123456789012"
        sanitized = orchestration.sanitize_text(raw_text)
        self.assertNotIn("<script>", sanitized)
        self.assertIn("&lt;script&gt;", sanitized)
        self.assertNotIn("ghp_1234567890", sanitized)
        self.assertIn("[REDACTED]", sanitized)

        # Raw secret token in ingest_event is rejected by contract validation
        session_id = "sess-sec"
        with self.assertRaises(orchestration.OrchestrationError):
            orchestration.ingest_event({
                "schema_version": "1.0.0",
                "event_type": "subagent_start",
                "tenant_id": "tenant-acme",
                "server_id": "srv-01",
                "session_id": session_id,
                "subagent_id": "sub-sec",
                "parent_subagent_id": None,
                "role": "tester",
                "goal": "Unsafe goal with token ghp_1234567890123456789012345678901234",
                "state": "RUNNING",
                "timestamp": "2026-09-21T12:00:00Z",
                "hermes_version": "v2026.9.14",
            }, state_root=self.state_root)

        # Potential XSS without secret token is safely ingested and HTML-escaped
        orchestration.ingest_event({
            "schema_version": "1.0.0",
            "event_type": "subagent_start",
            "tenant_id": "tenant-acme",
            "server_id": "srv-01",
            "session_id": session_id,
            "subagent_id": "sub-xss",
            "parent_subagent_id": None,
            "role": "tester",
            "goal": "Refactor <script>alert('xss')</script> component",
            "state": "RUNNING",
            "timestamp": "2026-09-21T12:00:00Z",
            "hermes_version": "v2026.9.14",
        }, state_root=self.state_root)

        tree = orchestration.build_tree(
            session_id=session_id,
            tenant_id="tenant-acme",
            server_id="srv-01",
            state_root=self.state_root,
        )
        node = next(n for n in tree["nodes"] if n["subagent_id"] == "sub-xss")
        self.assertNotIn("<script>", node["goal"])
        self.assertIn("&lt;script&gt;", node["goal"])

    def test_stale_detection(self) -> None:
        """Scenario: Active subagent past stale threshold transitions tree freshness to 'stale'."""
        session_id = "sess-stale"
        orchestration.ingest_event({
            "schema_version": "1.0.0",
            "event_type": "subagent_start",
            "tenant_id": "tenant-acme",
            "server_id": "srv-01",
            "session_id": session_id,
            "subagent_id": "sub-stale-01",
            "parent_subagent_id": None,
            "state": "RUNNING",
            "timestamp": "2026-09-21T12:00:00Z",
            "hermes_version": "v2026.9.14",
        }, state_root=self.state_root)

        # 10 minutes later (threshold is 5 mins = 300s)
        future_time = datetime(2026, 9, 21, 12, 10, 0, tzinfo=timezone.utc)
        tree = orchestration.build_tree(
            session_id=session_id,
            tenant_id="tenant-acme",
            server_id="srv-01",
            state_root=self.state_root,
            now=future_time,
            stale_threshold_seconds=300,
        )
        self.assertEqual(tree["freshness"], "stale")

    def test_tenant_isolation_boundary(self) -> None:
        """Scenario: Querying or ingesting across mismatched tenants raises TenantScopeError."""
        session_id = "sess-tenant"
        orchestration.ingest_event({
            "schema_version": "1.0.0",
            "event_type": "subagent_start",
            "tenant_id": "tenant-acme",
            "server_id": "srv-01",
            "session_id": session_id,
            "subagent_id": "sub-t1",
            "parent_subagent_id": None,
            "state": "RUNNING",
            "timestamp": "2026-09-21T12:00:00Z",
            "hermes_version": "v2026.9.14",
        }, state_root=self.state_root)

        # Querying with different tenant
        with self.assertRaises(orchestration.TenantScopeError):
            orchestration.build_tree(
                session_id=session_id,
                tenant_id="tenant-OTHER",
                server_id="srv-01",
                state_root=self.state_root,
            )

        # Ingesting with different tenant into existing session
        with self.assertRaises(orchestration.TenantScopeError):
            orchestration.ingest_event({
                "schema_version": "1.0.0",
                "event_type": "subagent_step",
                "tenant_id": "tenant-OTHER",
                "server_id": "srv-01",
                "session_id": session_id,
                "subagent_id": "sub-t1",
                "state": "RUNNING",
                "timestamp": "2026-09-21T12:01:00Z",
                "hermes_version": "v2026.9.14",
            }, state_root=self.state_root)

    def test_missing_session_error(self) -> None:
        """Scenario: Requesting nonexistent session raises SessionNotFoundError."""
        with self.assertRaises(orchestration.SessionNotFoundError):
            orchestration.build_tree(
                session_id="nonexistent-sess",
                tenant_id="tenant-acme",
                server_id="srv-01",
                state_root=self.state_root,
            )

    def test_list_orchestration_sessions(self) -> None:
        """Scenario: list_orchestration_sessions returns sessions for tenant."""
        for i in range(1, 4):
            orchestration.ingest_event({
                "schema_version": "1.0.0",
                "event_type": "subagent_start",
                "tenant_id": "tenant-acme",
                "server_id": "srv-01",
                "session_id": f"sess-list-{i}",
                "subagent_id": f"sub-{i}",
                "state": "RUNNING",
                "timestamp": f"2026-09-21T12:0{i}:00Z",
                "hermes_version": "v2026.9.14",
            }, state_root=self.state_root)

        sessions = orchestration.list_orchestration_sessions("tenant-acme", state_root=self.state_root)
        self.assertEqual(len(sessions), 3)

        other_sessions = orchestration.list_orchestration_sessions("tenant-other", state_root=self.state_root)
        self.assertEqual(len(other_sessions), 0)


if __name__ == "__main__":
    unittest.main()
