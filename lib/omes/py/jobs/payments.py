"""lib/omes/py/jobs/payments.py - stdlib-only webhook verification and
proration reference implementation (issue #94).

This module never calls a network, never stores a raw payment
credential, and never trusts a webhook payload before its signature is
verified. Every function is pure (given its explicit inputs) so it can be
unit tested without a real provider account - fixtures/tests generate
their own throwaway HMAC secrets at runtime with `hmac`/`secrets`, never
a literal secret-shaped string (AGENTS.md, and gitleaks scans full
history).

**Deterministic rounding rule (issue #94)**: proration uses HALF-UP
rounding in integer minor units, not banker's rounding (round-half-to-
even). Banker's rounding is designed to cancel bias across a very large
number of automated transactions; a subscription proration is a single,
often support-reviewed, human-facing number, where HALF-UP is the
intuitive "round 0.5 up" behavior most people (and most billing systems
customers already know, e.g. cash rounding) expect explained to them.
`round_half_up()` never uses `float`/`Decimal`; it is pure integer
division with a manual remainder check.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any


class SignatureVerificationError(Exception):
    """Raised when a webhook signature does not match."""


class StaleEventError(Exception):
    """Raised when a webhook's timestamp is outside the replay window."""


class ReplayedEventError(Exception):
    """Raised when an event_id has already been processed."""


def compute_signature(secret: bytes, payload: bytes) -> str:
    """Computes the HMAC-SHA256 signature (lowercase hex digest) a
    provider would send alongside `payload`, signed with `secret`."""
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_signature(secret: bytes, payload: bytes, signature_hex: str) -> bool:
    """Constant-time comparison (`hmac.compare_digest`) of the expected
    signature against the one the webhook claims. Never uses `==` on the
    two hex strings directly - a naive comparison is a timing side
    channel."""
    expected = compute_signature(secret, payload)
    return hmac.compare_digest(expected, signature_hex)


def assert_valid_signature(secret: bytes, payload: bytes, signature_hex: str) -> None:
    if not verify_signature(secret, payload, signature_hex):
        raise SignatureVerificationError("webhook signature does not match payload")


def check_freshness(occurred_at_epoch: int, received_at_epoch: int, replay_window_seconds: int) -> None:
    """Rejects a webhook whose claimed `occurred_at` is outside
    `replay_window_seconds` of `received_at` (issue #94: 'verify ...
    timestamps ... before applying state changes'), in EITHER direction -
    a timestamp far in the future is just as suspicious as a stale one."""
    delta = abs(received_at_epoch - occurred_at_epoch)
    if delta > replay_window_seconds:
        raise StaleEventError(
            f"event timestamp is {delta}s away from received time, outside the "
            f"{replay_window_seconds}s replay window"
        )


def check_not_replayed(seen_event_ids: set[str], event_id: str) -> None:
    """Raises `ReplayedEventError` if `event_id` has already been
    processed. The caller is expected to persist `seen_event_ids`
    (issue #90's job store / an outbox table); this function only
    implements the check itself, not the storage."""
    if event_id in seen_event_ids:
        raise ReplayedEventError(f"event_id {event_id!r} has already been processed")


def verify_webhook(
    secret: bytes,
    payload: bytes,
    signature_hex: str,
    occurred_at_epoch: int,
    received_at_epoch: int,
    replay_window_seconds: int,
    event_id: str,
    seen_event_ids: set[str],
) -> None:
    """Runs every issue #94 verification step in the mandatory order:
    signature first (fail closed on an unverifiable event before reading
    anything else from it), then freshness, then replay. Raises on the
    first failure; does not mutate `seen_event_ids` (the caller adds
    `event_id` to it only after successfully acting on the event, so a
    failure partway through downstream processing can still be retried)."""
    assert_valid_signature(secret, payload, signature_hex)
    check_freshness(occurred_at_epoch, received_at_epoch, replay_window_seconds)
    check_not_replayed(seen_event_ids, event_id)


def round_half_up(numerator: int, denominator: int) -> int:
    """Integer HALF-UP rounding of `numerator / denominator`, for
    non-negative `numerator`/`denominator`. Never uses float/Decimal.
    Example: round_half_up(5, 2) == 3 (2.5 rounds up), round_half_up(4, 2) == 2.
    """
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    if numerator < 0:
        raise ValueError("numerator must be non-negative")
    quotient, remainder = divmod(numerator, denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return quotient


def compute_proration(
    old_amount_minor: int,
    new_amount_minor: int,
    days_remaining: int,
    days_in_cycle: int,
) -> dict[str, int]:
    """Computes an upgrade/downgrade proration in integer minor units
    using HALF-UP rounding (see module docstring). Returns
    `{"unused_amount_minor", "new_amount_minor", "net_amount_minor"}`
    matching `proration.schema.json`. `unused_amount_minor` is the
    HALF-UP-rounded portion of the OLD plan's price for the remaining
    days (a credit); the new charge is HALF-UP-rounded the same way for
    consistency, even though `new_amount_minor` here is the already-
    prorated NEW charge for the remaining days, not the full-cycle price.
    """
    if days_in_cycle <= 0:
        raise ValueError("days_in_cycle must be positive")
    if not (0 <= days_remaining <= days_in_cycle):
        raise ValueError("days_remaining must be between 0 and days_in_cycle")

    unused_amount_minor = round_half_up(old_amount_minor * days_remaining, days_in_cycle)
    prorated_new_amount_minor = round_half_up(new_amount_minor * days_remaining, days_in_cycle)
    net_amount_minor = prorated_new_amount_minor - unused_amount_minor
    return {
        "unused_amount_minor": unused_amount_minor,
        "new_amount_minor": prorated_new_amount_minor,
        "net_amount_minor": net_amount_minor,
    }


def refund_amount_is_within_payment(payment_amount_minor: int, already_refunded_minor: int, requested_refund_minor: int) -> bool:
    """Pure helper mirroring `lib/omes/py/jobs/ledger.py`'s
    `record_refund()` check, exposed here too because issue #94's own
    webhook-driven refund path (a `refund.completed` event, not a manual
    confirmation) must enforce the identical rule without importing
    ledger.py's list-mutation API."""
    return already_refunded_minor + requested_refund_minor <= payment_amount_minor


def decide_automation(
    event_type: str,
    automation_policy: dict[str, Any],
) -> dict[str, Any]:
    """Decides whether a webhook-triggered DESTRUCTIVE action (one that
    would stop/degrade an existing deployment or block new
    provisioning/upgrades) may proceed automatically, or must be queued
    for human approval (issue #94: 'Require approval or explicit
    documented automation policy before destructive deployment actions
    caused by non-payment'). Non-destructive event types (a successful
    payment, a routine recurring invoice) never require approval here -
    this function only gates the destructive path."""
    destructive_event_types = {"payment.failed", "dunning.retry", "dispute.opened", "cancellation.requested"}
    if event_type not in destructive_event_types:
        return {"requires_approval": False, "reason": f"{event_type} is not a destructive event type"}

    requires_approval = automation_policy.get("requires_approval_for_destructive_action", True)
    if requires_approval:
        return {
            "requires_approval": True,
            "reason": f"{event_type} is destructive and automation_policy requires human approval",
        }
    return {
        "requires_approval": False,
        "reason": f"{event_type} is destructive but automation_policy {automation_policy.get('policy_id')!r} explicitly allows unattended action",
    }
