"""Tests for lib/omes/py/coolify/client.py (issue #97): the client must
never touch the network unless OMES_COOLIFY_LIVE=1, must never accept a
token except by reading the named environment variable, and must redact
any token/Authorization text that leaks into an error message. No
secret-shaped literal appears in this file - every test token is
generated at runtime with `secrets.token_hex`."""
import io
import os
import secrets
import unittest
import urllib.error
import urllib.request
from unittest import mock

from . import _pathfix  # noqa: F401

from coolify import client  # noqa: E402


def _fresh_token() -> str:
    return secrets.token_hex(20)


class ClientTestBase(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ.pop("OMES_COOLIFY_LIVE", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)


class TestNetworkNeverCalledByDefault(ClientTestBase):
    def test_live_disabled_by_default(self):
        self.assertFalse(client.live_enabled())

    @mock.patch("urllib.request.urlopen")
    def test_request_raises_before_touching_network(self, mock_urlopen):
        token_var = "OMES_COOLIFY_TOKEN_TEST_" + secrets.token_hex(4).upper()
        os.environ[token_var] = _fresh_token()
        c = client.CoolifyClient("https://coolify.example.com/api/v1", token_var)
        with self.assertRaises(client.CoolifyLiveDisabledError):
            c.list_applications()
        mock_urlopen.assert_not_called()

    @mock.patch("urllib.request.urlopen")
    def test_every_public_method_refuses_network_by_default(self, mock_urlopen):
        token_var = "OMES_COOLIFY_TOKEN_TEST_" + secrets.token_hex(4).upper()
        os.environ[token_var] = _fresh_token()
        c = client.CoolifyClient("https://coolify.example.com/api/v1", token_var)
        calls = [
            lambda: c.list_applications(),
            lambda: c.get_application("app-uuid"),
            lambda: c.deploy("app-uuid"),
            lambda: c.get_deployment("dep-uuid"),
            lambda: c.rollback_application("app-uuid", "commit-sha"),
            lambda: c.list_servers(),
            lambda: c.get_server("server-uuid"),
            lambda: c.list_projects(),
            lambda: c.get_project("project-uuid"),
            lambda: c.get_environment("project-uuid", "production"),
        ]
        for call in calls:
            with self.assertRaises(client.CoolifyLiveDisabledError):
                call()
        mock_urlopen.assert_not_called()


class TestTokenNeverPassedDirectly(ClientTestBase):
    def test_missing_token_env_var_fails_before_network(self):
        os.environ["OMES_COOLIFY_LIVE"] = "1"
        token_var = "OMES_COOLIFY_TOKEN_MISSING_" + secrets.token_hex(4).upper()
        os.environ.pop(token_var, None)
        c = client.CoolifyClient("https://coolify.example.com/api/v1", token_var)
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            with self.assertRaises(client.CoolifyTokenMissingError):
                c.list_applications()
            mock_urlopen.assert_not_called()


class TestErrorRedaction(ClientTestBase):
    def setUp(self):
        super().setUp()
        os.environ["OMES_COOLIFY_LIVE"] = "1"
        self.token_var = "OMES_COOLIFY_TOKEN_TEST_" + secrets.token_hex(4).upper()
        self.token = _fresh_token()
        os.environ[self.token_var] = self.token
        self.client = client.CoolifyClient("https://coolify.example.com/api/v1", self.token_var)

    def test_url_error_redacts_bearer_token(self):
        reason = f"connection reset; last header sent was Authorization: Bearer {self.token}"
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError(reason)):
            with self.assertRaises(client.CoolifyClientError) as ctx:
                self.client.list_applications()
        message = str(ctx.exception)
        self.assertNotIn(self.token, message)
        self.assertIn("[REDACTED]", message)

    def test_http_error_redacts_token_in_body(self):
        body = f"invalid request, token={self.token} rejected".encode("utf-8")
        http_error = urllib.error.HTTPError(
            url="https://coolify.example.com/api/v1/applications",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(body),
        )
        with mock.patch("urllib.request.urlopen", side_effect=http_error):
            with self.assertRaises(client.CoolifyClientError) as ctx:
                self.client.list_applications()
        message = str(ctx.exception)
        self.assertNotIn(self.token, message)
        self.assertIn("[REDACTED]", message)

    def test_successful_request_never_logs_the_token(self):
        """Sanity check: the Authorization header carries the token, but
        nothing this client returns or raises on the success path echoes
        it back."""
        response = mock.MagicMock()
        response.read.return_value = b"[]"
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        captured_request = {}

        def fake_urlopen(request, timeout=None):
            captured_request["headers"] = dict(request.header_items())
            return response

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = self.client.list_applications()
        self.assertEqual(result, [])
        # The token IS in the outgoing request header (that's how auth
        # works) - but never in anything the client returns or raises.
        auth_header = captured_request["headers"].get("Authorization", "")
        self.assertIn(self.token, auth_header)
        self.assertNotIn(self.token, repr(result))


if __name__ == "__main__":
    unittest.main()
