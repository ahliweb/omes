"""lib/omes/py/observability/envelope.py - cross-plane correlation/event
envelope validation and deterministic convergence (issue #272, ADR-0032
rule 5, threats MA-06/MA-07/MA-11).

EVIDENCE, NOT AUTHORITY. An envelope records that some authority observed
something about a (tenant, correlation, run/task). It carries identifiers,
a closed status, freshness, classification and redaction state only. It
never carries prompts, transcripts, commands, tool arguments/results,
headers, credentials or reasoning, and nothing here grants permission,
approves, or mutates state. This module is pure: no I/O besides loading the
checked-in schema, no clock, no network, no persistence.

Contract: contracts/observability/v1/correlation-envelope.schema.json,
validated with the repo's existing stdlib validator (jobs/schema.py) - there
is no second validator here.

Two services are provided:

1. `validate(envelope)` - fail-closed: unsupported major version, schema
   violations, forbidden raw/free-text field names, secret-shaped values,
   control characters and calendar-invalid timestamps are all errors.
2. `reduce_events(envelopes)` - a pure, order-independent reducer: dedupe by
   event_id, reject cross-tenant mixing inside one correlation_id, order by
   (source_timestamp, event_id) and project the status per
   (correlation_id, run_id, task_id). A terminal status is final; it is never
   reverted by a later-arriving older (or newer) non-terminal event.

Not implemented yet (tracked in #272): propagation of these identifiers
through the AWCMS -> job -> worker flows, and any producer wiring.
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
PY_ROOT = REPO_ROOT / "lib" / "omes" / "py"
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))

from jobs import schema as schema_mod  # noqa: E402

SCHEMA_PATH = REPO_ROOT / "contracts" / "observability" / "v1" / "correlation-envelope.schema.json"

SUPPORTED_MAJOR = 1

TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "interrupted", "expired", "rolled_back"}
)
NON_TERMINAL_STATUSES = frozenset({"pending", "running", "unknown"})

CLASSIFICATION_RANK = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "RESTRICTED": 3}

# Field names that would open a free-text, command or credential channel.
# The schema's `additionalProperties: false` already rejects every unlisted
# field; this explicit list exists so the rejection names the reason and so a
# future schema edit cannot silently add one of these without failing tests.
# Matching is case-insensitive on the exact key at any nesting depth.
FORBIDDEN_FIELD_NAMES = frozenset({
    "prompt", "transcript", "command", "cmd", "shell", "script", "exec", "argv",
    "args", "tool_args", "tool_result", "headers", "authorization", "token",
    "secret", "password", "credential", "reasoning", "chain_of_thought",
})

_VERSION_RE = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")
_TS_RE = re.compile(
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z$"
)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

_schema_cache: dict[str, Any] | None = None


def load_schema() -> dict[str, Any]:
    global _schema_cache
    if _schema_cache is None:
        schema = schema_mod.load_json(SCHEMA_PATH)
        schema_mod.validate_schema(schema)
        _schema_cache = schema
    return _schema_cache


def parse_timestamp(value: str) -> datetime.datetime:
    """Parses a UTC RFC 3339 `Z` timestamp (optional 1-6 digit fraction) into
    an aware datetime. Raises ValueError for a malformed or calendar-invalid
    value. Ordering is always done on the parsed value, never on the string:
    strings with differing fraction lengths do not sort chronologically."""
    match = _TS_RE.match(value) if isinstance(value, str) else None
    if match is None or value.endswith("\n"):
        raise ValueError(f"not a UTC RFC 3339 timestamp: {value!r}")
    year, month, day, hour, minute, second, frac = match.groups()
    micro = int((frac or "0").ljust(6, "0"))
    return datetime.datetime(
        int(year), int(month), int(day), int(hour), int(minute), int(second), micro,
        tzinfo=datetime.timezone.utc,
    )


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def check_major_version(envelope: Any) -> list[str]:
    """Fail-closed major-version gate. Anything that is not a parseable
    semantic version with the supported major is an error; the caller must
    not interpret any other field of such an envelope."""
    if not isinstance(envelope, dict):
        return [f"$: envelope must be an object, got {type(envelope).__name__}"]
    version = envelope.get("schema_version")
    if not isinstance(version, str):
        return ["$.schema_version: missing or not a string; refusing to interpret envelope (fail closed)"]
    match = _VERSION_RE.match(version)
    if match is None:
        return [f"$.schema_version: {version!r} is not a MAJOR.MINOR.PATCH version; refusing to interpret envelope (fail closed)"]
    major = int(match.group(1))
    if major != SUPPORTED_MAJOR:
        return [
            f"$.schema_version: unsupported major version {major} "
            f"(this consumer supports major {SUPPORTED_MAJOR} only); refusing to interpret envelope (fail closed)"
        ]
    return []


def scan_forbidden(envelope: Any, path: str = "$") -> list[str]:
    """Forbidden raw/free-text/credential field names plus the repo's shared
    raw-secret scan (`jobs.schema.scan_for_raw_secrets`)."""
    errors: list[str] = []

    def walk(node: Any, node_path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str) and key.lower() in FORBIDDEN_FIELD_NAMES:
                    errors.append(
                        f"{node_path}.{key}: forbidden field; the envelope carries identifiers and enums only, "
                        "never free text, commands, credentials or reasoning"
                    )
                walk(value, f"{node_path}.{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{node_path}[{i}]")

    walk(envelope, path)
    errors.extend(schema_mod.scan_for_raw_secrets(envelope, path))
    return _unique(errors)


def validate(envelope: Any) -> list[str]:
    """Returns a list of error strings (empty means valid). Never raises for
    a bad envelope; a malformed *schema file* still raises SchemaError."""
    errors = check_major_version(envelope)
    if errors:
        return errors

    errors = list(scan_forbidden(envelope))
    errors.extend(schema_mod.validate(envelope, load_schema()))

    for key, value in envelope.items():
        if isinstance(value, str) and _CONTROL_CHAR_RE.search(value):
            errors.append(f"$.{key}: control characters are not allowed")
    for key in ("observed_at", "source_timestamp"):
        value = envelope.get(key)
        if isinstance(value, str):
            try:
                parse_timestamp(value)
            except ValueError:
                errors.append(f"$.{key}: {value!r} is not a valid UTC RFC 3339 timestamp")
    return _unique(errors)


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES


def _canonical(envelope: dict[str, Any]) -> str:
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"))


def _order_key(envelope: dict[str, Any]) -> tuple[datetime.datetime, str]:
    return (parse_timestamp(envelope["source_timestamp"]), envelope["event_id"])


def _label(envelope: Any, index: int) -> str:
    if isinstance(envelope, dict) and isinstance(envelope.get("event_id"), str):
        return f"event_id {envelope['event_id'][:128]!r}"
    return f"input #{index}"


def reduce_events(envelopes: Iterable[Any]) -> dict[str, Any]:
    """Pure, deterministic convergence reducer.

    Returns `{"ok": bool, "errors": [...], "projection": [...], "stats": {...}}`.
    On ANY error `ok` is False and `projection` is empty (fail closed: a
    partial projection could hide a poisoned or cross-tenant event).

    Rules:
    - every envelope must pass `validate()`;
    - duplicates (same event_id and identical content) are collapsed; the
      same event_id with different content is an error (event poisoning);
    - one correlation_id must belong to exactly one tenant, otherwise error;
    - events are ordered by (source_timestamp, event_id), never by arrival;
    - projection key is (correlation_id, run_id, task_id) (absent -> null),
      always within one tenant;
    - a terminal status is final: later non-terminal events (including
      `unknown`) never revert it. The earliest terminal event decides; a
      later, different terminal status is surfaced as
      `conflicting_terminal: true`, not silently adopted;
    - `unknown` is a normal non-terminal status and is never promoted to
      success;
    - the projection `classification` is the maximum of its events, so a
      projection never under-classifies.

    The result depends only on the SET of input envelopes, so the same
    events in any order, with any duplication, produce an identical result.
    The input is not mutated.
    """
    items = list(envelopes)
    errors: list[str] = []
    stats = {"received": len(items), "unique": 0, "duplicates": 0}

    unique: dict[str, dict[str, Any]] = {}
    canonical: dict[str, str] = {}
    for index, item in enumerate(items):
        problems = validate(item)
        if problems:
            errors.extend(f"{_label(item, index)}: {p}" for p in problems)
            continue
        event_id = item["event_id"]
        text = _canonical(item)
        if event_id in canonical:
            if canonical[event_id] != text:
                errors.append(f"event_id {event_id!r} reused with different content (possible event poisoning)")
            else:
                stats["duplicates"] += 1
            continue
        canonical[event_id] = text
        unique[event_id] = item
    stats["unique"] = len(unique)

    by_correlation: dict[str, list[dict[str, Any]]] = {}
    for event in unique.values():
        by_correlation.setdefault(event["correlation_id"], []).append(event)
    for correlation_id, events in by_correlation.items():
        tenants = {e["tenant_id"] for e in events}
        if len(tenants) > 1:
            errors.append(
                f"correlation_id {correlation_id!r} is used by {len(tenants)} different tenants "
                "(cross-tenant substitution rejected)"
            )

    if errors:
        return {"ok": False, "errors": sorted(set(errors)), "projection": [], "stats": stats}

    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for event in unique.values():
        key = (event["tenant_id"], event["correlation_id"], event.get("run_id", ""), event.get("task_id", ""))
        groups.setdefault(key, []).append(event)

    projection: list[dict[str, Any]] = []
    for key in sorted(groups):
        events = sorted(groups[key], key=_order_key)
        deciding = events[0]
        conflicting = False
        for event in events[1:]:
            if is_terminal(deciding["status"]):
                if is_terminal(event["status"]) and event["status"] != deciding["status"]:
                    conflicting = True
            else:
                deciding = event
        tenant_id, correlation_id, run_id, task_id = key
        projection.append({
            "tenant_id": tenant_id,
            "correlation_id": correlation_id,
            "run_id": run_id or None,
            "task_id": task_id or None,
            "status": deciding["status"],
            "terminal": is_terminal(deciding["status"]),
            "freshness": deciding["freshness"],
            "source_authority": deciding["source_authority"],
            "source_timestamp": deciding["source_timestamp"],
            "deciding_event_id": deciding["event_id"],
            "classification": max((e["classification"] for e in events), key=CLASSIFICATION_RANK.__getitem__),
            "event_count": len(events),
            "conflicting_terminal": conflicting,
        })
    return {"ok": True, "errors": [], "projection": projection, "stats": stats}
