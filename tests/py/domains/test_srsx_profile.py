"""Issue #100 acceptance-criteria tests: registration, renewal, API
failure, duplicate request, document URL expiry, rejection, and
credential redaction, against the SRS-X profile."""
import unittest

from . import _pathfix  # noqa: F401

from domains import fake_provider, preflight, retry, routing
from domains.profiles import srsx


def _router_with_srsx_only() -> routing.Router:
    router = routing.Router()
    router.register(routing.Capability.from_dict(srsx.REGISTRAR_CAPABILITY))
    return router


def _registrar() -> fake_provider.FakeRegistrar:
    reg = fake_provider.FakeRegistrar("srsx", mode="documents", supported_tlds=srsx.REGISTRAR_TLDS)
    reg.price_book.set_price("id", "srsx", 150000, 250000, currency="IDR")
    return reg


def _config(**overrides):
    base = {
        "config_id": "cfg-srsx-01",
        "reseller_id": "reseller-0001",
        "api_username": "acme-reseller",
        "password_reference": {"store": "vault", "key": "srsx/api-password"},
        "endpoint": "https://srb3.srs-x.com/api",
        "mode": "sandbox",
        "authorized_egress_ip": "203.0.113.10",
    }
    base.update(overrides)
    return base


class TestSrsxRouting(unittest.TestCase):
    def test_id_extension_routes_to_srsx(self):
        router = _router_with_srsx_only()
        decision = router.resolve("tenant-1", "corr-1", "id", "registration")
        self.assertEqual(decision["provider"], "srsx")
        self.assertFalse(decision["manual_fallback"])

    def test_co_id_and_or_id_are_distinct_from_bare_id(self):
        router = _router_with_srsx_only()
        for tld in ("co.id", "or.id"):
            decision = router.resolve("tenant-1", "corr-1", tld, "registration")
            self.assertFalse(decision["manual_fallback"], f".{tld} should route to srsx")

    def test_transfer_and_contact_update_and_dns_are_manual_fallback(self):
        # Not yet verified against a live/sandbox account - see
        # profiles/srsx.py's REGISTRAR_CAPABILITY notes.
        router = _router_with_srsx_only()
        for operation in ("transfer", "contact_update", "dns_records", "dnssec"):
            decision = router.resolve("tenant-1", "corr-1", "id", operation)
            self.assertTrue(decision["manual_fallback"], f"{operation} must not be claimed automated for srsx")

    def test_unsupported_id_like_extension_is_not_suffix_matched(self):
        # "android" ends with "id" but must not match the .id capability.
        router = _router_with_srsx_only()
        decision = router.resolve("tenant-1", "corr-1", "android", "registration")
        self.assertTrue(decision["manual_fallback"])


class TestSrsxPreflight(unittest.TestCase):
    def test_preflight_passes_with_complete_config(self):
        result = preflight.run_srsx_preflight("tenant-acme", "corr-1", _config(), "2026-09-19T00:00:00Z")
        self.assertTrue(result["ok"])

    def test_preflight_fails_on_missing_field(self):
        config = _config()
        del config["authorized_egress_ip"]
        result = preflight.run_srsx_preflight("tenant-acme", "corr-1", config, "t")
        self.assertFalse(result["ok"])

    def test_preflight_fails_on_malformed_ip(self):
        result = preflight.run_srsx_preflight("tenant-acme", "corr-1", _config(authorized_egress_ip="not-an-ip"), "t")
        self.assertFalse(result["ok"])

    def test_preflight_fails_when_password_reference_is_raw(self):
        result = preflight.run_srsx_preflight("tenant-acme", "corr-1", _config(password_reference="raw-value"), "t")
        self.assertFalse(result["ok"])
        ref_check = next(c for c in result["checks"] if c["name"] == "password_reference_resolves")
        self.assertFalse(ref_check["ok"])


class TestSrsxDocumentAndRegistrationFlow(unittest.TestCase):
    def test_registration_then_renewal(self):
        reg = _registrar()
        snapshot = reg.price_book.snapshot("id")
        response = reg.register(domain="example.id", idempotency_key="idem-a01", price_snapshot_id=snapshot["snapshot_id"])
        order_id = response["order_id"]
        reg.request_document_upload(order_id, now_tick=0)
        reg.submit_documents(order_id, now_tick=1)
        result = reg.review_documents(order_id, approve=True)
        self.assertEqual(result["registration_status"], "succeeded")

        renew_snapshot = reg.price_book.snapshot("id", operation="renewal")
        renewal = reg.renew(domain="example.id", idempotency_key="idem-a02", price_snapshot_id=renew_snapshot["snapshot_id"])
        self.assertEqual(renewal["status"], "succeeded")

    def test_api_failure_is_retryable_and_does_not_consume_idempotency_key(self):
        reg = _registrar()
        snapshot = reg.price_book.snapshot("id")
        with self.assertRaises(fake_provider.TransportError):
            reg.register(domain="example2.id", idempotency_key="idem-a03", price_snapshot_id=snapshot["snapshot_id"], simulate_api_failure=True)
        # A retry with the SAME idempotency_key must succeed, not be
        # rejected as a duplicate - the failed attempt was never
        # persisted as an order.
        response = reg.register(domain="example2.id", idempotency_key="idem-a03", price_snapshot_id=snapshot["snapshot_id"])
        self.assertEqual(response["status"], "action_required")

    def test_duplicate_request_is_rejected(self):
        reg = _registrar()
        snapshot = reg.price_book.snapshot("id")
        reg.register(domain="example3.id", idempotency_key="idem-a04", price_snapshot_id=snapshot["snapshot_id"])
        with self.assertRaises(fake_provider.DuplicateRequestError):
            reg.register(domain="example3.id", idempotency_key="idem-a04", price_snapshot_id=snapshot["snapshot_id"])

    def test_document_url_expiry_rejects_late_submission(self):
        reg = _registrar()
        snapshot = reg.price_book.snapshot("id")
        response = reg.register(domain="example4.id", idempotency_key="idem-a05", price_snapshot_id=snapshot["snapshot_id"])
        order_id = response["order_id"]
        reg.request_document_upload(order_id, expires_in_ticks=10, now_tick=0)
        with self.assertRaises(fake_provider.ProviderError):
            reg.submit_documents(order_id, now_tick=11)  # past the 10-tick TTL

    def test_document_rejection_routes_to_action_required(self):
        reg = _registrar()
        snapshot = reg.price_book.snapshot("id")
        response = reg.register(domain="example5.id", idempotency_key="idem-a06", price_snapshot_id=snapshot["snapshot_id"])
        order_id = response["order_id"]
        reg.request_document_upload(order_id, now_tick=0)
        reg.submit_documents(order_id, now_tick=1)
        result = reg.review_documents(order_id, approve=False)
        self.assertEqual(result["lifecycle"], "rejected")
        self.assertEqual(result["registration_status"], "action_required")

    def test_credential_redaction(self):
        evidence = {"api_username": "acme-reseller", "password_reference": {"store": "vault", "key": "srsx/api-password"}}
        redacted = fake_provider.redact(evidence)
        # password_reference's VALUE is a dict, not a scalar secret, but
        # the field NAME still matches the secret-like pattern, so
        # redact() replaces the whole value defensively.
        self.assertEqual(redacted["password_reference"], "[REDACTED]")
        self.assertEqual(redacted["api_username"], "acme-reseller")


class TestRetryClassification(unittest.TestCase):
    def test_transport_error_is_retryable(self):
        result = retry.classify_exception(fake_provider.TransportError("x"))
        self.assertTrue(result["retryable"])

    def test_duplicate_and_provider_errors_are_not_retryable(self):
        self.assertFalse(retry.classify_exception(fake_provider.DuplicateRequestError("x"))["retryable"])
        self.assertFalse(retry.classify_exception(fake_provider.ProviderError("x"))["retryable"])

    def test_srsx_success_code(self):
        result = retry.classify_srsx_result_code(srsx.API_RESULT_CODE_SUCCESS, document_pending=False)
        self.assertEqual(result["code"], "succeeded")
        self.assertFalse(result["retryable"])

    def test_srsx_ambiguous_1001_is_never_retryable_even_when_pending(self):
        pending = retry.classify_srsx_result_code(srsx.API_RESULT_CODE_FAILED_OR_PENDING, document_pending=True)
        failed = retry.classify_srsx_result_code(srsx.API_RESULT_CODE_FAILED_OR_PENDING, document_pending=False)
        self.assertFalse(pending["retryable"])
        self.assertFalse(failed["retryable"])
        self.assertNotEqual(pending["code"], failed["code"])


if __name__ == "__main__":
    unittest.main()
