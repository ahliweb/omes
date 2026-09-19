"""Tests for lib/omes/py/jobs/projections.py (issue #95): fixture-based
projection building, staleness, reconciliation, and export redaction."""
import unittest

from . import _pathfix  # noqa: F401
from jobs import projections


def _event(event_id, occurred_at, occurred_at_epoch):
    return {"event_id": event_id, "occurred_at": occurred_at, "occurred_at_epoch": occurred_at_epoch}


class TestProjectionState(unittest.TestCase):
    def test_builds_cursor_from_last_event(self):
        events = [_event("evt-1", "2026-03-01T00:00:00Z", 1000), _event("evt-2", "2026-03-01T00:05:00Z", 1300)]
        state = projections.build_projection_state("mrr-projection", events, as_of="2026-03-01T00:06:00Z", as_of_epoch=1360, max_lag_seconds=300)
        self.assertEqual(state["cursor"]["last_event_id"], "evt-2")
        self.assertEqual(state["freshness"]["lag_seconds"], 60)
        self.assertFalse(state["freshness"]["stale"])

    def test_flags_stale_when_lag_exceeds_threshold(self):
        events = [_event("evt-1", "2026-03-01T00:00:00Z", 1000)]
        state = projections.build_projection_state("mrr-projection", events, as_of="2026-03-01T01:00:00Z", as_of_epoch=4600, max_lag_seconds=300)
        self.assertTrue(state["freshness"]["stale"])
        self.assertEqual(state["freshness"]["lag_seconds"], 3600)

    def test_rejects_empty_event_list(self):
        with self.assertRaises(ValueError):
            projections.build_projection_state("mrr-projection", [], as_of="2026-03-01T00:00:00Z", as_of_epoch=1000, max_lag_seconds=300)

    def test_is_stale_standalone_helper(self):
        self.assertTrue(projections.is_stale(500, 300))
        self.assertFalse(projections.is_stale(100, 300))


def _projection_state():
    events = [_event("evt-1", "2026-03-01T00:00:00Z", 1000)]
    return projections.build_projection_state("test-projection", events, as_of="2026-03-01T00:00:10Z", as_of_epoch=1010, max_lag_seconds=300)


class TestMrrReport(unittest.TestCase):
    def test_sums_active_and_trialing_subscriptions_only(self):
        subs = [
            {"subscription_id": "s1", "tenant_id": "tenant-a", "state": "active"},
            {"subscription_id": "s2", "tenant_id": "tenant-a", "state": "trialing"},
            {"subscription_id": "s3", "tenant_id": "tenant-a", "state": "cancelled"},
        ]
        prices = {"s1": 1900, "s2": 2900, "s3": 999999}
        report = projections.build_mrr_report("report-1", "tenant-a", subs, prices, "USD", _projection_state())
        mrr = next(m for m in report["measurements"] if m["name"] == "mrr_minor")
        self.assertEqual(mrr["value_minor_or_count"], 4800)
        self.assertEqual(mrr["label"], "provider_confirmed")

    def test_tenant_scope_excludes_other_tenants(self):
        subs = [
            {"subscription_id": "s1", "tenant_id": "tenant-a", "state": "active"},
            {"subscription_id": "s2", "tenant_id": "tenant-b", "state": "active"},
        ]
        prices = {"s1": 1000, "s2": 5000}
        report = projections.build_mrr_report("report-1", "tenant-a", subs, prices, "USD", _projection_state())
        mrr = next(m for m in report["measurements"] if m["name"] == "mrr_minor")
        self.assertEqual(mrr["value_minor_or_count"], 1000)

    def test_platform_aggregate_when_tenant_id_is_none(self):
        subs = [
            {"subscription_id": "s1", "tenant_id": "tenant-a", "state": "active"},
            {"subscription_id": "s2", "tenant_id": "tenant-b", "state": "active"},
        ]
        prices = {"s1": 1000, "s2": 5000}
        report = projections.build_mrr_report("report-1", None, subs, prices, "USD", _projection_state())
        mrr = next(m for m in report["measurements"] if m["name"] == "mrr_minor")
        self.assertEqual(mrr["value_minor_or_count"], 6000)


class TestOverdueReport(unittest.TestCase):
    def test_sums_unpaid_portion_of_overdue_invoices(self):
        invoices = [
            {"tenant_id": "tenant-a", "state": "overdue", "totals": {"total_minor": 2000, "paid_minor": 500}},
            {"tenant_id": "tenant-a", "state": "paid", "totals": {"total_minor": 1000, "paid_minor": 1000}},
        ]
        report = projections.build_overdue_report("report-2", "tenant-a", invoices, "USD", _projection_state())
        overdue = next(m for m in report["measurements"] if m["name"] == "overdue_minor")
        self.assertEqual(overdue["value_minor_or_count"], 1500)


class TestDeploymentAndJobReports(unittest.TestCase):
    def test_deployment_counts_by_status(self):
        deployments = [
            {"tenant_id": "tenant-a", "status": "running"},
            {"tenant_id": "tenant-a", "status": "degraded"},
            {"tenant_id": "tenant-a", "status": "running"},
        ]
        report = projections.build_deployment_counts_report("report-3", "tenant-a", deployments, _projection_state())
        running = next(m for m in report["measurements"] if m["name"] == "deployment_running")
        self.assertEqual(running["value"], 2.0)

    def test_failed_jobs_count(self):
        jobs = [
            {"tenant_id": "tenant-a", "state": "failed"},
            {"tenant_id": "tenant-a", "state": "succeeded"},
            {"tenant_id": "tenant-a", "state": "failed"},
        ]
        report = projections.build_failed_jobs_report("report-4", "tenant-a", jobs, _projection_state())
        failed = next(m for m in report["measurements"] if m["name"] == "failed_job_count")
        self.assertEqual(failed["value"], 2.0)


class TestReconciliation(unittest.TestCase):
    def test_matching_value_does_not_raise(self):
        report = projections.build_overdue_report(
            "report-2", "tenant-a",
            [{"tenant_id": "tenant-a", "state": "overdue", "totals": {"total_minor": 2000, "paid_minor": 500}}],
            "USD", _projection_state(),
        )
        projections.reconcile_measurement(report, "overdue_minor", 1500)  # must not raise

    def test_mismatched_value_raises(self):
        report = projections.build_overdue_report(
            "report-2", "tenant-a",
            [{"tenant_id": "tenant-a", "state": "overdue", "totals": {"total_minor": 2000, "paid_minor": 500}}],
            "USD", _projection_state(),
        )
        with self.assertRaises(projections.ReconciliationError):
            projections.reconcile_measurement(report, "overdue_minor", 999)

    def test_unknown_measurement_name_raises(self):
        report = projections.build_overdue_report(
            "report-2", "tenant-a",
            [{"tenant_id": "tenant-a", "state": "overdue", "totals": {"total_minor": 2000, "paid_minor": 500}}],
            "USD", _projection_state(),
        )
        with self.assertRaises(projections.ReconciliationError):
            projections.reconcile_measurement(report, "bogus_measurement", 0)


class TestExportRedaction(unittest.TestCase):
    def test_tenant_scope_is_redacted_when_not_allowed(self):
        report = projections.build_mrr_report(
            "report-1", "tenant-a", [{"subscription_id": "s1", "tenant_id": "tenant-a", "state": "active"}], {"s1": 1000}, "USD", _projection_state()
        )
        redacted = projections.redact_for_export(report, allow_tenant_scope=False)
        self.assertEqual(redacted["scope"]["tenant_id"], "[REDACTED]")
        # Measurements are never redacted - only identifying scope fields.
        self.assertEqual(redacted["measurements"], report["measurements"])

    def test_tenant_scope_is_preserved_when_allowed(self):
        report = projections.build_mrr_report(
            "report-1", "tenant-a", [{"subscription_id": "s1", "tenant_id": "tenant-a", "state": "active"}], {"s1": 1000}, "USD", _projection_state()
        )
        redacted = projections.redact_for_export(report, allow_tenant_scope=True)
        self.assertEqual(redacted["scope"]["tenant_id"], "tenant-a")

    def test_redaction_does_not_mutate_original_report(self):
        report = projections.build_mrr_report(
            "report-1", "tenant-a", [{"subscription_id": "s1", "tenant_id": "tenant-a", "state": "active"}], {"s1": 1000}, "USD", _projection_state()
        )
        projections.redact_for_export(report, allow_tenant_scope=False)
        self.assertEqual(report["scope"]["tenant_id"], "tenant-a")

    def test_platform_aggregate_null_tenant_is_left_null(self):
        report = projections.build_mrr_report(
            "report-1", None, [{"subscription_id": "s1", "tenant_id": "tenant-a", "state": "active"}], {"s1": 1000}, "USD", _projection_state()
        )
        redacted = projections.redact_for_export(report, allow_tenant_scope=False)
        self.assertIsNone(redacted["scope"]["tenant_id"])
