"""lib/omes/py/agent/jsonschema_lite.py - minimal, dependency-free JSON
Schema (draft-07 subset) validator.

ADR-0012 forbids third-party packages (no `jsonschema` from PyPI), and
issue #87 explicitly says the OMES-side validator for
contracts/agent/v1/agent-deployment.schema.json must be our own stdlib
code (a separate agent's scripts/check-contracts.py, on another branch,
owns contracts/control-center/v1). This module implements only the
keywords contracts/agent/v1/agent-deployment.schema.json actually uses:
`type`, `const`, `enum`, `pattern`, `minLength`, `maxLength`, `minimum`,
`maximum`, `required`, `properties`, `additionalProperties`, `items`.
It is intentionally not a general-purpose JSON Schema implementation.
"""
from __future__ import annotations

import re
from typing import Any, List

_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def _type_ok(value: Any, type_name: str) -> bool:
    py_type = _TYPE_MAP.get(type_name)
    if py_type is None:
        return True
    if type_name == "integer" and isinstance(value, bool):
        # bool is a subclass of int in Python; JSON booleans are not integers.
        return False
    if type_name == "number" and isinstance(value, bool):
        return False
    return isinstance(value, py_type)


def validate(instance: Any, schema: dict, path: str = "$") -> List[str]:
    """Returns a list of human-readable error strings; empty means valid."""
    errors: List[str] = []

    if "const" in schema:
        if instance != schema["const"]:
            errors.append(f"{path}: must equal {schema['const']!r} (got {instance!r})")
            return errors

    if "enum" in schema:
        if instance not in schema["enum"]:
            errors.append(f"{path}: must be one of {schema['enum']!r} (got {instance!r})")
            return errors

    if "type" in schema:
        type_names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(instance, t) for t in type_names):
            errors.append(f"{path}: expected type {schema['type']!r}, got {type(instance).__name__}")
            return errors

    if isinstance(instance, str):
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: does not match pattern {schema['pattern']!r} (got {instance!r})")
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property '{key}'")
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in properties:
                errors.extend(validate(value, properties[key], f"{path}.{key}"))
            elif additional is False:
                errors.append(f"{path}: unexpected property '{key}' (additionalProperties is false)")

    if isinstance(instance, list) and "items" in schema:
        item_schema = schema["items"]
        for idx, item in enumerate(instance):
            errors.extend(validate(item, item_schema, f"{path}[{idx}]"))

    return errors
