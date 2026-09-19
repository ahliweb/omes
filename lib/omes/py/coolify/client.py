"""lib/omes/py/coolify/client.py - thin urllib-based Coolify API client
(issue #97).

Endpoints used (verified against the Coolify OpenAPI spec at
https://github.com/coollabsio/coolify, `openapi.yaml`, and
https://coolify.io/docs/api/overview - base URL
`https://<instance>/api/v1`, Bearer token auth):

    GET  /applications
    GET  /applications/{uuid}
    POST /deploy?uuid=...&force=...          (deploy-by-tag-or-uuid)
    GET  /deployments
    GET  /deployments/{uuid}
    GET  /deployments/applications/{uuid}
    POST /applications/{uuid}/rollback       (body: {"commit": ...})
    GET  /servers
    GET  /servers/{uuid}
    GET  /projects
    GET  /projects/{uuid}
    GET  /projects/{uuid}/{environment_name_or_uuid}

No other endpoint is invented. The token is never accepted as a
constructor/CLI argument - only the *name* of the environment variable
that holds it (the credential_ref.key from
contracts/coolify/v1/instance-registration.request.schema.json), read at
call time with `os.environ`. The token is never logged, put in an
exception message unredacted, or passed on any argv. By default this
client makes zero network calls: `_request()` raises
`CoolifyLiveDisabledError` unless `OMES_COOLIFY_LIVE=1` is set in the
environment, so the default test suite (and any accidental production
misconfiguration) never reaches the network.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import audit

DEFAULT_TIMEOUT_SECONDS = 30


class CoolifyClientError(Exception):
    """Raised for any client-side or transport failure. The message is
    always redacted before it reaches this exception (see `_redact`)."""


class CoolifyLiveDisabledError(CoolifyClientError):
    """Raised by every network-calling method unless
    OMES_COOLIFY_LIVE=1 is set - the opt-in-only gate required by the
    issue #97 brief ("default tests must never touch the network")."""


class CoolifyTokenMissingError(CoolifyClientError):
    """Raised when the environment variable named by `token_env_var`
    is not set - never falls back to a literal/default token."""


def _redact(text: str) -> str:
    return audit.redact_text(text)


def live_enabled() -> bool:
    return os.environ.get("OMES_COOLIFY_LIVE") == "1"


class CoolifyClient:
    """One registered Coolify instance. `base_url` must already be the
    API root (e.g. `https://coolify.example.com/api/v1`); `token_env_var`
    is the *name* of the environment variable holding the Bearer token -
    never the token value itself."""

    def __init__(self, base_url: str, token_env_var: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._base_url = base_url.rstrip("/")
        self._token_env_var = token_env_var
        self._timeout = timeout

    def _token(self) -> str:
        token = os.environ.get(self._token_env_var)
        if not token:
            raise CoolifyTokenMissingError(
                f"environment variable {self._token_env_var} is not set; "
                "the Coolify API token must never be passed as an argument"
            )
        return token

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not live_enabled():
            raise CoolifyLiveDisabledError(
                "OMES_COOLIFY_LIVE is not set to '1'; refusing to make a real "
                "Coolify API call (this is the opt-in-only live gate, see "
                "docs/coolify-adapter.md)"
            )

        url = f"{self._base_url}{path}"
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if query:
                url = f"{url}?{query}"

        # The token is read fresh for every call and only ever placed in
        # this one Authorization header - never in the URL, never logged.
        token = self._token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310 - urllib is stdlib-only per ADR-0012
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise CoolifyClientError(
                _redact(f"Coolify API HTTP {exc.code} for {method} {path}: {body}")
            ) from None
        except urllib.error.URLError as exc:
            raise CoolifyClientError(
                _redact(f"Coolify API request failed for {method} {path}: {exc}")
            ) from None

        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise CoolifyClientError(
                _redact(f"Coolify API returned non-JSON response for {method} {path}: {exc}")
            ) from None

    # -- Applications ------------------------------------------------
    def list_applications(self, *, tag: str | None = None) -> list[dict[str, Any]]:
        result = self._request("GET", "/applications", params={"tag": tag})
        return result if isinstance(result, list) else []

    def get_application(self, uuid: str) -> dict[str, Any]:
        return self._request("GET", f"/applications/{urllib.parse.quote(uuid)}")

    def rollback_application(self, uuid: str, commit: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/applications/{urllib.parse.quote(uuid)}/rollback",
            json_body={"commit": commit},
        )

    # -- Deployments ---------------------------------------------------
    def deploy(self, uuid: str, *, force: bool = False) -> dict[str, Any]:
        return self._request("POST", "/deploy", params={"uuid": uuid, "force": force})

    def get_deployment(self, uuid: str) -> dict[str, Any]:
        return self._request("GET", f"/deployments/{urllib.parse.quote(uuid)}")

    def list_deployments_for_application(
        self, uuid: str, *, skip: int = 0, take: int = 10
    ) -> list[dict[str, Any]]:
        result = self._request(
            "GET",
            f"/deployments/applications/{urllib.parse.quote(uuid)}",
            params={"skip": skip, "take": take},
        )
        return result if isinstance(result, list) else []

    # -- Servers ---------------------------------------------------------
    def list_servers(self) -> list[dict[str, Any]]:
        result = self._request("GET", "/servers")
        return result if isinstance(result, list) else []

    def get_server(self, uuid: str) -> dict[str, Any]:
        return self._request("GET", f"/servers/{urllib.parse.quote(uuid)}")

    # -- Projects and environments ---------------------------------------
    def list_projects(self) -> list[dict[str, Any]]:
        result = self._request("GET", "/projects")
        return result if isinstance(result, list) else []

    def get_project(self, uuid: str) -> dict[str, Any]:
        return self._request("GET", f"/projects/{urllib.parse.quote(uuid)}")

    def get_environment(self, project_uuid: str, environment_name_or_uuid: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/projects/{urllib.parse.quote(project_uuid)}/{urllib.parse.quote(environment_name_or_uuid)}",
        )
