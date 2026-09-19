"""Issue #99 acceptance-criteria tests against the fake provider, wired
through the Cloudflare capability profile (lib/omes/py/domains/profiles/
cloudflare.py) rather than ad hoc test data, plus the routing and
redaction behavior specific to this profile."""
import unittest

from . import _pathfix  # noqa: F401

from domains import fake_provider, routing
from domains.profiles import cloudflare


def _router_with_cloudflare_only() -> routing.Router:
    router = routing.Router()
    router.register(routing.Capability.from_dict(cloudflare.REGISTRAR_CAPABILITY))
    router.register(routing.Capability.from_dict(cloudflare.DNS_CAPABILITY))
    return router


def _registrar() -> fake_provider.FakeRegistrar:
    reg = fake_provider.FakeRegistrar("cloudflare", mode="async", supported_tlds=cloudflare.REGISTRAR_TLDS, async_steps=2)
    reg.price_book.set_price("com", "cloudflare", 899, 1499)
    return reg


class TestCloudflareRoutingProfile(unittest.TestCase):
    def test_supported_extension_routes_to_cloudflare(self):
        router = _router_with_cloudflare_only()
        decision = router.resolve("tenant-1", "corr-1", "com", "registration")
        self.assertEqual(decision["provider"], "cloudflare")
        self.assertFalse(decision["manual_fallback"])

    def test_unsupported_extension_routes_to_manual_fallback(self):
        # ".xyz" is not in cloudflare.REGISTRAR_TLDS (the conservative,
        # illustrative subset) - issue #99: "explicitly return/manual-route
        # unsupported API extensions; do not assume dashboard support
        # equals API support".
        router = _router_with_cloudflare_only()
        decision = router.resolve("tenant-1", "corr-1", "xyz", "registration")
        self.assertTrue(decision["manual_fallback"])

    def test_renewal_and_transfer_are_not_claimed_supported(self):
        # Cloudflare's own docs (see profile module docstring) do not yet
        # offer renewal/transfer/contact-update via the API.
        router = _router_with_cloudflare_only()
        for operation in ("renewal", "transfer", "contact_update"):
            decision = router.resolve("tenant-1", "corr-1", "com", operation)
            self.assertTrue(decision["manual_fallback"], f"{operation} must not be claimed automated")

    def test_dns_capability_is_separate_from_registrar_capability(self):
        router = _router_with_cloudflare_only()
        dns_decision = router.resolve("tenant-1", "corr-1", "com", "dns_records")
        self.assertEqual(dns_decision["capability_id"], "cap-cloudflare-dns-v1")
        registrar_decision = router.resolve("tenant-1", "corr-1", "com", "registration")
        self.assertEqual(registrar_decision["capability_id"], "cap-cloudflare-registrar-v1")


class TestCloudflareAsyncRegistrationAcceptance(unittest.TestCase):
    """Issue #99: "Add fake-provider contract tests, async polling tests,
    duplicate job tests, price-change tests, unsupported-TLD tests, DNS
    drift tests, and secret-redaction tests." """

    def test_async_polling_reaches_terminal_state(self):
        reg = _registrar()
        snapshot = reg.price_book.snapshot("com")
        response = reg.register(domain="example.com", idempotency_key="idem-cf-poll-001", price_snapshot_id=snapshot["snapshot_id"])
        self.assertEqual(response["status"], "in_progress")
        for _ in range(reg.async_steps):
            polled = reg.poll_registration(response["order_id"])
        self.assertEqual(polled["status"], "succeeded")

    def test_duplicate_job_is_rejected_not_double_registered(self):
        reg = _registrar()
        snapshot = reg.price_book.snapshot("com")
        reg.register(domain="example.com", idempotency_key="idem-cf-dup-001", price_snapshot_id=snapshot["snapshot_id"])
        with self.assertRaises(fake_provider.DuplicateRequestError):
            reg.register(domain="example.com", idempotency_key="idem-cf-dup-001", price_snapshot_id=snapshot["snapshot_id"])

    def test_price_change_between_snapshot_and_checkout(self):
        reg = _registrar()
        snapshot_at_search = reg.price_book.snapshot("com")
        reg.price_book.set_price("com", "cloudflare", 949, 1599)
        snapshot_at_checkout = reg.price_book.snapshot("com")
        self.assertNotEqual(snapshot_at_search["snapshot_id"], snapshot_at_checkout["snapshot_id"])

    def test_unsupported_tld_via_fake_provider_raises(self):
        reg = _registrar()
        with self.assertRaises(fake_provider.ProviderError):
            reg.check_availability("example.xyz")

    def test_dns_drift_detected_for_cloudflare_zone(self):
        reg = _registrar()
        desired = [{"zone_id": "zone-cf-1", "record_id": "rec-1", "type": "A", "name": "example.com", "content": "203.0.113.10", "ttl": 3600, "managed_by": "omes"}]
        reg.upsert_dns_record("zone-cf-1", dict(desired[0], content="203.0.113.55"))
        drift = reg.detect_drift("zone-cf-1", desired)
        self.assertEqual(len(drift), 1)

    def test_secret_redaction_on_credential_evidence(self):
        evidence = {"provider": "cloudflare", "api_token": "should-never-appear", "order_id": "order-0001"}
        redacted = fake_provider.redact(evidence)
        self.assertEqual(redacted["api_token"], "[REDACTED]")
        self.assertEqual(redacted["order_id"], "order-0001")


if __name__ == "__main__":
    unittest.main()
