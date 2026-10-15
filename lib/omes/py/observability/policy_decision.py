"""lib/omes/py/observability/policy_decision.py - policy/capability decision
envelope validation and pure composition (issue #274, ADR-0032 rule 7,
threat MA-05).

THIS IS NOT A POLICY ENGINE. Decisions are made by the authorities that
already own them (AWCMS approval and entitlement, OMES job allowlist and
approval, Hermes tool approval, infrastructure controls, providers). This
module only (1) validates the envelope that records one such decision and
(2) composes several recorded decisions about the SAME action into one
result. It evaluates no rule, reads no policy body, stores nothing, creates
no approval, runs no inbox, and performs no I/O besides loading the
checked-in schema. It is pure: the clock is a parameter.

Contract: contracts/observability/v1/policy-decision.schema.json, validated
with the repo's existing stdlib validator (jobs/schema.py). The
major-version gate, forbidden field-name scan and timestamp parser are
reused from `observability.envelope`, not copied.

Composition rules (`compose`):

1. Inputs must share one (tenant_id, correlation_id, requested_action);
   anything else is an error and no composed result is produced.
2. Normalisation: a decision whose `evidence.freshness` is not `live`, or
   whose `expires_at` is not after `now`, is treated as `unavailable`
   (stale or expired evidence is never a basis for allow, deny or approval).
3. Precedence: deny > unavailable > approval_required > allow.
4. `allow` requires that every input is allow AND that every required
   authority contributed at least one decision. A missing required
   authority counts as an `unavailable` contribution with reason code
   `AUTHORITY_DECISION_MISSING`.
5. `approval_required` propagates the `approval_ref` values of the
   contributing approval-required decisions unchanged. Nothing here
   creates, grants, or infers an approval.
6. Empty input is `unavailable`.

The result depends only on the SET of input decisions (any order or
duplication gives an identical result). The input is not mutated.

Not implemented yet (tracked in #274): any producer that emits these
envelopes and any caller in the job runner, worker or AWCMS flows.
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
from observability import envelope  # noqa: E402

SCHEMA_PATH = REPO_ROOT / "contracts" / "observability" / "v1" / "policy-decision.schema.json"

# Lower rank wins. Order is the ADR-0032 rule 7 precedence.
PRECEDENCE = ("deny", "unavailable", "approval_required", "allow")
_RANK = {name: index for index, name in enumerate(PRECEDENCE)}

REASON_EVIDENCE_NOT_LIVE = "EVIDENCE_NOT_LIVE"
REASON_DECISION_EXPIRED = "DECISION_EXPIRED"
REASON_AUTHORITY_DECISION_MISSING = "AUTHORITY_DECISION_MISSING"
REASON_NO_DECISIONS = "NO_DECISIONS"

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

_schema_cache: dict[str, Any] | None = None


def load_schema() -> dict[str, Any]:
    global _schema_cache
    if _schema_cache is None:
        schema = schema_mod.load_json(SCHEMA_PATH)
        schema_mod.validate_schema(schema)
        _schema_cache = schema
    return _schema_cache


def authorities() -> list[str]:
    """The closed authority vocabulary of the contract."""
    return list(load_schema()["properties"]["deciding_authority"]["enum"])


def _control_chars(node: Any, path: str) -> list[str]:
    errors: list[str] = []
    if isinstance(node, str):
        if _CONTROL_CHAR_RE.search(node):
            errors.append(f"{path}: control characters are not allowed")
    elif isinstance(node, dict):
        for key, value in node.items():
            errors.extend(_control_chars(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            errors.extend(_control_chars(item, f"{path}[{index}]"))
    return errors


def validate(decision: Any) -> list[str]:
    """Returns a list of error strings (empty means valid). Fail-closed:
    unsupported major version, schema violations, forbidden raw/free-text
    field names, secret-shaped values, control characters and
    calendar-invalid or inconsistent timestamps are all errors. Never raises
    for a bad decision; a malformed *schema file* still raises SchemaError."""
    errors = envelope.check_major_version(decision)
    if errors:
        return errors

    errors = list(envelope.scan_forbidden(decision))
    errors.extend(schema_mod.validate(decision, load_schema()))
    errors.extend(_control_chars(decision, "$"))

    parsed: dict[str, datetime.datetime] = {}
    evidence = decision.get("evidence")
    candidates = (
        ("$.evidence.observed_at", evidence.get("observed_at") if isinstance(evidence, dict) else None),
        ("$.expires_at", decision.get("expires_at")),
    )
    for label, value in candidates:
        if isinstance(value, str):
            try:
                parsed[label] = envelope.parse_timestamp(value)
            except ValueError:
                errors.append(f"{label}: {value!r} is not a valid UTC RFC 3339 timestamp")
    if "$.evidence.observed_at" in parsed and "$.expires_at" in parsed:
        if parsed["$.expires_at"] <= parsed["$.evidence.observed_at"]:
            errors.append("$.expires_at: must be after $.evidence.observed_at")
    return list(dict.fromkeys(errors))


def _canonical(decision: dict[str, Any]) -> str:
    return json.dumps(decision, sort_keys=True, separators=(",", ":"))


def _format_timestamp(value: datetime.datetime) -> str:
    return value.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _effective(decision: dict[str, Any], now: datetime.datetime) -> tuple[str, str]:
    """(effective decision, effective reason_code) after normalisation."""
    if decision["evidence"]["freshness"] != "live":
        return "unavailable", REASON_EVIDENCE_NOT_LIVE
    expires_at = decision.get("expires_at")
    if expires_at is not None and envelope.parse_timestamp(expires_at) <= now:
        return "unavailable", REASON_DECISION_EXPIRED
    return decision["decision"], decision["reason_code"]


def _failure(errors: Iterable[str]) -> dict[str, Any]:
    return {"ok": False, "errors": sorted(set(errors)), "composed": None}


def compose(
    decisions: Iterable[Any],
    *,
    now: datetime.datetime,
    required_authorities: Iterable[str],
) -> dict[str, Any]:
    """Pure, deterministic composition of decisions about ONE action.

    Returns `{"ok": bool, "errors": [...], "composed": {...} | None}`.
    On any error `ok` is False and `composed` is None (fail closed: there is
    no result a caller could mistake for a decision). `now` (timezone-aware)
    and `required_authorities` are required keyword arguments with no
    default so a caller cannot forget either; an empty
    `required_authorities` is an explicit statement that no authority is
    mandatory.

    The composed result holds: tenant_id, correlation_id, requested_action,
    `decision`, `winning_authority`, `winning_decision_id`,
    `winning_reason_code`, `winning_policy_id`, `contributing_decision_ids`
    (sorted), `approval_refs` (sorted, only from approval-required decisions
    that were in effect), `missing_authorities`, `required_authorities`,
    `expires_at` (earliest input expiry or null) and `evaluated_at`.
    """
    if not isinstance(now, datetime.datetime) or now.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime (the composer never reads the clock)")
    now = now.astimezone(datetime.timezone.utc)

    required_list = list(required_authorities)
    known = set(authorities())
    unknown_required = sorted({str(a) for a in required_list if a not in known})
    if unknown_required:
        return _failure(f"required_authorities: unknown authority {unknown_required!r}")
    required = sorted(set(required_list))

    items = list(decisions)
    errors: list[str] = []
    unique: dict[str, dict[str, Any]] = {}
    canonical: dict[str, str] = {}
    for index, item in enumerate(items):
        problems = validate(item)
        if problems:
            label = (
                f"decision_id {item['decision_id'][:128]!r}"
                if isinstance(item, dict) and isinstance(item.get("decision_id"), str)
                else f"input #{index}"
            )
            errors.extend(f"{label}: {p}" for p in problems)
            continue
        decision_id = item["decision_id"]
        text = _canonical(item)
        if decision_id in canonical:
            if canonical[decision_id] != text:
                errors.append(f"decision_id {decision_id!r} reused with different content (possible decision poisoning)")
            continue
        canonical[decision_id] = text
        unique[decision_id] = item
    if errors:
        return _failure(errors)

    evaluated_at = _format_timestamp(now)
    if not unique:
        return {
            "ok": True,
            "errors": [],
            "composed": {
                "tenant_id": None,
                "correlation_id": None,
                "requested_action": None,
                "decision": "unavailable",
                "winning_authority": None,
                "winning_decision_id": None,
                "winning_reason_code": REASON_NO_DECISIONS,
                "winning_policy_id": None,
                "contributing_decision_ids": [],
                "approval_refs": [],
                "missing_authorities": required,
                "required_authorities": required,
                "expires_at": None,
                "evaluated_at": evaluated_at,
            },
        }

    for field in ("tenant_id", "correlation_id", "requested_action"):
        values = sorted({d[field] for d in unique.values()})
        if len(values) > 1:
            errors.append(
                f"mixed {field} across decisions ({len(values)} distinct values); "
                "composition requires one tenant, one correlation and one action"
            )
    if errors:
        return _failure(errors)

    effective: dict[str, tuple[str, str]] = {did: _effective(d, now) for did, d in unique.items()}
    present = {d["deciding_authority"] for d in unique.values()}
    missing = [a for a in required if a not in present]

    def sort_key(decision_id: str) -> tuple[int, str, str]:
        return (_RANK[effective[decision_id][0]], unique[decision_id]["deciding_authority"], decision_id)

    ordered = sorted(unique, key=sort_key)
    best = ordered[0]
    result_decision = effective[best][0]
    winner: str | None = best
    reason_code = effective[best][1]

    # A missing required authority is an `unavailable` contribution. It only
    # changes the outcome when nothing at least as strong (deny/unavailable)
    # already decided it.
    if missing and _RANK[result_decision] > _RANK["unavailable"]:
        result_decision = "unavailable"
        winner = None
        reason_code = REASON_AUTHORITY_DECISION_MISSING

    winning = unique[winner] if winner is not None else None
    if result_decision == "approval_required":
        approval_refs = sorted({
            d["approval_ref"]
            for did, d in unique.items()
            if effective[did][0] == "approval_required" and d.get("approval_ref") is not None
        })
    else:
        approval_refs = []
    expiries = [envelope.parse_timestamp(d["expires_at"]) for d in unique.values() if d.get("expires_at") is not None]
    first = next(iter(unique.values()))
    return {
        "ok": True,
        "errors": [],
        "composed": {
            "tenant_id": first["tenant_id"],
            "correlation_id": first["correlation_id"],
            "requested_action": first["requested_action"],
            "decision": result_decision,
            "winning_authority": winning["deciding_authority"] if winning else (missing[0] if missing else None),
            "winning_decision_id": winner,
            "winning_reason_code": reason_code,
            "winning_policy_id": winning["policy_id"] if winning else None,
            "contributing_decision_ids": sorted(unique),
            "approval_refs": approval_refs,
            "missing_authorities": missing,
            "required_authorities": required,
            "expires_at": _format_timestamp(min(expiries)) if expiries else None,
            "evaluated_at": evaluated_at,
        },
    }
