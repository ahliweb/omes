"""lib/omes/py/privacy/posture_evidence.py - deterministic AI privacy
posture/egress evidence evaluator (issue #216, ADR-0029).

This module turns a bounded, already-collected "observation" (host/Hermes
facts a caller gathered through supported interfaces) into a bounded
PASS/FAIL/WARN/BLOCKED evidence object. Like
lib/omes/py/privacy/egress_policy.py, it is a pure, stdlib-only function of
metadata: no network I/O, no subprocess, no filesystem access, no logging,
and no persistence. Collecting the observation is the caller's job (see
lib/omes/py/health/ai_privacy.py); this module only ever sees the small,
already-bounded facts listed below - never prompt text, response text,
transcripts, embeddings, retrieved documents, environment dumps, or a real
credential/secret value.

Hard boundaries (do not weaken these):

- `evaluate()` accepts and returns only bounded metadata. Any field not in
  the documented observation shape is silently ignored - never echoed,
  never inspected for content - so a caller that mistakenly stuffs a
  prompt, secret, or raw provider response into an unexpected key cannot
  make it appear in the evidence output.
- Every free-text-shaped field this module DOES echo back
  (`evidence_source`, `hermes_version_reference.value`) is validated
  against a closed vocabulary or a strict bounded pattern - and, for
  `hermes_version_reference.value`, additionally against a set of
  well-known secret-value shapes (issue #218) - before being echoed; a
  value that fails validation is replaced with a fixed "rejected"
  placeholder rather than passed through. This module never
  hashes a value as a substitute for validating it - hashing a low-entropy
  secret and calling it "anonymized" is explicitly out of scope (see
  docs/ai-data-privacy-and-model-security.md section 7).
- Missing, malformed, or unrecognized policy version/destination
  class/evidence timestamp fails closed to `BLOCKED`, never to a
  healthy-looking default. Unknown is never reported as PASS.
- Drift from a `restricted_local_only` expected posture to an observed
  `cloud` destination class is always `FAIL` (never downgraded to WARN).
- A missing/unavailable #215 local-only posture source under an expected
  `restricted_local_only` posture degrades this evidence to `BLOCKED`
  (with an explicit reason code) rather than silently passing.
- This module does not select, call, or route to any model provider, and
  it does not implement host hardening/network isolation itself - it only
  reports evidence a caller already gathered through supported interfaces
  (ADR-0017, ADR-0029). Hermes remains authoritative for model/provider
  routing.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

# ---------------------------------------------------------------------------
# Bounded vocabularies (contracts/ai-egress/v1/privacy-posture-evidence.schema.json
# mirrors these)
# ---------------------------------------------------------------------------

#: Policy versions this evaluator knows how to apply. Mirrors
#: egress_policy.KNOWN_POLICY_VERSIONS - both modules version the same
#: docs/ai-data-privacy-and-model-security.md policy, so they are kept in
#: lockstep rather than drifting into two version vocabularies.
KNOWN_POLICY_VERSIONS: frozenset[str] = frozenset({"v1"})

#: Effective provider destination class, bounded per issue #216 scope
#: ("local / private / cloud / unknown").
DESTINATION_CLASSES: frozenset[str] = frozenset({"local", "private", "cloud", "unknown"})

#: Local endpoint network classification.
LOCAL_ENDPOINT_CLASSIFICATIONS: frozenset[str] = frozenset({
    "loopback",
    "private_network",
    "public",
    "not_applicable",
    "unknown",
})

#: Tri-state booleans (True/False/None-as-"unknown") are reported as one of
#: these closed strings in the evidence output, so `null`/missing can never
#: be silently coerced into a JSON `false` that reads as "confirmed off".
TRISTATE_ENABLED: tuple[str, str, str] = ("enabled", "disabled", "unknown")
TRISTATE_ACTIVE: tuple[str, str, str] = ("active", "inactive", "unknown")

#: The intended/target posture an operator has declared for this
#: deployment. "unknown" (no declared expectation) is conservative: it
#: never upgrades an evidence report to PASS on its own.
EXPECTED_POSTURES: frozenset[str] = frozenset({"restricted_local_only", "unrestricted", "unknown"})

#: Status codes for a #215 local-only posture source, if one is available.
LOCAL_ONLY_SOURCE_STATUSES: frozenset[str] = frozenset({"pass", "fail", "warn", "unknown"})

#: Closed vocabulary for `evidence_source` - deliberately NOT free text.
#: Any value outside this set is rejected (see module docstring); this is
#: the primary control that makes it structurally impossible for this
#: field to carry a secret or prompt fragment.
EVIDENCE_SOURCES: frozenset[str] = frozenset({
    "hermes-cli:hermes --version",
    "hermes-cli:hermes config get",
    "hermes-cli:hermes doctor",
    "omes-host:network-classification",
    "omes-host:state",
    "local-only-posture-source:issue-215",
    "local-only-posture-source:unavailable",
    "unknown",
})

#: Bounded pattern for the one field allowed to hold a short version
#: banner (`hermes_version_reference.value`, e.g. "hermes 2026.9.14").
#: Deliberately short and restrictive - long enough for a version string,
#: far too short and too narrow a character class to smuggle a credential,
#: prompt fragment, or provider response.
_VERSION_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,63}$")

#: Well-known secret-value shapes, rejected in `hermes_version_reference.value`
#: even when they satisfy _VERSION_VALUE_PATTERN's character class (issue
#: #218: a `sk_live_...`-shaped token is made only of characters a version
#: banner may legitimately contain, so the pattern alone does not stop it).
#: Deliberately a small COPY of lib/omes/py/jobs/schema.py's
#: `_SECRET_VALUE_SHAPE_RE` rather than an import - packages under
#: lib/omes/py/<pkg>/ do not import across each other (see
#: lib/omes/py/jobs/audit.py's module docstring for the same convention).
#: `observed_at` is echoed back as `last_verified_at`, whose published
#: contract (contracts/ai-egress/v1/privacy-posture-evidence.schema.json)
#: pins it to an ISO-8601 UTC instant. Issue #218: the evaluator used to
#: accept ANY non-empty string here and echo it verbatim, which both
#: produced output that failed its own schema and turned an unvalidated
#: free-text field into an echo path. A value that does not match is now
#: treated exactly like a missing timestamp: BLOCKED, `last_verified_at`
#: null.
_OBSERVED_AT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_SECRET_VALUE_SHAPE_PATTERN = re.compile(
    r"(sk_live_|sk_test_|gh[pousr]_[A-Za-z0-9]|AKIA[0-9A-Z]{12,}|xox[baprs]-|Bearer [A-Za-z0-9._-]{10,})"
)

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_WARN = "WARN"
STATUS_BLOCKED = "BLOCKED"

#: Severity ordering used to combine multiple findings into one overall
#: status - worst finding wins, never averaged or overridden by a later
#: "looks fine" check.
_SEVERITY = {STATUS_PASS: 0, STATUS_WARN: 1, STATUS_FAIL: 2, STATUS_BLOCKED: 3}

#: Stable, machine-readable reason-code vocabulary for this evidence
#: surface. Distinct from (and does not duplicate) egress_policy's
#: `AI_EGRESS_*` decision codes - those describe one egress-decision
#: evaluation, these describe host/deployment privacy *posture* evidence.
#: A new reason must be a new code, not a repurposed one.
REASON_CODES: frozenset[str] = frozenset({
    "AI_PRIVACY_POSTURE_PASS_CONSISTENT",
    "AI_PRIVACY_POSTURE_BLOCKED_UNKNOWN_POLICY_VERSION",
    "AI_PRIVACY_POSTURE_BLOCKED_UNKNOWN_DESTINATION_CLASS",
    "AI_PRIVACY_POSTURE_BLOCKED_EVIDENCE_MISSING_TIMESTAMP",
    "AI_PRIVACY_POSTURE_BLOCKED_EVIDENCE_STALE",
    "AI_PRIVACY_POSTURE_BLOCKED_LOCAL_ONLY_SOURCE_UNAVAILABLE",
    "AI_PRIVACY_POSTURE_FAIL_DRIFT_LOCAL_ONLY_TO_CLOUD",
    "AI_PRIVACY_POSTURE_FAIL_LOCAL_ENDPOINT_PUBLICLY_EXPOSED",
    "AI_PRIVACY_POSTURE_FAIL_CLOUD_FALLBACK_ENABLED_UNDER_RESTRICTED_POSTURE",
    "AI_PRIVACY_POSTURE_FAIL_NETWORK_ISOLATION_INACTIVE_UNDER_RESTRICTED_POSTURE",
    "AI_PRIVACY_POSTURE_FAIL_LOCAL_ONLY_SOURCE_REPORTS_FAIL",
    "AI_PRIVACY_POSTURE_WARN_DRIFT_LOCAL_ONLY_TO_PRIVATE_ENDPOINT",
    "AI_PRIVACY_POSTURE_WARN_LOCAL_ENDPOINT_CLASSIFICATION_UNKNOWN",
    "AI_PRIVACY_POSTURE_WARN_CLOUD_FALLBACK_STATE_UNKNOWN",
    "AI_PRIVACY_POSTURE_WARN_NETWORK_ISOLATION_STATE_UNKNOWN",
    "AI_PRIVACY_POSTURE_WARN_LOCAL_ONLY_SOURCE_REPORTS_WARN",
    "AI_PRIVACY_POSTURE_WARN_LOCAL_ONLY_SOURCE_REPORTS_UNKNOWN",
    "AI_PRIVACY_POSTURE_WARN_EXPECTED_POSTURE_UNKNOWN",
    "AI_PRIVACY_POSTURE_WARN_EVIDENCE_SOURCE_REJECTED",
    "AI_PRIVACY_POSTURE_WARN_HERMES_VERSION_REFERENCE_REJECTED",
})

#: Default maximum age, in seconds, before evidence is considered stale
#: and the report fails closed to BLOCKED. 24 hours: long enough that a
#: routine polling cadence never flaps, short enough that a silently
#: abandoned evidence collector is caught within a day.
DEFAULT_MAX_EVIDENCE_AGE_SECONDS = 24 * 60 * 60


def _bump(current: str, candidate: str) -> str:
    return candidate if _SEVERITY[candidate] > _SEVERITY[current] else current


def _validate_evidence_source(value: Any) -> tuple[str, Optional[str]]:
    """Returns (echoed_value, reason_code_if_rejected)."""
    if isinstance(value, str) and value in EVIDENCE_SOURCES:
        return value, None
    return "unknown", "AI_PRIVACY_POSTURE_WARN_EVIDENCE_SOURCE_REJECTED"


def _validate_version_reference(ref: Any) -> tuple[Optional[dict], Optional[str]]:
    """Bounds `hermes_version_reference` to {value, source}. Returns
    (echoed_object_or_None, reason_code_if_rejected). `ref` absent is not
    an error - the field is simply omitted from the output."""
    if ref is None:
        return None, None
    if not isinstance(ref, Mapping):
        return {"value": None, "source": "unknown"}, "AI_PRIVACY_POSTURE_WARN_HERMES_VERSION_REFERENCE_REJECTED"

    raw_value = ref.get("value")
    raw_source = ref.get("source")
    source, source_reason = _validate_evidence_source(raw_source)

    if raw_value is None:
        return {"value": None, "source": source}, source_reason

    if (
        isinstance(raw_value, str)
        and _VERSION_VALUE_PATTERN.match(raw_value)
        and not _SECRET_VALUE_SHAPE_PATTERN.search(raw_value)
    ):
        return {"value": raw_value, "source": source}, source_reason

    return (
        {"value": None, "source": source},
        source_reason or "AI_PRIVACY_POSTURE_WARN_HERMES_VERSION_REFERENCE_REJECTED",
    )


def _tristate(value: Any, labels: tuple[str, str, str]) -> str:
    """Maps True/False/anything-else to labels[0]/labels[1]/labels[2]
    ("unknown"). Only an explicit boolean resolves to a non-unknown
    label - this is the same fail-closed reading egress_policy.py uses
    for `contains_authentication_material`: a missing or malformed value
    is never assumed to mean "the safe answer"."""
    if value is True:
        return labels[0]
    if value is False:
        return labels[1]
    return labels[2]


def _local_only_source_evidence(source: Any) -> tuple[dict, bool, Optional[str]]:
    """Normalizes the optional #215 local-only-posture integration point.

    Returns (echoed_object, available, reason_code). `available` is False
    whenever #215's evidence cannot be confirmed present and well-formed -
    including when #215 has not landed yet, matching this issue's
    requirement that a missing #215 source degrade to an explicit
    unknown/BLOCKED state rather than a healthy-looking default.
    """
    if not isinstance(source, Mapping) or source.get("available") is not True:
        return {"available": False, "status": "unknown", "source": "local-only-posture-source:unavailable"}, False, None

    status = source.get("status")
    if status not in LOCAL_ONLY_SOURCE_STATUSES:
        status = "unknown"

    src_value, src_reason = _validate_evidence_source(source.get("source"))
    echoed = {"available": True, "status": status, "source": src_value}

    reason = None
    if status == "fail":
        reason = "AI_PRIVACY_POSTURE_FAIL_LOCAL_ONLY_SOURCE_REPORTS_FAIL"
    elif status == "warn":
        reason = "AI_PRIVACY_POSTURE_WARN_LOCAL_ONLY_SOURCE_REPORTS_WARN"
    elif status == "unknown":
        reason = "AI_PRIVACY_POSTURE_WARN_LOCAL_ONLY_SOURCE_REPORTS_UNKNOWN"

    return echoed, True, (reason or src_reason)


def evaluate(observation: Mapping[str, Any], now: Optional[str] = None) -> dict[str, Any]:
    """Evaluates a bounded, already-collected observation and returns a
    bounded PASS/FAIL/WARN/BLOCKED privacy-posture evidence object.

    `observation` is expected to be shaped like
    `contracts/ai-egress/v1/privacy-posture-evidence-observation.schema.json`,
    but this function does not perform JSON Schema validation itself (a
    contract boundary should validate separately with
    lib/omes/py/jobs/schema.py) - it is deliberately defensive against
    missing/malformed/unexpected fields, and it fails closed rather than
    guessing.

    `now` is an optional ISO-8601 timestamp string used only to evaluate
    evidence staleness against `observation["observed_at"]` /
    `observation["max_evidence_age_seconds"]`; passing it explicitly (as
    the CLI wrapper does with the real current time) keeps this function
    deterministic and easy to test with fixed clocks.

    Returns a dict shaped like
    `contracts/ai-egress/v1/privacy-posture-evidence.schema.json`. Every
    field is bounded metadata; no prompt text, response text, embeddings,
    retrieved documents, filenames, environment values, API keys, or raw
    provider responses ever appear, because this function never reads an
    unexpected key from `observation` in the first place.
    """
    if not isinstance(observation, Mapping):
        observation = {}

    reasons: list[str] = []
    status = STATUS_PASS

    def _flag(new_status: str, reason: str) -> None:
        nonlocal status
        status = _bump(status, new_status)
        reasons.append(reason)

    policy_version = observation.get("policy_version")
    if policy_version not in KNOWN_POLICY_VERSIONS:
        policy_version_out = policy_version if isinstance(policy_version, str) else "unknown"
        return {
            "policy_version": policy_version_out,
            "classification_mode": "unknown",
            "destination_class": "unknown",
            "local_endpoint_classification": "unknown",
            "cloud_fallback_enabled": "unknown",
            "network_isolation_active": "unknown",
            "last_verified_at": None,
            "evidence_source": "unknown",
            "local_only_posture": {"available": False, "status": "unknown", "source": "local-only-posture-source:unavailable"},
            "hermes_version_reference": None,
            "status": STATUS_BLOCKED,
            "reason_codes": ["AI_PRIVACY_POSTURE_BLOCKED_UNKNOWN_POLICY_VERSION"],
        }

    destination_class = observation.get("destination_class")
    if destination_class not in DESTINATION_CLASSES:
        destination_class = "unknown"

    local_endpoint = observation.get("local_endpoint")
    local_endpoint_classification = (
        local_endpoint.get("classification") if isinstance(local_endpoint, Mapping) else None
    )
    if local_endpoint_classification not in LOCAL_ENDPOINT_CLASSIFICATIONS:
        local_endpoint_classification = "unknown"

    cloud_fallback_enabled = _tristate(observation.get("cloud_fallback_enabled"), TRISTATE_ENABLED)
    network_isolation_active = _tristate(observation.get("network_isolation_active"), TRISTATE_ACTIVE)

    expected_posture = observation.get("expected_posture")
    if expected_posture not in EXPECTED_POSTURES:
        expected_posture = "unknown"

    evidence_source, source_reason = _validate_evidence_source(observation.get("evidence_source"))
    if source_reason:
        reasons.append(source_reason)
        status = _bump(status, STATUS_WARN)

    version_ref, version_reason = _validate_version_reference(observation.get("hermes_version_reference"))
    if version_reason:
        reasons.append(version_reason)
        status = _bump(status, STATUS_WARN)

    local_only_evidence, local_only_available, local_only_reason = _local_only_source_evidence(
        observation.get("local_only_posture_source")
    )
    if local_only_reason:
        reasons.append(local_only_reason)
        if local_only_reason.startswith("AI_PRIVACY_POSTURE_FAIL_"):
            status = _bump(status, STATUS_FAIL)
        else:
            status = _bump(status, STATUS_WARN)

    # Staleness: observed_at is required and must be within the bounded
    # window, or the whole report fails closed to BLOCKED - stale evidence
    # is never reported as healthy, no matter what the individual signals
    # below say.
    observed_at = observation.get("observed_at")
    max_age = observation.get("max_evidence_age_seconds")
    if not isinstance(max_age, (int, float)) or max_age <= 0:
        max_age = DEFAULT_MAX_EVIDENCE_AGE_SECONDS

    if not isinstance(observed_at, str) or not _OBSERVED_AT_PATTERN.match(observed_at):
        observed_at = None

    stale = False
    if observed_at is None:
        stale = True
        reasons.append("AI_PRIVACY_POSTURE_BLOCKED_EVIDENCE_MISSING_TIMESTAMP")
    else:
        age_seconds = observation.get("evidence_age_seconds")
        if isinstance(age_seconds, (int, float)) and age_seconds > max_age:
            stale = True
            reasons.append("AI_PRIVACY_POSTURE_BLOCKED_EVIDENCE_STALE")

    if stale:
        return {
            "policy_version": policy_version,
            "classification_mode": "unknown",
            "destination_class": destination_class,
            "local_endpoint_classification": local_endpoint_classification,
            "cloud_fallback_enabled": cloud_fallback_enabled,
            "network_isolation_active": network_isolation_active,
            "last_verified_at": observed_at if isinstance(observed_at, str) else None,
            "evidence_source": evidence_source,
            "local_only_posture": local_only_evidence,
            "hermes_version_reference": version_ref,
            "status": STATUS_BLOCKED,
            "reason_codes": reasons,
        }

    # destination_class unknown fails closed - never "PASS by absence".
    if destination_class == "unknown":
        _flag(STATUS_BLOCKED, "AI_PRIVACY_POSTURE_BLOCKED_UNKNOWN_DESTINATION_CLASS")

    if expected_posture == "unknown":
        _flag(STATUS_WARN, "AI_PRIVACY_POSTURE_WARN_EXPECTED_POSTURE_UNKNOWN")

    if expected_posture == "restricted_local_only":
        if not local_only_available:
            _flag(STATUS_BLOCKED, "AI_PRIVACY_POSTURE_BLOCKED_LOCAL_ONLY_SOURCE_UNAVAILABLE")

        if destination_class == "cloud":
            _flag(STATUS_FAIL, "AI_PRIVACY_POSTURE_FAIL_DRIFT_LOCAL_ONLY_TO_CLOUD")
        elif destination_class == "private":
            _flag(STATUS_WARN, "AI_PRIVACY_POSTURE_WARN_DRIFT_LOCAL_ONLY_TO_PRIVATE_ENDPOINT")

        if cloud_fallback_enabled == "enabled":
            _flag(STATUS_FAIL, "AI_PRIVACY_POSTURE_FAIL_CLOUD_FALLBACK_ENABLED_UNDER_RESTRICTED_POSTURE")
        elif cloud_fallback_enabled == "unknown":
            _flag(STATUS_WARN, "AI_PRIVACY_POSTURE_WARN_CLOUD_FALLBACK_STATE_UNKNOWN")

        if network_isolation_active == "inactive":
            _flag(STATUS_FAIL, "AI_PRIVACY_POSTURE_FAIL_NETWORK_ISOLATION_INACTIVE_UNDER_RESTRICTED_POSTURE")
        elif network_isolation_active == "unknown":
            _flag(STATUS_WARN, "AI_PRIVACY_POSTURE_WARN_NETWORK_ISOLATION_STATE_UNKNOWN")

    if destination_class == "local" and local_endpoint_classification == "public":
        _flag(STATUS_FAIL, "AI_PRIVACY_POSTURE_FAIL_LOCAL_ENDPOINT_PUBLICLY_EXPOSED")
    elif destination_class == "local" and local_endpoint_classification == "unknown":
        _flag(STATUS_WARN, "AI_PRIVACY_POSTURE_WARN_LOCAL_ENDPOINT_CLASSIFICATION_UNKNOWN")

    if status == STATUS_PASS:
        reasons.append("AI_PRIVACY_POSTURE_PASS_CONSISTENT")

    return {
        "policy_version": policy_version,
        "classification_mode": "fail_closed_v1",
        "destination_class": destination_class,
        "local_endpoint_classification": local_endpoint_classification,
        "cloud_fallback_enabled": cloud_fallback_enabled,
        "network_isolation_active": network_isolation_active,
        "last_verified_at": observed_at,
        "evidence_source": evidence_source,
        "local_only_posture": local_only_evidence,
        "hermes_version_reference": version_ref,
        "status": status,
        "reason_codes": reasons,
    }
