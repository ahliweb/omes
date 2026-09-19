"""lib/omes/py/domains/preflight.py - provider-neutral preflight
validation (issue #99: "scoped-token secret reference + preflight
contract"; reused by issue #100's SRS-X preflight).

This module never makes a network call. "Preflight" here means:
structurally validating that a `credential-reference` is well-formed and
declares the scopes a provider profile requires, before any job is
allowed to reference it. A live account/token check (does this token
actually work against the provider) is explicitly out of scope for this
repository - see docs/domain-providers.md "What remains in awcms-one".
"""
from __future__ import annotations

from typing import Any


def run_preflight(
    tenant_id: str,
    correlation_id: str,
    provider: str,
    credential: dict[str, Any],
    required_scopes: tuple[str, ...],
    checked_at: str,
) -> dict[str, Any]:
    """Returns a `provider-preflight.response`-shaped dict.

    `credential` is a `credential-reference` instance (see
    `contracts/domains/v1/credential-reference.schema.json`), never a raw
    secret value - this function only inspects its metadata (`provider`,
    `kind`, `scopes`), never `credential["reference"]`'s contents.
    """
    checks: list[dict[str, Any]] = []

    provider_matches = credential.get("provider") == provider
    checks.append(
        {
            "name": "credential_provider_matches",
            "ok": provider_matches,
            "detail": f"credential provider is {credential.get('provider')!r}, expected {provider!r}",
        }
    )

    reference = credential.get("reference")
    reference_is_secret_ref = isinstance(reference, dict) and {"store", "key"} <= set(reference.keys())
    checks.append({"name": "credential_reference_resolves", "ok": reference_is_secret_ref})

    declared_scopes = set(credential.get("scopes") or [])
    missing_scopes = [s for s in required_scopes if s not in declared_scopes]
    checks.append(
        {
            "name": "token_scope_sufficient",
            "ok": not missing_scopes,
            "detail": "missing: " + ", ".join(missing_scopes) if missing_scopes else "all required scopes present",
        }
    )

    ok = all(check["ok"] for check in checks)
    return {
        "tenant_id": tenant_id,
        "correlation_id": correlation_id,
        "provider": provider,
        "ok": ok,
        "checks": checks,
        "checked_at": checked_at,
    }
