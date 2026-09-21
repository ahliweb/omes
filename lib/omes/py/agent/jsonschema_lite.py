"""lib/omes/py/agent/jsonschema_lite.py - minimal, dependency-free JSON
Schema validator unified with jobs.schema (ADR-0012, issue #172).

This module reconciles the agent validator with `lib/omes/py/jobs/schema.py`
so that both use the exact same supported JSON Schema keyword subset,
recursively validate against unsupported keywords (failing closed with
SchemaError), and scan for secret leakage.
"""
from __future__ import annotations

from typing import Any, List

from jobs import schema as _jobs_schema
from jobs.schema import (
    ALLOWED_ANNOTATION_KEYWORDS,
    ALLOWED_SCHEMA_KEYWORDS,
    SUPPORTED_VALIDATION_KEYWORDS,
    SchemaError,
    validate_schema,
)

__all__ = [
    "ALLOWED_ANNOTATION_KEYWORDS",
    "ALLOWED_SCHEMA_KEYWORDS",
    "SUPPORTED_VALIDATION_KEYWORDS",
    "SchemaError",
    "validate",
    "validate_schema",
]


def validate(instance: Any, schema: dict, path: str = "$") -> List[str]:
    """Returns a list of human-readable error strings; empty means valid.
    Fails closed with SchemaError if schema contains unsupported keywords."""
    return _jobs_schema.validate(instance, schema, path=path)
