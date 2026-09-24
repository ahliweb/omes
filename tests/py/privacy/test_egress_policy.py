"""Unit tests for lib/omes/py/privacy/egress_policy.py, the deterministic
AI data-classification and model-egress policy evaluator (issue #214,
ADR-0029).

These tests exercise the evaluator directly with plain dict metadata -
they never construct or pass prompt text, response text, embeddings, or a
real credential value, matching the metadata-only contract the evaluator
itself enforces.
"""
import unittest

from . import _pathfix  # noqa: F401  (sets sys.path)

from privacy import egress_policy  # noqa: E402


def _req(**overrides):
    base = {
        "policy_version": "v1",
        "classification": "PUBLIC",
        "destination": "local_only",
        "purpose": "code_generation",
        "provider_posture": {"status": "approved"},
        "contains_authentication_material": False,
    }
    base.update(overrides)
    return base


class TestFailClosedOnUnknownOrMissingValues(unittest.TestCase):
    def test_unknown_classification_fails_closed(self):
        result = egress_policy.evaluate(_req(classification="TOP_SECRET"))
        self.assertEqual(result["decision"], "deny")
        self.assertIn("AI_EGRESS_DENY_UNKNOWN_CLASSIFICATION", result["reason_codes"])

    def test_missing_classification_fails_closed(self):
        req = _req()
        del req["classification"]
        result = egress_policy.evaluate(req)
        self.assertEqual(result["decision"], "deny")
        self.assertIn("AI_EGRESS_DENY_UNKNOWN_CLASSIFICATION", result["reason_codes"])

    def test_unknown_destination_fails_closed(self):
        result = egress_policy.evaluate(_req(destination="email_attachment"))
        self.assertEqual(result["decision"], "deny")
        self.assertIn("AI_EGRESS_DENY_UNKNOWN_DESTINATION", result["reason_codes"])

    def test_missing_destination_fails_closed(self):
        req = _req()
        del req["destination"]
        result = egress_policy.evaluate(req)
        self.assertEqual(result["decision"], "deny")
        self.assertIn("AI_EGRESS_DENY_UNKNOWN_DESTINATION", result["reason_codes"])

    def test_unknown_policy_version_fails_closed(self):
        result = egress_policy.evaluate(_req(policy_version="v99-does-not-exist"))
        self.assertEqual(result["decision"], "deny")
        self.assertIn("AI_EGRESS_DENY_UNKNOWN_POLICY_VERSION", result["reason_codes"])

    def test_missing_policy_version_fails_closed(self):
        req = _req()
        del req["policy_version"]
        result = egress_policy.evaluate(req)
        self.assertEqual(result["decision"], "deny")
        self.assertIn("AI_EGRESS_DENY_UNKNOWN_POLICY_VERSION", result["reason_codes"])

    def test_missing_provider_posture_fails_closed_for_cloud_sanitized(self):
        req = _req(classification="INTERNAL", destination="cloud_sanitized")
        del req["provider_posture"]
        result = egress_policy.evaluate(req)
        self.assertEqual(result["decision"], "deny")
        self.assertIn("AI_EGRESS_DENY_UNKNOWN_PROVIDER_POSTURE", result["reason_codes"])

    def test_explicit_unknown_provider_posture_fails_closed(self):
        result = egress_policy.evaluate(
            _req(classification="PUBLIC", destination="private_endpoint", provider_posture={"status": "unknown"})
        )
        self.assertEqual(result["decision"], "deny")
        self.assertIn("AI_EGRESS_DENY_UNKNOWN_PROVIDER_POSTURE", result["reason_codes"])

    def test_malformed_provider_posture_fails_closed(self):
        result = egress_policy.evaluate(
            _req(classification="PUBLIC", destination="private_endpoint", provider_posture="approved")
        )
        self.assertEqual(result["decision"], "deny")
        self.assertIn("AI_EGRESS_DENY_UNKNOWN_PROVIDER_POSTURE", result["reason_codes"])

    def test_evaluate_never_raises_on_garbage_input(self):
        for garbage in (None, {}, [], "not-a-dict", 42, {"policy_version": None}):
            result = egress_policy.evaluate(garbage)  # type: ignore[arg-type]
            self.assertEqual(result["decision"], "deny")

    def test_never_fails_open_when_any_fail_closed_field_is_missing(self):
        # Removing any of the fields this module documents as fail-closed
        # triggers (policy_version, classification, destination, provider
        # posture for a non-local destination, authentication-material
        # flag for a non-local destination) must never turn into an
        # allow/approval_required decision.
        full = _req(classification="PUBLIC", destination="cloud_sanitized", provider_posture={"status": "approved"})
        fail_closed_fields = (
            "policy_version",
            "classification",
            "destination",
            "provider_posture",
            "contains_authentication_material",
        )
        for key in fail_closed_fields:
            req = dict(full)
            del req[key]
            result = egress_policy.evaluate(req)
            self.assertEqual(
                result["decision"],
                "deny",
                f"removing {key!r} must not produce a non-deny decision, got {result}",
            )


class TestDestinationDeny(unittest.TestCase):
    def test_destination_deny_always_denies_regardless_of_classification(self):
        for classification in egress_policy.CLASSIFICATIONS:
            result = egress_policy.evaluate(_req(classification=classification, destination="deny"))
            self.assertEqual(result["decision"], "deny")
            self.assertIn("AI_EGRESS_DENY_DESTINATION_EXPLICITLY_DENIED", result["reason_codes"])


class TestRestrictedData(unittest.TestCase):
    def test_restricted_to_cloud_sanitized_is_denied(self):
        result = egress_policy.evaluate(
            _req(classification="RESTRICTED", destination="cloud_sanitized", provider_posture={"status": "approved"})
        )
        self.assertEqual(result["decision"], "deny")
        self.assertIn("AI_EGRESS_DENY_RESTRICTED_CLOUD_SANITIZED", result["reason_codes"])

    def test_restricted_to_cloud_sanitized_is_denied_even_when_provider_approved_and_sanitized(self):
        # RESTRICTED -> cloud_sanitized must be an unconditional deny: an
        # approved provider and sanitization evidence must not flip it.
        result = egress_policy.evaluate(
            _req(
                classification="RESTRICTED",
                destination="cloud_sanitized",
                provider_posture={"status": "approved"},
                sanitization_evidence={"method": "tokenization", "evidence_id": "ev-1"},
            )
        )
        self.assertEqual(result["decision"], "deny")

    def test_restricted_local_only_is_allowed(self):
        result = egress_policy.evaluate(_req(classification="RESTRICTED", destination="local_only"))
        self.assertEqual(result["decision"], "allow")
        self.assertIn("AI_EGRESS_ALLOW_LOCAL_ONLY", result["reason_codes"])

    def test_restricted_private_endpoint_requires_approval_when_provider_approved(self):
        result = egress_policy.evaluate(
            _req(classification="RESTRICTED", destination="private_endpoint", provider_posture={"status": "approved"})
        )
        self.assertEqual(result["decision"], "approval_required")

    def test_restricted_private_endpoint_denied_when_provider_not_approved(self):
        result = egress_policy.evaluate(
            _req(
                classification="RESTRICTED",
                destination="private_endpoint",
                provider_posture={"status": "not_approved"},
            )
        )
        self.assertEqual(result["decision"], "deny")


class TestAuthenticationMaterialAlwaysDenied(unittest.TestCase):
    def test_credential_material_denied_for_cloud_sanitized_regardless_of_classification(self):
        for classification in egress_policy.CLASSIFICATIONS:
            result = egress_policy.evaluate(
                _req(
                    classification=classification,
                    destination="cloud_sanitized",
                    provider_posture={"status": "approved"},
                    contains_authentication_material=True,
                    sanitization_evidence={"method": "redaction", "evidence_id": "ev-1"},
                )
            )
            self.assertEqual(result["decision"], "deny", classification)
            self.assertIn("AI_EGRESS_DENY_AUTHENTICATION_MATERIAL", result["reason_codes"])

    def test_credential_material_denied_for_private_endpoint_regardless_of_classification(self):
        for classification in egress_policy.CLASSIFICATIONS:
            result = egress_policy.evaluate(
                _req(
                    classification=classification,
                    destination="private_endpoint",
                    provider_posture={"status": "approved"},
                    contains_authentication_material=True,
                )
            )
            self.assertEqual(result["decision"], "deny", classification)
            self.assertIn("AI_EGRESS_DENY_AUTHENTICATION_MATERIAL", result["reason_codes"])

    def test_credential_material_flag_missing_is_treated_as_true(self):
        req = _req(classification="PUBLIC", destination="cloud_sanitized", provider_posture={"status": "approved"})
        del req["contains_authentication_material"]
        result = egress_policy.evaluate(req)
        self.assertEqual(result["decision"], "deny")
        self.assertIn("AI_EGRESS_DENY_AUTHENTICATION_MATERIAL", result["reason_codes"])

    def test_credential_material_does_not_block_local_only(self):
        # local_only means no model request leaves the host, so the
        # authentication-material flag does not need to block it.
        result = egress_policy.evaluate(
            _req(classification="PUBLIC", destination="local_only", contains_authentication_material=True)
        )
        self.assertEqual(result["decision"], "allow")


class TestDecisionMatrix(unittest.TestCase):
    """Mirrors docs/ai-data-privacy-and-model-security.md section 6."""

    def test_public_allowed_everywhere_when_provider_approved(self):
        approved = {"status": "approved"}
        for destination in ("local_only", "private_endpoint", "cloud_sanitized"):
            result = egress_policy.evaluate(_req(classification="PUBLIC", destination=destination, provider_posture=approved))
            self.assertEqual(result["decision"], "allow", destination)

    def test_public_cloud_sanitized_denied_when_provider_not_approved(self):
        result = egress_policy.evaluate(
            _req(classification="PUBLIC", destination="cloud_sanitized", provider_posture={"status": "not_approved"})
        )
        self.assertEqual(result["decision"], "deny")

    def test_internal_cloud_sanitized_requires_minimization_and_approval(self):
        approved = {"status": "approved"}
        # No sanitization evidence -> deny even with an approved provider.
        result = egress_policy.evaluate(_req(classification="INTERNAL", destination="cloud_sanitized", provider_posture=approved))
        self.assertEqual(result["decision"], "deny")
        self.assertIn("AI_EGRESS_DENY_MISSING_SANITIZATION_EVIDENCE", result["reason_codes"])

        # Sanitization evidence + approved provider -> allow.
        result = egress_policy.evaluate(
            _req(
                classification="INTERNAL",
                destination="cloud_sanitized",
                provider_posture=approved,
                sanitization_evidence={"method": "aggregation", "evidence_id": "ev-1"},
            )
        )
        self.assertEqual(result["decision"], "allow")

    def test_confidential_cloud_sanitized_is_deny_by_default(self):
        result = egress_policy.evaluate(
            _req(classification="CONFIDENTIAL", destination="cloud_sanitized", provider_posture={"status": "approved"})
        )
        self.assertEqual(result["decision"], "deny")
        self.assertIn("AI_EGRESS_DENY_MISSING_SANITIZATION_EVIDENCE", result["reason_codes"])

    def test_confidential_cloud_sanitized_with_evidence_requires_approval_not_silent_allow(self):
        result = egress_policy.evaluate(
            _req(
                classification="CONFIDENTIAL",
                destination="cloud_sanitized",
                provider_posture={"status": "approved"},
                sanitization_evidence={"method": "tokenization", "evidence_id": "ev-1"},
            )
        )
        # Per ADR-0029, a "deny by default" class must never silently
        # become an allow just because evidence and provider approval are
        # both present - it becomes a human approval step.
        self.assertEqual(result["decision"], "approval_required")

    def test_confidential_private_endpoint_without_explicit_policy_requires_approval(self):
        result = egress_policy.evaluate(
            _req(classification="CONFIDENTIAL", destination="private_endpoint", provider_posture={"status": "not_approved"})
        )
        self.assertEqual(result["decision"], "approval_required")


class TestOutputIsBounded(unittest.TestCase):
    def test_decision_is_always_one_of_the_closed_set(self):
        cases = [
            _req(),
            _req(classification="RESTRICTED", destination="cloud_sanitized"),
            _req(classification="CONFIDENTIAL", destination="private_endpoint", provider_posture={"status": "not_approved"}),
            {"garbage": True},
        ]
        for case in cases:
            result = egress_policy.evaluate(case)
            self.assertIn(result["decision"], egress_policy.DECISIONS)

    def test_reason_codes_are_always_from_the_closed_vocabulary(self):
        cases = [
            _req(),
            _req(classification="TOP_SECRET"),
            _req(destination="nowhere"),
            _req(classification="RESTRICTED", destination="cloud_sanitized"),
        ]
        for case in cases:
            result = egress_policy.evaluate(case)
            self.assertTrue(result["reason_codes"], result)
            for code in result["reason_codes"]:
                self.assertIn(code, egress_policy.REASON_CODES)

    def test_response_never_contains_executable_looking_fields(self):
        result = egress_policy.evaluate(_req())
        forbidden_keys = {"command", "cmd", "shell", "path", "argv", "exec"}
        self.assertFalse(forbidden_keys & set(result.keys()))

    def test_response_echoes_tenant_and_resource_scope_when_present(self):
        result = egress_policy.evaluate(_req(tenant_id="tenant-001", resource_scope="server-001"))
        self.assertEqual(result["tenant_id"], "tenant-001")
        self.assertEqual(result["resource_scope"], "server-001")

    def test_response_omits_tenant_and_resource_scope_when_absent(self):
        result = egress_policy.evaluate(_req())
        self.assertNotIn("tenant_id", result)
        self.assertNotIn("resource_scope", result)


if __name__ == "__main__":
    unittest.main()
