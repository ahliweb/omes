"""lib/omes/py/jobs/ledger.py - a pure, stdlib-only invoice reconciliation
helper (issue #93).

Every money amount handled here is an integer count of a currency's minor
units (cents, etc.) - never a float - per docs/control-center-contracts.md
"Money precision rules". Mixing currencies within one invoice's lines,
credits, adjustments, or payments is always an error: this module never
converts currency, it only rejects a mismatch.
"""
from __future__ import annotations

from typing import Any


class CurrencyMismatchError(Exception):
    """Raised when two amounts that must share a currency do not."""


class DuplicatePaymentError(Exception):
    """Raised when a payment/refund idempotency_key has already been recorded."""


class InvalidTransitionError(Exception):
    """Raised when a computed invoice state would not be a legal transition."""


def _require_same_currency(expected: str, actual: str, what: str) -> None:
    if expected != actual:
        raise CurrencyMismatchError(f"{what}: expected currency {expected!r}, got {actual!r}")


def compute_totals(
    currency: str,
    lines: list[dict[str, Any]],
    credits: list[dict[str, Any]] | None = None,
    adjustments: list[dict[str, Any]] | None = None,
    tax_rates_bps: dict[str, int] | None = None,
    payments: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    """Computes an invoice's `totals` block from its lines plus any
    credits/adjustments/payments, entirely in integer minor units.

    `tax_rates_bps` maps a line's optional `tax_code` to a basis-point
    rate (see tax-metadata.schema.json); a line with no `tax_code`, or
    whose `tax_code` is not in `tax_rates_bps`, is taxed at 0. Tax is
    computed per line (`floor(line_subtotal * rate_bps / 10000)`) and
    summed, so rounding never accumulates non-deterministically across
    lines.
    """
    credits = credits or []
    adjustments = adjustments or []
    payments = payments or []
    tax_rates_bps = tax_rates_bps or {}

    subtotal_minor = 0
    tax_minor = 0
    for line in lines:
        snap = line["price_snapshot"]
        _require_same_currency(currency, snap["currency"], f"line {line['line_id']}")
        line_subtotal = snap["unit_amount_minor"] * line["quantity"]
        subtotal_minor += line_subtotal
        rate_bps = tax_rates_bps.get(line.get("tax_code"), 0)
        tax_minor += (line_subtotal * rate_bps) // 10000

    credit_minor = 0
    for credit in credits:
        if not credit.get("applied", False):
            continue
        _require_same_currency(currency, credit["currency"], f"credit {credit['credit_id']}")
        credit_minor += credit["amount_minor"]

    adjustment_minor = 0
    for adjustment in adjustments:
        _require_same_currency(currency, adjustment["currency"], f"adjustment {adjustment['adjustment_id']}")
        adjustment_minor += adjustment["amount_minor"]

    total_minor = subtotal_minor + tax_minor - credit_minor + adjustment_minor
    if total_minor < 0:
        total_minor = 0

    paid_minor = 0
    for payment in payments:
        _require_same_currency(currency, payment["currency"], f"payment {payment['payment_id']}")
        paid_minor += payment["amount_minor"]

    return {
        "subtotal_minor": subtotal_minor,
        "tax_minor": tax_minor,
        "credit_minor": credit_minor,
        "adjustment_minor": adjustment_minor,
        "total_minor": total_minor,
        "paid_minor": paid_minor,
    }


def record_payment(
    existing_payments: list[dict[str, Any]],
    new_payment: dict[str, Any],
    invoice_currency: str,
) -> list[dict[str, Any]]:
    """Appends `new_payment` to `existing_payments`, enforcing:

    - **Currency match**: `new_payment.currency` must equal the invoice's
      currency (`CurrencyMismatchError` otherwise).
    - **No duplicate confirmation**: a payment whose `idempotency_key`
      already appears in `existing_payments` is rejected
      (`DuplicatePaymentError`) rather than counted twice - issue #93:
      "reject duplicate confirmations".

    Returns a NEW list (does not mutate `existing_payments`).
    """
    _require_same_currency(invoice_currency, new_payment["currency"], f"payment {new_payment['payment_id']}")
    for existing in existing_payments:
        if existing["idempotency_key"] == new_payment["idempotency_key"]:
            raise DuplicatePaymentError(
                f"idempotency_key {new_payment['idempotency_key']!r} was already recorded "
                f"as payment {existing['payment_id']!r}"
            )
    return [*existing_payments, new_payment]


def record_refund(
    existing_refunds: list[dict[str, Any]],
    existing_payments: list[dict[str, Any]],
    new_refund: dict[str, Any],
) -> list[dict[str, Any]]:
    """Appends `new_refund`, enforcing duplicate-idempotency-key rejection
    (same rule as `record_payment`) and that the refunded total against a
    given `payment_id` never exceeds that payment's own amount."""
    for existing in existing_refunds:
        if existing["idempotency_key"] == new_refund["idempotency_key"]:
            raise DuplicatePaymentError(
                f"idempotency_key {new_refund['idempotency_key']!r} was already recorded "
                f"as refund {existing['refund_id']!r}"
            )

    payment = next((p for p in existing_payments if p["payment_id"] == new_refund["payment_id"]), None)
    if payment is None:
        raise ValueError(f"refund references unknown payment_id {new_refund['payment_id']!r}")
    _require_same_currency(payment["currency"], new_refund["currency"], f"refund {new_refund['refund_id']}")

    already_refunded = sum(
        r["amount_minor"] for r in existing_refunds if r["payment_id"] == new_refund["payment_id"]
    )
    if already_refunded + new_refund["amount_minor"] > payment["amount_minor"]:
        raise ValueError(
            f"refund would exceed payment {payment['payment_id']!r}: "
            f"already refunded {already_refunded} + {new_refund['amount_minor']} > paid {payment['amount_minor']}"
        )
    return [*existing_refunds, new_refund]


def state_after_payment(current_state: str, total_minor: int, paid_minor: int) -> str:
    """Given a fully-recomputed `paid_minor` (from `compute_totals()`),
    returns the invoice state that should follow - `paid` once
    `paid_minor >= total_minor`, `partially_paid` if something has been
    paid but not enough, or `current_state` unchanged otherwise. This
    function only recommends the next state; the caller is responsible for
    checking it against `lib/omes/py/jobs/states.py`'s transition table
    before applying it, so an invoice already in `void`/`refunded`/`disputed`
    is never silently overwritten by a stale payment recomputation."""
    if paid_minor >= total_minor and total_minor > 0:
        return "paid"
    if paid_minor > 0:
        return "partially_paid"
    return current_state
