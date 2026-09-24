"""lib/omes/py/privacy/posture_projection.py - Control Center projection of
AI privacy posture and AI egress policy decisions (issue #217, ADR-0029).

This module turns the OMES-owned evidence already produced by
`lib/omes/py/privacy/posture_evidence.py` (issue #216) and
`lib/omes/py/privacy/egress_policy.py` (issue #214) into the bounded,
tenant-scoped read shapes a Control Center screen consumes
(`contracts/control-center/v1/ai-privacy-posture-view.schema.json`), and
provides the pure authorization gate for the one owner-approval path issue
#217 allows (`contracts/control-center/v1/ai-egress-approval.request.schema.json`).

Like its sibling modules, this is a pure, stdlib-only function of metadata:
no network I/O, no subprocess, no filesystem access, no logging, no
persistence, and no trust of a client-supplied "granted"/"approved" flag.

Authority split (AGENTS.md, ADR-0029, issue #217 acceptance criteria):

- Hermes remains authoritative for agent runtime, reasoning, and
  model/provider routing. This module never selects, calls, or routes to
  a model provider, and it never reads a Hermes database directly.
- OMES owns host/deployment evidence and the pure evaluation logic in this
  package (`egress_policy.py`, `posture_evidence.py`, this module). This
  module does not implement host hardening or network isolation itself -
  it only projects evidence a caller already gathered through supported
  interfaces.
- AWCMS owns business/tenant policy: who may read a given tenant's
  projection, who may approve a pending decision, and the RBAC/ABAC
  authorization decision behind every read and every approval. The
  cross-tenant and not-approvable checks in this module are a SECOND,
  independent backstop behind that AWCMS-side authorization - never a
  replacement for it, and never merely "hidden in the UI" (UI hiding is
  not authorization).

Hard boundary this module enforces unconditionally, with no override:
`RESTRICTED` classification may never resolve to a `cloud_sanitized`
destination through the approval path. There is no reason_code in
`APPROVABLE_REASON_CODES` for that combination (matching
`egress_policy.py`'s own unconditional `AI_EGRESS_DENY_RESTRICTED_CLOUD_SANITIZED`
deny), and `authorize_approval()` additionally refuses that combination by
value even if a caller somehow constructs a decision_ref schema validation
did not catch.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from . import posture_evidence as _posture_evidence

# ---------------------------------------------------------------------------
# Bounded vocabularies (mirrors contracts/control-center/v1/ai-privacy-posture-view.schema.json
# and contracts/control-center/v1/ai-egress-approval.*.schema.json)
# ---------------------------------------------------------------------------

#: Which system asserted a given fact. Never inferred from the reader.
AUTHORITIES: frozenset[str] = frozenset({"hermes", "omes-host", "awcms"})

FRESHNESS_FRESH = "fresh"
FRESHNESS_STALE = "stale"
FRESHNESS_UNKNOWN = "unknown"
FRESHNESS_VALUES: frozenset[str] = frozenset({FRESHNESS_FRESH, FRESHNESS_STALE, FRESHNESS_UNKNOWN})

#: A projection-layer-only reason code (distinct namespace from
#: posture_evidence.REASON_CODES - this module never redefines or
#: overloads a canonical AI_PRIVACY_POSTURE_* or AI_EGRESS_* code from
#: #214/#216) used when a read is refused for tenant mismatch.
REASON_PROJECTION_CROSS_TENANT_DENIED = "AI_PRIVACY_POSTURE_PROJECTION_CROSS_TENANT_DENIED"

#: The only three egress_policy.py reason codes that ever mean
#: "approval_required" - the sole reason codes an approval request may
#: reference. Mirrors contracts/control-center/v1/ai-egress-approval.request.schema.json's
#: decision_ref.reason_code enum exactly; keep in lockstep.
APPROVABLE_REASON_CODES: frozenset[str] = frozenset({
    "AI_EGRESS_APPROVAL_REQUIRED_CONFIDENTIAL_PRIVATE_ENDPOINT",
    "AI_EGRESS_APPROVAL_REQUIRED_CONFIDENTIAL_CLOUD_SANITIZED",
    "AI_EGRESS_APPROVAL_REQUIRED_RESTRICTED_PRIVATE_ENDPOINT",
})

#: The one classification/destination combination that may NEVER be
#: approved, unconditionally, regardless of any future reason_code
#: addition - RESTRICTED -> cloud stays denied, full stop (issue #217
#: scope). Checked by value, independent of the reason_code enum check,
#: so this module fails closed even if a caller's decision_ref is
#: internally inconsistent.
_RESTRICTED_CLOUD = ("RESTRICTED", "cloud_sanitized")

#: Approval-response reason codes for denial paths (mirrors
#: contracts/control-center/v1/events/ai-egress-approval.recorded.schema.json).
REASON_APPROVAL_DENIED_CROSS_TENANT = "AI_EGRESS_APPROVAL_DENIED_CROSS_TENANT"
REASON_APPROVAL_DENIED_NOT_APPROVABLE = "AI_EGRESS_APPROVAL_DENIED_NOT_APPROVABLE"
REASON_APPROVAL_DENIED_RESTRICTED_CLOUD = "AI_EGRESS_APPROVAL_DENIED_RESTRICTED_CLOUD_NEVER_APPROVABLE"
REASON_APPROVAL_DENIED_BY_ACTOR = "AI_EGRESS_APPROVAL_DENIED_BY_ACTOR"


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        # Evidence timestamps are always "...Z" (UTC); normalize to a
        # timezone-aware datetime for a safe subtraction below.
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def classify_freshness(
    timestamp: Any,
    now: Any,
    max_age_seconds: int = _posture_evidence.DEFAULT_MAX_EVIDENCE_AGE_SECONDS,
) -> str:
    """Bounded fresh/stale/unknown classification. A missing or
    unparsable timestamp (on either side) - or a timestamp in the future,
    which indicates a clock/producer problem - is always 'unknown', never
    'fresh'. Stale/unknown evidence must never be rendered as healthy."""
    ts = _parse_timestamp(timestamp)
    now_ts = _parse_timestamp(now)
    if ts is None or now_ts is None:
        return FRESHNESS_UNKNOWN
    age_seconds = (now_ts - ts).total_seconds()
    if age_seconds < 0:
        return FRESHNESS_UNKNOWN
    return FRESHNESS_STALE if age_seconds > max_age_seconds else FRESHNESS_FRESH


def _decision(allow: bool, reason: str) -> dict[str, Any]:
    return {"allow": allow, "reason": reason}


def project_posture_view(
    *,
    requester_tenant_id: Optional[str],
    tenant_id: str,
    target: Mapping[str, Any],
    evidence: Mapping[str, Any],
    correlation_id: str,
    now: str,
    authority: str = "omes-host",
    latest_decision: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Builds an `ai-privacy-posture-view` projection, or refuses it.

    Cross-tenant check runs first, unconditionally, before anything else -
    the same pattern `lib/omes/py/jobs/entitlement.py`'s `evaluate()` uses
    for entitlements: a projection belongs to exactly one tenant, and no
    evidence state can override that.

    Returns `{"allow": False, "reason": ...}` on denial, or
    `{"allow": True, "reason": ..., "view": {...}}` shaped like
    `contracts/control-center/v1/ai-privacy-posture-view.schema.json`.
    Never raises on malformed `evidence`/`latest_decision` - unrecognized
    or missing fields fail closed to 'unknown'/'BLOCKED', matching
    posture_evidence.py's own fail-closed behavior, rather than crashing
    or silently defaulting to a healthy-looking projection.
    """
    if requester_tenant_id is not None and requester_tenant_id != tenant_id:
        return _decision(False, "cross_tenant_denied: requester tenant does not match projection tenant")

    if authority not in AUTHORITIES:
        authority = "omes-host"

    evidence = evidence if isinstance(evidence, Mapping) else {}
    target = dict(target) if isinstance(target, Mapping) else {}

    last_verified_at = evidence.get("last_verified_at")
    freshness = classify_freshness(last_verified_at, now)

    classification_mode = evidence.get("classification_mode")
    if classification_mode not in ("fail_closed_v1", "unknown"):
        classification_mode = "unknown"

    destination_class = evidence.get("destination_class")
    if destination_class not in _posture_evidence.DESTINATION_CLASSES:
        destination_class = "unknown"

    status = evidence.get("status")
    if status not in (
        _posture_evidence.STATUS_PASS,
        _posture_evidence.STATUS_FAIL,
        _posture_evidence.STATUS_WARN,
        _posture_evidence.STATUS_BLOCKED,
    ):
        status = _posture_evidence.STATUS_BLOCKED

    reason_codes = [
        code for code in evidence.get("reason_codes", []) if code in _posture_evidence.REASON_CODES
    ]
    if not reason_codes:
        reason_codes = ["AI_PRIVACY_POSTURE_BLOCKED_EVIDENCE_MISSING_TIMESTAMP"]
        status = _posture_evidence.STATUS_BLOCKED

    view: dict[str, Any] = {
        "tenant_id": tenant_id,
        "correlation_id": correlation_id,
        "target": target,
        "authority": authority,
        "evidence_freshness": freshness,
        "classification_mode": classification_mode,
        "destination_class": destination_class,
        "status": status,
        "reason_codes": reason_codes,
        "last_verified_at": last_verified_at if isinstance(last_verified_at, str) else None,
        "projected_at": now,
        "latest_decision": _project_latest_decision(latest_decision, now),
    }
    return {"allow": True, "reason": "projected", "view": view}


def _project_latest_decision(latest_decision: Optional[Mapping[str, Any]], now: str) -> Optional[dict[str, Any]]:
    if not isinstance(latest_decision, Mapping):
        return None
    decided_at = latest_decision.get("decided_at")
    return {
        "policy_version": str(latest_decision.get("policy_version", "unknown")),
        "classification": latest_decision.get("classification", "unknown"),
        "destination": latest_decision.get("destination", "unknown"),
        "decision": latest_decision.get("decision", "deny"),
        "reason_codes": list(latest_decision.get("reason_codes", [])) or ["AI_EGRESS_DENY_UNKNOWN_POLICY_VERSION"],
        "authority": latest_decision.get("authority") if latest_decision.get("authority") in AUTHORITIES else "omes-host",
        "decided_at": decided_at if isinstance(decided_at, str) else None,
        "evidence_freshness": classify_freshness(decided_at, now),
    }


def authorize_approval(
    *,
    requester_tenant_id: Optional[str],
    tenant_id: str,
    decision_ref: Mapping[str, Any],
    approve: bool,
) -> dict[str, Any]:
    """Pure allow/deny gate for an owner-approval decision.

    This is a SECOND, independent backstop behind AWCMS's own RBAC/ABAC
    authorization (docs/control-center-contracts.md section 1) - it never
    grants an approval path egress_policy.py did not already mark
    `approval_required`, and it unconditionally refuses to let a
    RESTRICTED classification ever resolve to a cloud_sanitized
    destination, regardless of what a caller submits.

    Returns `{"allow": bool, "reason": str, "reason_code": str}`.
    `reason_code` is always populated (approved or denied) so the
    caller can build `ai-egress-approval.response` and
    `ai-egress-approval.recorded` deterministically.
    """
    if requester_tenant_id is not None and requester_tenant_id != tenant_id:
        return {
            "allow": False,
            "reason": "cross_tenant_denied: requester tenant does not match decision tenant",
            "reason_code": REASON_APPROVAL_DENIED_CROSS_TENANT,
        }

    decision_ref = decision_ref if isinstance(decision_ref, Mapping) else {}
    classification = decision_ref.get("classification")
    destination = decision_ref.get("destination")
    reason_code = decision_ref.get("reason_code")

    if (classification, destination) == _RESTRICTED_CLOUD:
        return {
            "allow": False,
            "reason": "restricted_cloud_denied: RESTRICTED classification may never egress to cloud_sanitized; no approval path exists",
            "reason_code": REASON_APPROVAL_DENIED_RESTRICTED_CLOUD,
        }

    if reason_code not in APPROVABLE_REASON_CODES:
        return {
            "allow": False,
            "reason": f"not_approvable: reason_code {reason_code!r} is not one of the approval_required decisions",
            "reason_code": REASON_APPROVAL_DENIED_NOT_APPROVABLE,
        }

    if not approve:
        return {
            "allow": False,
            "reason": "denied_by_actor: the approving actor declined this request",
            "reason_code": REASON_APPROVAL_DENIED_BY_ACTOR,
        }

    return {
        "allow": True,
        "reason": f"approved: reason_code={reason_code}",
        "reason_code": reason_code,
    }
