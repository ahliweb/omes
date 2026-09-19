"""lib/omes/py/domains/billing.py - domain billing/reconciliation rules
(issue #102).

This module implements the OMES-side LEDGER rules ADR-0011 §4
describes: OMES stores immutable price/checkout snapshots and links them
to provider operations; registrar, invoice, entitlement, and DNS state
remain separate and are reconciled, never conflated. It never calls a
payment provider, registrar, or DNS API - see docs/domain-providers.md
section 5 for what remains in awcms-one.
"""
from __future__ import annotations

from typing import Any


class PaymentRequiredError(Exception):
    """Raised when a billable job is attempted before payment/approval
    is confirmed - issue #102: "Require payment/approval before billable
    registration jobs"."""


def create_checkout_snapshot(
    checkout_id: str,
    tenant_id: str,
    correlation_id: str,
    product: dict[str, Any],
    price_snapshot: dict[str, Any],
    captured_at: str,
    *,
    approval_required: bool = False,
) -> dict[str, Any]:
    """Returns a `checkout-snapshot` instance. `payment_required` is
    always `True` here - a domain product that does not require payment
    before registration is not modeled by this contract (see
    docs/domain-providers.md section 5.3): if a future product tier
    needs a genuinely free operation, that is a new, explicitly-named
    product type, not a `False` value on this field."""
    return {
        "checkout_id": checkout_id,
        "tenant_id": tenant_id,
        "correlation_id": correlation_id,
        "product_id": product["product_id"],
        "price_snapshot_id": price_snapshot["snapshot_id"],
        "payment_required": True,
        "approval_required": approval_required,
        "captured_at": captured_at,
    }


def assert_registration_allowed(checkout: dict[str, Any], *, payment_confirmed: bool, approval_confirmed: bool) -> None:
    """Gate function a job submission path must call before creating a
    billable registration/renewal job. Raises `PaymentRequiredError`
    rather than silently proceeding - there is no "trust the caller"
    path."""
    if checkout["payment_required"] and not payment_confirmed:
        raise PaymentRequiredError(f"checkout {checkout['checkout_id']!r} requires confirmed payment before registration")
    if checkout["approval_required"] and not approval_confirmed:
        raise PaymentRequiredError(f"checkout {checkout['checkout_id']!r} requires confirmed approval before registration")


# ---------------------------------------------------------------------------
# Renewal reminders (issue #102: "90/30/7 days, payment failure, action
# required, manual fallback")
# ---------------------------------------------------------------------------

_DAYS_BEFORE_EXPIRY = {"day_90": 90, "day_30": 30, "day_7": 7}


def build_expiry_reminder_schedule(
    reminder_id_prefix: str, tenant_id: str, domain: str, order_id: str, expires_at_date: str, iso_date_to_offset_datetime
) -> list[dict[str, Any]]:
    """Returns the three date-based reminders (90/30/7 days before
    `expires_at_date`). `iso_date_to_offset_datetime(date_str, days_before)
    -> datetime_str` is injected rather than this module doing its own
    date arithmetic, so it stays dependency-free and trivially testable
    with fixed inputs (no real-clock/timezone logic to get subtly
    wrong)."""
    reminders = []
    for schedule, days_before in _DAYS_BEFORE_EXPIRY.items():
        reminders.append(
            {
                "reminder_id": f"{reminder_id_prefix}-{schedule}",
                "tenant_id": tenant_id,
                "domain": domain,
                "order_id": order_id,
                "schedule": schedule,
                "scheduled_at": iso_date_to_offset_datetime(expires_at_date, days_before),
                "sent_at": None,
            }
        )
    return reminders


def build_state_triggered_reminder(reminder_id: str, tenant_id: str, domain: str, order_id: str, schedule: str, scheduled_at: str) -> dict[str, Any]:
    """A reminder triggered by a job/order state (`payment_failure`,
    `action_required`, `manual_fallback`) rather than a fixed date."""
    if schedule not in ("payment_failure", "action_required", "manual_fallback"):
        raise ValueError(f"not a state-triggered reminder schedule: {schedule!r}")
    return {
        "reminder_id": reminder_id,
        "tenant_id": tenant_id,
        "domain": domain,
        "order_id": order_id,
        "schedule": schedule,
        "scheduled_at": scheduled_at,
        "sent_at": None,
    }


# ---------------------------------------------------------------------------
# Duplicate-event dedupe (issue #102: "duplicate payment/provider events
# do not create duplicate domain orders, registrations, invoices, or
# renewals")
# ---------------------------------------------------------------------------


class DedupeIndex:
    """An in-memory idempotency-key index across every billing-relevant
    event source (payment provider, registrar, DNS, GitHub). A real
    deployment needs durable storage (awcms-one's responsibility); this
    mirrors `lib/omes/py/jobs/store.py`'s `idempotency.json` pattern at
    the billing layer."""

    def __init__(self) -> None:
        self._by_key: dict[str, str] = {}  # idempotency_key -> event_id

    def record_event(self, event_id: str, source: str, idempotency_key: str, first_seen_at: str) -> dict[str, Any]:
        existing_event_id = self._by_key.get(idempotency_key)
        if existing_event_id is not None:
            return {
                "event_id": event_id,
                "source": source,
                "idempotency_key": idempotency_key,
                "first_seen_at": first_seen_at,
                "duplicate_of": existing_event_id,
            }
        self._by_key[idempotency_key] = event_id
        return {
            "event_id": event_id,
            "source": source,
            "idempotency_key": idempotency_key,
            "first_seen_at": first_seen_at,
            "duplicate_of": None,
        }


# ---------------------------------------------------------------------------
# Non-refundable-after-success semantics (issue #102)
# ---------------------------------------------------------------------------

# A provider operation that has actually reached the provider and
# succeeded is never refundable through this ledger - the provider cost
# was incurred and (for a registrar) the resource now exists. Every
# other state either never reached the provider or is still recoverable
# without a provider-side cost having been finalized.
_NON_REFUNDABLE_STATES = frozenset({"succeeded", "active", "renewal_due"})


def refund_eligibility(order_id: str, operation: str, order_state: str) -> dict[str, Any]:
    if order_state in _NON_REFUNDABLE_STATES:
        return {
            "order_id": order_id,
            "operation": operation,
            "order_state": order_state,
            "refundable": False,
            "reason": f"{operation} reached provider state {order_state!r}; the provider operation is not reversible through this ledger",
        }
    return {
        "order_id": order_id,
        "operation": operation,
        "order_state": order_state,
        "refundable": True,
        "reason": f"{operation} has not succeeded at the provider (state={order_state!r}); no irreversible provider cost was incurred",
    }


# ---------------------------------------------------------------------------
# State separation / reconciliation (issue #102: "Keep registrar state,
# invoice state, entitlement state, and DNS state separate with explicit
# reconciliation rules")
# ---------------------------------------------------------------------------

STATE_DOMAINS = ("registrar", "invoice", "entitlement", "dns")


def build_reconciliation_rule(
    rule_id: str,
    state_domain: str,
    authoritative_source: str,
    reconciled_with: list[str],
    last_reconciled_at: str,
    *,
    drift_detected: bool = False,
) -> dict[str, Any]:
    if state_domain not in STATE_DOMAINS:
        raise ValueError(f"unknown state_domain: {state_domain!r}")
    for other in reconciled_with:
        if other not in STATE_DOMAINS:
            raise ValueError(f"unknown state_domain in reconciled_with: {other!r}")
        if other == state_domain:
            raise ValueError("a state_domain cannot be reconciled with itself")
    return {
        "rule_id": rule_id,
        "state_domain": state_domain,
        "authoritative_source": authoritative_source,
        "reconciled_with": reconciled_with,
        "last_reconciled_at": last_reconciled_at,
        "drift_detected": drift_detected,
    }


# ---------------------------------------------------------------------------
# Reports (issue #102)
# ---------------------------------------------------------------------------

REPORT_TYPES = (
    "margin",
    "upcoming_renewals",
    "failed_registrations",
    "pending_documents",
    "provider_balance_action",
    "reconciliation_drift",
)


def build_report(report_id: str, report_type: str, generated_at: str, rows: list[dict[str, Any]], *, tenant_id: str | None = None) -> dict[str, Any]:
    if report_type not in REPORT_TYPES:
        raise ValueError(f"unknown report_type: {report_type!r}")
    return {
        "report_id": report_id,
        "report_type": report_type,
        "tenant_id": tenant_id,
        "generated_at": generated_at,
        "rows": rows,
    }


def margin_row(domain: str, price_snapshot: dict[str, Any]) -> dict[str, Any]:
    margin_minor = price_snapshot["customer_price_minor"] - price_snapshot["provider_cost_minor"]
    return {
        "domain": domain,
        "currency": price_snapshot["currency"],
        "provider_cost_minor": price_snapshot["provider_cost_minor"],
        "customer_price_minor": price_snapshot["customer_price_minor"],
        "margin_minor": margin_minor,
    }
