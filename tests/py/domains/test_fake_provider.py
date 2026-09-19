import unittest

from . import _pathfix  # noqa: F401

from domains import fake_provider


def _cloudflare_like():
    reg = fake_provider.FakeRegistrar("cloudflare", mode="async", supported_tlds=("com", "net"), async_steps=2)
    reg.price_book.set_price("com", "cloudflare", 899, 1499)
    return reg


def _srsx_like():
    reg = fake_provider.FakeRegistrar("srsx", mode="documents", supported_tlds=("id",))
    reg.price_book.set_price("id", "srsx", 1500, 2500, currency="IDR")
    return reg


class TestCloudflareLikeAsyncFlow(unittest.TestCase):
    def test_search_is_discovery_only(self):
        reg = _cloudflare_like()
        result = reg.search("example", ("com", "xyz"))
        self.assertTrue(result["discovery_only"])
        self.assertEqual(len(result["results"]), 2)
        self.assertTrue(result["results"][0]["likely_available"])
        self.assertFalse(result["results"][1]["likely_available"])

    def test_authoritative_availability_check(self):
        reg = _cloudflare_like()
        result = reg.check_availability("example.com")
        self.assertTrue(result["authoritative"])
        self.assertTrue(result["available"])

    def test_unsupported_tld_raises_for_manual_routing(self):
        reg = _cloudflare_like()
        with self.assertRaises(fake_provider.ProviderError):
            reg.check_availability("example.xyz")

    def test_registration_is_in_progress_then_succeeds_after_polling(self):
        reg = _cloudflare_like()
        snapshot = reg.price_book.snapshot("com")
        response = reg.register(domain="example.com", idempotency_key="idem-0001aaaa", price_snapshot_id=snapshot["snapshot_id"])
        self.assertEqual(response["status"], "in_progress")

        polled = reg.poll_registration(response["order_id"])
        self.assertEqual(polled["status"], "in_progress")  # async_steps=2, still one to go

        polled = reg.poll_registration(response["order_id"])
        self.assertEqual(polled["status"], "succeeded")

    def test_polling_without_completion_never_reports_success(self):
        reg = _cloudflare_like()
        snapshot = reg.price_book.snapshot("com")
        response = reg.register(domain="example.com", idempotency_key="idem-0002aaaa", price_snapshot_id=snapshot["snapshot_id"])
        self.assertNotEqual(response["status"], "succeeded")

    def test_duplicate_registration_request_is_rejected(self):
        reg = _cloudflare_like()
        snapshot = reg.price_book.snapshot("com")
        reg.register(domain="example.com", idempotency_key="idem-dup-0001", price_snapshot_id=snapshot["snapshot_id"])
        with self.assertRaises(fake_provider.DuplicateRequestError):
            reg.register(domain="example.com", idempotency_key="idem-dup-0001", price_snapshot_id=snapshot["snapshot_id"])

    def test_price_change_between_snapshot_and_checkout_is_visible(self):
        reg = _cloudflare_like()
        snapshot_1 = reg.price_book.snapshot("com")
        reg.price_book.set_price("com", "cloudflare", 999, 1599)
        snapshot_2 = reg.price_book.snapshot("com")
        self.assertNotEqual(snapshot_1["snapshot_id"], snapshot_2["snapshot_id"])
        self.assertNotEqual(snapshot_1["customer_price_minor"], snapshot_2["customer_price_minor"])

    def test_transfer_and_contact_update_are_manual_fallback(self):
        reg = _cloudflare_like()
        self.assertEqual(reg.transfer()["status"], "action_required")
        self.assertEqual(reg.update_contact()["status"], "action_required")

    def test_dns_drift_detection(self):
        reg = _cloudflare_like()
        desired = [{"zone_id": "zone-1", "record_id": "rec-1", "type": "A", "name": "example.com", "content": "203.0.113.10", "ttl": 3600, "managed_by": "omes"}]
        reg.upsert_dns_record("zone-1", desired[0])
        self.assertEqual(reg.detect_drift("zone-1", desired), [])

        # provider-side change without OMES's knowledge -> drift
        drifted = dict(desired[0], content="203.0.113.99")
        reg.upsert_dns_record("zone-1", drifted)
        drift = reg.detect_drift("zone-1", desired)
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0]["kind"], "mismatch")

    def test_dnssec_status_defaults_unsigned_and_can_be_signed(self):
        reg = _cloudflare_like()
        self.assertEqual(reg.dnssec_status("example.com")["status"], "unsigned")
        reg.set_dnssec_status("example.com", "signed")
        self.assertEqual(reg.dnssec_status("example.com")["status"], "signed")

    def test_redact_hides_secret_like_fields(self):
        payload = {"api_token": "not-a-real-value-just-a-placeholder", "domain": "example.com", "nested": {"password": "also-a-placeholder"}}
        redacted = fake_provider.redact(payload)
        self.assertEqual(redacted["api_token"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["password"], "[REDACTED]")
        self.assertEqual(redacted["domain"], "example.com")


class TestSrsxLikeDocumentFlow(unittest.TestCase):
    def test_registration_immediately_requires_documents(self):
        reg = _srsx_like()
        snapshot = reg.price_book.snapshot("id")
        response = reg.register(domain="example.id", idempotency_key="idem-srsx-0001", price_snapshot_id=snapshot["snapshot_id"])
        self.assertEqual(response["status"], "action_required")
        self.assertEqual(reg.document_lifecycle(response["order_id"]), "documents_required")

    def test_document_lifecycle_is_separate_from_submission_status(self):
        reg = _srsx_like()
        snapshot = reg.price_book.snapshot("id")
        response = reg.register(domain="example.id", idempotency_key="idem-srsx-0002", price_snapshot_id=snapshot["snapshot_id"])
        order_id = response["order_id"]

        upload_ref = reg.request_document_upload(order_id)
        self.assertIn("upload_url_reference", upload_ref)
        self.assertEqual(reg.document_lifecycle(order_id), "upload_pending")

        reg.submit_documents(order_id)
        self.assertEqual(reg.document_lifecycle(order_id), "submitted")

        result = reg.review_documents(order_id, approve=True)
        self.assertEqual(result["lifecycle"], "active")
        self.assertEqual(result["registration_status"], "succeeded")

    def test_document_rejection_routes_to_action_required(self):
        reg = _srsx_like()
        snapshot = reg.price_book.snapshot("id")
        response = reg.register(domain="reject.id", idempotency_key="idem-srsx-0003", price_snapshot_id=snapshot["snapshot_id"])
        order_id = response["order_id"]
        reg.request_document_upload(order_id)
        reg.submit_documents(order_id)
        result = reg.review_documents(order_id, approve=False)
        self.assertEqual(result["lifecycle"], "rejected")
        self.assertEqual(result["registration_status"], "action_required")

    def test_submit_without_pending_upload_is_rejected(self):
        reg = _srsx_like()
        snapshot = reg.price_book.snapshot("id")
        response = reg.register(domain="example2.id", idempotency_key="idem-srsx-0004", price_snapshot_id=snapshot["snapshot_id"])
        with self.assertRaises(fake_provider.ProviderError):
            reg.submit_documents(response["order_id"])

    def test_unsupported_extension_raises_for_manual_routing(self):
        reg = _srsx_like()
        with self.assertRaises(fake_provider.ProviderError):
            reg.check_availability("example.com")

    def test_duplicate_request_is_rejected(self):
        reg = _srsx_like()
        snapshot = reg.price_book.snapshot("id")
        reg.register(domain="example3.id", idempotency_key="idem-srsx-dup", price_snapshot_id=snapshot["snapshot_id"])
        with self.assertRaises(fake_provider.DuplicateRequestError):
            reg.register(domain="example3.id", idempotency_key="idem-srsx-dup", price_snapshot_id=snapshot["snapshot_id"])


if __name__ == "__main__":
    unittest.main()
