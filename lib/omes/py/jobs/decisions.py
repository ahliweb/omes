"""lib/omes/py/jobs/decisions.py - the OMES job store as a producer and a
consumer of policy-decision envelopes (issue #358, ADR-0032 rule 7, threat
MA-05).

THIS IS NOT A POLICY ENGINE. The three decisions produced here are facts
that `jobs/store.py` already decides today (the operation allowlist, the
tenant scope check and the approval requirement). This module only records
each fact in the `policy-decision` envelope
(contracts/observability/v1/policy-decision.schema.json) and composes the
recorded decisions with any externally supplied ones through the existing
pure composer (`observability.policy_decision.compose`). It evaluates no
rule, reads no policy body, creates no approval and runs no inbox.

Producer (OMES-owned decisions only, deciding_authority `omes`):

- `omes.jobs.allowlist`: `OPERATION_ALLOWLISTED` / `OPERATION_NOT_ALLOWLISTED`
  (deny).
- `omes.jobs.tenant_scope`: `TENANT_SCOPE_MATCH` / `TENANT_MISMATCH` (deny) /
  `TENANT_NOT_CONFIGURED` (deny; default-deny, issue #279).
- `omes.jobs.approval`: `APPROVAL_NOT_REQUIRED` (allow) or
  `approval_required` with `APPROVAL_REQUIRED_DESTRUCTIVE_OPERATION` or
  `APPROVAL_REQUIRED_NOT_AUTO_APPROVED`. The `approval_ref` is an opaque
  locator of the approval step that already exists (`omes job approve`); it
  is a reference only. Nothing here creates or grants an approval.

The `AI_EGRESS_*` namespace is reserved for egress decisions and is never
used here.

Consumer (`evaluate`): composes the OMES decisions with decisions passed in
process (for example a fake provider in tests; AWCMS and Hermes emission is
Not implemented yet, tracked in #358). Any deny or unavailable wins, stale
or non-live evidence is unavailable, a missing required authority is
unavailable, and mixed tenant, correlation or action across decisions makes
composition fail, which this module reports as unavailable (fail closed).
An external decision claiming authority `omes` is rejected: OMES emits only
its own decisions.

The caller (`store.submit`) turns a `deny` or `unavailable` result into an
exception; `allow` and `approval_required` both let the job be queued
(`approval_required` is the existing behaviour: the job waits for
`omes job approve`).

Stdlib only (ADR-0012). The clock is a parameter.
"""
from __future__ import annotations

import datetime
import hashlib
import sys
from pathlib import Path
from typing import Any, Iterable

_PY_ROOT = Path(__file__).resolve().parent.parent
if str(_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(_PY_ROOT))

from observability import policy_decision  # noqa: E402

SCHEMA_VERSION = "1.0.0"
AUTHORITY = "omes"
REQUIRED_AUTHORITIES = (AUTHORITY,)
EVIDENCE_VERSION = "omes-jobs-1"

POLICY_ALLOWLIST = "omes.jobs.allowlist"
POLICY_TENANT_SCOPE = "omes.jobs.tenant_scope"
POLICY_APPROVAL = "omes.jobs.approval"

REASON_OPERATION_ALLOWLISTED = "OPERATION_ALLOWLISTED"
REASON_OPERATION_NOT_ALLOWLISTED = "OPERATION_NOT_ALLOWLISTED"
REASON_TENANT_SCOPE_MATCH = "TENANT_SCOPE_MATCH"
REASON_TENANT_MISMATCH = "TENANT_MISMATCH"
REASON_TENANT_NOT_CONFIGURED = "TENANT_NOT_CONFIGURED"
REASON_APPROVAL_NOT_REQUIRED = "APPROVAL_NOT_REQUIRED"
REASON_APPROVAL_DESTRUCTIVE = "APPROVAL_REQUIRED_DESTRUCTIVE_OPERATION"
REASON_APPROVAL_NOT_AUTO = "APPROVAL_REQUIRED_NOT_AUTO_APPROVED"
REASON_POLICY_DECISION_INVALID = "POLICY_DECISION_INVALID"
REASON_EXTERNAL_CLAIMS_OMES = "EXTERNAL_DECISION_CLAIMS_OMES_AUTHORITY"

# Closed set of composed-result fields persisted as evidence (no free text).
_EVIDENCE_FIELDS = (
    "decision",
    "winning_authority",
    "winning_decision_id",
    "winning_reason_code",
    "winning_policy_id",
    "contributing_decision_ids",
    "approval_refs",
    "missing_authorities",
    "required_authorities",
    "evaluated_at",
)


def decision_correlation_id(correlation_id: str) -> str:
    """The envelope's correlation_id for a job request. The request contract
    allows 1-128 characters while the envelope requires 8-256, so a shorter
    id is prefixed (`corr:`) rather than rejected. External decisions about
    the same job must carry this same value, otherwise composition fails
    closed (mixed correlation)."""
    if len(correlation_id) < 8:
        return f"corr:{correlation_id}"
    return correlation_id


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def approval_ref(idempotency_key: str) -> str:
    """Opaque locator of the (existing) `omes job approve` step for a job
    request. A reference only: it is not an approval and proves none."""
    return f"omes:job-approval/{_digest('approval', idempotency_key)}"


def _ts(now: datetime.datetime) -> str:
    return now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _envelope(
    request: dict[str, Any],
    *,
    kind: str,
    policy_id: str,
    decision: str,
    reason_code: str,
    now: datetime.datetime,
    approval: str | None = None,
) -> dict[str, Any]:
    idem = request["idempotency_key"]
    target = request.get("target") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "decision_id": f"omes-{kind}-{_digest(kind, idem)}",
        "tenant_id": request["tenant_id"],
        "correlation_id": decision_correlation_id(request["correlation_id"]),
        "actor": {"type": request["actor"]["type"], "id": request["actor"]["id"]},
        "resource": {"kind": "host", "id": target.get("server_id")},
        "requested_action": request["operation"],
        "deciding_authority": AUTHORITY,
        "decision": decision,
        "policy_id": policy_id,
        "reason_code": reason_code,
        "evidence": {"version": EVIDENCE_VERSION, "observed_at": _ts(now), "freshness": "live"},
        "approval_ref": approval,
        "expires_at": None,
        "idempotency_key": idem,
        "classification": "INTERNAL",
        "redaction_state": "none_required",
    }


def omes_decisions(
    request: dict[str, Any],
    *,
    operations: Iterable[str],
    local_tenant: str | None,
    destructive: bool,
    auto_approvable: bool,
    now: datetime.datetime,
) -> list[dict[str, Any]]:
    """Pure producer: the decisions OMES itself makes about `request`.

    `operations` is the allowlist, `local_tenant` the resolved local tenant
    (None when not configured), `destructive` / `auto_approvable` the
    existing approval facts for the operation. Nothing is evaluated here
    beyond recording those facts."""
    allowed = request["operation"] in set(operations)
    if local_tenant is None:
        tenant_decision, tenant_reason = "deny", REASON_TENANT_NOT_CONFIGURED
    elif request["tenant_id"] != local_tenant:
        tenant_decision, tenant_reason = "deny", REASON_TENANT_MISMATCH
    else:
        tenant_decision, tenant_reason = "allow", REASON_TENANT_SCOPE_MATCH

    if auto_approvable and not destructive:
        approval_decision, approval_reason, ref = "allow", REASON_APPROVAL_NOT_REQUIRED, None
    else:
        approval_decision = "approval_required"
        approval_reason = REASON_APPROVAL_DESTRUCTIVE if destructive else REASON_APPROVAL_NOT_AUTO
        ref = approval_ref(request["idempotency_key"])

    return [
        _envelope(
            request,
            kind="allowlist",
            policy_id=POLICY_ALLOWLIST,
            decision="allow" if allowed else "deny",
            reason_code=REASON_OPERATION_ALLOWLISTED if allowed else REASON_OPERATION_NOT_ALLOWLISTED,
            now=now,
        ),
        _envelope(
            request,
            kind="tenant",
            policy_id=POLICY_TENANT_SCOPE,
            decision=tenant_decision,
            reason_code=tenant_reason,
            now=now,
        ),
        _envelope(
            request,
            kind="approval",
            policy_id=POLICY_APPROVAL,
            decision=approval_decision,
            reason_code=approval_reason,
            now=now,
            approval=ref,
        ),
    ]


def _unavailable(reason_code: str, errors: list[str]) -> dict[str, Any]:
    return {
        "decision": "unavailable",
        "winning_authority": None,
        "winning_decision_id": None,
        "winning_reason_code": reason_code,
        "winning_policy_id": None,
        "contributing_decision_ids": [],
        "approval_refs": [],
        "missing_authorities": [],
        "required_authorities": list(REQUIRED_AUTHORITIES),
        "evaluated_at": None,
        "errors": errors,
    }


def evaluate(
    omes: list[dict[str, Any]],
    external_decisions: Iterable[Any] | None = None,
    *,
    required_authorities: Iterable[str] = REQUIRED_AUTHORITIES,
    now: datetime.datetime,
) -> dict[str, Any]:
    """Consumer: composes the OMES decisions with `external_decisions`
    through the existing pure composer and returns the composed result
    (a dict with `decision` of allow, deny, unavailable or
    approval_required). Never raises for bad input and never returns
    allow when composition fails (fail closed: unavailable with
    `POLICY_DECISION_INVALID`). `required_authorities` always includes
    `omes`; a required authority that supplied no decision makes the
    result unavailable."""
    external = list(external_decisions or [])
    for item in external:
        if isinstance(item, dict) and item.get("deciding_authority") == AUTHORITY:
            return _unavailable(REASON_EXTERNAL_CLAIMS_OMES, [REASON_EXTERNAL_CLAIMS_OMES])
    required = sorted({*REQUIRED_AUTHORITIES, *required_authorities})
    result = policy_decision.compose(omes + external, now=now, required_authorities=required)
    if not result["ok"] or result["composed"] is None:
        return _unavailable(REASON_POLICY_DECISION_INVALID, list(result["errors"]))
    composed = dict(result["composed"])
    composed["errors"] = []
    return composed


def evidence_for_record(composed: dict[str, Any], envelopes: list[dict[str, Any]]) -> dict[str, Any]:
    """Bounded evidence for the job record: the composed result's closed
    fields and the OMES-produced envelopes. No free text, no secrets, no
    external envelope bodies (their ids are in `contributing_decision_ids`).
    """
    return {
        "composed": {key: composed.get(key) for key in _EVIDENCE_FIELDS},
        "omes_decisions": envelopes,
    }


def audit_detail(composed: dict[str, Any]) -> dict[str, Any]:
    """The few closed fields placed on the `submitted` audit line."""
    return {
        "decision": composed["decision"],
        "reason_code": composed["winning_reason_code"],
        "policy_id": composed["winning_policy_id"],
        "approval_refs": list(composed["approval_refs"]),
    }
