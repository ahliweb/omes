"""lib/omes/py/agent/migration.py - Safe migration mapping from AgentDeployment v1
to RuntimeDeployment v2 (issue #174).

Enforces the boundary rules from ADR-0017 and Issue #174:
- Every v1 field is classified into:
    MIGRATE  -> mapped to a corresponding v2 deployment field;
    DELEGATE -> delegated to upstream Hermes profile/bot config;
    DROP     -> obsolete/informational;
    BLOCKED  -> requires explicit operator action.
- Migration is non-destructive: it produces a valid v2 manifest and an audit
  classification report without creating/deleting Hermes profiles or copying credentials.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

FIELD_CLASSIFICATIONS: List[Dict[str, str]] = [
    {
        "v1_field": "apiVersion",
        "action": "MIGRATE",
        "target": "apiVersion: omes.ahliweb.com/v2",
        "description": "Upgraded to versioned RuntimeDeployment v2 contract.",
    },
    {
        "v1_field": "kind",
        "action": "MIGRATE",
        "target": "kind: RuntimeDeployment",
        "description": "Standardized to RuntimeDeployment (AgentDeployment supported as kind alias).",
    },
    {
        "v1_field": "metadata.name",
        "action": "MIGRATE",
        "target": "metadata.name",
        "description": "Retained as deployment identity, decoupled from Hermes profile identity.",
    },
    {
        "v1_field": "metadata.workspace",
        "action": "MIGRATE",
        "target": "metadata.workspace",
        "description": "Retained for logical grouping/multi-agent tenant boundaries.",
    },
    {
        "v1_field": "metadata.environment",
        "action": "MIGRATE",
        "target": "metadata.environment",
        "description": "Retained for environment scope (production, staging, development).",
    },
    {
        "v1_field": "spec.runtime",
        "action": "MIGRATE",
        "target": "runtime.kind",
        "description": "Retained as runtime engine type ('hermes').",
    },
    {
        "v1_field": "spec.profile",
        "action": "MIGRATE",
        "target": "runtime.profileRef",
        "description": "Mapped to explicit Hermes profile reference resolved via Hermes CLI.",
    },
    {
        "v1_field": "spec.backend",
        "action": "MIGRATE",
        "target": "placement.backend",
        "description": "Host execution backend ('native', 'systemd', 'compose').",
    },
    {
        "v1_field": "spec.serviceMode",
        "action": "MIGRATE",
        "target": "placement.serviceScope",
        "description": "Host privilege execution scope ('user', 'system').",
    },
    {
        "v1_field": "spec.restartPolicy",
        "action": "MIGRATE",
        "target": "placement.restartPolicy",
        "description": "Host service restart policy ('always', 'on-failure', 'no').",
    },
    {
        "v1_field": "spec.resources",
        "action": "MIGRATE",
        "target": "resources",
        "description": "Host resource limits (memory, cpu, pids) enforced by systemd/cgroups.",
    },
    {
        "v1_field": "spec.health",
        "action": "MIGRATE",
        "target": "health",
        "description": "Deployment health verification command and bounded timeout.",
    },
    {
        "v1_field": "spec.compose",
        "action": "MIGRATE",
        "target": "compose",
        "description": "Rootless Docker compose container placement parameters.",
    },
    {
        "v1_field": "spec.role",
        "action": "DELEGATE",
        "target": "Hermes profile / bot mode config",
        "description": "Hermes owns bot persona, instructions, and specialist role configuration.",
    },
    {
        "v1_field": "spec.capabilities",
        "action": "DELEGATE",
        "target": "Hermes profile toolsets / skills",
        "description": "Hermes owns skill discovery, MCP tools, and runtime capability allowlists.",
    },
    {
        "v1_field": "spec.deny",
        "action": "DELEGATE",
        "target": "Hermes profile approval / deny rules",
        "description": "Hermes owns command approval and tool execution policy.",
    },
    {
        "v1_field": "spec.storage.memory",
        "action": "DELEGATE",
        "target": "Hermes profile memory backend",
        "description": "Hermes owns session memory isolation and storage semantics.",
    },
    {
        "v1_field": "spec.storage.sessions",
        "action": "DELEGATE",
        "target": "Hermes profile session backend",
        "description": "Hermes owns session persistence and history.",
    },
    {
        "v1_field": "spec.storage.skills",
        "action": "DELEGATE",
        "target": "Hermes profile skill store",
        "description": "Hermes owns skill directory and git skill checkout.",
    },
    {
        "v1_field": "spec.secrets",
        "action": "DELEGATE",
        "target": "Hermes profile credentials ($HERMES_HOME/.env)",
        "description": "Hermes owns profile credentials; OMES does not copy or manage API keys.",
    },
]


class MigrationError(ValueError):
    """Raised when a v1 manifest cannot be safely migrated."""


def migrate_manifest_v1_to_v2(v1_data: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Converts a valid v1 AgentDeployment manifest into a v2 RuntimeDeployment manifest.

    Returns:
        (v2_manifest, audit_records)
    """
    if not isinstance(v1_data, dict):
        raise MigrationError("v1 manifest must be a JSON object")

    api_version = v1_data.get("apiVersion")
    if api_version != "omes.ahliweb.com/v1":
        raise MigrationError(f"Expected apiVersion 'omes.ahliweb.com/v1', got '{api_version}'")

    metadata = v1_data.get("metadata", {})
    spec = v1_data.get("spec", {})

    name = metadata.get("name")
    if not name:
        raise MigrationError("v1 manifest missing metadata.name")

    profile = spec.get("profile") or name
    backend = spec.get("backend", "systemd")
    service_scope = spec.get("serviceMode", "user")
    restart_policy = spec.get("restartPolicy", "always")

    v2_manifest: Dict[str, Any] = {
        "apiVersion": "omes.ahliweb.com/v2",
        "kind": "RuntimeDeployment",
        "metadata": {
            "name": name,
            "environment": metadata.get("environment", "production"),
        },
        "runtime": {
            "kind": spec.get("runtime", "hermes"),
            "profileRef": profile,
        },
        "placement": {
            "backend": backend,
            "serviceScope": service_scope,
            "restartPolicy": restart_policy,
        },
    }

    if "workspace" in metadata:
        v2_manifest["metadata"]["workspace"] = metadata["workspace"]

    if "resources" in spec and isinstance(spec["resources"], dict):
        v2_manifest["resources"] = dict(spec["resources"])

    if "health" in spec and isinstance(spec["health"], dict):
        health_spec = spec["health"]
        v2_manifest["health"] = {
            "adapter": "command",
            "command": health_spec.get("command", f"omes agent health {name}"),
            "timeout": health_spec.get("timeout", "10s"),
        }

    is_compose = backend == "compose"
    v2_manifest["security"] = {
        "hardeningProfile": "strict",
        "exposurePolicy": "loopback",
        "isolationClass": "rootless-container" if is_compose else "standard",
    }

    env = metadata.get("environment", "production")
    v2_manifest["recovery"] = {
        "policy": "production" if env == "production" else "staging",
    }

    if is_compose and "compose" in spec and isinstance(spec["compose"], dict):
        v2_manifest["compose"] = dict(spec["compose"])

    audit_records: List[Dict[str, str]] = []
    for classification in FIELD_CLASSIFICATIONS:
        record = dict(classification)
        v1_field = record["v1_field"]
        if v1_field.startswith("spec."):
            key = v1_field.split(".", 1)[1]
            if "." in key:
                parent, child = key.split(".", 1)
                present = parent in spec and isinstance(spec[parent], dict) and child in spec[parent]
            else:
                present = key in spec
            record["present_in_source"] = str(present)
        else:
            record["present_in_source"] = "true"
        audit_records.append(record)

    return v2_manifest, audit_records
