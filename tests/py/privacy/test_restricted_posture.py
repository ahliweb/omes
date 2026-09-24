"""Unit tests for lib/omes/py/privacy/restricted_posture.py, the
restricted/local-only inference deployment posture check (issue #215).

These tests exercise endpoint-locality classification and the resulting
posture decision. They deliberately do not re-test the decision matrix
itself (that is lib/omes/py/privacy/egress_policy.py's own
responsibility, covered by tests/py/privacy/test_egress_policy.py) -
these tests instead prove that this module correctly DERIVES the bounded
`destination` value it hands to that evaluator, and that a
positive/negative endpoint always maps to the expected allow/deny.
"""
import unittest

from . import _pathfix  # noqa: F401  (sets sys.path)

from privacy import restricted_posture  # noqa: E402


class TestClassifyEndpoint(unittest.TestCase):
    def test_unset_endpoint_fails_closed(self):
        self.assertEqual(restricted_posture.classify_endpoint(None), "unset")
        self.assertEqual(restricted_posture.classify_endpoint(""), "unset")
        self.assertEqual(restricted_posture.classify_endpoint("   "), "unset")

    def test_non_string_fails_closed(self):
        self.assertEqual(restricted_posture.classify_endpoint(123), "unset")

    def test_loopback_ip_literal_is_local(self):
        self.assertEqual(restricted_posture.classify_endpoint("http://127.0.0.1:11434"), "local_only")
        self.assertEqual(restricted_posture.classify_endpoint("127.0.0.1:11434"), "local_only")

    def test_ipv6_loopback_is_local(self):
        self.assertEqual(restricted_posture.classify_endpoint("http://[::1]:11434"), "local_only")

    def test_localhost_hostname_is_local(self):
        self.assertEqual(restricted_posture.classify_endpoint("http://localhost:8000"), "local_only")
        self.assertEqual(restricted_posture.classify_endpoint("http://foo.localhost:8000"), "local_only")

    def test_rfc1918_private_ip_is_private_endpoint(self):
        self.assertEqual(restricted_posture.classify_endpoint("http://10.0.5.5:8000"), "private_endpoint")
        self.assertEqual(restricted_posture.classify_endpoint("http://192.168.1.20:8000"), "private_endpoint")
        self.assertEqual(restricted_posture.classify_endpoint("http://172.16.0.4:8000"), "private_endpoint")

    def test_link_local_ip_is_private_endpoint(self):
        self.assertEqual(restricted_posture.classify_endpoint("http://169.254.1.1:8000"), "private_endpoint")

    def test_public_ip_literal_is_public(self):
        self.assertEqual(restricted_posture.classify_endpoint("http://8.8.8.8"), "public")

    def test_arbitrary_hostname_is_unresolvable_without_dns(self):
        # A bare hostname that is not "localhost" and not an IP literal
        # cannot be classified without a DNS query, which this read-only
        # preflight module never performs - it must fail closed rather
        # than guess.
        self.assertEqual(restricted_posture.classify_endpoint("https://api.openai.com/v1"), "unresolvable_hostname")
        self.assertEqual(restricted_posture.classify_endpoint("my-ollama-box.internal:8000"), "unresolvable_hostname")

    def test_unparsable_value_fails_closed(self):
        self.assertEqual(restricted_posture.classify_endpoint("http://"), "unparsable")


class TestResolveDestination(unittest.TestCase):
    def test_local_only_maps_to_local_only(self):
        self.assertEqual(restricted_posture.resolve_destination("local_only"), "local_only")

    def test_private_endpoint_maps_to_private_endpoint(self):
        self.assertEqual(restricted_posture.resolve_destination("private_endpoint"), "private_endpoint")

    def test_everything_else_fails_closed_to_cloud_sanitized(self):
        for bucket in ("public", "unset", "unparsable", "unresolvable_hostname", "some_future_value"):
            self.assertEqual(restricted_posture.resolve_destination(bucket), "cloud_sanitized")


class TestEvaluateRestrictedPosture(unittest.TestCase):
    def test_local_loopback_endpoint_is_allowed(self):
        result = restricted_posture.evaluate_restricted_posture({"base_url": "http://127.0.0.1:11434"})
        self.assertTrue(result["pass"])
        self.assertEqual(result["decision"], "allow")
        self.assertEqual(result["destination"], "local_only")
        self.assertIn("AI_EGRESS_ALLOW_LOCAL_ONLY", result["reason_codes"])

    def test_missing_endpoint_is_denied_not_assumed_local(self):
        result = restricted_posture.evaluate_restricted_posture({})
        self.assertFalse(result["pass"])
        self.assertEqual(result["decision"], "deny")
        self.assertEqual(result["destination"], "cloud_sanitized")
        self.assertIn("AI_EGRESS_DENY_RESTRICTED_CLOUD_SANITIZED", result["reason_codes"])

    def test_cloud_endpoint_is_denied(self):
        result = restricted_posture.evaluate_restricted_posture({"base_url": "https://api.openai.com/v1"})
        self.assertFalse(result["pass"])
        self.assertEqual(result["decision"], "deny")

    def test_public_ip_endpoint_is_denied(self):
        result = restricted_posture.evaluate_restricted_posture({"base_url": "http://8.8.8.8:11434"})
        self.assertFalse(result["pass"])
        self.assertEqual(result["decision"], "deny")

    def test_private_endpoint_without_operator_approval_is_denied(self):
        result = restricted_posture.evaluate_restricted_posture({"base_url": "http://10.0.5.5:8000"})
        self.assertFalse(result["pass"])
        self.assertEqual(result["decision"], "deny")
        self.assertIn("AI_EGRESS_DENY_RESTRICTED_PROVIDER_NOT_APPROVED", result["reason_codes"])

    def test_private_endpoint_with_operator_approval_still_requires_human_approval_not_auto_allow(self):
        # RESTRICTED + private_endpoint + approved provider posture is
        # "approval_required" in the decision matrix, never a silent
        # "allow" - OMES must not auto-approve.
        result = restricted_posture.evaluate_restricted_posture(
            {"base_url": "http://10.0.5.5:8000", "private_endpoint_approved": True}
        )
        self.assertFalse(result["pass"])
        self.assertEqual(result["decision"], "approval_required")
        self.assertIn("AI_EGRESS_APPROVAL_REQUIRED_RESTRICTED_PRIVATE_ENDPOINT", result["reason_codes"])

    def test_never_sends_authentication_material_flag_true(self):
        # This is a posture/configuration check, not a real data
        # transmission - contains_authentication_material must always be
        # asserted False by this module (there is no content).
        result = restricted_posture.evaluate_restricted_posture({"base_url": "http://127.0.0.1:11434"})
        self.assertNotIn("AI_EGRESS_DENY_AUTHENTICATION_MATERIAL", result["reason_codes"])


class TestMain(unittest.TestCase):
    def test_exit_code_allow_is_zero(self):
        import io
        import json as json_mod
        import sys

        stdin = io.StringIO(json_mod.dumps({"base_url": "http://127.0.0.1:11434"}))
        old_stdin = sys.stdin
        sys.stdin = stdin
        try:
            rc = restricted_posture.main([])
        finally:
            sys.stdin = old_stdin
        self.assertEqual(rc, restricted_posture.EXIT_OK_ALLOW)

    def test_exit_code_deny_is_not_allowed(self):
        import io
        import json as json_mod
        import sys

        stdin = io.StringIO(json_mod.dumps({}))
        old_stdin = sys.stdin
        sys.stdin = stdin
        try:
            rc = restricted_posture.main([])
        finally:
            sys.stdin = old_stdin
        self.assertEqual(rc, restricted_posture.EXIT_NOT_ALLOWED)

    def test_invalid_json_is_an_internal_error(self):
        import io
        import sys

        stdin = io.StringIO("not json")
        old_stdin = sys.stdin
        sys.stdin = stdin
        try:
            rc = restricted_posture.main([])
        finally:
            sys.stdin = old_stdin
        self.assertEqual(rc, restricted_posture.EXIT_ERROR)


if __name__ == "__main__":
    unittest.main()
