"""Unit tests for lib/omes/py/observability/policy_decision.py, the policy/
capability decision envelope validator and pure composer (issue #274,
ADR-0032 rule 7; threat MA-05).

All identifiers are synthetic. The composer is not a policy engine: these
tests exercise only the composition of already-made decisions.
"""
import copy
import datetime
import itertools
import json
import unittest
from pathlib import Path

from . import _pathfix  # noqa: F401  (sets sys.path)

from jobs import schema as schema_mod  # noqa: E402
from observability import envelope, policy_decision  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "contracts" / "observability" / "v1" / "fixtures" / "policy-decision"

NOW = datetime.datetime(2026, 10, 3, 10, 30, 0, tzinfo=datetime.timezone.utc)
FUTURE = "2026-10-03T12:00:00Z"
PAST = "2026-10-03T10:15:00Z"


def _dec(decision_id, authority, decision, **overrides):
    base = {
        "schema_version": "1.0.0",
        "decision_id": decision_id,
        "tenant_id": "tenant-a",
        "correlation_id": "corr-0000-0001",
        "actor": {"type": "user", "id": "user-1"},
        "resource": {"kind": "deployment", "id": "deploy-1"},
        "requested_action": "restore",
        "deciding_authority": authority,
        "decision": decision,
        "policy_id": f"{authority}.policy",
        "reason_code": f"REASON_{decision.upper()}",
        "evidence": {"version": "v1", "observed_at": "2026-10-03T10:00:00Z", "freshness": "live"},
        "approval_ref": None,
        "expires_at": FUTURE,
        "idempotency_key": None,
        "classification": "INTERNAL",
        "redaction_state": "none_required",
    }
    base.update(overrides)
    return base


def _compose(decisions, required=(), now=NOW):
    return policy_decision.compose(decisions, now=now, required_authorities=required)


class TestValidate(unittest.TestCase):
    def test_checked_in_valid_fixtures_pass(self):
        paths = sorted(FIXTURES.glob("valid-*.json"))
        self.assertGreaterEqual(len(paths), 3)
        for path in paths:
            with self.subTest(fixture=path.name):
                self.assertEqual(policy_decision.validate(json.loads(path.read_text())), [])

    def test_checked_in_invalid_fixtures_fail_for_the_stated_reason(self):
        paths = sorted(FIXTURES.glob("invalid-*.json"))
        self.assertGreaterEqual(len(paths), 6)
        for path in paths:
            with self.subTest(fixture=path.name):
                instance = json.loads(path.read_text())
                self.assertNotEqual(policy_decision.validate(instance), [])
                # The .reason.txt substring is checked against the plain schema
                # validator, exactly as scripts/check-contracts.py does.
                schema_errors = schema_mod.validate(instance, policy_decision.load_schema())
                reason = path.with_suffix(".reason.txt")
                self.assertTrue(reason.exists())
                self.assertIn(reason.read_text().strip(), "; ".join(schema_errors))

    def test_unknown_major_fails_closed_with_single_error(self):
        for version in ("2.0.0", "0.9.0", "1", "", None, 1):
            with self.subTest(version=version):
                errors = policy_decision.validate(_dec("dec-00000001", "omes", "allow", schema_version=version))
                self.assertEqual(len(errors), 1)
                self.assertIn("fail closed", errors[0])

    def test_known_major_unknown_minor_fails_closed(self):
        self.assertTrue(policy_decision.validate(_dec("dec-00000001", "omes", "allow", schema_version="1.1.0")))

    def test_non_object_rejected(self):
        for bad in ([], "x", None, 3):
            self.assertTrue(policy_decision.validate(bad))

    def test_forbidden_free_text_command_and_credential_fields_rejected(self):
        for field in ("reasoning", "prompt", "command", "argv", "headers", "token", "tool_args", "reason"):
            with self.subTest(field=field):
                bad = _dec("dec-00000001", "omes", "allow")
                bad[field] = "x"
                self.assertTrue(policy_decision.validate(bad))
        nested = _dec("dec-00000001", "omes", "allow")
        nested["actor"]["command"] = "id"
        self.assertTrue(policy_decision.validate(nested))
        for field in ("reasoning", "prompt", "command", "argv", "headers", "token"):
            self.assertIn(field, envelope.FORBIDDEN_FIELD_NAMES)

    def test_forbidden_field_scan_names_the_reason(self):
        bad = _dec("dec-00000001", "omes", "allow")
        bad["reasoning"] = "x"
        self.assertTrue(any("forbidden field" in e for e in policy_decision.validate(bad)))

    def test_missing_required_fields_rejected(self):
        for field in policy_decision.load_schema()["required"]:
            with self.subTest(field=field):
                bad = _dec("dec-00000001", "omes", "allow")
                del bad[field]
                self.assertTrue(any(field in e for e in policy_decision.validate(bad)))

    def test_decision_enum_is_closed(self):
        for ok in ("allow", "deny", "approval_required", "unavailable"):
            self.assertEqual(policy_decision.validate(_dec("dec-00000001", "omes", ok)), [])
        for bad in ("granted", "ALLOW", "unknown", "pending", ""):
            broken = _dec("dec-00000001", "omes", "allow")
            broken["decision"] = bad
            self.assertTrue(policy_decision.validate(broken))

    def test_reason_code_must_be_upper_snake_case(self):
        for bad in ("lower_case", "Mixed_Case", "AB", "1BAD_CODE", "HAS SPACE", "A" * 65):
            with self.subTest(code=bad):
                self.assertTrue(policy_decision.validate(_dec("dec-00000001", "omes", "allow", reason_code=bad)))

    def test_approval_ref_is_a_reference_not_an_object(self):
        self.assertEqual(
            policy_decision.validate(_dec("dec-00000001", "awcms", "approval_required", approval_ref="awcms:approvals/apr-1")),
            [],
        )
        self.assertTrue(
            policy_decision.validate(_dec("dec-00000001", "awcms", "approval_required", approval_ref={"granted": True}))
        )
        self.assertTrue(
            policy_decision.validate(_dec("dec-00000001", "awcms", "approval_required", approval_ref="has space"))
        )

    def test_timestamps_must_be_valid_utc_and_expiry_after_observation(self):
        bad_evidence = _dec("dec-00000001", "omes", "allow")
        bad_evidence["evidence"]["observed_at"] = "2026-02-30T10:00:00Z"
        self.assertTrue(any("observed_at" in e for e in policy_decision.validate(bad_evidence)))
        self.assertTrue(policy_decision.validate(_dec("dec-00000001", "omes", "allow", expires_at="2026-10-03T12:00:00+00:00")))
        self.assertTrue(policy_decision.validate(_dec("dec-00000001", "omes", "allow", expires_at="2026-10-03T09:00:00Z")))

    def test_control_characters_rejected_at_any_depth(self):
        bad = _dec("dec-00000001", "omes", "allow")
        bad["actor"]["id"] = "user\n1"
        self.assertTrue(policy_decision.validate(bad))

    def test_secret_shaped_value_rejected(self):
        bad = _dec("dec-00000001", "omes", "allow", policy_id="ghp_" + "a" * 36)
        self.assertTrue(policy_decision.validate(bad))

    def test_deciding_authority_mirrors_registry_authority_enum(self):
        registry_schema = json.loads(
            (REPO_ROOT / "contracts" / "architecture" / "v1" / "capabilities.schema.json").read_text()
        )
        registry_enum = registry_schema["properties"]["capabilities"]["items"]["properties"]["authority"]["enum"]
        self.assertEqual(sorted(policy_decision.authorities()), sorted(registry_enum))
        self.assertEqual(
            sorted(policy_decision.authorities()),
            sorted(envelope.load_schema()["properties"]["source_authority"]["enum"]),
        )

    def test_classification_enum_matches_correlation_envelope(self):
        for key in ("classification", "redaction_state"):
            self.assertEqual(
                policy_decision.load_schema()["properties"][key]["enum"],
                envelope.load_schema()["properties"][key]["enum"],
            )

    def test_decision_enum_matches_precedence_vocabulary(self):
        self.assertEqual(
            sorted(policy_decision.load_schema()["properties"]["decision"]["enum"]),
            sorted(policy_decision.PRECEDENCE),
        )

    def test_egress_decisions_are_a_subset_of_the_decision_enum(self):
        egress = json.loads(
            (REPO_ROOT / "contracts" / "ai-egress" / "v1" / "egress-decision-response.schema.json").read_text()
        )
        self.assertLessEqual(
            set(egress["properties"]["decision"]["enum"]),
            set(policy_decision.load_schema()["properties"]["decision"]["enum"]),
        )


class TestComposePrecedence(unittest.TestCase):
    def test_precedence_order_is_deny_unavailable_approval_required_allow(self):
        self.assertEqual(policy_decision.PRECEDENCE, ("deny", "unavailable", "approval_required", "allow"))

    def test_precedence_table_exhaustive_over_pairs(self):
        names = policy_decision.PRECEDENCE
        for a, b in itertools.product(names, repeat=2):
            with self.subTest(a=a, b=b):
                expected = min(a, b, key=names.index)
                result = _compose([
                    _dec("dec-aaaaaaaa", "omes", a, approval_ref="awcms:approvals/apr-1" if a == "approval_required" else None),
                    _dec("dec-bbbbbbbb", "awcms", b, approval_ref="awcms:approvals/apr-2" if b == "approval_required" else None),
                ])
                self.assertTrue(result["ok"], result["errors"])
                self.assertEqual(result["composed"]["decision"], expected)

    def test_all_allow_with_required_authorities_present_is_allow(self):
        result = _compose(
            [_dec("dec-aaaaaaaa", "omes", "allow"), _dec("dec-bbbbbbbb", "awcms", "allow")],
            required=("awcms", "omes"),
        )
        composed = result["composed"]
        self.assertEqual(composed["decision"], "allow")
        self.assertEqual(composed["contributing_decision_ids"], ["dec-aaaaaaaa", "dec-bbbbbbbb"])
        self.assertEqual(composed["missing_authorities"], [])
        self.assertEqual(composed["tenant_id"], "tenant-a")
        self.assertEqual(composed["requested_action"], "restore")
        self.assertEqual(composed["expires_at"], "2026-10-03T12:00:00.000000Z")

    def test_winner_reports_authority_reason_and_policy(self):
        result = _compose([
            _dec("dec-aaaaaaaa", "omes", "allow"),
            _dec("dec-bbbbbbbb", "awcms", "deny", reason_code="ENTITLEMENT_EXCEEDED", policy_id="awcms.entitlement"),
        ])
        composed = result["composed"]
        self.assertEqual(composed["decision"], "deny")
        self.assertEqual(composed["winning_authority"], "awcms")
        self.assertEqual(composed["winning_decision_id"], "dec-bbbbbbbb")
        self.assertEqual(composed["winning_reason_code"], "ENTITLEMENT_EXCEEDED")
        self.assertEqual(composed["winning_policy_id"], "awcms.entitlement")

    def test_single_allow_without_required_authorities_is_allow(self):
        self.assertEqual(_compose([_dec("dec-aaaaaaaa", "omes", "allow")])["composed"]["decision"], "allow")

    def test_deny_beats_missing_required_authority(self):
        result = _compose([_dec("dec-aaaaaaaa", "omes", "deny")], required=("omes", "awcms"))
        self.assertEqual(result["composed"]["decision"], "deny")
        self.assertEqual(result["composed"]["missing_authorities"], ["awcms"])


class TestComposeFailClosed(unittest.TestCase):
    def test_empty_input_is_unavailable(self):
        for required in ((), ("omes",)):
            result = _compose([], required=required)
            self.assertTrue(result["ok"])
            self.assertEqual(result["composed"]["decision"], "unavailable")
            self.assertEqual(result["composed"]["winning_reason_code"], "NO_DECISIONS")
            self.assertEqual(result["composed"]["contributing_decision_ids"], [])

    def test_stale_or_unknown_evidence_is_treated_as_unavailable(self):
        for freshness in ("stale", "unknown"):
            for decision in ("allow", "approval_required", "deny"):
                with self.subTest(freshness=freshness, decision=decision):
                    d = _dec("dec-aaaaaaaa", "omes", decision, approval_ref="awcms:approvals/apr-1" if decision == "approval_required" else None)
                    d["evidence"]["freshness"] = freshness
                    composed = _compose([d])["composed"]
                    self.assertEqual(composed["decision"], "unavailable")
                    self.assertEqual(composed["winning_reason_code"], "EVIDENCE_NOT_LIVE")
                    self.assertEqual(composed["approval_refs"], [])

    def test_stale_allow_never_wins_over_live_allow(self):
        stale = _dec("dec-aaaaaaaa", "omes", "allow")
        stale["evidence"]["freshness"] = "stale"
        composed = _compose([stale, _dec("dec-bbbbbbbb", "awcms", "allow")], required=("omes", "awcms"))["composed"]
        self.assertEqual(composed["decision"], "unavailable")
        self.assertEqual(composed["winning_authority"], "omes")

    def test_expired_decision_is_treated_as_unavailable(self):
        composed = _compose([_dec("dec-aaaaaaaa", "omes", "allow", expires_at=PAST)])["composed"]
        self.assertEqual(composed["decision"], "unavailable")
        self.assertEqual(composed["winning_reason_code"], "DECISION_EXPIRED")

    def test_expiry_boundary_is_exclusive(self):
        exactly_now = _dec("dec-aaaaaaaa", "omes", "allow", expires_at="2026-10-03T10:30:00Z")
        self.assertEqual(_compose([exactly_now])["composed"]["decision"], "unavailable")
        just_after = _dec("dec-aaaaaaaa", "omes", "allow", expires_at="2026-10-03T10:30:00.000001Z")
        self.assertEqual(_compose([just_after])["composed"]["decision"], "allow")

    def test_no_expiry_means_not_expired(self):
        composed = _compose([_dec("dec-aaaaaaaa", "omes", "allow", expires_at=None)])["composed"]
        self.assertEqual(composed["decision"], "allow")
        self.assertIsNone(composed["expires_at"])

    def test_composed_expiry_is_the_earliest_input_expiry(self):
        composed = _compose([
            _dec("dec-aaaaaaaa", "omes", "allow", expires_at="2026-10-03T13:00:00Z"),
            _dec("dec-bbbbbbbb", "awcms", "allow", expires_at="2026-10-03T11:00:00Z"),
            _dec("dec-cccccccc", "hermes", "allow", expires_at=None),
        ])["composed"]
        self.assertEqual(composed["expires_at"], "2026-10-03T11:00:00.000000Z")

    def test_missing_required_authority_is_unavailable(self):
        result = _compose([_dec("dec-aaaaaaaa", "omes", "allow")], required=("omes", "awcms"))
        composed = result["composed"]
        self.assertEqual(composed["decision"], "unavailable")
        self.assertEqual(composed["winning_reason_code"], "AUTHORITY_DECISION_MISSING")
        self.assertIsNone(composed["winning_decision_id"])
        self.assertEqual(composed["winning_authority"], "awcms")
        self.assertEqual(composed["missing_authorities"], ["awcms"])

    def test_missing_required_authority_downgrades_approval_required(self):
        d = _dec("dec-aaaaaaaa", "omes", "approval_required", approval_ref="awcms:approvals/apr-1")
        composed = _compose([d], required=("omes", "hermes"))["composed"]
        self.assertEqual(composed["decision"], "unavailable")
        self.assertEqual(composed["approval_refs"], [])

    def test_unknown_required_authority_is_an_error(self):
        result = _compose([_dec("dec-aaaaaaaa", "omes", "allow")], required=("coordinator",))
        self.assertFalse(result["ok"])
        self.assertIsNone(result["composed"])

    def test_now_must_be_timezone_aware(self):
        with self.assertRaises(ValueError):
            policy_decision.compose([], now=datetime.datetime(2026, 10, 3, 10, 30), required_authorities=())

    def test_now_and_required_authorities_have_no_default(self):
        with self.assertRaises(TypeError):
            policy_decision.compose([])  # type: ignore[call-arg]

    def test_invalid_input_fails_closed_without_partial_result(self):
        bad = _dec("dec-bbbbbbbb", "awcms", "allow")
        bad["reasoning"] = "x"
        result = _compose([_dec("dec-aaaaaaaa", "omes", "allow"), bad])
        self.assertFalse(result["ok"])
        self.assertIsNone(result["composed"])
        self.assertTrue(any("dec-bbbbbbbb" in e for e in result["errors"]))

    def test_unknown_major_input_fails_closed(self):
        result = _compose([_dec("dec-aaaaaaaa", "omes", "allow", schema_version="2.0.0")])
        self.assertFalse(result["ok"])
        self.assertIsNone(result["composed"])

    def test_mixed_tenant_rejected(self):
        result = _compose([_dec("dec-aaaaaaaa", "omes", "allow"), _dec("dec-bbbbbbbb", "awcms", "allow", tenant_id="tenant-b")])
        self.assertFalse(result["ok"])
        self.assertIsNone(result["composed"])
        self.assertTrue(any("tenant_id" in e for e in result["errors"]))

    def test_mixed_correlation_rejected(self):
        result = _compose([
            _dec("dec-aaaaaaaa", "omes", "allow"),
            _dec("dec-bbbbbbbb", "awcms", "allow", correlation_id="corr-0000-0002"),
        ])
        self.assertFalse(result["ok"])
        self.assertTrue(any("correlation_id" in e for e in result["errors"]))

    def test_mixed_action_rejected(self):
        result = _compose([
            _dec("dec-aaaaaaaa", "omes", "allow"),
            _dec("dec-bbbbbbbb", "awcms", "allow", requested_action="backup"),
        ])
        self.assertFalse(result["ok"])
        self.assertTrue(any("requested_action" in e for e in result["errors"]))

    def test_decision_id_reuse_with_different_content_rejected(self):
        result = _compose([_dec("dec-aaaaaaaa", "omes", "allow"), _dec("dec-aaaaaaaa", "omes", "deny")])
        self.assertFalse(result["ok"])
        self.assertTrue(any("reused with different content" in e for e in result["errors"]))

    def test_exact_duplicates_collapse(self):
        d = _dec("dec-aaaaaaaa", "omes", "allow")
        self.assertEqual(_compose([d, copy.deepcopy(d)]), _compose([d]))


class TestComposeApproval(unittest.TestCase):
    def test_approval_required_propagates_refs_unchanged(self):
        result = _compose([
            _dec("dec-aaaaaaaa", "awcms", "approval_required", approval_ref="awcms:approvals/apr-2"),
            _dec("dec-bbbbbbbb", "omes", "approval_required", approval_ref="omes:jobs/job-7#approve"),
            _dec("dec-cccccccc", "hermes", "allow"),
        ])
        composed = result["composed"]
        self.assertEqual(composed["decision"], "approval_required")
        self.assertEqual(composed["approval_refs"], ["awcms:approvals/apr-2", "omes:jobs/job-7#approve"])

    def test_approval_required_without_ref_does_not_synthesise_one(self):
        composed = _compose([_dec("dec-aaaaaaaa", "awcms", "approval_required", approval_ref=None)])["composed"]
        self.assertEqual(composed["decision"], "approval_required")
        self.assertEqual(composed["approval_refs"], [])

    def test_approval_ref_on_allow_is_not_propagated(self):
        d = _dec("dec-aaaaaaaa", "awcms", "allow", approval_ref="awcms:approvals/apr-9")
        self.assertEqual(_compose([d])["composed"]["approval_refs"], [])

    def test_approval_ref_of_losing_deny_is_not_propagated(self):
        composed = _compose([
            _dec("dec-aaaaaaaa", "awcms", "approval_required", approval_ref="awcms:approvals/apr-1"),
            _dec("dec-bbbbbbbb", "omes", "deny"),
        ])["composed"]
        self.assertEqual(composed["decision"], "deny")
        self.assertEqual(composed["approval_refs"], [])

    def test_composed_result_never_contains_approval_objects_or_free_text(self):
        composed = _compose([
            _dec("dec-aaaaaaaa", "awcms", "approval_required", approval_ref="awcms:approvals/apr-1"),
        ])["composed"]
        for forbidden in envelope.FORBIDDEN_FIELD_NAMES | {"approval", "granted", "reason"}:
            self.assertNotIn(forbidden, composed)
        self.assertTrue(all(isinstance(ref, str) for ref in composed["approval_refs"]))


class TestComposeDeterminism(unittest.TestCase):
    def _mixed(self):
        stale = _dec("dec-eeeeeeee", "platform", "allow")
        stale["evidence"]["freshness"] = "stale"
        return [
            _dec("dec-aaaaaaaa", "omes", "allow"),
            _dec("dec-bbbbbbbb", "awcms", "approval_required", approval_ref="awcms:approvals/apr-1"),
            _dec("dec-cccccccc", "hermes", "allow", expires_at=PAST),
            _dec("dec-dddddddd", "awcms", "deny", reason_code="ENTITLEMENT_EXCEEDED"),
            stale,
        ]

    def test_every_permutation_gives_the_same_result(self):
        decisions = self._mixed()
        expected = _compose(decisions, required=("omes", "awcms"))
        self.assertTrue(expected["ok"])
        for permutation in itertools.permutations(decisions):
            self.assertEqual(_compose(list(permutation), required=("awcms", "omes")), expected)

    def test_every_permutation_without_a_deny_is_stable(self):
        decisions = [d for d in self._mixed() if d["decision"] != "deny"]
        expected = _compose(decisions)
        for permutation in itertools.permutations(decisions):
            self.assertEqual(_compose(list(permutation)), expected)

    def test_inputs_are_not_mutated(self):
        decisions = self._mixed()
        snapshot = copy.deepcopy(decisions)
        _compose(decisions, required=("omes",))
        self.assertEqual(decisions, snapshot)

    def test_result_is_a_function_of_now_only_through_the_parameter(self):
        d = [_dec("dec-aaaaaaaa", "omes", "allow", expires_at="2026-10-03T10:45:00Z")]
        self.assertEqual(_compose(d, now=NOW)["composed"]["decision"], "allow")
        later = NOW + datetime.timedelta(minutes=30)
        self.assertEqual(_compose(d, now=later)["composed"]["decision"], "unavailable")
        self.assertEqual(_compose(d, now=NOW), _compose(d, now=NOW))

    def test_generator_input_is_accepted(self):
        decisions = self._mixed()
        self.assertEqual(_compose(iter(decisions)), _compose(decisions))


if __name__ == "__main__":
    unittest.main()
