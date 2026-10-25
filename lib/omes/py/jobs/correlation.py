"""lib/omes/py/jobs/correlation.py - correlation/trace identifier handling
for the AWCMS -> job -> pull worker -> host operation flow (issue #356,
follow-up to #272, ADR-0032 rule 5).

EVIDENCE, NOT AUTHORITY. These identifiers let an operator line up what
AWCMS asked for, what the job store recorded and what the worker reported.
They never grant, approve or select anything: they are not read by the
allowlist, the approval policy, the tenant check or the argv builder.

Identifiers are opaque, validated tokens (a pattern and a length bound).
They never carry free text or secrets; a value that does not match is
rejected, never sanitised or truncated (fail closed).

- `correlation_id` is required on every job and flows unchanged into the job
  record, audit log, worker result and every envelope.
- `causation_id` is optional and caller-supplied only (the event_id of the
  event that caused the job). It is never generated: absent means the job
  is a root.
- `trace_id` / `span_id` are optional W3C Trace Context shapes. When the
  caller supplies none, OMES generates one locally and records the name in
  `generated_ids` so a consumer can tell a propagated id from a local one.

`envelopes_for_job()` is a pure projection of a job record's own history
into `contracts/observability/v1/correlation-envelope` events. It is
rebuildable at any time, writes nothing and carries identifiers and closed
enums only (no parameters, no output, no error message, no actor).

Stdlib only (ADR-0012).
"""
from __future__ import annotations

import re
import secrets
from typing import Any

CORRELATION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
CAUSATION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
TRACE_ID_RE = re.compile(r"^(?!0{32}$)[0-9a-f]{32}$")
SPAN_ID_RE = re.compile(r"^(?!0{16}$)[0-9a-f]{16}$")

# Audit entries and envelopes carry exactly these identifier keys.
AUDIT_ID_KEYS = ("correlation_id", "causation_id", "trace_id", "span_id")
GENERATABLE = ("trace_id", "span_id")

_OPTIONAL_PATTERNS = {
    "causation_id": CAUSATION_ID_RE,
    "trace_id": TRACE_ID_RE,
    "span_id": SPAN_ID_RE,
}

# Job state -> envelope status (closed enum). Jobs have no `unknown` state.
_STATUS_FOR_STATE = {
    "queued": "pending",
    "approved": "pending",
    "running": "running",
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "expired": "expired",
    "rolled_back": "rolled_back",
}


class CorrelationError(ValueError):
    """A correlation/trace identifier is malformed. The message names the
    field but never echoes the offending value (it may be attacker text)."""


def _check(field: str, value: Any, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.match(value) is None or value.endswith("\n"):
        raise CorrelationError(
            f"{field}: malformed identifier (expected an opaque token matching {pattern.pattern}); rejected"
        )
    return value


def validate_inbound(request: dict[str, Any]) -> None:
    """Fail-closed check of the identifiers on an inbound job request.
    `correlation_id` is required; the rest are optional but, when present
    (an explicit null included) must be well formed."""
    if "correlation_id" not in request:
        raise CorrelationError("correlation_id: missing; every job must carry one")
    _check("correlation_id", request["correlation_id"], CORRELATION_ID_RE)
    for field, pattern in _OPTIONAL_PATTERNS.items():
        if field in request:
            _check(field, request[field], pattern)


def new_trace_id() -> str:
    return _nonzero_hex(16)


def new_span_id() -> str:
    return _nonzero_hex(8)


def _nonzero_hex(nbytes: int) -> str:
    while True:
        value = secrets.token_hex(nbytes)
        if int(value, 16) != 0:
            return value


def resolve(request: dict[str, Any]) -> dict[str, Any]:
    """Validates the inbound identifiers and returns the ones a job record
    stores: `causation_id` (only when supplied), `trace_id`, `span_id` and the
    `generated_ids` list naming those OMES generated locally."""
    validate_inbound(request)
    trace_id = request.get("trace_id")
    span_id = request.get("span_id")
    generated: list[str] = []
    if trace_id is None:
        trace_id = new_trace_id()
        generated.append("trace_id")
    if span_id is None:
        span_id = new_span_id()
        generated.append("span_id")
    resolved: dict[str, Any] = {"trace_id": trace_id, "span_id": span_id, "generated_ids": generated}
    if request.get("causation_id") is not None:
        resolved["causation_id"] = request["causation_id"]
    return resolved


def audit_fields(record: dict[str, Any]) -> dict[str, str]:
    """The identifier fields of a job record that go into an audit entry.
    Records written before #356 simply lack the optional ones."""
    return {k: record[k] for k in AUDIT_ID_KEYS if isinstance(record.get(k), str)}


def result_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Optional identifier fields for a worker-result.request, taken from a
    local job record. Only well-formed values are echoed."""
    out: dict[str, Any] = {}
    for field, pattern in _OPTIONAL_PATTERNS.items():
        value = record.get(field)
        if isinstance(value, str) and pattern.match(value) and not value.endswith("\n"):
            out[field] = value
    generated = [g for g in record.get("generated_ids") or [] if g in GENERATABLE and g in out]
    if generated:
        out["generated_ids"] = list(dict.fromkeys(generated))
    return out


def echo_fields(job: dict[str, Any]) -> dict[str, Any]:
    """Like `result_fields` for a job as received from the Control Center,
    used where no local record exists (a rejected job). Nothing is
    generated here: a rejection echoes only what the caller supplied."""
    return result_fields({k: job.get(k) for k in _OPTIONAL_PATTERNS})


# ---------------------------------------------------------------------------
# Envelope projection
# ---------------------------------------------------------------------------


def _event_id(job_id: str, index: int) -> str:
    # Deterministic and zero-padded: re-projecting yields identical events
    # (the reducer dedupes them) and same-second events keep their order
    # under the reducer's (source_timestamp, event_id) sort.
    return f"{job_id}.{index:04d}"


def envelopes_for_job(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Projects `record["history"]` into correlation envelopes, one per
    state-history entry. Pure and deterministic: no clock, no I/O, so the
    same record always yields byte-identical envelopes.

    Each event's `causation_id` is the previous event's `event_id`; the
    first event's is the job's caller-supplied `causation_id`, if any.
    Raises CorrelationError if an identifier cannot be expressed in the
    envelope contract (for example a `correlation_id` shorter than the
    envelope's 8-character minimum): no envelope is better than a
    rewritten identifier."""
    from observability import envelope as envelope_mod  # lazy: avoids an import cycle

    out: list[dict[str, Any]] = []
    previous: str | None = record.get("causation_id")
    for index, entry in enumerate(record.get("history") or []):
        status = _STATUS_FOR_STATE.get(entry.get("to"))
        if status is None:
            raise CorrelationError(f"history[{index}]: state has no envelope status mapping")
        event: dict[str, Any] = {
            "schema_version": "1.0.0",
            "event_id": _event_id(record["job_id"], index),
            "source_authority": "omes",
            "tenant_id": record["tenant_id"],
            "correlation_id": record["correlation_id"],
            "event_type": f"job.{entry['to']}",
            "operation": record["operation"],
            "observed_at": entry["ts"],
            "source_timestamp": entry["ts"],
            "freshness": "live",
            "status": status,
            "evidence_ref": f"jobs/audit.jsonl#{record['job_id']}",
            "classification": "INTERNAL",
            "redaction_state": "none_required",
        }
        if previous is not None:
            event["causation_id"] = previous
        for field in ("trace_id", "span_id"):
            if isinstance(record.get(field), str):
                event[field] = record[field]
        problems = envelope_mod.validate(event)
        if problems:
            raise CorrelationError(f"history[{index}]: not expressible as a correlation envelope: {'; '.join(problems)}")
        out.append(event)
        previous = event["event_id"]
    return out
