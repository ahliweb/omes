"""lib/omes/py/jobs/schema.py - a minimal, dependency-free JSON Schema
(draft 2020-12 subset) validator.

Why this exists (ADR-0012, issue #89/#90): OMES is Python-stdlib-only, so
`jsonschema` and other PyPI validators are not available. This module
implements only the keywords the OMES/Control Center contracts under
`contracts/control-center/v1/*.schema.json` actually use:

    type, required, properties, additionalProperties, enum, const,
    pattern, minimum, maximum, minItems, maxItems, items, oneOf, anyOf

It is deliberately not a general-purpose JSON Schema engine. Anything
outside that keyword list is ignored rather than rejected, so schema
authors must keep contracts inside this subset (see
docs/control-center-contracts.md "Validator subset").

`scripts/check-contracts.py` is the CLI entry point that walks
`contracts/**/v1/fixtures/**` and validates every fixture against its
schema; it imports this module rather than duplicating it (the CLI must
stay a thin wrapper so `lib/omes/py/jobs/cli.py` can reuse the exact same
validation code path when validating a live `omes job submit` request).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Secret-value ban (docs/control-center-contracts.md "Authentication and
# secret-reference rules"): no field whose name looks like it carries a
# secret (token/password/secret/credential/api_key/...) may hold a raw
# scalar value anywhere in a contract instance. The only allowed shape for
# such a field is a `secret_ref` object (`{"store": ..., "key": ...}`) or
# `null`. This is enforced independently of whatever schema is in use,
# because a schema bug must never be the only thing standing between a
# fixture/request and a leaked secret.
# ---------------------------------------------------------------------------

_SECRET_NAME_RE = re.compile(
    r"(token|password|secret|credential|api[_-]?key|passphrase|cookie|authorization)", re.IGNORECASE
)

# Identifier shape allowed as an element of a list under a secret-like key
# (a secret NAME, never a value): letters/digits/._- only, <= 64 chars.
_SECRET_NAME_ITEM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")

# Defense in depth beyond the key-name check below: a handful of
# well-known secret-value shapes (Stripe, GitHub, AWS, Slack, generic
# bearer tokens) are rejected wherever they appear, even under a field
# name that does not look secret-like. A key-name-only ban would miss a
# secret placed under a misleadingly generic field (e.g. "notes"). None
# of these literals are real credentials - they are the well-documented
# public prefix formats those providers use.
_SECRET_VALUE_SHAPE_RE = re.compile(
    r"(sk_live_|sk_test_|gh[pousr]_[A-Za-z0-9]|AKIA[0-9A-Z]{12,}|xox[baprs]-|Bearer [A-Za-z0-9._-]{10,})"
)


class SchemaError(Exception):
    """Raised for a malformed schema (a bug in the contract itself)."""


class ValidationError(Exception):
    """Raised by `validate()` with every failure reason collected."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors) if errors else "validation failed")
        self.errors = errors


def _type_matches(instance: Any, type_name: str) -> bool:
    if type_name == "object":
        return isinstance(instance, dict)
    if type_name == "array":
        return isinstance(instance, list)
    if type_name == "string":
        return isinstance(instance, str)
    if type_name == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if type_name == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if type_name == "boolean":
        return isinstance(instance, bool)
    if type_name == "null":
        return instance is None
    raise SchemaError(f"unsupported type keyword: {type_name!r}")


def _is_secret_like_scalar(key: str, value: Any) -> bool:
    if not _SECRET_NAME_RE.search(key):
        return False
    if value is None:
        return False
    if isinstance(value, dict):
        # Only a well-formed secret_ref indirection is allowed to sit
        # behind a secret-like field name.
        return not (set(value.keys()) <= {"store", "key"} and "store" in value and "key" in value)
    if isinstance(value, list):
        # A list of secret NAMES (references resolved by the runtime, e.g.
        # the agent-deployment manifest's `spec.secrets: ["provider-primary"]`
        # from docs/agent-orchestration-roadmap.md) is a reference, not a
        # value: every element must be a short identifier. Anything else in
        # the list (a value-shaped string, an object, a number) is rejected.
        return not all(
            isinstance(v, str) and _SECRET_NAME_ITEM_RE.fullmatch(v) is not None for v in value
        )
    # Any other JSON type (string, number, bool) directly under a
    # secret-like field name is a raw value, which is always rejected.
    return True


def scan_for_raw_secrets(instance: Any, path: str = "$") -> list[str]:
    """Recursively finds (a) fields whose NAME matches a secret-like
    pattern but whose value is not a `secret_ref` object or null, and (b)
    ANY string value, regardless of field name, that matches a
    well-known secret-value shape (Stripe/GitHub/AWS/Slack/bearer-token
    prefixes). Returns a list of human-readable error strings (empty
    means clean)."""
    errors: list[str] = []
    if isinstance(instance, str) and _SECRET_VALUE_SHAPE_RE.search(instance):
        errors.append(f"{path}: value matches a known secret-value shape (e.g. sk_live_/ghp_/AKIA.../xoxb-/Bearer ...)")
    if isinstance(instance, dict):
        for key, value in instance.items():
            child_path = f"{path}.{key}"
            if _is_secret_like_scalar(key, value):
                errors.append(
                    f"{child_path}: field name matches a secret pattern "
                    "but does not hold a secret_ref object ({'store','key'}) or null"
                )
            errors.extend(scan_for_raw_secrets(value, child_path))
    elif isinstance(instance, list):
        for i, item in enumerate(instance):
            errors.extend(scan_for_raw_secrets(item, f"{path}[{i}]"))
    return errors


def _validate(instance: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    if not isinstance(schema, dict):
        raise SchemaError(f"schema at {path} is not an object")

    if "const" in schema:
        if instance != schema["const"]:
            errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")
            return

    if "enum" in schema:
        if instance not in schema["enum"]:
            errors.append(f"{path}: {instance!r} is not one of {schema['enum']!r}")
            return

    type_spec = schema.get("type")
    if type_spec is not None:
        type_names = type_spec if isinstance(type_spec, list) else [type_spec]
        if not any(_type_matches(instance, t) for t in type_names):
            errors.append(f"{path}: expected type {type_spec!r}, got {type(instance).__name__}")
            return

    if isinstance(instance, str):
        if "pattern" in schema:
            if not re.search(schema["pattern"], instance):
                errors.append(f"{path}: {instance!r} does not match pattern {schema['pattern']!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance!r} is less than minimum {schema['minimum']!r}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance!r} is greater than maximum {schema['maximum']!r}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: has {len(instance)} items, fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: has {len(instance)} items, more than maxItems {schema['maxItems']}")
        item_schema = schema.get("items")
        if item_schema is not None:
            for i, item in enumerate(instance):
                _validate(item, item_schema, f"{path}[{i}]", errors)

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in instance:
                errors.append(f"{path}: missing required property {name!r}")

        properties = schema.get("properties", {})
        for name, value in instance.items():
            if name in properties:
                _validate(value, properties[name], f"{path}.{name}", errors)

        additional = schema.get("additionalProperties", True)
        if additional is False:
            extra = sorted(set(instance.keys()) - set(properties.keys()))
            if extra:
                errors.append(f"{path}: additional properties not allowed: {extra}")
        elif isinstance(additional, dict):
            for name in set(instance.keys()) - set(properties.keys()):
                _validate(instance[name], additional, f"{path}.{name}", errors)

    for combinator in ("oneOf", "anyOf"):
        if combinator in schema:
            sub_schemas = schema[combinator]
            matches = 0
            for sub in sub_schemas:
                sub_errors: list[str] = []
                _validate(instance, sub, path, sub_errors)
                if not sub_errors:
                    matches += 1
            if combinator == "oneOf" and matches != 1:
                errors.append(f"{path}: matched {matches} of {len(sub_schemas)} oneOf branches, expected exactly 1")
            if combinator == "anyOf" and matches < 1:
                errors.append(f"{path}: matched 0 of {len(sub_schemas)} anyOf branches, expected at least 1")


def validate(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Validates `instance` against `schema`. Returns a list of error
    strings (empty means valid). Always also runs the secret-value ban,
    regardless of what the schema itself declares."""
    errors: list[str] = []
    _validate(instance, schema, "$", errors)
    errors.extend(scan_for_raw_secrets(instance))
    return errors


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def validate_file(instance_path: Path, schema_path: Path) -> list[str]:
    instance = load_json(instance_path)
    schema = load_json(schema_path)
    return validate(instance, schema)
