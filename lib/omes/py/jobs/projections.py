"""lib/omes/py/jobs/projections.py - a pure, stdlib-only, fixture-based
read-model projection builder (issue #95).

A report projection is NEVER a second source of truth (AGENTS.md, issue
#95 objective: "without making reporting tables a second source of
truth"). Every function here is a pure transform from an explicit list of
source events/records to a report + projection-state shape; nothing in
this module reads a database, calls a network, or caches anything beyond
what its caller passes in and gets back.
"""
from __future__ import annotations

from typing import Any


class ReconciliationError(Exception):
    """Raised when a report's totals do not match the authoritative source."""


def build_projection_state(
    projection_name: str,
    events: list[dict[str, Any]],
    as_of: str,
    as_of_epoch: int,
    max_lag_seconds: int,
    rebuild_status: str = "not_rebuilding",
    last_reconciled_at: str | None = None,
    discrepancies_found: int = 0,
) -> dict[str, Any]:
    """Builds the `projection-state` block from a list of source events
    (each expected to carry `event_id` and an epoch-seconds
    `occurred_at_epoch` plus an ISO `occurred_at`). `events` must already
    be in the order they were applied; the LAST one in the list becomes
    the cursor."""
    if not events:
        raise ValueError("cannot build a projection state from zero events")
    last_event = events[-1]
    lag_seconds = max(0, as_of_epoch - last_event["occurred_at_epoch"])
    return {
        "projection_name": projection_name,
        "cursor": {
            "last_event_id": last_event["event_id"],
            "last_event_at": last_event["occurred_at"],
        },
        "freshness": {
            "as_of": as_of,
            "lag_seconds": lag_seconds,
            "stale": lag_seconds > max_lag_seconds,
        },
        "rebuild": {"status": rebuild_status, "started_at": None, "completed_at": None},
        "reconciliation": {
            "last_reconciled_at": last_reconciled_at,
            "discrepancies_found": discrepancies_found,
        },
    }


def is_stale(lag_seconds: int, max_lag_seconds: int) -> bool:
    """The same staleness rule `build_projection_state()` applies
    inline, exposed standalone so a consumer can re-check staleness
    against a DIFFERENT (e.g. tighter, role-specific) threshold without
    rebuilding the whole projection state."""
    return lag_seconds > max_lag_seconds


def build_mrr_report(
    report_id: str,
    tenant_id: str | None,
    subscriptions: list[dict[str, Any]],
    prices_minor_by_subscription: dict[str, int],
    currency: str,
    projection_state: dict[str, Any],
) -> dict[str, Any]:
    """Builds a `report-billing` (report_type=mrr) record: sums the
    monthly-normalized price of every subscription currently in
    `active`/`trialing` state. All amounts here are `provider_confirmed`
    because they are read directly from `subscription`/price records
    this repository considers authoritative (issue #92); a future
    projection that estimates MRR from partial data would label those
    entries `estimate` instead."""
    active_states = {"active", "trialing"}
    mrr_minor = 0
    counted = 0
    for sub in subscriptions:
        if sub.get("tenant_id") != tenant_id and tenant_id is not None:
            continue
        if sub["state"] not in active_states:
            continue
        mrr_minor += prices_minor_by_subscription.get(sub["subscription_id"], 0)
        counted += 1

    return {
        "report_id": report_id,
        "report_type": "mrr",
        "scope": {"tenant_id": tenant_id},
        "projection": projection_state,
        "measurements": [
            {"name": "mrr_minor", "value_minor_or_count": mrr_minor, "unit": "currency_minor_unit", "currency": currency, "label": "provider_confirmed"},
            {"name": "active_subscription_count", "value_minor_or_count": counted, "unit": "count", "label": "provider_confirmed"},
        ],
    }


def build_overdue_report(
    report_id: str,
    tenant_id: str | None,
    invoices: list[dict[str, Any]],
    currency: str,
    projection_state: dict[str, Any],
) -> dict[str, Any]:
    """Builds a `report-billing` (report_type=overdue) record: sums
    `total_minor - paid_minor` for every invoice in the `overdue` state
    that matches `tenant_id` (or every tenant when `tenant_id` is None -
    a platform-level aggregate, gated by AWCMS's own role check, not by
    this function)."""
    overdue_minor = 0
    overdue_count = 0
    for invoice in invoices:
        if tenant_id is not None and invoice.get("tenant_id") != tenant_id:
            continue
        if invoice["state"] != "overdue":
            continue
        totals = invoice["totals"]
        overdue_minor += totals["total_minor"] - totals["paid_minor"]
        overdue_count += 1

    return {
        "report_id": report_id,
        "report_type": "overdue",
        "scope": {"tenant_id": tenant_id},
        "projection": projection_state,
        "measurements": [
            {"name": "overdue_minor", "value_minor_or_count": overdue_minor, "unit": "currency_minor_unit", "currency": currency, "label": "provider_confirmed"},
            {"name": "overdue_invoice_count", "value_minor_or_count": overdue_count, "unit": "count", "label": "provider_confirmed"},
        ],
    }


def build_deployment_counts_report(
    report_id: str,
    tenant_id: str | None,
    deployments: list[dict[str, Any]],
    projection_state: dict[str, Any],
) -> dict[str, Any]:
    """Builds a `report-operations` (report_type=deployment_counts)
    record from a list of `{"tenant_id", "status"}` deployment records."""
    scoped = [d for d in deployments if tenant_id is None or d.get("tenant_id") == tenant_id]
    running = sum(1 for d in scoped if d["status"] == "running")
    degraded = sum(1 for d in scoped if d["status"] == "degraded")
    return {
        "report_id": report_id,
        "report_type": "deployment_counts",
        "scope": {"tenant_id": tenant_id},
        "projection": projection_state,
        "measurements": [
            {"name": "deployment_total", "value": float(len(scoped)), "unit": "count", "label": "provider_confirmed"},
            {"name": "deployment_running", "value": float(running), "unit": "count", "label": "provider_confirmed"},
            {"name": "deployment_degraded", "value": float(degraded), "unit": "count", "label": "provider_confirmed"},
        ],
    }


def build_failed_jobs_report(
    report_id: str,
    tenant_id: str | None,
    jobs: list[dict[str, Any]],
    projection_state: dict[str, Any],
) -> dict[str, Any]:
    """Builds a `report-operations` (report_type=failed_jobs) record from
    a list of `{"tenant_id", "state"}` job records (issue #90's job
    store shape)."""
    scoped = [j for j in jobs if tenant_id is None or j.get("tenant_id") == tenant_id]
    failed = sum(1 for j in scoped if j["state"] == "failed")
    return {
        "report_id": report_id,
        "report_type": "failed_jobs",
        "scope": {"tenant_id": tenant_id},
        "projection": projection_state,
        "measurements": [
            {"name": "failed_job_count", "value": float(failed), "unit": "count", "label": "provider_confirmed"},
            {"name": "total_job_count", "value": float(len(scoped)), "unit": "count", "label": "provider_confirmed"},
        ],
    }


def reconcile_measurement(report: dict[str, Any], measurement_name: str, authoritative_value: int | float) -> None:
    """Raises `ReconciliationError` if `report`'s named measurement does
    not match `authoritative_value` exactly (issue #95: 'reconciliation
    tests'). The authoritative value must come from re-deriving the
    number directly from source records (as `build_*_report()` does),
    never from another projection - reconciling one projection against
    another projection proves nothing."""
    value_key = "value_minor_or_count" if "value_minor_or_count" in report["measurements"][0] else "value"
    for measurement in report["measurements"]:
        if measurement["name"] == measurement_name:
            if measurement[value_key] != authoritative_value:
                raise ReconciliationError(
                    f"{measurement_name}: report says {measurement[value_key]!r}, "
                    f"authoritative source says {authoritative_value!r}"
                )
            return
    raise ReconciliationError(f"measurement {measurement_name!r} not found in report")


_REDACTED_FIELD_NAMES = frozenset({"tenant_id", "legal_entity_id"})


def redact_for_export(report: dict[str, Any], allow_tenant_scope: bool) -> dict[str, Any]:
    """Returns a deep-copied, export-safe version of `report`. When
    `allow_tenant_scope` is False (the exporting role is not authorized
    to see which tenant a report covers - e.g. an aggregate-only
    dashboard role), `scope.tenant_id`/`scope.legal_entity_id` are
    replaced with `"[REDACTED]"` rather than removed, so the exported
    shape still matches the schema (both fields stay required-shaped
    strings-or-null) while the identifying value is gone. This never
    redacts `measurements` - a redacted report still functions as a
    report, it just cannot be traced back to one tenant, matching issue
    #95's export authorization / redaction requirement."""
    import copy

    redacted = copy.deepcopy(report)
    if not allow_tenant_scope and "scope" in redacted:
        for field in _REDACTED_FIELD_NAMES:
            if field in redacted["scope"] and redacted["scope"][field] is not None:
                redacted["scope"][field] = "[REDACTED]"
    return redacted
