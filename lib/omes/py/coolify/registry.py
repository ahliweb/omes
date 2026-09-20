"""Secure local Coolify instance registry (issue #97).

The registry stores only the provider instance identity, HTTPS API root, and a
credential reference. It never resolves or persists the referenced secret.
Writes are atomic and the registry file is mode 0600 under the mode-0700
Coolify state directory. Every mutation emits the existing hash-chained
Coolify audit record.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from jobs.schema import validate

from . import audit, paths


class RegistryError(Exception):
    """Base class for registry failures."""


class InvalidInstanceError(RegistryError):
    """Raised when an instance-registration request violates its contract."""


class InstanceExistsError(RegistryError):
    """Raised when an ID is already registered with different data."""


class InstanceNotFoundError(RegistryError):
    """Raised when a requested instance ID is absent."""


_SCHEMA_PATH = (
    Path(__file__).resolve().parents[4]
    / "contracts"
    / "coolify"
    / "v1"
    / "instance-registration.request.schema.json"
)


def _schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _validate(instance: dict[str, Any]) -> None:
    errors = validate(instance, _schema())
    if errors:
        raise InvalidInstanceError("; ".join(errors))


def _read(root: Path | None) -> list[dict[str, Any]]:
    path = paths.instances_path(root)
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"cannot read Coolify instance registry: {exc}") from exc
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise RegistryError("Coolify instance registry must contain a JSON array of objects")
    for item in value:
        _validate(item)
    return value


def _write(root: Path | None, records: list[dict[str, Any]]) -> None:
    directory = paths.ensure_layout(root)
    target = paths.instances_path(root)
    fd, temporary = tempfile.mkstemp(prefix="instances.", dir=directory, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(records, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def list_instances(root: Path | None = None) -> list[dict[str, Any]]:
    """Returns registered instances sorted by stable instance ID."""
    return sorted(_read(root), key=lambda item: item["instance_id"])


def get_instance(instance_id: str, root: Path | None = None) -> dict[str, Any]:
    for item in _read(root):
        if item["instance_id"] == instance_id:
            return item
    raise InstanceNotFoundError(f"Coolify instance not found: {instance_id}")


def register_instance(
    record: dict[str, Any],
    root: Path | None = None,
    *,
    actor: str = "coolify-registry",
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Registers an instance, replaying an identical request safely."""
    _validate(record)
    records = _read(root)
    instance_id = record["instance_id"]
    for existing in records:
        if existing["instance_id"] != instance_id:
            continue
        if existing == record:
            audit.append(
                root,
                actor=actor,
                job_id=correlation_id or f"registry:{instance_id}",
                event="instance_registration_replayed",
                detail={"instance_id": instance_id},
            )
            return existing
        raise InstanceExistsError(f"Coolify instance already exists: {instance_id}")
    records.append(dict(record))
    _write(root, records)
    audit.append(
        root,
        actor=actor,
        job_id=correlation_id or f"registry:{instance_id}",
        event="instance_registered",
        detail={"instance_id": instance_id, "base_url": record["base_url"]},
    )
    return dict(record)


def remove_instance(
    instance_id: str,
    root: Path | None = None,
    *,
    actor: str = "coolify-registry",
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Removes an instance registration and records the mutation."""
    records = _read(root)
    removed = next((item for item in records if item["instance_id"] == instance_id), None)
    if removed is None:
        raise InstanceNotFoundError(f"Coolify instance not found: {instance_id}")
    _write(root, [item for item in records if item["instance_id"] != instance_id])
    audit.append(
        root,
        actor=actor,
        job_id=correlation_id or f"registry:{instance_id}",
        event="instance_removed",
        detail={"instance_id": instance_id, "base_url": removed["base_url"]},
    )
    return removed
