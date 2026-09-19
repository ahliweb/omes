"""lib/omes/py/domains/github.py - GitHub App installation/webhook
stdlib verifier (issue #101).

Implements only what GitHub's own documentation specifies
(https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries,
fetched 2026-09-19):

- the `X-Hub-Signature-256` header always starts with `sha256=` followed
  by an HMAC-SHA256 hex digest of the raw request body, keyed by the
  webhook secret;
- the comparison must be constant-time (GitHub explicitly recommends
  `crypto.timingSafeEqual`/`secure_compare`-style comparison to mitigate
  timing attacks) - this module uses `hmac.compare_digest()`.

This module never makes a live GitHub API call and never stores a real
webhook secret or GitHub App private key; `tests/py/domains/
test_github.py` generates test HMAC secrets at runtime
(`secrets.token_bytes()`), never as a literal secret-shaped string, per
issue #101's instruction (gitleaks scans full history).

Replay/idempotency handling (delivery ID dedupe, timestamp/replay
window) is this module's own addition on top of what GitHub's docs
specify, because GitHub's webhook delivery guarantees do not themselves
prevent an attacker who captured a valid signed payload from replaying
it later - see docs/domain-providers.md section 4.4 and
docs/threat-model.md T34.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any


class SignatureVerificationError(Exception):
    pass


def compute_signature(secret: bytes, payload: bytes) -> str:
    """Returns the `X-Hub-Signature-256` header VALUE (including the
    `sha256=` prefix) for `payload`, HMAC-SHA256-keyed by `secret`."""
    digest = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: bytes, payload: bytes, signature_header: str) -> bool:
    """Verifies `signature_header` (the raw `X-Hub-Signature-256` header
    value) against `payload` using a constant-time comparison. Returns
    False for any malformed header rather than raising, so a caller can
    treat "invalid" and "malformed" identically (both mean: reject)."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = compute_signature(secret, payload)
    return hmac.compare_digest(expected, signature_header)


class ReplayGuard:
    """Tracks webhook delivery IDs already processed, and enforces a
    replay window on the event timestamp. In-memory only - a real
    deployment needs durable storage, which is awcms-one's
    responsibility (see docs/domain-providers.md section 4.6)."""

    def __init__(self, replay_window_seconds: int = 300):
        self.replay_window_seconds = replay_window_seconds
        self._seen_delivery_ids: set[str] = set()

    def is_duplicate(self, delivery_id: str) -> bool:
        return delivery_id in self._seen_delivery_ids

    def mark_seen(self, delivery_id: str) -> None:
        self._seen_delivery_ids.add(delivery_id)

    def within_replay_window(self, event_epoch_seconds: int, now_epoch_seconds: int) -> bool:
        age = now_epoch_seconds - event_epoch_seconds
        return 0 <= age <= self.replay_window_seconds


class AccessDeniedError(Exception):
    pass


class RevokedInstallationError(Exception):
    pass


class BranchMismatchError(Exception):
    pass


class FakeGitHubApp:
    """An in-memory GitHub App installation for tests (issue #101: "fake
    webhook and API contract tests"). Never makes a live API call."""

    def __init__(self, installation_id: str, secret: bytes, permissions: dict[str, str], repository_full_names: tuple[str, ...]):
        self.installation_id = installation_id
        self.secret = secret
        self.permissions = dict(permissions)
        self.repository_full_names = set(repository_full_names)
        self.revoked = False
        self.replay_guard = ReplayGuard()
        self.environment_mappings: dict[str, str] = {}  # environment_name -> branch_pattern

    def revoke(self) -> None:
        self.revoked = True

    def map_environment(self, environment_name: str, branch_pattern: str) -> None:
        self.environment_mappings[environment_name] = branch_pattern

    def receive_webhook(
        self,
        *,
        delivery_id: str,
        event: str,
        payload: dict[str, Any],
        payload_bytes: bytes,
        signature_header: str,
        event_epoch_seconds: int,
        now_epoch_seconds: int,
        required_permission: str | None = None,
    ) -> dict[str, Any]:
        """Verifies and accepts (or rejects) one webhook delivery.
        Raises a typed exception for every rejection reason rather than
        returning a bare False, so callers/tests can assert on WHY a
        delivery was rejected."""
        if self.revoked:
            raise RevokedInstallationError(f"installation {self.installation_id} is revoked")

        if not verify_signature(self.secret, payload_bytes, signature_header):
            raise SignatureVerificationError("X-Hub-Signature-256 did not verify")

        if not self.replay_guard.within_replay_window(event_epoch_seconds, now_epoch_seconds):
            raise SignatureVerificationError("event timestamp is outside the replay window")

        if self.replay_guard.is_duplicate(delivery_id):
            # A duplicate delivery is not an error condition to raise on
            # in a live system (GitHub redelivers on its own retries) -
            # it is idempotently ignored. Tests assert on this return
            # shape rather than an exception.
            return {"accepted": False, "reason": "duplicate_delivery", "delivery_id": delivery_id}

        repository_full_name = payload.get("repository", {}).get("full_name")
        if repository_full_name is not None and repository_full_name not in self.repository_full_names:
            raise AccessDeniedError(f"installation {self.installation_id} has no access to {repository_full_name!r}")

        if required_permission is not None and self.permissions.get(required_permission) not in ("read", "write"):
            raise AccessDeniedError(f"installation {self.installation_id} lacks permission {required_permission!r}")

        ref = payload.get("ref") or payload.get("deployment", {}).get("ref")
        environment_name = payload.get("deployment", {}).get("environment") or payload.get("environment")
        if environment_name and ref:
            expected_pattern = self.environment_mappings.get(environment_name)
            if expected_pattern is not None and expected_pattern != ref:
                raise BranchMismatchError(f"environment {environment_name!r} expects ref {expected_pattern!r}, got {ref!r}")

        self.replay_guard.mark_seen(delivery_id)
        return {"accepted": True, "event": event, "delivery_id": delivery_id}
