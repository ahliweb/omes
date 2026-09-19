"""Issue #101 acceptance-criteria tests: installation, access denied,
duplicate delivery, revoked app, branch mismatch, and deployment status
change - all against the fake webhook/API implementation in
lib/omes/py/domains/github.py.

Test HMAC secrets are generated at runtime via `secrets.token_bytes()` -
never a secret-shaped literal - per issue #101's explicit instruction
(gitleaks scans full history)."""
import json
import secrets
import unittest

from . import _pathfix  # noqa: F401

from domains import github


def _secret() -> bytes:
    return secrets.token_bytes(32)


def _payload_bytes(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


class TestSignatureVerification(unittest.TestCase):
    def test_valid_signature_verifies(self):
        secret = _secret()
        payload = _payload_bytes({"hello": "world"})
        signature = github.compute_signature(secret, payload)
        self.assertTrue(github.verify_signature(secret, payload, signature))

    def test_tampered_payload_fails_verification(self):
        secret = _secret()
        payload = _payload_bytes({"hello": "world"})
        signature = github.compute_signature(secret, payload)
        tampered = _payload_bytes({"hello": "tampered"})
        self.assertFalse(github.verify_signature(secret, tampered, signature))

    def test_wrong_secret_fails_verification(self):
        payload = _payload_bytes({"hello": "world"})
        signature = github.compute_signature(_secret(), payload)
        self.assertFalse(github.verify_signature(_secret(), payload, signature))

    def test_malformed_header_is_rejected_not_raised(self):
        secret = _secret()
        payload = _payload_bytes({"hello": "world"})
        self.assertFalse(github.verify_signature(secret, payload, "not-a-valid-header"))
        self.assertFalse(github.verify_signature(secret, payload, ""))

    def test_signature_always_has_sha256_prefix(self):
        signature = github.compute_signature(_secret(), b"x")
        self.assertTrue(signature.startswith("sha256="))


class TestFakeGitHubAppWebhook(unittest.TestCase):
    def _app(self, **overrides) -> github.FakeGitHubApp:
        defaults = dict(
            installation_id="install-0001",
            secret=_secret(),
            permissions={"deployments": "write", "contents": "read"},
            repository_full_names=("acme-org/storefront",),
        )
        defaults.update(overrides)
        return github.FakeGitHubApp(**defaults)

    def _send(self, app: github.FakeGitHubApp, payload: dict, **overrides):
        payload_bytes = _payload_bytes(payload)
        kwargs = dict(
            delivery_id="delivery-0001",
            event="deployment_status",
            payload=payload,
            payload_bytes=payload_bytes,
            signature_header=github.compute_signature(app.secret, payload_bytes),
            event_epoch_seconds=1_000_000,
            now_epoch_seconds=1_000_010,
        )
        kwargs.update(overrides)
        return app.receive_webhook(**kwargs)

    def test_installation_accepts_a_valid_delivery(self):
        app = self._app()
        payload = {"repository": {"full_name": "acme-org/storefront"}, "deployment": {"environment": "production", "ref": "main"}}
        result = self._send(app, payload)
        self.assertTrue(result["accepted"])

    def test_access_denied_for_repository_outside_installation(self):
        app = self._app()
        payload = {"repository": {"full_name": "other-org/other-repo"}}
        with self.assertRaises(github.AccessDeniedError):
            self._send(app, payload)

    def test_access_denied_for_missing_required_permission(self):
        app = self._app(permissions={"contents": "read"})  # no "deployments"
        payload = {"repository": {"full_name": "acme-org/storefront"}}
        with self.assertRaises(github.AccessDeniedError):
            self._send(app, payload, required_permission="deployments")

    def test_duplicate_delivery_is_idempotently_ignored(self):
        app = self._app()
        payload = {"repository": {"full_name": "acme-org/storefront"}}
        first = self._send(app, payload)
        self.assertTrue(first["accepted"])
        second = self._send(app, payload)
        self.assertFalse(second["accepted"])
        self.assertEqual(second["reason"], "duplicate_delivery")

    def test_revoked_installation_rejects_every_delivery(self):
        app = self._app()
        app.revoke()
        payload = {"repository": {"full_name": "acme-org/storefront"}}
        with self.assertRaises(github.RevokedInstallationError):
            self._send(app, payload)

    def test_branch_mismatch_is_rejected(self):
        app = self._app()
        app.map_environment("production", "main")
        payload = {
            "repository": {"full_name": "acme-org/storefront"},
            "deployment": {"environment": "production", "ref": "feature-branch"},
        }
        with self.assertRaises(github.BranchMismatchError):
            self._send(app, payload)

    def test_deployment_status_change_is_accepted_when_branch_matches(self):
        app = self._app()
        app.map_environment("production", "main")
        payload = {
            "repository": {"full_name": "acme-org/storefront"},
            "deployment": {"environment": "production", "ref": "main"},
        }
        result = self._send(app, payload, event="deployment_status")
        self.assertTrue(result["accepted"])

    def test_invalid_signature_is_rejected(self):
        app = self._app()
        payload = {"repository": {"full_name": "acme-org/storefront"}}
        with self.assertRaises(github.SignatureVerificationError):
            self._send(app, payload, signature_header="sha256=" + "0" * 64)

    def test_event_outside_replay_window_is_rejected(self):
        app = self._app()
        payload = {"repository": {"full_name": "acme-org/storefront"}}
        with self.assertRaises(github.SignatureVerificationError):
            self._send(app, payload, event_epoch_seconds=1_000_000, now_epoch_seconds=1_000_000 + app.replay_guard.replay_window_seconds + 1)


class TestReplayGuard(unittest.TestCase):
    def test_within_window_true_at_boundary(self):
        guard = github.ReplayGuard(replay_window_seconds=300)
        self.assertTrue(guard.within_replay_window(1000, 1300))
        self.assertFalse(guard.within_replay_window(1000, 1301))

    def test_future_event_timestamp_is_rejected(self):
        guard = github.ReplayGuard(replay_window_seconds=300)
        self.assertFalse(guard.within_replay_window(1300, 1000))


if __name__ == "__main__":
    unittest.main()
