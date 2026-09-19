"""Tests for lib/omes/py/jobs/ledger.py (issue #93): money precision,
currency mismatch, duplicate idempotency keys, and invalid transitions."""
import os
import unittest

from . import _pathfix  # noqa: F401
from jobs import ledger, states

_THIS = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS, "..", "..", ".."))
_TABLE_PATH = os.path.join(_REPO_ROOT, "contracts", "control-center", "v1", "invoice.states.json")


def _line(line_id, quantity, unit_amount_minor, currency="USD", tax_code=None):
    line = {
        "line_id": line_id,
        "description": "test line",
        "quantity": quantity,
        "price_snapshot": {
            "price_id": "price-1",
            "catalog_version_id": "version-1",
            "currency": currency,
            "unit_amount_minor": unit_amount_minor,
        },
    }
    if tax_code:
        line["tax_code"] = tax_code
    return line


class TestComputeTotalsMoneyPrecision(unittest.TestCase):
    def test_simple_subtotal_is_exact_integer_arithmetic(self):
        totals = ledger.compute_totals("USD", [_line("l1", 3, 1999)])
        self.assertEqual(totals["subtotal_minor"], 5997)
        self.assertEqual(totals["total_minor"], 5997)
        self.assertIsInstance(totals["subtotal_minor"], int)

    def test_tax_is_computed_in_basis_points_with_floor_rounding(self):
        # 1000 minor units * 833 bps (8.33%) = 83.3 -> floor to 83
        totals = ledger.compute_totals(
            "USD", [_line("l1", 1, 1000, tax_code="tax-standard")], tax_rates_bps={"tax-standard": 833}
        )
        self.assertEqual(totals["tax_minor"], 83)
        self.assertEqual(totals["total_minor"], 1083)

    def test_untagged_line_has_zero_tax(self):
        totals = ledger.compute_totals("USD", [_line("l1", 1, 1000)])
        self.assertEqual(totals["tax_minor"], 0)

    def test_applied_credit_reduces_total(self):
        credit = {"credit_id": "c1", "currency": "USD", "amount_minor": 500, "applied": True}
        totals = ledger.compute_totals("USD", [_line("l1", 1, 2000)], credits=[credit])
        self.assertEqual(totals["total_minor"], 1500)

    def test_unapplied_credit_is_ignored(self):
        credit = {"credit_id": "c1", "currency": "USD", "amount_minor": 500, "applied": False}
        totals = ledger.compute_totals("USD", [_line("l1", 1, 2000)], credits=[credit])
        self.assertEqual(totals["total_minor"], 2000)

    def test_negative_adjustment_reduces_total(self):
        adjustment = {"adjustment_id": "a1", "currency": "USD", "amount_minor": -300}
        totals = ledger.compute_totals("USD", [_line("l1", 1, 1000)], adjustments=[adjustment])
        self.assertEqual(totals["total_minor"], 700)

    def test_total_never_goes_negative(self):
        adjustment = {"adjustment_id": "a1", "currency": "USD", "amount_minor": -100000}
        totals = ledger.compute_totals("USD", [_line("l1", 1, 1000)], adjustments=[adjustment])
        self.assertEqual(totals["total_minor"], 0)


class TestCurrencyMismatch(unittest.TestCase):
    def test_line_in_different_currency_raises(self):
        with self.assertRaises(ledger.CurrencyMismatchError):
            ledger.compute_totals("USD", [_line("l1", 1, 1000, currency="EUR")])

    def test_credit_in_different_currency_raises(self):
        credit = {"credit_id": "c1", "currency": "EUR", "amount_minor": 500, "applied": True}
        with self.assertRaises(ledger.CurrencyMismatchError):
            ledger.compute_totals("USD", [_line("l1", 1, 1000)], credits=[credit])

    def test_payment_in_different_currency_is_rejected_by_record_payment(self):
        with self.assertRaises(ledger.CurrencyMismatchError):
            ledger.record_payment(
                [],
                {"payment_id": "p1", "currency": "EUR", "amount_minor": 500, "idempotency_key": "idem-1-aaaaaaaa"},
                invoice_currency="USD",
            )


class TestDuplicateIdempotencyKey(unittest.TestCase):
    def test_second_payment_with_same_idempotency_key_is_rejected(self):
        payments = ledger.record_payment(
            [], {"payment_id": "p1", "currency": "USD", "amount_minor": 500, "idempotency_key": "idem-1-aaaaaaaa"}, "USD"
        )
        with self.assertRaises(ledger.DuplicatePaymentError):
            ledger.record_payment(
                payments,
                {"payment_id": "p2", "currency": "USD", "amount_minor": 999, "idempotency_key": "idem-1-aaaaaaaa"},
                "USD",
            )

    def test_different_idempotency_key_is_accepted(self):
        payments = ledger.record_payment(
            [], {"payment_id": "p1", "currency": "USD", "amount_minor": 500, "idempotency_key": "idem-1-aaaaaaaa"}, "USD"
        )
        payments = ledger.record_payment(
            payments, {"payment_id": "p2", "currency": "USD", "amount_minor": 300, "idempotency_key": "idem-2-bbbbbbbb"}, "USD"
        )
        self.assertEqual(len(payments), 2)

    def test_refund_duplicate_idempotency_key_is_rejected(self):
        payments = [{"payment_id": "p1", "currency": "USD", "amount_minor": 1000}]
        refunds = ledger.record_refund(
            [], payments, {"refund_id": "r1", "payment_id": "p1", "currency": "USD", "amount_minor": 200, "idempotency_key": "ridem-1-aaaa"}
        )
        with self.assertRaises(ledger.DuplicatePaymentError):
            ledger.record_refund(
                refunds, payments, {"refund_id": "r2", "payment_id": "p1", "currency": "USD", "amount_minor": 100, "idempotency_key": "ridem-1-aaaa"}
            )

    def test_refund_exceeding_payment_amount_is_rejected(self):
        payments = [{"payment_id": "p1", "currency": "USD", "amount_minor": 1000}]
        with self.assertRaises(ValueError):
            ledger.record_refund(
                [], payments, {"refund_id": "r1", "payment_id": "p1", "currency": "USD", "amount_minor": 1001, "idempotency_key": "ridem-1-aaaa"}
            )


class TestInvoiceStateTransitions(unittest.TestCase):
    def setUp(self):
        self.machine = states.load_state_machine(_TABLE_PATH)

    def test_draft_to_issued_is_valid(self):
        self.machine.assert_transition("draft", "issued")

    def test_issued_to_paid_is_valid(self):
        self.machine.assert_transition("issued", "paid")

    def test_paid_to_draft_is_invalid(self):
        with self.assertRaises(states.TransitionError):
            self.machine.assert_transition("paid", "draft")

    def test_void_is_terminal(self):
        self.assertTrue(self.machine.is_terminal("void"))
        self.assertTrue(self.machine.is_terminal("refunded"))
        self.assertFalse(self.machine.is_terminal("disputed"))

    def test_state_after_payment_recommends_paid_when_fully_paid(self):
        self.assertEqual(ledger.state_after_payment("issued", 1000, 1000), "paid")

    def test_state_after_payment_recommends_partially_paid(self):
        self.assertEqual(ledger.state_after_payment("issued", 1000, 400), "partially_paid")

    def test_state_after_payment_unchanged_when_nothing_paid(self):
        self.assertEqual(ledger.state_after_payment("issued", 1000, 0), "issued")

    def test_caller_must_still_validate_the_recommended_transition(self):
        # state_after_payment() itself doesn't know an invoice is void; the
        # caller is responsible for checking the transition before applying it.
        recommended = ledger.state_after_payment("void", 1000, 1000)
        self.assertEqual(recommended, "paid")
        with self.assertRaises(states.TransitionError):
            self.machine.assert_transition("void", recommended)
