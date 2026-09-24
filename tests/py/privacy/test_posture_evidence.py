"""Unit tests for lib/omes/py/privacy/posture_evidence.py, the deterministic
AI privacy posture/egress evidence evaluator (issue #216, ADR-0029).

These tests exercise the evaluator directly with plain dict "observation"
metadata. Several tests are deliberately adversarial: they feed synthetic
canary values (fake secrets, fake prompt text) through the evaluation
path - both as unexpected extra keys and as values placed in the small
set of fields this module DOES echo back - and assert those canaries
never appear anywhere in the resulting evidence object. A test that only
exercises the happy path would not prove the "no raw prompt/secret
capture" requirement; these do.
"""
import json
import unittest

from . import _pathfix  # noqa: F401  (sets sys.path)

from privacy import posture_evidence as pe  # noqa: E402
from jobs import schema as schema_mod  # noqa: E402


def _obs(**overrides):
    base = {
        "policy_version": "v1",
        "destination_class": "local",
        "local_endpoint": {"classification": "loopback"},
        "cloud_fallback_enabled": False,
        "network_isolation_active": True,
        "expected_posture": "restricted_local_only",
        "evidence_source": "hermes-cli:hermes config get",
        "observed_at": "2026-09-24T12:00:00Z",
        "evidence_age_seconds": 5,
        "local_only_posture_source": {
            "available": True,
            "status": "pass",
            "source": "local-only-posture-source:issue-215",
        },
    }
    base.update(overrides)
    return base


class TestFailClosedOnUnknownOrMissingValues(unittest.TestCase):
    def test_unknown_policy_version_is_blocked(self):
        result = pe.evaluate(_obs(policy_version="v99-does-not-exist"))
        self.assertEqual(result["status"], pe.STATUS_BLOCKED)
        self.assertIn("AI_PRIVACY_POSTURE_BLOCKED_UNKNOWN_POLICY_VERSION", result["reason_codes"])

    def test_missing_policy_version_is_blocked(self):
        obs = _obs()
        del obs["policy_version"]
        result = pe.evaluate(obs)
        self.assertEqual(result["status"], pe.STATUS_BLOCKED)
        self.assertIn("AI_PRIVACY_POSTURE_BLOCKED_UNKNOWN_POLICY_VERSION", result["reason_codes"])

    def test_unknown_destination_class_is_blocked_never_pass(self):
        result = pe.evaluate(_obs(destination_class="carrier-pigeon"))
        self.assertEqual(result["status"], pe.STATUS_BLOCKED)
        self.assertIn("AI_PRIVACY_POSTURE_BLOCKED_UNKNOWN_DESTINATION_CLASS", result["reason_codes"])

    def test_missing_destination_class_is_blocked_never_pass(self):
        obs = _obs()
        del obs["destination_class"]
        result = pe.evaluate(obs)
        self.assertEqual(result["status"], pe.STATUS_BLOCKED)
        self.assertIn("AI_PRIVACY_POSTURE_BLOCKED_UNKNOWN_DESTINATION_CLASS", result["reason_codes"])

    def test_missing_observed_at_is_blocked(self):
        obs = _obs()
        del obs["observed_at"]
        result = pe.evaluate(obs)
        self.assertEqual(result["status"], pe.STATUS_BLOCKED)
        self.assertIn("AI_PRIVACY_POSTURE_BLOCKED_EVIDENCE_MISSING_TIMESTAMP", result["reason_codes"])

    def test_stale_evidence_is_blocked_never_pass(self):
        result = pe.evaluate(_obs(evidence_age_seconds=pe.DEFAULT_MAX_EVIDENCE_AGE_SECONDS + 1))
        self.assertEqual(result["status"], pe.STATUS_BLOCKED)
        self.assertIn("AI_PRIVACY_POSTURE_BLOCKED_EVIDENCE_STALE", result["reason_codes"])

    def test_evaluate_never_raises_on_garbage_input(self):
        for garbage in (None, {}, [], "not-a-dict", 42, {"policy_version": None}):
            result = pe.evaluate(garbage)  # type: ignore[arg-type]
            self.assertIn(result["status"], (pe.STATUS_BLOCKED, pe.STATUS_WARN))

    def test_missing_local_only_source_under_restricted_posture_is_blocked_not_healthy(self):
        # This is the explicit issue #216/#215-coordination requirement:
        # a missing #215 posture source must degrade to an
        # unknown/BLOCKED state, never a healthy-looking default.
        obs = _obs()
        del obs["local_only_posture_source"]
        result = pe.evaluate(obs)
        self.assertEqual(result["status"], pe.STATUS_BLOCKED)
        self.assertIn("AI_PRIVACY_POSTURE_BLOCKED_LOCAL_ONLY_SOURCE_UNAVAILABLE", result["reason_codes"])
        self.assertFalse(result["local_only_posture"]["available"])

    def test_local_only_source_present_but_available_false_is_still_unavailable(self):
        result = pe.evaluate(_obs(local_only_posture_source={"available": False, "status": "pass"}))
        self.assertEqual(result["status"], pe.STATUS_BLOCKED)
        self.assertFalse(result["local_only_posture"]["available"])

    def test_unrestricted_posture_does_not_require_local_only_source(self):
        # When the operator has NOT declared a restricted-local-only
        # expectation, a missing #215 source must not itself block an
        # otherwise-consistent report - it is simply not applicable.
        result = pe.evaluate(
            _obs(expected_posture="unrestricted", destination_class="cloud", local_only_posture_source=None)
        )
        self.assertNotIn("AI_PRIVACY_POSTURE_BLOCKED_LOCAL_ONLY_SOURCE_UNAVAILABLE", result["reason_codes"])

    def test_unknown_expected_posture_never_upgrades_to_pass_by_itself(self):
        obs = _obs(expected_posture="totally-made-up", local_only_posture_source=None)
        result = pe.evaluate(obs)
        self.assertIn("AI_PRIVACY_POSTURE_WARN_EXPECTED_POSTURE_UNKNOWN", result["reason_codes"])
        self.assertNotEqual(result["status"], pe.STATUS_PASS)


class TestDrift(unittest.TestCase):
    def test_drift_from_restricted_local_only_to_cloud_is_fail_not_warn(self):
        result = pe.evaluate(_obs(destination_class="cloud", cloud_fallback_enabled=True))
        self.assertEqual(result["status"], pe.STATUS_FAIL)
        self.assertIn("AI_PRIVACY_POSTURE_FAIL_DRIFT_LOCAL_ONLY_TO_CLOUD", result["reason_codes"])

    def test_drift_from_restricted_local_only_to_cloud_is_fail_regardless_of_other_pass_signals(self):
        # Even when every other signal looks healthy, drift to cloud must
        # still surface as FAIL - it must never be diluted to WARN by
        # combining with otherwise-good signals.
        result = pe.evaluate(
            _obs(
                destination_class="cloud",
                cloud_fallback_enabled=False,
                network_isolation_active=True,
                local_only_posture_source={"available": True, "status": "pass", "source": "local-only-posture-source:issue-215"},
            )
        )
        self.assertEqual(result["status"], pe.STATUS_FAIL)
        self.assertIn("AI_PRIVACY_POSTURE_FAIL_DRIFT_LOCAL_ONLY_TO_CLOUD", result["reason_codes"])

    def test_drift_from_restricted_local_only_to_private_endpoint_is_warn_not_fail(self):
        result = pe.evaluate(_obs(destination_class="private"))
        self.assertEqual(result["status"], pe.STATUS_WARN)
        self.assertIn("AI_PRIVACY_POSTURE_WARN_DRIFT_LOCAL_ONLY_TO_PRIVATE_ENDPOINT", result["reason_codes"])
        self.assertNotIn("AI_PRIVACY_POSTURE_FAIL_DRIFT_LOCAL_ONLY_TO_CLOUD", result["reason_codes"])

    def test_no_drift_flagged_when_destination_stays_local(self):
        result = pe.evaluate(_obs(destination_class="local"))
        for code in result["reason_codes"]:
            self.assertNotIn("DRIFT", code)

    def test_cloud_fallback_enabled_under_restricted_posture_is_fail(self):
        result = pe.evaluate(_obs(destination_class="local", cloud_fallback_enabled=True))
        self.assertEqual(result["status"], pe.STATUS_FAIL)
        self.assertIn("AI_PRIVACY_POSTURE_FAIL_CLOUD_FALLBACK_ENABLED_UNDER_RESTRICTED_POSTURE", result["reason_codes"])

    def test_network_isolation_inactive_under_restricted_posture_is_fail(self):
        result = pe.evaluate(_obs(network_isolation_active=False))
        self.assertEqual(result["status"], pe.STATUS_FAIL)
        self.assertIn("AI_PRIVACY_POSTURE_FAIL_NETWORK_ISOLATION_INACTIVE_UNDER_RESTRICTED_POSTURE", result["reason_codes"])

    def test_local_endpoint_publicly_exposed_is_fail(self):
        result = pe.evaluate(_obs(local_endpoint={"classification": "public"}))
        self.assertEqual(result["status"], pe.STATUS_FAIL)
        self.assertIn("AI_PRIVACY_POSTURE_FAIL_LOCAL_ENDPOINT_PUBLICLY_EXPOSED", result["reason_codes"])


class TestUnknownSignalsNeverReportHealthy(unittest.TestCase):
    def test_unknown_cloud_fallback_is_warn_not_pass(self):
        result = pe.evaluate(_obs(cloud_fallback_enabled=None))
        self.assertNotEqual(result["status"], pe.STATUS_PASS)
        self.assertIn("AI_PRIVACY_POSTURE_WARN_CLOUD_FALLBACK_STATE_UNKNOWN", result["reason_codes"])

    def test_unknown_network_isolation_is_warn_not_pass(self):
        result = pe.evaluate(_obs(network_isolation_active=None))
        self.assertNotEqual(result["status"], pe.STATUS_PASS)
        self.assertIn("AI_PRIVACY_POSTURE_WARN_NETWORK_ISOLATION_STATE_UNKNOWN", result["reason_codes"])

    def test_unknown_local_endpoint_classification_is_warn(self):
        result = pe.evaluate(_obs(local_endpoint={"classification": "does-not-exist"}))
        self.assertIn("AI_PRIVACY_POSTURE_WARN_LOCAL_ENDPOINT_CLASSIFICATION_UNKNOWN", result["reason_codes"])

    def test_local_only_source_reports_fail_propagates_to_fail(self):
        result = pe.evaluate(
            _obs(local_only_posture_source={"available": True, "status": "fail", "source": "local-only-posture-source:issue-215"})
        )
        self.assertEqual(result["status"], pe.STATUS_FAIL)
        self.assertIn("AI_PRIVACY_POSTURE_FAIL_LOCAL_ONLY_SOURCE_REPORTS_FAIL", result["reason_codes"])

    def test_local_only_source_reports_unknown_status_is_warn_not_pass(self):
        result = pe.evaluate(
            _obs(local_only_posture_source={"available": True, "status": "unknown", "source": "local-only-posture-source:issue-215"})
        )
        self.assertNotEqual(result["status"], pe.STATUS_PASS)
        self.assertIn("AI_PRIVACY_POSTURE_WARN_LOCAL_ONLY_SOURCE_REPORTS_UNKNOWN", result["reason_codes"])


class TestConsistentPosturePasses(unittest.TestCase):
    def test_fully_consistent_restricted_local_only_posture_passes(self):
        result = pe.evaluate(_obs())
        self.assertEqual(result["status"], pe.STATUS_PASS)
        self.assertEqual(result["reason_codes"], ["AI_PRIVACY_POSTURE_PASS_CONSISTENT"])

    def test_unrestricted_posture_with_consistent_cloud_evidence_passes(self):
        result = pe.evaluate(
            _obs(
                expected_posture="unrestricted",
                destination_class="cloud",
                local_endpoint={"classification": "not_applicable"},
                cloud_fallback_enabled=True,
                local_only_posture_source=None,
            )
        )
        self.assertEqual(result["status"], pe.STATUS_PASS)


class TestOutputShapeIsBoundedMetadataOnly(unittest.TestCase):
    """Cross-checks evaluate() output against the published contract
    schema and the shared secret-value scanner from lib/omes/py/jobs/schema.py -
    the same scanner every contract fixture is checked with - so this
    module's bounded-metadata guarantee is verified two independent ways:
    the module's own closed vocabularies, AND a generic secret-shape
    scanner that has no knowledge of this module's internals."""

    def _schema(self):
        import pathlib

        repo_root = pathlib.Path(__file__).resolve().parents[3]
        schema_path = repo_root / "contracts" / "ai-egress" / "v1" / "privacy-posture-evidence.schema.json"
        return schema_mod.load_json(schema_path)

    def test_pass_output_matches_published_contract_schema(self):
        result = pe.evaluate(_obs())
        errors = schema_mod.validate(result, self._schema())
        self.assertEqual(errors, [], errors)

    def test_blocked_output_matches_published_contract_schema(self):
        result = pe.evaluate(_obs(destination_class="unknown"))
        errors = schema_mod.validate(result, self._schema())
        self.assertEqual(errors, [], errors)

    def test_fail_output_matches_published_contract_schema(self):
        result = pe.evaluate(_obs(destination_class="cloud"))
        errors = schema_mod.validate(result, self._schema())
        self.assertEqual(errors, [], errors)


class TestNoSecretOrPromptLeakage(unittest.TestCase):
    """Adversarial tests: feed synthetic canary values (fake secrets, fake
    prompt/response text) through evaluate() and prove they never surface
    anywhere in the output - not verbatim, not truncated, not hashed."""

    # Secret-shaped canary, assembled at runtime so no secret-shaped literal
    # is ever committed (repo convention; see tests/py/contracts/test_schema.py).
    CANARY_SECRET = "sk_live_" + "CANARY_1234567890ABCDEFGHIJ"
    CANARY_PROMPT = "CANARY_PROMPT: the patient's full name is Jane Doe and her SSN is 123-45-6789"
    CANARY_TOKEN = "Bearer CANARY_TOKEN_abcdef0123456789"

    def _assert_no_canary_leak(self, result: dict) -> None:
        dumped = json.dumps(result)
        for canary in (self.CANARY_SECRET, self.CANARY_PROMPT, self.CANARY_TOKEN, "CANARY"):
            self.assertNotIn(canary, dumped, f"canary value leaked into evidence output: {dumped}")

    def test_unexpected_fields_carrying_canaries_are_dropped_entirely(self):
        # A caller that mistakenly (or maliciously) stuffs prompt text,
        # secrets, or a raw provider response into keys this module does
        # not document must have those keys silently ignored, not echoed.
        obs = _obs()
        obs["prompt"] = self.CANARY_PROMPT
        obs["response_text"] = self.CANARY_PROMPT
        obs["api_key"] = self.CANARY_SECRET
        obs["raw_provider_response"] = {"choices": [{"text": self.CANARY_PROMPT}]}
        obs["chain_of_thought"] = self.CANARY_PROMPT
        obs["authorization_header"] = self.CANARY_TOKEN
        obs["environ"] = {"OPENAI_API_KEY": self.CANARY_SECRET}
        result = pe.evaluate(obs)
        self._assert_no_canary_leak(result)

    def test_canary_in_evidence_source_is_rejected_not_echoed(self):
        result = pe.evaluate(_obs(evidence_source=self.CANARY_SECRET))
        self._assert_no_canary_leak(result)
        self.assertEqual(result["evidence_source"], "unknown")
        self.assertIn("AI_PRIVACY_POSTURE_WARN_EVIDENCE_SOURCE_REJECTED", result["reason_codes"])

    def test_canary_in_hermes_version_reference_value_is_rejected_not_echoed(self):
        result = pe.evaluate(
            _obs(hermes_version_reference={"value": self.CANARY_PROMPT, "source": "hermes-cli:hermes --version"})
        )
        self._assert_no_canary_leak(result)
        self.assertIsNone(result["hermes_version_reference"]["value"])
        self.assertIn("AI_PRIVACY_POSTURE_WARN_HERMES_VERSION_REFERENCE_REJECTED", result["reason_codes"])

    def test_canary_in_local_only_posture_source_free_text_is_rejected(self):
        result = pe.evaluate(
            _obs(
                local_only_posture_source={
                    "available": True,
                    "status": "pass",
                    "source": self.CANARY_TOKEN,
                }
            )
        )
        self._assert_no_canary_leak(result)
        self.assertEqual(result["local_only_posture"]["source"], "unknown")

    def test_canary_in_destination_class_and_expected_posture_is_dropped(self):
        result = pe.evaluate(
            _obs(destination_class=self.CANARY_SECRET, expected_posture=self.CANARY_PROMPT)
        )
        self._assert_no_canary_leak(result)
        self.assertEqual(result["destination_class"], "unknown")

    def test_oversized_evidence_source_value_is_rejected(self):
        # Even a value that happens to use only "safe" characters but is
        # implausibly long for a closed-vocabulary label must not sneak
        # through - the evaluator only accepts exact membership in
        # EVIDENCE_SOURCES, never a prefix/substring/length-bounded match.
        long_value = "hermes-cli:hermes config get" + ("a" * 5000)
        result = pe.evaluate(_obs(evidence_source=long_value))
        self.assertEqual(result["evidence_source"], "unknown")
        self.assertNotIn(long_value, json.dumps(result))

    def test_hashing_is_never_used_as_a_substitute_for_rejection(self):
        # docs/ai-data-privacy-and-model-security.md section 7: hashing a
        # low-entropy secret is not anonymization. Confirms the rejected
        # value is replaced with the fixed placeholder/None, never with a
        # hash of the original (which would still be a derived encoding
        # of the secret).
        import hashlib

        canary = self.CANARY_SECRET
        digest = hashlib.sha256(canary.encode()).hexdigest()
        result = pe.evaluate(_obs(evidence_source=canary))
        dumped = json.dumps(result)
        self.assertNotIn(digest, dumped)
        self.assertNotIn(canary, dumped)

    def test_output_never_contains_a_secret_ref_shaped_or_raw_secret_value(self):
        # Cross-check with the shared, module-agnostic scanner used for
        # every contract fixture in this repository.
        obs = _obs()
        obs["extra_unexpected_field"] = self.CANARY_SECRET
        result = pe.evaluate(obs)
        errors = schema_mod.scan_for_raw_secrets(result)
        self.assertEqual(errors, [], errors)


if __name__ == "__main__":
    unittest.main()
