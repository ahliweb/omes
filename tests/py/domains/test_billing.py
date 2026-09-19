import unittest

from . import _pathfix  # noqa: F401

from domains import billing


def _iso_date_minus_days(date_str: str, days_before: int) -> str:
    """Minimal, dependency-free date arithmetic for fixed test dates
    (no calendar library available under ADR-0012's stdlib-only rule
    beyond what's needed here); this is intentionally NOT a general
    date library - see build_expiry_reminder_schedule()'s docstring."""
    import datetime

    dt = datetime.datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ")
    result = dt - datetime.timedelta(days=days_before)
    return result.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestCheckoutSnapshotAndPaymentGate(unittest.TestCase):
    def test_checkout_snapshot_always_requires_payment(self):
        product = {"product_id": "prod-registration-intl"}
        price_snapshot = {"snapshot_id": "price-0001"}
        checkout = billing.create_checkout_snapshot("checkout-01", "tenant-1", "corr-1", product, price_snapshot, "2026-09-19T00:00:00Z")
        self.assertTrue(checkout["payment_required"])

    def test_registration_blocked_without_payment_confirmation(self):
        product = {"product_id": "prod-registration-intl"}
        price_snapshot = {"snapshot_id": "price-0001"}
        checkout = billing.create_checkout_snapshot("checkout-01", "tenant-1", "corr-1", product, price_snapshot, "t")
        with self.assertRaises(billing.PaymentRequiredError):
            billing.assert_registration_allowed(checkout, payment_confirmed=False, approval_confirmed=True)

    def test_registration_blocked_without_approval_when_required(self):
        product = {"product_id": "prod-registration-intl"}
        price_snapshot = {"snapshot_id": "price-0001"}
        checkout = billing.create_checkout_snapshot("checkout-01", "tenant-1", "corr-1", product, price_snapshot, "t", approval_required=True)
        with self.assertRaises(billing.PaymentRequiredError):
            billing.assert_registration_allowed(checkout, payment_confirmed=True, approval_confirmed=False)

    def test_registration_allowed_once_payment_and_approval_confirmed(self):
        product = {"product_id": "prod-registration-intl"}
        price_snapshot = {"snapshot_id": "price-0001"}
        checkout = billing.create_checkout_snapshot("checkout-01", "tenant-1", "corr-1", product, price_snapshot, "t", approval_required=True)
        billing.assert_registration_allowed(checkout, payment_confirmed=True, approval_confirmed=True)  # must not raise


class TestReminderSchedule(unittest.TestCase):
    def test_builds_90_30_7_day_reminders(self):
        reminders = billing.build_expiry_reminder_schedule("rem-example", "tenant-1", "example.com", "order-1", "2026-12-19T00:00:00Z", _iso_date_minus_days)
        schedules = {r["schedule"] for r in reminders}
        self.assertEqual(schedules, {"day_90", "day_30", "day_7"})
        day_7 = next(r for r in reminders if r["schedule"] == "day_7")
        self.assertEqual(day_7["scheduled_at"], "2026-12-12T00:00:00Z")

    def test_state_triggered_reminder_rejects_unknown_schedule(self):
        with self.assertRaises(ValueError):
            billing.build_state_triggered_reminder("r1", "tenant-1", "example.com", "order-1", "day_30", "t")

    def test_state_triggered_reminder_for_manual_fallback(self):
        reminder = billing.build_state_triggered_reminder("r1", "tenant-1", "example.com", "order-1", "manual_fallback", "2026-09-19T00:00:00Z")
        self.assertEqual(reminder["schedule"], "manual_fallback")


class TestDedupe(unittest.TestCase):
    def test_first_event_is_not_a_duplicate(self):
        index = billing.DedupeIndex()
        result = index.record_event("event-1", "payment_provider", "idem-pay-1", "t")
        self.assertIsNone(result["duplicate_of"])

    def test_replayed_idempotency_key_is_flagged_as_duplicate(self):
        index = billing.DedupeIndex()
        index.record_event("event-1", "payment_provider", "idem-pay-1", "t")
        result = index.record_event("event-2", "payment_provider", "idem-pay-1", "t2")
        self.assertEqual(result["duplicate_of"], "event-1")

    def test_dedupe_is_shared_across_sources(self):
        # A registrar-sourced event and a payment-provider-sourced event
        # sharing the same idempotency_key (e.g. both referencing the
        # same order) must still be recognized as duplicates of the
        # first-seen event.
        index = billing.DedupeIndex()
        index.record_event("event-1", "registrar", "idem-order-1", "t")
        result = index.record_event("event-2", "payment_provider", "idem-order-1", "t2")
        self.assertEqual(result["duplicate_of"], "event-1")


class TestRefundEligibility(unittest.TestCase):
    def test_succeeded_is_not_refundable(self):
        result = billing.refund_eligibility("order-1", "registration", "succeeded")
        self.assertFalse(result["refundable"])

    def test_active_is_not_refundable(self):
        result = billing.refund_eligibility("order-1", "registration", "active")
        self.assertFalse(result["refundable"])

    def test_failed_is_refundable(self):
        result = billing.refund_eligibility("order-1", "registration", "failed")
        self.assertTrue(result["refundable"])

    def test_pending_is_refundable(self):
        result = billing.refund_eligibility("order-1", "registration", "pending")
        self.assertTrue(result["refundable"])


class TestReconciliationRule(unittest.TestCase):
    def test_builds_a_valid_rule(self):
        rule = billing.build_reconciliation_rule("rule-1", "invoice", "invoice", ["registrar"], "2026-09-19T00:00:00Z")
        self.assertFalse(rule["drift_detected"])

    def test_rejects_unknown_state_domain(self):
        with self.assertRaises(ValueError):
            billing.build_reconciliation_rule("rule-1", "billing", "invoice", ["registrar"], "t")

    def test_rejects_self_reconciliation(self):
        with self.assertRaises(ValueError):
            billing.build_reconciliation_rule("rule-1", "invoice", "invoice", ["invoice"], "t")


class TestReports(unittest.TestCase):
    def test_build_report_rejects_unknown_type(self):
        with self.assertRaises(ValueError):
            billing.build_report("report-1", "customer_ltv", "t", [])

    def test_margin_row_computes_margin(self):
        price_snapshot = {"currency": "USD", "provider_cost_minor": 800, "customer_price_minor": 1500}
        row = billing.margin_row("example.com", price_snapshot)
        self.assertEqual(row["margin_minor"], 700)

    def test_build_report_for_upcoming_renewals(self):
        report = billing.build_report("report-1", "upcoming_renewals", "2026-09-19T00:00:00Z", [{"domain": "example.com"}])
        self.assertEqual(report["report_type"], "upcoming_renewals")
        self.assertEqual(len(report["rows"]), 1)


if __name__ == "__main__":
    unittest.main()
