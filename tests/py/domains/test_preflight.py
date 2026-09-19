import unittest

from . import _pathfix  # noqa: F401

from domains import preflight
from domains.profiles import cloudflare


def _credential(**overrides):
    base = {
        "reference_id": "cred-cf-scoped-01",
        "provider": "cloudflare",
        "kind": "api_token",
        "reference": {"store": "vault", "key": "cloudflare/registrar/scoped-token"},
        "scopes": list(cloudflare.REQUIRED_TOKEN_SCOPES),
    }
    base.update(overrides)
    return base


class TestPreflight(unittest.TestCase):
    def test_preflight_passes_with_correct_provider_and_scopes(self):
        result = preflight.run_preflight(
            "tenant-acme", "corr-1", "cloudflare", _credential(), cloudflare.REQUIRED_TOKEN_SCOPES, "2026-09-19T00:00:00Z"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["checks"]), 3)

    def test_preflight_fails_on_provider_mismatch(self):
        result = preflight.run_preflight(
            "tenant-acme", "corr-1", "cloudflare", _credential(provider="srsx"), cloudflare.REQUIRED_TOKEN_SCOPES, "t"
        )
        self.assertFalse(result["ok"])

    def test_preflight_fails_on_missing_scope(self):
        result = preflight.run_preflight(
            "tenant-acme", "corr-1", "cloudflare", _credential(scopes=["registrar:read"]), cloudflare.REQUIRED_TOKEN_SCOPES, "t"
        )
        self.assertFalse(result["ok"])
        scope_check = next(c for c in result["checks"] if c["name"] == "token_scope_sufficient")
        self.assertIn("registrar:write", scope_check["detail"])

    def test_preflight_never_inspects_raw_reference_value(self):
        # A malformed credential whose "reference" is a raw string (which
        # a real caller should never construct - the contract forbids it)
        # must still not crash preflight, and must fail the
        # credential_reference_resolves check rather than reading through it.
        result = preflight.run_preflight(
            "tenant-acme", "corr-1", "cloudflare", _credential(reference="not-a-secret-ref"), cloudflare.REQUIRED_TOKEN_SCOPES, "t"
        )
        self.assertFalse(result["ok"])
        ref_check = next(c for c in result["checks"] if c["name"] == "credential_reference_resolves")
        self.assertFalse(ref_check["ok"])


if __name__ == "__main__":
    unittest.main()
