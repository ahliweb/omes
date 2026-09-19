"""lib/omes/py/agent/manifest.py - AgentDeployment manifest loading and
validation (issue #87).

Validation happens in two layers:

1. Structural: contracts/agent/v1/agent-deployment.schema.json, checked
   by jsonschema_lite.validate(). This covers types, enums, required
   fields, and most string patterns (including the name/profile regexes
   and the secret-reference pattern).
2. Semantic: checks the schema's regex/enum keywords cannot express by
   themselves - defence in depth against path traversal and unsafe
   systemd unit identifiers, and the two "MVP only supports X" business
   rules (runtime == hermes, backend == systemd) called out explicitly by
   name in the issue so a future schema edit that widens the enum still
   gets caught here too.

Nothing in this module ever accepts a secret VALUE - `spec.secrets` is
schema-constrained to bare reference names (see the schema's pattern),
and this module never resolves or dereferences them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, List

from . import jsonschema_lite

SCHEMA_RELATIVE_PATH = Path("contracts") / "agent" / "v1" / "agent-deployment.schema.json"

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
SUPPORTED_RUNTIMES = ("hermes",)
SUPPORTED_BACKENDS = ("systemd",)
SUPPORTED_SERVICE_MODES = ("user", "system")

# Reserved unit-name components that must never appear in a derived
# systemd unit name, even though NAME_RE already excludes the characters
# that would normally spell them out (defence in depth, and it documents
# intent for anyone editing NAME_RE later).
_UNSAFE_NAME_FRAGMENTS = ("..", "/", "\\", "\0")


class ManifestError(ValueError):
    """Raised with a list of human-readable validation errors."""

    def __init__(self, errors: List[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _schema_path(omes_root: Path) -> Path:
    return Path(omes_root) / SCHEMA_RELATIVE_PATH


def load_schema(omes_root: Path) -> dict:
    with open(_schema_path(omes_root), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _semantic_errors(data: dict) -> List[str]:
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
    if backend is not None and backend not in SUPPORTED_BACKENDS:
        errors.append(f"spec.backend: '{backend}' is not supported in the MVP (only {SUPPORTED_BACKENDS!r})")

    service_mode = spec.get("serviceMode")
    if service_mode is not None and service_mode not in SUPPORTED_SERVICE_MODES:
        errors.append(f"spec.serviceMode: '{service_mode}' must be one of {SUPPORTED_SERVICE_MODES!r}")

    profile = spec.get("profile", "")
    if isinstance(profile, str):
        for fragment in _UNSAFE_NAME_FRAGMENTS:
            if fragment in profile:
                errors.append(f"spec.profile: contains unsafe fragment {fragment!r}")

    return errors


def validate(data: Any, omes_root: Path) -> List[str]:
    """Returns a list of error strings (empty means valid). Never raises
    for an invalid-but-well-formed manifest; raises only if `data` is not
    even the right top-level shape for the schema loader itself (that
    case is folded into the returned error list by jsonschema_lite for a
    non-dict `data` too, so callers can treat this uniformly)."""
    schema = load_schema(omes_root)
    errors = jsonschema_lite.validate(data, schema)
    if isinstance(data, dict):
        errors.extend(_semantic_errors(data))
    return errors


def load_and_validate(path: Path, omes_root: Path, expected_name: str = None) -> dict:
    """Loads a manifest file and validates it, raising ManifestError with
    every collected error on failure (never partially-applies a manifest
    that failed validation - see plan.py/cli.py callers).

    `expected_name`, when given, must equal `metadata.name` - this is how
    callers that look manifests up by filename (paths.manifest_path(name))
    catch a duplicate/mismatched declaration: two files cannot silently
    both claim to be the same logical agent, and a copy-pasted manifest
    saved under a new filename cannot silently keep the old agent's name
    (which would collide on the derived systemd unit name)."""
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
