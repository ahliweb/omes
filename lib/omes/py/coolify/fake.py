"""lib/omes/py/coolify/fake.py - in-memory fake Coolify provider
(issue #97).

Used by default and by every test in tests/py/coolify/ so no test ever
touches the network (see client.py's `OMES_COOLIFY_LIVE` gate for the
opt-in live path). Implements `provider.CoolifyProvider` deterministically:
the same `idempotency_key` always produces the same deployment UUID and
never triggers a second simulated deploy, which is what the idempotency
contract tests in tests/py/coolify/ assert.
"""
from __future__ import annotations

import hashlib
from typing import Any

from . import audit
from .provider import CoolifyProvider, RollbackReferenceUnknownError, require_mapping_fields


def _key(mapping: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        mapping["instance_id"],
        mapping["project"],
        mapping["environment"],
        mapping["resource"],
    )


def _deployment_uuid(idempotency_key: str) -> str:
    """Deterministic, fake-looking Coolify deployment UUID derived from
    the idempotency key - never random, so a replayed request with the
    same key always yields the same UUID without any stored state."""
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"fake{digest[:22]}"


class FakeCoolifyProvider(CoolifyProvider):
    """In-memory only. Nothing here persists across process restarts;
    tests construct a fresh instance per case."""

    def __init__(self, *, actor: str = "coolify-fake-provider", audit_root=None) -> None:
        super().__init__(actor=actor, audit_root=audit_root)
        self._resources: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._idempotency: dict[str, dict[str, Any]] = {}
        self._deploy_counts: dict[tuple[str, str, str, str], int] = {}

    def _resource(self, mapping: dict[str, Any]) -> dict[str, Any]:
        key = _key(mapping)
        return self._resources.setdefault(
            key,
            {
                "deployment_status": "unknown",
                "history": [],
                "healthy": False,
                "target_server": {"server_uuid": f"fake-server-{key[0]}"},
                "proxy_domain_config": {"proxy_type": "none", "domains": []},
            },
        )

    def _deploy(
        self,
        mapping: dict[str, Any],
        correlation_id: str,
        idempotency_key: str,
        *,
        event: str,
        force: bool,
    ) -> dict[str, Any]:
        require_mapping_fields(mapping)
        if idempotency_key in self._idempotency:
            cached = dict(self._idempotency[idempotency_key])
            self._audit(
                correlation_id=correlation_id,
                event=f"{event}.replayed",
                detail={"idempotency_key": idempotency_key, "mapping": mapping},
            )
            return cached

        key = _key(mapping)
        resource = self._resource(mapping)
        from_state = resource["deployment_status"]
        deployment_uuid = _deployment_uuid(idempotency_key)
        resource["history"].append(deployment_uuid)
        resource["deployment_status"] = "finished"
        resource["healthy"] = True
        self._deploy_counts[key] = self._deploy_counts.get(key, 0) + 1

        response = {
            "correlation_id": correlation_id,
            "idempotency_key": idempotency_key,
            "operation": "redeploy" if event == "coolify.redeploy" else "apply",
            "result": "applied",
            "external": {
                "resource_uuid": mapping["resource"],
                "deployment_uuid": deployment_uuid,
            },
            "message": f"deployed (force={force})",
        }
        self._idempotency[idempotency_key] = response
        self._audit(
            correlation_id=correlation_id,
            event=event,
            from_state=from_state,
            to_state=resource["deployment_status"],
            detail={
                "idempotency_key": idempotency_key,
                "mapping": mapping,
                "deployment_uuid": deployment_uuid,
                "deploy_count": self._deploy_counts[key],
            },
        )
        return dict(response)

    def apply(
        self,
        mapping: dict[str, Any],
        correlation_id: str,
        idempotency_key: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        return self._deploy(
            mapping, correlation_id, idempotency_key, event="coolify.apply", force=force
        )

    def redeploy(
        self, mapping: dict[str, Any], correlation_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        return self._deploy(
            mapping, correlation_id, idempotency_key, event="coolify.redeploy", force=True
        )

    def status(self, mapping: dict[str, Any], correlation_id: str) -> dict[str, Any]:
        require_mapping_fields(mapping)
        resource = self._resource(mapping)
        self._audit(correlation_id=correlation_id, event="coolify.status", detail={"mapping": mapping})
        return {
            "correlation_id": correlation_id,
            "idempotency_key": f"status-{correlation_id}",
            "operation": "status",
            "result": "applied" if resource["deployment_status"] == "finished" else "unknown",
            "external": {
                "resource_uuid": mapping["resource"],
                "deployment_uuid": resource["history"][-1] if resource["history"] else "",
            },
            "message": f"deployment_status={resource['deployment_status']}",
        }

    def health(self, mapping: dict[str, Any], correlation_id: str) -> dict[str, Any]:
        require_mapping_fields(mapping)
        resource = self._resource(mapping)
        self._audit(correlation_id=correlation_id, event="coolify.health", detail={"mapping": mapping})
        return {
            "correlation_id": correlation_id,
            "idempotency_key": f"health-{correlation_id}",
            "operation": "health",
            "result": "healthy" if resource["healthy"] else "unhealthy",
            "message": f"healthy={resource['healthy']}",
        }

    def rollback(
        self,
        mapping: dict[str, Any],
        correlation_id: str,
        rollback_ref: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        require_mapping_fields(mapping)
        if idempotency_key in self._idempotency:
            cached = dict(self._idempotency[idempotency_key])
            self._audit(
                correlation_id=correlation_id,
                event="coolify.rollback.replayed",
                detail={"idempotency_key": idempotency_key, "mapping": mapping},
            )
            return cached

        resource = self._resource(mapping)
        if rollback_ref not in resource["history"]:
            self._audit(
                correlation_id=correlation_id,
                event="coolify.rollback.rejected",
                detail={
                    "reason": "rollback_ref not previously observed for this resource",
                    "rollback_ref": rollback_ref,
                    "mapping": mapping,
                },
            )
            raise RollbackReferenceUnknownError(
                f"rollback_ref {rollback_ref!r} was never observed for this resource; "
                "refusing to roll back to an unknown target"
            )

        from_state = resource["deployment_status"]
        new_deployment_uuid = _deployment_uuid(idempotency_key)
        resource["history"].append(new_deployment_uuid)
        resource["deployment_status"] = "finished"
        resource["healthy"] = True
        response = {
            "correlation_id": correlation_id,
            "idempotency_key": idempotency_key,
            "operation": "rollback",
            "result": "rolled_back",
            "external": {
                "resource_uuid": mapping["resource"],
                "deployment_uuid": new_deployment_uuid,
            },
            "message": f"rolled back to {rollback_ref}",
        }
        self._idempotency[idempotency_key] = response
        self._audit(
            correlation_id=correlation_id,
            event="coolify.rollback",
            from_state=from_state,
            to_state=resource["deployment_status"],
            detail={
                "idempotency_key": idempotency_key,
                "mapping": mapping,
                "rollback_ref": rollback_ref,
                "deployment_uuid": new_deployment_uuid,
            },
        )
        return dict(response)

    def observed_state(self, mapping: dict[str, Any], correlation_id: str) -> dict[str, Any]:
        require_mapping_fields(mapping)
        resource = self._resource(mapping)
        return {
            "correlation_id": correlation_id,
            "observed_at": audit.now_iso(),
            "external_resource_id": mapping["resource"],
            "target_server": dict(resource["target_server"]),
            "deployment_status": resource["deployment_status"],
            "build_history_ref": (
                {"deployment_uuid": resource["history"][-1]} if resource["history"] else {}
            ),
            "proxy_domain_config": dict(resource["proxy_domain_config"]),
            "logs_metadata": {
                "available": bool(resource["history"]),
                "ref": f"fake/{mapping['resource']}/logs",
            },
        }

    def deploy_count(self, mapping: dict[str, Any]) -> int:
        """Test helper: how many times an actual (non-replayed) deploy
        happened for this resource."""
        return self._deploy_counts.get(_key(mapping), 0)
