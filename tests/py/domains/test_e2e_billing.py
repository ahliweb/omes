"""End-to-end fixtures for a Cloudflare international flow and an SRS-X
.id flow (issue #102 acceptance criterion), composing:

  checkout-snapshot -> payment gate -> fake-provider registration
    -> domain-order state transitions -> reconciliation
    -> reminder schedule -> report

No live credentials are used anywhere in this module - `FakeRegistrar`
never makes a network call, and every credential-shaped value here is
either absent or a `{"store","key"}` reference.
"""
import unittest

from . import _pathfix  # noqa: F401

from domains import billing, fake_provider, states
from domains.profiles import cloudflare, srsx

ACTOR = {"type": "service", "id": "svc-e2e-test"}


def _order(domain: str, provider: str, order_id: str, tenant_id: str, idempotency_key: str) -> dict:
    return {
        "order_id": order_id,
        "tenant_id": tenant_id,
        "correlation_id": f"corr-{order_id}",
        "idempotency_key": idempotency_key,
        "domain": domain,
        "provider": provider,
        "operation": "registration",
        "state": "pending",
        "previous_state": None,
        "actor": ACTOR,
        "transitioned_at": "2026-09-19T00:00:00Z",
    }


class TestCloudflareInternationalEndToEnd(unittest.TestCase):
    def test_full_flow_from_checkout_to_active_report(self):
        # 1. Checkout snapshot + payment gate.
        product = {"product_id": "prod-registration-intl"}
        registrar = fake_provider.FakeRegistrar("cloudflare", mode="async", supported_tlds=cloudflare.REGISTRAR_TLDS, async_steps=2)
        registrar.price_book.set_price("com", "cloudflare", 899, 1499)
        price_snapshot = registrar.price_book.snapshot("com")
        checkout = billing.create_checkout_snapshot("checkout-cf-01", "tenant-acme", "corr-cf-01", product, price_snapshot, "2026-09-19T00:00:00Z")
        billing.assert_registration_allowed(checkout, payment_confirmed=True, approval_confirmed=True)

        # 2. Fake-provider registration (async, Cloudflare-like).
        response = registrar.register(domain="example-shop.com", idempotency_key="idem-e2e-cf01", price_snapshot_id=price_snapshot["snapshot_id"])
        order = _order("example-shop.com", "cloudflare", response["order_id"], "tenant-acme", "idem-e2e-cf01")
        self.assertEqual(order["state"], "pending")

        # 3. Poll to a terminal outcome; a domain-order only becomes
        # "succeeded" once the poll actually confirms it (never on a
        # queued/paid assumption).
        for _ in range(registrar.async_steps):
            polled = registrar.poll_registration(response["order_id"])
        self.assertEqual(polled["status"], "succeeded")
        order = states.apply_transition(order, "succeeded", ACTOR, "2026-09-19T00:05:00Z")
        order = states.apply_transition(order, "active", ACTOR, "2026-09-19T00:05:01Z")
        self.assertEqual(order["state"], "active")

        # 4. Reconciliation: registrar and invoice state are tracked
        # separately, and a rule records whether they currently agree.
        rule = billing.build_reconciliation_rule(
            "rule-cf-01", "registrar", "registrar", ["invoice"], "2026-09-19T00:05:02Z", drift_detected=False
        )
        self.assertFalse(rule["drift_detected"])

        # 5. Renewal reminders, scheduled off the registered domain's
        # expiry.
        registered = registrar._registered_domains["example-shop.com"]  # test-internal read, not a public contract
        reminders = billing.build_expiry_reminder_schedule(
            "rem-cf-01", "tenant-acme", "example-shop.com", order["order_id"], registered["expires_at"], lambda d, n: d
        )
        self.assertEqual(len(reminders), 3)

        # 6. A margin report row for this domain.
        report = billing.build_report(
            "report-cf-01", "margin", "2026-09-19T00:10:00Z", [billing.margin_row("example-shop.com", price_snapshot)]
        )
        self.assertEqual(report["rows"][0]["margin_minor"], price_snapshot["customer_price_minor"] - price_snapshot["provider_cost_minor"])

        # 7. Once active, the order is non-refundable.
        eligibility = billing.refund_eligibility(order["order_id"], "registration", order["state"])
        self.assertFalse(eligibility["refundable"])


class TestSrsxIdEndToEnd(unittest.TestCase):
    def test_full_flow_including_document_workflow_and_action_required_report(self):
        product = {"product_id": "prod-registration-id"}
        registrar = fake_provider.FakeRegistrar("srsx", mode="documents", supported_tlds=srsx.REGISTRAR_TLDS)
        registrar.price_book.set_price("id", "srsx", 150000, 250000, currency="IDR")
        price_snapshot = registrar.price_book.snapshot("id")
        checkout = billing.create_checkout_snapshot("checkout-srsx-01", "tenant-acme", "corr-srsx-01", product, price_snapshot, "2026-09-19T01:00:00Z")
        billing.assert_registration_allowed(checkout, payment_confirmed=True, approval_confirmed=True)

        response = registrar.register(domain="example.id", idempotency_key="idem-e2e-sx01", price_snapshot_id=price_snapshot["snapshot_id"])
        order = _order("example.id", "srsx", response["order_id"], "tenant-acme", "idem-e2e-sx01")
        order = states.apply_transition(order, "action_required", ACTOR, "2026-09-19T01:00:01Z")
        self.assertEqual(registrar.document_lifecycle(response["order_id"]), "documents_required")

        # A pending-documents report row while the order sits in
        # action_required.
        pending_docs_report = billing.build_report(
            "report-srsx-01",
            "pending_documents",
            "2026-09-19T01:00:02Z",
            [{"domain": "example.id", "order_id": order["order_id"], "lifecycle": registrar.document_lifecycle(response["order_id"])}],
        )
        self.assertEqual(pending_docs_report["rows"][0]["lifecycle"], "documents_required")

        # Upload, submit, approve -> succeeded -> active.
        registrar.request_document_upload(response["order_id"], now_tick=0)
        registrar.submit_documents(response["order_id"], now_tick=1)
        result = registrar.review_documents(response["order_id"], approve=True)
        self.assertEqual(result["registration_status"], "succeeded")

        order = states.apply_transition(order, "pending", ACTOR, "2026-09-19T01:05:00Z")
        order = states.apply_transition(order, "succeeded", ACTOR, "2026-09-19T01:05:01Z")
        order = states.apply_transition(order, "active", ACTOR, "2026-09-19T01:05:02Z")
        self.assertEqual(order["state"], "active")

        # Duplicate payment/provider event for the SAME order must not
        # create a second order/registration.
        dedupe = billing.DedupeIndex()
        first = dedupe.record_event("event-1", "registrar", "idem-e2e-sx01", "2026-09-19T01:05:03Z")
        second = dedupe.record_event("event-2", "payment_provider", "idem-e2e-sx01", "2026-09-19T01:05:04Z")
        self.assertIsNone(first["duplicate_of"])
        self.assertEqual(second["duplicate_of"], "event-1")

    def test_rejected_document_produces_failed_registration_report_row(self):
        registrar = fake_provider.FakeRegistrar("srsx", mode="documents", supported_tlds=srsx.REGISTRAR_TLDS)
        registrar.price_book.set_price("id", "srsx", 150000, 250000, currency="IDR")
        price_snapshot = registrar.price_book.snapshot("id")
        response = registrar.register(domain="reject-example.id", idempotency_key="idem-e2e-sx02", price_snapshot_id=price_snapshot["snapshot_id"])
        registrar.request_document_upload(response["order_id"], now_tick=0)
        registrar.submit_documents(response["order_id"], now_tick=1)
        result = registrar.review_documents(response["order_id"], approve=False)
        self.assertEqual(result["registration_status"], "action_required")

        report = billing.build_report(
            "report-srsx-02",
            "failed_registrations",
            "2026-09-19T01:10:00Z",
            [{"domain": "reject-example.id", "order_id": response["order_id"], "status": result["registration_status"]}],
        )
        self.assertEqual(report["rows"][0]["status"], "action_required")

        # An order that never reached success is refundable.
        eligibility = billing.refund_eligibility(response["order_id"], "registration", "failed")
        self.assertTrue(eligibility["refundable"])


if __name__ == "__main__":
    unittest.main()
