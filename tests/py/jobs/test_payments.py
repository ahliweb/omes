"""Tests for lib/omes/py/jobs/payments.py (issue #94): signature
verification, replay/idempotency, proration rounding, and automation
gating. Every secret used here is generated at runtime with
`secrets.token_bytes()` - never a literal secret-shaped string."""
import json
import secrets
import unittest

from . import _pathfix  # noqa: F401
from jobs import payments


def _secret() -> bytes:
    return secrets.token_bytes(32)


class TestSignatureVerification(unittest.TestCase):
    def test_valid_signature_is_accepted(self):
        secret = _secret()
        payload = json.dumps({"event_id": "evt-1"}).encode("utf-8")
        sig = payments.compute_signature(secret, payload)
        self.assertTrue(payments.verify_signature(secret, payload, sig))
        payments.assert_valid_signature(secret, payload, sig)  # must not raise

    def test_signature_failure_wrong_secret_is_rejected(self):
        payload = json.dumps({"event_id": "evt-1"}).encode("utf-8")
        sig = payments.compute_signature(_secret(), payload)
        with self.assertRaises(payments.SignatureVerificationError):
            payments.assert_valid_signature(_secret(), payload, sig)

    def test_signature_failure_tampered_payload_is_rejected(self):
        secret = _secret()
        original_payload = json.dumps({"amount_minor": 1000}).encode("utf-8")
        sig = payments.compute_signature(secret, original_payload)
        tampered_payload = json.dumps({"amount_minor": 100000}).encode("utf-8")
        self.assertFalse(payments.verify_signature(secret, tampered_payload, sig))

    def test_malformed_signature_is_rejected_not_crashed(self):
        secret = _secret()
        payload = b"{}"
        self.assertFalse(payments.verify_signature(secret, payload, "not-a-real-signature"))


class TestFreshness(unittest.TestCase):
    def test_within_window_passes(self):
        payments.check_freshness(occurred_at_epoch=1000, received_at_epoch=1050, replay_window_seconds=300)

    def test_stale_timestamp_is_rejected(self):
        with self.assertRaises(payments.StaleEventError):
            payments.check_freshness(occurred_at_epoch=1000, received_at_epoch=10000, replay_window_seconds=300)

    def test_future_timestamp_is_also_rejected(self):
        with self.assertRaises(payments.StaleEventError):
            payments.check_freshness(occurred_at_epoch=100000, received_at_epoch=1000, replay_window_seconds=300)


class TestReplayIdempotency(unittest.TestCase):
    def test_first_occurrence_is_not_a_replay(self):
        payments.check_not_replayed(set(), "evt-1")  # must not raise

    def test_duplicate_event_id_is_rejected(self):
        with self.assertRaises(payments.ReplayedEventError):
            payments.check_not_replayed({"evt-1"}, "evt-1")

    def test_verify_webhook_runs_signature_before_freshness_or_replay(self):
        secret = _secret()
        payload = b"{}"
        bad_sig = payments.compute_signature(_secret(), payload)  # wrong secret
        with self.assertRaises(payments.SignatureVerificationError):
            payments.verify_webhook(
                secret=secret,
                payload=payload,
                signature_hex=bad_sig,
                occurred_at_epoch=10**9,  # would also fail freshness
                received_at_epoch=1,
                replay_window_seconds=1,
                event_id="evt-1",
                seen_event_ids={"evt-1"},  # would also fail replay
            )

    def test_verify_webhook_succeeds_when_everything_is_valid(self):
        secret = _secret()
        payload = b'{"event_id": "evt-1"}'
        sig = payments.compute_signature(secret, payload)
        payments.verify_webhook(
            secret=secret,
            payload=payload,
            signature_hex=sig,
            occurred_at_epoch=1000,
            received_at_epoch=1010,
            replay_window_seconds=300,
            event_id="evt-1",
            seen_event_ids=set(),
        )

    def test_duplicate_event_after_valid_signature_and_freshness_is_still_rejected(self):
        secret = _secret()
        payload = b'{"event_id": "evt-1"}'
        sig = payments.compute_signature(secret, payload)
        with self.assertRaises(payments.ReplayedEventError):
            payments.verify_webhook(
                secret=secret,
                payload=payload,
                signature_hex=sig,
                occurred_at_epoch=1000,
                received_at_epoch=1010,
                replay_window_seconds=300,
                event_id="evt-1",
                seen_event_ids={"evt-1"},
            )


class TestProrationRounding(unittest.TestCase):
    def test_round_half_up_rounds_half_up_not_to_even(self):
        # 5/2 = 2.5 -> half-up rounds to 3 (banker's rounding would round to 2)
        self.assertEqual(payments.round_half_up(5, 2), 3)
        # 3/2 = 1.5 -> half-up rounds to 2 (banker's rounding would also round to 2 here,
        # so this alone wouldn't distinguish the rules - paired with the 5/2 case above it does)
        self.assertEqual(payments.round_half_up(3, 2), 2)

    def test_round_half_up_exact_division_has_no_rounding(self):
        self.assertEqual(payments.round_half_up(10, 5), 2)

    def test_round_half_up_rejects_negative_numerator(self):
        with self.assertRaises(ValueError):
            payments.round_half_up(-1, 2)

    def test_compute_proration_upgrade_produces_net_charge(self):
        # Old plan 1000/mo, new plan 2000/mo, 15 of 30 days remaining.
        result = payments.compute_proration(old_amount_minor=1000, new_amount_minor=2000, days_remaining=15, days_in_cycle=30)
        self.assertEqual(result["unused_amount_minor"], 500)
        self.assertEqual(result["new_amount_minor"], 1000)
        self.assertEqual(result["net_amount_minor"], 500)

    def test_compute_proration_downgrade_produces_net_credit(self):
        result = payments.compute_proration(old_amount_minor=2000, new_amount_minor=1000, days_remaining=15, days_in_cycle=30)
        self.assertEqual(result["net_amount_minor"], -500)

    def test_compute_proration_zero_days_remaining_is_zero(self):
        result = payments.compute_proration(old_amount_minor=1000, new_amount_minor=2000, days_remaining=0, days_in_cycle=30)
        self.assertEqual(result["unused_amount_minor"], 0)
        self.assertEqual(result["new_amount_minor"], 0)
        self.assertEqual(result["net_amount_minor"], 0)

    def test_compute_proration_rejects_days_remaining_greater_than_cycle(self):
        with self.assertRaises(ValueError):
            payments.compute_proration(old_amount_minor=1000, new_amount_minor=2000, days_remaining=31, days_in_cycle=30)


class TestRefundWithinPayment(unittest.TestCase):
    def test_refund_within_remaining_amount_is_allowed(self):
        self.assertTrue(payments.refund_amount_is_within_payment(1000, 0, 1000))

    def test_refund_exceeding_remaining_amount_is_rejected(self):
        self.assertFalse(payments.refund_amount_is_within_payment(1000, 600, 500))


class TestAutomationDecision(unittest.TestCase):
    def test_non_destructive_event_never_requires_approval(self):
        result = payments.decide_automation("payment.succeeded", {"policy_id": "p1", "requires_approval_for_destructive_action": True})
        self.assertFalse(result["requires_approval"])

    def test_destructive_event_requires_approval_by_default(self):
        result = payments.decide_automation("payment.failed", {"policy_id": "p1", "requires_approval_for_destructive_action": True})
        self.assertTrue(result["requires_approval"])

    def test_destructive_event_may_skip_approval_when_policy_explicitly_allows(self):
        result = payments.decide_automation("dunning.retry", {"policy_id": "p1", "requires_approval_for_destructive_action": False})
        self.assertFalse(result["requires_approval"])

    def test_missing_policy_field_defaults_to_requiring_approval(self):
        result = payments.decide_automation("dispute.opened", {"policy_id": "p1"})
        self.assertTrue(result["requires_approval"])
