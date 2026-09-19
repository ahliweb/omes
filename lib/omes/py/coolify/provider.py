"""lib/omes/py/coolify/provider.py - the Coolify adapter interface
(issue #97).

Any Coolify backend - `fake.py`'s in-memory provider (used by default and
by every test), or a future live provider built on `client.py` - implements
`CoolifyProvider`. Every implementation's return shape mirrors
contracts/coolify/v1/deployment.response.schema.json so a caller never
needs to know which provider produced a response, and every mutating
operation is audited through `_audit()` using the same record shape as
lib/omes/py/jobs/audit.py (see coolify/audit.py's docstring).

Per docs/agent-orchestration-roadmap.md section 2.3 and AGENTS.md section
2: OMES remains the source of truth for logical deployment identity, role
and capability policy, secret references, data classification, health
contract, backup policy, and desired intent; Coolify remains the source
of truth for the delegated resource's external id, target server,
deployment status, build/deployment history, proxy/domain configuration,
and platform-specific logs. A provider implementation must never claim to
own the logical side, and must never return a raw log body (only
`logs_metadata`, matching observed-state.schema.json).
"""
from __future__ import annotations

import abc
from typing import Any

from . import audit


class CoolifyProviderError(Exception):
    """Base class for adapter errors (fail closed - never silently
    substitute a provider or invent a resource)."""


class MappingRequiredFieldsError(CoolifyProviderError):
    """Raised when `mapping` is missing instance_id/project/environment/
    resource, or when `resource` is not a single unambiguous string."""


class RollbackReferenceUnknownError(CoolifyProviderError):
    """Raised when a rollback is requested for a `rollback_ref` (Coolify
    deployment UUID) this provider has no record of - never invent a
    target to roll back to."""


OPERATIONS = ("apply", "status", "health", "redeploy", "rollback")


def require_mapping_fields(mapping: dict[str, Any]) -> None:
    """Fail-closed mapping validation shared by every provider
    implementation. Mirrors the schema-level ambiguity rule in
    contracts/coolify/v1/mapping.schema.json: each of instance_id,
    project, environment, resource must be present and a single
    non-empty string - not missing, not a list of candidates."""
    required = ("instance_id", "project", "environment", "resource")
    missing = [name for name in required if not mapping.get(name)]
    if missing:
        raise MappingRequiredFieldsError(
            f"mapping is missing required field(s): {', '.join(missing)}"
        )
    for name in required:
        value = mapping[name]
        if not isinstance(value, str):
            raise MappingRequiredFieldsError(
                f"mapping.{name} must resolve to exactly one string identifier, "
                f"got {type(value).__name__} (ambiguous match)"
            )


class CoolifyProvider(abc.ABC):
    """Abstract Coolify adapter. `audit_root` is where this provider's
    audit log lives (None resolves to the default OMES state directory,
    same convention as lib/omes/py/jobs)."""

    def __init__(self, *, actor: str = "coolify-adapter", audit_root=None) -> None:
        self._actor = actor
        self._audit_root = audit_root

    def _audit(
        self,
        *,
        correlation_id: str,
        event: str,
        from_state: str | None = None,
        to_state: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return audit.append(
            self._audit_root,
            actor=self._actor,
            job_id=correlation_id,
            event=event,
            from_state=from_state,
            to_state=to_state,
            detail=detail,
        )

    @abc.abstractmethod
    def apply(
        self,
        mapping: dict[str, Any],
        correlation_id: str,
        idempotency_key: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Deploys the mapped resource (Coolify `POST /deploy`).
        Idempotent: the same `idempotency_key` must return the same
        result without triggering a second deploy."""

    @abc.abstractmethod
    def status(self, mapping: dict[str, Any], correlation_id: str) -> dict[str, Any]:
        """Reads current deployment status (Coolify
        `GET /applications/{uuid}` / `GET /deployments/{uuid}`)."""

    @abc.abstractmethod
    def health(self, mapping: dict[str, Any], correlation_id: str) -> dict[str, Any]:
        """Reads current health (derived from status; Coolify has no
        separate health endpoint for a generic application)."""

    @abc.abstractmethod
    def redeploy(
        self, mapping: dict[str, Any], correlation_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        """Forces a rebuild/redeploy (Coolify `POST /deploy?force=true`).
        Idempotent on `idempotency_key` like `apply`."""

    @abc.abstractmethod
    def rollback(
        self,
        mapping: dict[str, Any],
        correlation_id: str,
        rollback_ref: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Rolls back to a previously observed Coolify deployment UUID
        (Coolify `POST /applications/{uuid}/rollback`). Fails closed
        (`RollbackReferenceUnknownError`) if `rollback_ref` was never
        observed for this resource - never invent a rollback target.
        Idempotent on `idempotency_key`."""

    @abc.abstractmethod
    def observed_state(self, mapping: dict[str, Any], correlation_id: str) -> dict[str, Any]:
        """Returns the current observed-state.schema.json-shaped record
        for the mapped resource (used by mapping.py's drift detection and
        reconcile.py)."""
