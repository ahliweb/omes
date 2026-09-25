"""lib/omes/py/privacy/egress_policy.py - deterministic AI data-classification
and model-egress policy evaluator (issue #214, ADR-0029).

This module implements the decision matrix described in
docs/ai-data-privacy-and-model-security.md section 6, as a pure,
stdlib-only function of bounded request metadata. It performs no network
I/O, no logging, and no persistence.

Hard boundaries (do not weaken these):

- `evaluate()` takes and returns only bounded metadata: a policy version,
  a data classification, a destination class, a purpose label, an
  authentication-material flag, a provider-posture status, and an optional
  sanitization-evidence reference. It must never be passed prompt text,
  response text, embeddings, retrieved documents, or a real credential
  value - callers own keeping this boundary metadata-only.
- Every branch that encounters a missing or unrecognized value fails
  closed (`deny`), never "best effort allow". This includes an unknown
  policy version, classification, destination, or provider-posture status,
  and a missing/non-boolean authentication-material flag (treated as if
  the flag were `True`).
- A request that says it carries authentication material (credentials,
  API tokens, private keys, secret values) is always denied for any
  destination other than `local_only`, regardless of classification,
  provider posture, or sanitization evidence.
- `RESTRICTED` data is denied for `cloud_sanitized` unconditionally.
- This module does not select, call, or route to any model provider. It
  is a policy decision function only; Hermes remains authoritative for
  model/provider routing (ADR-0017, ADR-0029).
- The decision output is one of `allow` / `deny` / `approval_required`
  plus a bounded set of stable reason codes (`REASON_CODES`). It never
  returns a command, shell fragment, path, or anything executable.
- `cloud_sanitized` (issue #237, threat AI-07): a provider claim such as
  "not used for training" is never treated as a complete privacy
  guarantee. Before `cloud_sanitized` can be approved (`allow` or
  `approval_required`) the request's `provider_assurance` object must
  record the 10-item due-diligence checklist from
  docs/ai-data-privacy-and-model-security.md section 5 as `verified`
  (item 10 may instead be `not_applicable`); a missing or incomplete
  record is a hard, fail-closed `deny` - it never silently allows. That
  assurance record must also be bound to the exact destination provider:
  `provider_posture.provider_id` must be present and exactly equal
  `provider_assurance.provider_id`, or the request is denied
  (`AI_EGRESS_DENY_PROVIDER_ASSURANCE_MISMATCH`) - adequate due diligence
  recorded for one provider must never approve egress to a different one.
"""
from __future__ import annotations

from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Bounded vocabularies (contracts/ai-egress/v1/*.schema.json mirrors these)
# ---------------------------------------------------------------------------

#: Policy versions this evaluator knows how to apply. Any other value
#: (missing, typo'd, or a future version this evaluator has not been
#: upgraded to understand) fails closed.
KNOWN_POLICY_VERSIONS: frozenset[str] = frozenset({"v1"})

CLASSIFICATIONS: frozenset[str] = frozenset({
    "PUBLIC",
    "INTERNAL",
    "CONFIDENTIAL",
    "RESTRICTED",
})

DESTINATIONS: frozenset[str] = frozenset({
    "local_only",
    "private_endpoint",
    "cloud_sanitized",
    "deny",
})

PROVIDER_POSTURE_STATUSES: frozenset[str] = frozenset({
    "approved",
    "not_approved",
    "unknown",
})

#: Provider-assurance-evidence contract versions this evaluator knows how to
#: apply (issue #237, threat AI-07). Any other value fails closed the same
#: way an unknown `policy_version` does.
KNOWN_PROVIDER_ASSURANCE_VERSIONS: frozenset[str] = frozenset({"v1"})

#: The 10-item provider due-diligence checklist from
#: docs/ai-data-privacy-and-model-security.md section 5. Items 1-9 must be
#: `verified`; `_OPTIONAL_PROVIDER_ASSURANCE_ITEM` (item 10, "where
#: available") may instead be `not_applicable`. This is the single
#: canonical list of item keys - contracts/ai-egress/v1/provider-assurance-
#: evidence.schema.json and egress-decision-request.schema.json's embedded
#: `provider_assurance.items` mirror these names and must be kept in sync.
_REQUIRED_PROVIDER_ASSURANCE_ITEMS: tuple[str, ...] = (
    "data_categories_and_purpose",
    "controller_processor_roles",
    "retention_and_deletion",
    "training_use",
    "abuse_monitoring_human_access",
    "subprocessors_and_transfer_locations",
    "encryption_and_tenant_isolation",
    "contractual_dpa_terms",
    "incident_notification_and_audit",
)

_OPTIONAL_PROVIDER_ASSURANCE_ITEM = "private_networking_zero_retention_options"

_PROVIDER_ASSURANCE_ITEM_STATUSES: frozenset[str] = frozenset({
    "verified",
    "not_verified",
    "unknown",
    "not_applicable",
})

DECISIONS: tuple[str, ...] = ("allow", "deny", "approval_required")

#: Stable, machine-readable reason-code vocabulary. Callers/tests may rely
#: on these names not changing meaning once published; a new reason must
#: be a new code, not a repurposed one.
REASON_CODES: frozenset[str] = frozenset({
    "AI_EGRESS_ALLOW_LOCAL_ONLY",
    "AI_EGRESS_ALLOW_PRIVATE_ENDPOINT_APPROVED",
    "AI_EGRESS_ALLOW_CLOUD_SANITIZED_APPROVED",
    "AI_EGRESS_ALLOW_PUBLIC_CLOUD_POLICY",
    "AI_EGRESS_APPROVAL_REQUIRED_CONFIDENTIAL_PRIVATE_ENDPOINT",
    "AI_EGRESS_APPROVAL_REQUIRED_CONFIDENTIAL_CLOUD_SANITIZED",
    "AI_EGRESS_APPROVAL_REQUIRED_RESTRICTED_PRIVATE_ENDPOINT",
    "AI_EGRESS_DENY_UNKNOWN_POLICY_VERSION",
    "AI_EGRESS_DENY_UNKNOWN_CLASSIFICATION",
    "AI_EGRESS_DENY_UNKNOWN_DESTINATION",
    "AI_EGRESS_DENY_UNKNOWN_PROVIDER_POSTURE",
    "AI_EGRESS_DENY_DESTINATION_EXPLICITLY_DENIED",
    "AI_EGRESS_DENY_AUTHENTICATION_MATERIAL",
    "AI_EGRESS_DENY_RESTRICTED_CLOUD_SANITIZED",
    "AI_EGRESS_DENY_RESTRICTED_PROVIDER_NOT_APPROVED",
    "AI_EGRESS_DENY_PROVIDER_NOT_APPROVED",
    "AI_EGRESS_DENY_MISSING_SANITIZATION_EVIDENCE",
    "AI_EGRESS_DENY_MISSING_PROVIDER_ASSURANCE",
    "AI_EGRESS_DENY_PROVIDER_ASSURANCE_INCOMPLETE",
    "AI_EGRESS_DENY_PROVIDER_ASSURANCE_MISMATCH",
})


def _has_sanitization_evidence(request: Mapping[str, Any]) -> bool:
    evidence = request.get("sanitization_evidence")
    if not isinstance(evidence, dict):
        return False
    method = evidence.get("method")
    evidence_id = evidence.get("evidence_id")
    return isinstance(method, str) and bool(method) and isinstance(evidence_id, str) and bool(evidence_id)


def _provider_assurance_status(request: Mapping[str, Any]) -> str:
    """Evaluates the caller-supplied `provider_assurance` object (issue
    #237, threat AI-07) against the 10-item due-diligence checklist.

    Returns one of `"adequate"`, `"missing"`, or `"incomplete"`. This is
    metadata-shape validation only (bounded field/enum checks against
    already-parsed dict/str values) - it never inspects, hashes, or infers
    anything about actual provider behavior, and it performs no I/O. A
    provider's own claim (e.g. "not used for training") is never treated as
    evidence by itself; only a caller-recorded `verified` status counts.
    """
    assurance = request.get("provider_assurance")
    if not isinstance(assurance, dict):
        return "missing"
    if assurance.get("schema_version") not in KNOWN_PROVIDER_ASSURANCE_VERSIONS:
        return "missing"
    if not isinstance(assurance.get("provider_id"), str) or not assurance.get("provider_id"):
        return "missing"
    items = assurance.get("items")
    if not isinstance(items, dict):
        return "missing"

    for key in _REQUIRED_PROVIDER_ASSURANCE_ITEMS:
        entry = items.get(key)
        if not isinstance(entry, dict):
            return "incomplete"
        status = entry.get("status")
        if status not in _PROVIDER_ASSURANCE_ITEM_STATUSES or status != "verified":
            return "incomplete"

    optional_entry = items.get(_OPTIONAL_PROVIDER_ASSURANCE_ITEM)
    if not isinstance(optional_entry, dict):
        return "incomplete"
    optional_status = optional_entry.get("status")
    if optional_status not in ("verified", "not_applicable"):
        return "incomplete"

    return "adequate"


def _provider_assurance_matches_posture_provider(request: Mapping[str, Any]) -> bool:
    """An `adequate` `provider_assurance` record proves due diligence was
    recorded for SOME provider - this checks it was recorded for the SAME
    provider the request is actually about to send content to (issue #237
    follow-up: a fully verified assurance record for provider A must never
    approve a request to provider B). Requires `provider_posture.provider_id`
    to be present (a non-empty string) AND exactly equal to
    `provider_assurance.provider_id`; a missing posture `provider_id` fails
    closed rather than being treated as "any provider matches"."""
    posture = request.get("provider_posture")
    posture_provider_id = posture.get("provider_id") if isinstance(posture, dict) else None
    if not isinstance(posture_provider_id, str) or not posture_provider_id:
        return False

    assurance = request.get("provider_assurance")
    assurance_provider_id = assurance.get("provider_id") if isinstance(assurance, dict) else None
    if not isinstance(assurance_provider_id, str) or not assurance_provider_id:
        return False

    return posture_provider_id == assurance_provider_id


def _provider_posture_status(request: Mapping[str, Any]) -> str | None:
    posture = request.get("provider_posture")
    if not isinstance(posture, dict):
        return None
    status = posture.get("status")
    return status if status in PROVIDER_POSTURE_STATUSES else None


def _contains_authentication_material(request: Mapping[str, Any]) -> bool:
    """Fail-closed read of the authentication-material flag: only an
    explicit `False` is treated as "does not carry authentication
    material". Missing, `True`, or any non-boolean value is treated as
    `True` (the conservative/deny-leaning reading) rather than assumed
    safe."""
    flag = request.get("contains_authentication_material")
    if flag is False:
        return False
    return True


def _matrix_decision(
    classification: str,
    destination: str,
    posture_status: str | None,
    has_sanitization: bool,
) -> tuple[str, str]:
    """The core decision matrix from
    docs/ai-data-privacy-and-model-security.md section 6. Called only
    after policy_version/classification/destination/authentication-material
    have already passed fail-closed checks, and only for a destination
    other than `deny` (handled by the caller)."""
    approved = posture_status == "approved"

    if destination == "local_only":
        return "allow", "AI_EGRESS_ALLOW_LOCAL_ONLY"

    # destination in {"private_endpoint", "cloud_sanitized"} from here on;
    # both require a resolved (non-unknown) provider posture, enforced by
    # the caller before this function runs.

    if classification == "PUBLIC":
        if destination == "private_endpoint":
            return ("allow", "AI_EGRESS_ALLOW_PRIVATE_ENDPOINT_APPROVED") if approved \
                else ("deny", "AI_EGRESS_DENY_PROVIDER_NOT_APPROVED")
        return ("allow", "AI_EGRESS_ALLOW_PUBLIC_CLOUD_POLICY") if approved \
            else ("deny", "AI_EGRESS_DENY_PROVIDER_NOT_APPROVED")

    if classification == "INTERNAL":
        if destination == "private_endpoint":
            return ("allow", "AI_EGRESS_ALLOW_PRIVATE_ENDPOINT_APPROVED") if approved \
                else ("deny", "AI_EGRESS_DENY_PROVIDER_NOT_APPROVED")
        # cloud_sanitized: allowed only when minimized (sanitization
        # evidence present) AND the provider is approved.
        if not has_sanitization:
            return "deny", "AI_EGRESS_DENY_MISSING_SANITIZATION_EVIDENCE"
        return ("allow", "AI_EGRESS_ALLOW_CLOUD_SANITIZED_APPROVED") if approved \
            else ("deny", "AI_EGRESS_DENY_PROVIDER_NOT_APPROVED")

    if classification == "CONFIDENTIAL":
        if destination == "private_endpoint":
            return ("allow", "AI_EGRESS_ALLOW_PRIVATE_ENDPOINT_APPROVED") if approved \
                else ("approval_required", "AI_EGRESS_APPROVAL_REQUIRED_CONFIDENTIAL_PRIVATE_ENDPOINT")
        # cloud_sanitized: deny-by-default; explicit policy (approved
        # provider) plus sanitization evidence together downgrade to a
        # human approval step - never a silent allow.
        if not has_sanitization:
            return "deny", "AI_EGRESS_DENY_MISSING_SANITIZATION_EVIDENCE"
        return ("approval_required", "AI_EGRESS_APPROVAL_REQUIRED_CONFIDENTIAL_CLOUD_SANITIZED") if approved \
            else ("deny", "AI_EGRESS_DENY_PROVIDER_NOT_APPROVED")

    # RESTRICTED
    if destination == "private_endpoint":
        return ("approval_required", "AI_EGRESS_APPROVAL_REQUIRED_RESTRICTED_PRIVATE_ENDPOINT") if approved \
            else ("deny", "AI_EGRESS_DENY_RESTRICTED_PROVIDER_NOT_APPROVED")
    # cloud_sanitized: RESTRICTED is always denied for cloud egress,
    # unconditionally (security requirement in issue #214 / ADR-0029).
    return "deny", "AI_EGRESS_DENY_RESTRICTED_CLOUD_SANITIZED"


def evaluate(request: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate a metadata-only egress request and return a bounded
    decision.

    `request` is expected to be a mapping shaped like
    `contracts/ai-egress/v1/egress-decision-request.schema.json`, but this
    function does not itself perform JSON Schema validation (callers at a
    contract boundary should validate separately with
    `lib/omes/py/jobs/schema.py`) - it is deliberately defensive against
    missing/malformed fields so it never raises on bad input and never
    fails open.

    Returns a dict shaped like
    `contracts/ai-egress/v1/egress-decision-response.schema.json`:
    `policy_version`, `classification`, `destination`, `decision`,
    `reason_codes` (non-empty list, most-specific first), and any
    `tenant_id`/`resource_scope` echoed back unchanged for audit
    correlation.
    """
    if not isinstance(request, Mapping):
        request = {}

    policy_version = request.get("policy_version")
    classification = request.get("classification")
    destination = request.get("destination")

    def _respond(decision: str, reason_codes: list[str]) -> dict[str, Any]:
        response: dict[str, Any] = {
            "policy_version": policy_version if isinstance(policy_version, str) else "unknown",
            "classification": classification if isinstance(classification, str) else "unknown",
            "destination": destination if isinstance(destination, str) else "unknown",
            "decision": decision,
            "reason_codes": reason_codes,
        }
        tenant_id = request.get("tenant_id")
        if isinstance(tenant_id, str) and tenant_id:
            response["tenant_id"] = tenant_id
        resource_scope = request.get("resource_scope")
        if isinstance(resource_scope, str) and resource_scope:
            response["resource_scope"] = resource_scope
        return response

    if policy_version not in KNOWN_POLICY_VERSIONS:
        return _respond("deny", ["AI_EGRESS_DENY_UNKNOWN_POLICY_VERSION"])

    if classification not in CLASSIFICATIONS:
        return _respond("deny", ["AI_EGRESS_DENY_UNKNOWN_CLASSIFICATION"])

    if destination not in DESTINATIONS:
        return _respond("deny", ["AI_EGRESS_DENY_UNKNOWN_DESTINATION"])

    if destination == "deny":
        return _respond("deny", ["AI_EGRESS_DENY_DESTINATION_EXPLICITLY_DENIED"])

    if _contains_authentication_material(request) and destination != "local_only":
        return _respond("deny", ["AI_EGRESS_DENY_AUTHENTICATION_MATERIAL"])

    posture_status = _provider_posture_status(request)
    if destination != "local_only" and posture_status is None:
        # Missing, malformed, or explicitly "unknown" provider posture -
        # fail closed rather than guessing.
        return _respond("deny", ["AI_EGRESS_DENY_UNKNOWN_PROVIDER_POSTURE"])
    if posture_status == "unknown":
        return _respond("deny", ["AI_EGRESS_DENY_UNKNOWN_PROVIDER_POSTURE"])

    has_sanitization = _has_sanitization_evidence(request)
    decision, reason_code = _matrix_decision(classification, destination, posture_status, has_sanitization)

    # Provider-assurance gate (issue #237, threat AI-07): a provider claim
    # such as "not used for training" is never a complete privacy
    # guarantee. Before any cloud_sanitized destination is actually
    # approved (allow or approval_required - approval_required still means
    # a human could approve it), the caller must have separately recorded
    # verified due-diligence evidence for retention, human access,
    # subprocessors, residency/transfer, and the rest of the section 5
    # checklist. This only ever downgrades an approval into a deny; it
    # never overrides an existing deny (e.g. missing sanitization evidence,
    # unapproved provider, RESTRICTED-to-cloud) with a different reason,
    # and it never turns a deny into an allow.
    if destination == "cloud_sanitized" and decision in ("allow", "approval_required"):
        assurance_status = _provider_assurance_status(request)
        if assurance_status == "missing":
            return _respond("deny", ["AI_EGRESS_DENY_MISSING_PROVIDER_ASSURANCE"])
        if assurance_status == "incomplete":
            return _respond("deny", ["AI_EGRESS_DENY_PROVIDER_ASSURANCE_INCOMPLETE"])
        # assurance_status == "adequate" here, but adequate evidence for
        # SOME provider must never approve egress to a DIFFERENT provider -
        # the assurance record must be bound to the exact destination
        # provider named in provider_posture.
        if not _provider_assurance_matches_posture_provider(request):
            return _respond("deny", ["AI_EGRESS_DENY_PROVIDER_ASSURANCE_MISMATCH"])

    return _respond(decision, [reason_code])
