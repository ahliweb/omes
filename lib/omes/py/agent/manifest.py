"""lib/omes/py/agent/manifest.py - AgentDeployment v1 & RuntimeDeployment v2
manifest loading and validation (issues #87, #174).

Validation happens in two layers:

1. Structural: contracts/agent/v1/agent-deployment.schema.json or
   contracts/agent/v2/runtime-deployment.schema.json, checked by
   jsonschema_lite.validate(). This covers types, enums, required
   fields, and string patterns.
2. Semantic: checks the schema's regex/enum keywords cannot express by
   themselves - defence in depth against path traversal and unsafe
   systemd unit identifiers, and runtime/backend validation.

Nothing in this module ever accepts a secret VALUE.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, List

from . import compose as compose_mod
from . import jsonschema_lite

SCHEMA_V1_RELATIVE_PATH = Path("contracts") / "agent" / "v1" / "agent-deployment.schema.json"
SCHEMA_V2_RELATIVE_PATH = Path("contracts") / "agent" / "v2" / "runtime-deployment.schema.json"

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
SUPPORTED_RUNTIMES = ("hermes",)
SUPPORTED_BACKENDS_V1 = ("systemd", "compose")
SUPPORTED_BACKENDS_V2 = ("native", "systemd", "compose")
SUPPORTED_SERVICE_MODES = ("user", "system")

# Reserved unit-name components that must never appear in a derived
# systemd unit name, even though NAME_RE already excludes the characters
# that would normally spell them out.
_UNSAFE_NAME_FRAGMENTS = ("..", "/", "\\", "\0")


class ManifestError(ValueError):
    """Raised with a list of human-readable validation errors."""

    def __init__(self, errors: List[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _schema_path(omes_root: Path, version: str = "v1") -> Path:
    if version == "v2":
        return Path(omes_root) / SCHEMA_V2_RELATIVE_PATH
    return Path(omes_root) / SCHEMA_V1_RELATIVE_PATH


def load_schema(omes_root: Path, version: str = "v1") -> dict:
    with open(_schema_path(omes_root, version=version), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _semantic_errors(data: dict) -> List[str]:
    """Semantic validation for v1 manifests."""
    errors: List[str] = []

    metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
    spec = data.get("spec", {}) if isinstance(data, dict) else {}

    name = metadata.get("name", "")
    if not isinstance(name, str) or not NAME_RE.match(name):
        errors.append(f"metadata.name: '{name}' does not match the required pattern {NAME_RE.pattern}")
    else:
        for fragment in _UNSAFE_NAME_FRAGMENTS:
            if fragment in name:
                errors.append(f"metadata.name: contains unsafe fragment {fragment!r}")

    for field in ("workspace", "environment"):
        value = metadata.get(field, "")
        if isinstance(value, str):
            for fragment in ("..", "/", "\\", "\0"):
                if fragment in value:
                    errors.append(f"metadata.{field}: contains unsafe fragment {fragment!r}")

    runtime = spec.get("runtime")
    if runtime is not None and runtime not in SUPPORTED_RUNTIMES:
        errors.append(f"spec.runtime: '{runtime}' is not supported in the MVP (only {SUPPORTED_RUNTIMES!r})")

    backend = spec.get("backend")
    if backend is not None and backend not in SUPPORTED_BACKENDS_V1:
        errors.append(f"spec.backend: '{backend}' is not supported in the MVP (only {SUPPORTED_BACKENDS_V1!r})")

    service_mode = spec.get("serviceMode")
    if service_mode is not None and service_mode not in SUPPORTED_SERVICE_MODES:
        errors.append(f"spec.serviceMode: '{service_mode}' must be one of {SUPPORTED_SERVICE_MODES!r}")

    profile = spec.get("profile", "")
    if isinstance(profile, str):
        for fragment in _UNSAFE_NAME_FRAGMENTS:
            if fragment in profile:
                errors.append(f"spec.profile: contains unsafe fragment {fragment!r}")

    compose_spec = spec.get("compose")
    if backend == "compose":
        if not isinstance(compose_spec, dict):
            errors.append("spec.compose: required when spec.backend is 'compose'")
        elif isinstance(name, str) and NAME_RE.match(name):
            errors.extend(compose_mod.validate_compose_spec(compose_spec, name))
    elif compose_spec is not None:
        errors.append("spec.compose: must not be set unless spec.backend is 'compose'")

    return errors


def _semantic_errors_v2(data: dict) -> List[str]:
    """Semantic validation for v2 RuntimeDeployment manifests."""
    errors: List[str] = []

    metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
    runtime = data.get("runtime", {}) if isinstance(data, dict) else {}
    placement = data.get("placement", {}) if isinstance(data, dict) else {}

    name = metadata.get("name", "")
    if not isinstance(name, str) or not NAME_RE.match(name):
        errors.append(f"metadata.name: '{name}' does not match the required pattern {NAME_RE.pattern}")
    else:
        for fragment in _UNSAFE_NAME_FRAGMENTS:
            if fragment in name:
                errors.append(f"metadata.name: contains unsafe fragment {fragment!r}")

    for field in ("workspace", "environment"):
        value = metadata.get(field, "")
        if isinstance(value, str):
            for fragment in ("..", "/", "\\", "\0"):
                if fragment in value:
                    errors.append(f"metadata.{field}: contains unsafe fragment {fragment!r}")

    runtime_kind = runtime.get("kind")
    if runtime_kind is not None and runtime_kind not in SUPPORTED_RUNTIMES:
        errors.append(f"runtime.kind: '{runtime_kind}' is not supported (only {SUPPORTED_RUNTIMES!r})")

    profile_ref = runtime.get("profileRef", "")
    if isinstance(profile_ref, str):
        for fragment in _UNSAFE_NAME_FRAGMENTS:
            if fragment in profile_ref:
                errors.append(f"runtime.profileRef: contains unsafe fragment {fragment!r}")

    backend = placement.get("backend")
    if backend is not None and backend not in SUPPORTED_BACKENDS_V2:
        errors.append(f"placement.backend: '{backend}' is not supported (only {SUPPORTED_BACKENDS_V2!r})")

    service_scope = placement.get("serviceScope")
    if service_scope is not None and service_scope not in SUPPORTED_SERVICE_MODES:
        errors.append(f"placement.serviceScope: '{service_scope}' must be one of {SUPPORTED_SERVICE_MODES!r}")

    compose_spec = data.get("compose")
    if backend == "compose":
        if not isinstance(compose_spec, dict):
            errors.append("compose: required when placement.backend is 'compose'")
        elif isinstance(name, str) and NAME_RE.match(name):
            errors.extend(compose_mod.validate_compose_spec(compose_spec, name))
    elif compose_spec is not None:
        errors.append("compose: must not be set unless placement.backend is 'compose'")

    return errors


def validate(data: Any, omes_root: Path) -> List[str]:
    """Returns a list of error strings (empty means valid). Never raises
    for an invalid-but-well-formed manifest."""
    if not isinstance(data, dict):
        schema = load_schema(omes_root, version="v1")
        return jsonschema_lite.validate(data, schema)

    api_version = data.get("apiVersion")
    if api_version == "omes.ahliweb.com/v1":
        schema = load_schema(omes_root, version="v1")
        errors = jsonschema_lite.validate(data, schema)
        errors.extend(_semantic_errors(data))
        return errors
    elif api_version == "omes.ahliweb.com/v2":
        schema = load_schema(omes_root, version="v2")
        errors = jsonschema_lite.validate(data, schema)
        errors.extend(_semantic_errors_v2(data))
        return errors
    else:
        return [f"unsupported or missing apiVersion: '{api_version}' (expected 'omes.ahliweb.com/v1' or 'omes.ahliweb.com/v2')"]


def load_and_validate(path: Path, omes_root: Path, expected_name: str = None) -> dict:
    """Loads a manifest file and validates it, raising ManifestError with
    every collected error on failure."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError([f"could not read/parse manifest {path}: {exc}"]) from exc

    errors = validate(data, omes_root)
    if expected_name is not None and isinstance(data, dict):
        actual_name = data.get("metadata", {}).get("name")
        if actual_name != expected_name:
            errors.append(
                f"manifest file {path.name} declares metadata.name '{actual_name}' "
                f"but was looked up as '{expected_name}' - refusing a mismatched/duplicate name"
            )
    if errors:
        raise ManifestError(errors)
    return data
