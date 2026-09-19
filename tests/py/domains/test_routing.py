import unittest

from . import _pathfix  # noqa: F401

from domains import routing


class TestRouting(unittest.TestCase):
    def test_cloudflare_capability_matches_supported_tld(self):
        router = routing.default_router()
        decision = router.resolve("tenant-1", "corr-1", "com", "registration")
        self.assertEqual(decision["provider"], "cloudflare")
        self.assertFalse(decision["manual_fallback"])
        self.assertEqual(decision["capability_id"], "cap-cloudflare-intl-01")

    def test_srsx_capability_matches_id_tld(self):
        router = routing.default_router()
        decision = router.resolve("tenant-1", "corr-1", "id", "registration")
        self.assertEqual(decision["provider"], "srsx")
        self.assertFalse(decision["manual_fallback"])

    def test_unsupported_tld_routes_to_manual_fallback(self):
        router = routing.default_router()
        decision = router.resolve("tenant-1", "corr-1", "xyz", "registration")
        self.assertEqual(decision["provider"], "manual")
        self.assertTrue(decision["manual_fallback"])
        self.assertIn("reason", decision)

    def test_operation_not_supported_by_capability_routes_to_manual(self):
        # Cloudflare capability in default_router() does not list
        # "transfer" as a supported operation.
        router = routing.default_router()
        decision = router.resolve("tenant-1", "corr-1", "com", "transfer")
        self.assertTrue(decision["manual_fallback"])

    def test_suffix_only_matching_is_insufficient_co_id_vs_id(self):
        # "co.id" and "id" are distinct capabilities/extensions, not a
        # bare suffix match - a naive `tld.endswith("id")` would
        # incorrectly match "avoid" or "android".
        cap = routing.Capability.from_dict(
            {
                "capability_id": "cap-test",
                "provider": "srsx",
                "extension_pattern": r"^id$",
                "account_scope": "acct-1",
                "supported_operations": ["registration"],
            }
        )
        self.assertTrue(cap.matches("id", "registration"))
        self.assertFalse(cap.matches("android", "registration"))

    def test_manual_fallback_capability_never_matches(self):
        router = routing.Router()
        router.register(
            routing.Capability(
                capability_id="cap-manual",
                provider="manual",
                extension_pattern=r"^.*$",
                account_scope="none",
                supported_operations=("registration",),
                manual_fallback=True,
            )
        )
        decision = router.resolve("tenant-1", "corr-1", "com", "registration")
        self.assertTrue(decision["manual_fallback"])


if __name__ == "__main__":
    unittest.main()
