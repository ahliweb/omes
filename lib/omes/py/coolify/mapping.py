"""lib/omes/py/coolify/mapping.py - the OMES logical deployment identity
<-> Coolify project/environment/resource mapping (issue #97), matching
contracts/coolify/v1/mapping.schema.json.

Fails closed: registering, resolving, or comparing a mapping never
proceeds with a missing or ambiguous instance/project/environment/
resource. `correlation_id` and `created_at` are immutable once a mapping
is registered - re-mapping a deployment to a different Coolify resource
requires `remap()`, which records a brand-new correlation_id rather than
mutating the existing one in place, so the mapping's own history is an
audit trail.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import paths
from .provider import MappingRequiredFieldsError, require_mapping_fields

OBSERVED_STATE_KEYS = (
    "external_resource_id",
    "target_server",
    "deployment_status",
    "build_history_ref",
    "proxy_domain_config",
    "logs_metadata",
)


class MappingError(Exception):
    """Base class for mapping-store errors."""


class MappingNotFoundError(MappingError):
    pass


class MappingAlreadyExistsError(MappingError):
    """Raised by `register()` when a mapping already exists for this
    deployment_id - call `remap()` to deliberately replace it."""


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)


def register(
    deployment_id: str,
    coolify: dict[str, Any],
    correlation_id: str,
    created_at: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Registers a new mapping. Fails closed via `require_mapping_fields`
    (missing or ambiguous instance_id/project/environment/resource) and
    refuses to overwrite an existing mapping (use `remap`)."""
    if not deployment_id:
        raise MappingRequiredFieldsError("deployment_id is required")
    require_mapping_fields(coolify)

    path = paths.mapping_path(deployment_id, root)
    if path.is_file():
        raise MappingAlreadyExistsError(
            f"a mapping already exists for deployment_id {deployment_id!r}; use remap()"
        )

    record = {
        "correlation_id": correlation_id,
        "created_at": created_at,
        "omes": {"deployment_id": deployment_id},
        "coolify": dict(coolify),
    }
    _write_json(path, record)
    return record


def remap(
    deployment_id: str,
    coolify: dict[str, Any],
    correlation_id: str,
    created_at: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Deliberately replaces an existing mapping with a new
    correlation_id/created_at. Unlike `register`, this succeeds even if a
    mapping already exists - the immutability rule is that a single
    mapping record's own correlation_id/created_at never change once
    written, not that a deployment can never be remapped."""
    require_mapping_fields(coolify)
    record = {
        "correlation_id": correlation_id,
        "created_at": created_at,
        "omes": {"deployment_id": deployment_id},
        "coolify": dict(coolify),
    }
    _write_json(paths.mapping_path(deployment_id, root), record)
    return record


def resolve(deployment_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """Returns the stored mapping record or fails closed with
    MappingNotFoundError - never returns a partial/default mapping."""
    path = paths.mapping_path(deployment_id, root)
    if not path.is_file():
        raise MappingNotFoundError(f"no mapping registered for deployment_id {deployment_id!r}")
    record = json.loads(path.read_text(encoding="utf-8"))
    require_mapping_fields(record.get("coolify", {}))
    return record


def read_observed(deployment_id: str, *, root: Path | None = None) -> dict[str, Any]:
    path = paths.observed_path(deployment_id, root)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_observed(deployment_id: str, observed: dict[str, Any], *, root: Path | None = None) -> None:
    _write_json(paths.observed_path(deployment_id, root), observed)


def detect_drift(
    coolify_mapping: dict[str, Any],
    desired_observed: dict[str, Any],
    fresh_observed: dict[str, Any],
) -> dict[str, Any]:
    """Compares the last stored observed-state snapshot for a mapping
    against a fresh read, over exactly the closed observed-state key set
    (contracts/coolify/v1/observed-state.schema.json). Returns a
    drift-report.schema.json-shaped dict. `coolify_mapping` must already
    have passed `require_mapping_fields` (fail closed on an ambiguous or
    incomplete mapping before ever comparing state for it)."""
    require_mapping_fields(coolify_mapping)
    changed = [
        key
        for key in OBSERVED_STATE_KEYS
        if desired_observed.get(key) != fresh_observed.get(key)
    ]
    return {
        "correlation_id": fresh_observed.get("correlation_id", desired_observed.get("correlation_id", "")),
        "generated_at": fresh_observed.get("observed_at", ""),
        "mapping": dict(coolify_mapping),
        "has_drift": bool(changed),
        "fields": changed,
    }
