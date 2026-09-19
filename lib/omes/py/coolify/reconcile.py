"""lib/omes/py/coolify/reconcile.py - merges a fresh Coolify observed-state
read into the mapping's stored observation, and never anything else
(issue #97).

Matches contracts/coolify/v1/reconciliation.request.schema.json: a patch
may only touch the closed observed-state key set (external_resource_id,
target_server, deployment_status, build_history_ref, proxy_domain_config,
logs_metadata). Any other key - in particular any OMES logical field such
as role/capability policy, secret_ref, entitlement, or backup policy - is
rejected before it ever reaches the stored mapping/observed-state files,
matching AGENTS.md section 2 ("OMES remains the source of truth for
logical agent identity; role and capability policy; secret references;
... Coolify remains the source of truth for the delegated resource's
external state").
"""
from __future__ import annotations

from typing import Any

from .mapping import OBSERVED_STATE_KEYS

ALLOWED_OBSERVATION_KEYS = frozenset(OBSERVED_STATE_KEYS)

# A representative (not exhaustive) set of field names that must never be
# accepted in an observation_patch, used only to produce a clearer error
# message when one of them is the offending key; the actual enforcement
# is the allow-list check below, not this list.
KNOWN_LOGICAL_FIELD_NAMES = frozenset(
    {
        "role",
        "capabilities",
        "policy",
        "entitlement",
        "entitlement_tier",
        "secret_ref",
        "backup_policy",
        "data_classification",
        "health_contract",
        "desired_mapping",
    }
)


class ReconcileError(Exception):
    """Raised when an observation_patch tries to touch a field outside
    the closed observed-state key set."""


def validate_patch(patch: dict[str, Any]) -> None:
    disallowed = sorted(set(patch.keys()) - ALLOWED_OBSERVATION_KEYS)
    if disallowed:
        logical_hits = [name for name in disallowed if name in KNOWN_LOGICAL_FIELD_NAMES]
        detail = f"disallowed field(s): {disallowed}"
        if logical_hits:
            detail += f" (looks like OMES logical/policy field(s): {logical_hits})"
        raise ReconcileError(
            "observation_patch may only contain observed-state fields "
            f"{sorted(ALLOWED_OBSERVATION_KEYS)}; {detail}"
        )


def reconcile(observed_state: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Returns a new observed-state dict with `patch` merged in.
    `observed_state` is never mutated in place. Raises ReconcileError
    (without merging anything) if `patch` contains any key outside the
    closed observed-state set - reconciliation either fully applies a
    legitimate patch or fully rejects an illegitimate one; it never
    partially applies one."""
    validate_patch(patch)
    merged = dict(observed_state)
    merged.update(patch)
    return merged
