"""lib/omes/py/jobs/entitlement.py - a pure, stdlib-only entitlement
evaluator and subscription-event applier (issue #92).

`evaluate()` is the ONLY function anything in this repository (or, per
docs/control-center-foundation.md, in awcms-one) should call to decide
whether a requested action is allowed under a tenant's current
entitlement. It never reads a database, never makes a network call, and
never trusts a client-supplied "granted" flag (AGENTS.md, issue #92
security requirements: "no client-supplied entitlement").

Suspension-policy rule (issue #92 acceptance criteria, AGENTS.md §3):
"do not silently stop a healthy existing deployment unless policy says
so". This is implemented literally: `keep_existing_running` is allowed by
default and only denied when a `resource_policy`'s
`existing_healthy_deployments_action` explicitly says `stop` or
`degrade`. Every other action type (`provision_new`, `upgrade`,
`start_optional_worker`) is independently controllable through the same
resource policy, and is denied by default once a subscription is not
`active`/`trialing`.
"""
from __future__ import annotations

from typing import Any

RESOURCE_KEYS = (
    "servers",
    "logical_agents",
    "specialist_agents",
    "isolated_workers",
    "storage_gb",
    "backup_retention_days",
)

# Suspension-affected states - anything that is not simply "healthy and
# paying" (active/trialing) falls back to resource-policy-gated behavior
# rather than a plain limit check.
_SUSPENSION_AFFECTED_STATES = frozenset({"past_due", "grace_period", "suspended"})
_TERMINAL_STATES = frozenset({"cancelled", "expired"})
_HEALTHY_STATES = frozenset({"active", "trialing"})

_DEFAULT_RESOURCE_POLICY = {
    "new_provisioning_action": "block",
    "upgrade_action": "block",
    "optional_workers_action": "stop",
    "existing_healthy_deployments_action": "keep_running",
}


def _decision(allow: bool, reason: str) -> dict[str, Any]:
    return {"allow": allow, "reason": reason}


def evaluate(
    entitlement: dict[str, Any],
    action: dict[str, Any],
    resource_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide whether `action` is allowed under `entitlement`.

    `action` = {
        "type": "provision_new" | "upgrade" | "start_optional_worker" | "keep_existing_running",
        "tenant_id": "...",           # the tenant actually making the request
        "resource": "servers", ...    # required for provision_new/upgrade/start_optional_worker
        "requested_total": 3,         # the total amount the tenant would hold after this action
    }

    Returns {"allow": bool, "reason": str}. `reason` is always populated,
    including on allow, so every decision is self-explaining for the
    audit log (AGENTS.md: "audit every grant/revoke/suspension decision").
    """
    policy = dict(_DEFAULT_RESOURCE_POLICY)
    if resource_policy:
        policy.update({k: v for k, v in resource_policy.items() if k in _DEFAULT_RESOURCE_POLICY})

    action_type = action.get("type")
    if action_type not in ("provision_new", "upgrade", "start_optional_worker", "keep_existing_running"):
        return _decision(False, f"unknown action type: {action_type!r}")

    # Cross-tenant check first, unconditionally: an entitlement belongs to
    # exactly one tenant, and no policy state can override this.
    requester_tenant = action.get("tenant_id")
    if requester_tenant is not None and requester_tenant != entitlement.get("tenant_id"):
        return _decision(False, "cross_tenant_denied: action tenant does not match entitlement tenant")

    state = entitlement.get("state")

    if action_type == "keep_existing_running":
        existing_action = policy["existing_healthy_deployments_action"]
        if existing_action == "keep_running":
            return _decision(True, f"existing healthy deployment kept running (state={state}, policy=keep_running)")
        return _decision(
            False,
            f"existing_healthy_deployments_action={existing_action!r} requires this deployment to be {existing_action}",
        )

    # From here on: provision_new / upgrade / start_optional_worker.
    if state in _TERMINAL_STATES:
        return _decision(False, f"subscription is {state}: no new provisioning, upgrade, or optional worker allowed")

    if state in _SUSPENSION_AFFECTED_STATES:
        policy_key = {
            "provision_new": "new_provisioning_action",
            "upgrade": "upgrade_action",
            "start_optional_worker": "optional_workers_action",
        }[action_type]
        decided = policy[policy_key]
        if decided != "allow":
            return _decision(
                False,
                f"{action_type} blocked while subscription is {state}: {policy_key}={decided!r}",
            )
        # Policy allows it even while suspended/past_due/grace_period -
        # fall through to the ordinary limit check below.
    elif state not in _HEALTHY_STATES:
        return _decision(False, f"unknown or unsupported subscription state: {state!r}")

    resource = action.get("resource")
    if resource not in RESOURCE_KEYS:
        return _decision(False, f"unknown or missing resource: {resource!r}")

    limits = entitlement.get("limits", {})
    if resource not in limits:
        return _decision(False, f"entitlement has no limit configured for resource {resource!r}")

    limit = limits[resource]
    requested_total = action.get("requested_total")
    if requested_total is None:
        return _decision(False, "requested_total is required to check a limit")
    if requested_total > limit:
        return _decision(
            False,
            f"limit_exceeded: requested_total={requested_total} exceeds limit {limit} for resource {resource!r}",
        )
    return _decision(True, f"within limit: requested_total={requested_total} <= limit {limit} for resource {resource!r}")


def apply_subscription_event(
    current: dict[str, Any] | None,
    event: dict[str, Any],
    machine: Any,
) -> dict[str, Any]:
    """Apply a subscription-lifecycle event to `current` (or create a new
    record if `current` is None), enforcing:

    - **Idempotency by event id**: replaying an event whose `event_id`
      matches `current["source_event_id"]` returns `current` unchanged
      rather than re-applying it (issue #92: "replayed subscription
      events" must be a no-op).
    - **Valid transitions only**: `machine.assert_transition()` (see
      `lib/omes/py/jobs/states.py`) raises `TransitionError` for a state
      change this subscription's state machine does not allow.

    `event` = {
        "event_id": "...",
        "tenant_id": "...",
        "subscription_id": "...",
        "to_state": "active",
        "limits": {...},            # optional; only present on limits_changed-shaped events
        "effective_from": "...",
    }
    """
    event_id = event["event_id"]
    if current is not None and current.get("source_event_id") == event_id:
        return current  # already applied - idempotent replay

    to_state = event["to_state"]
    if current is None:
        if to_state not in machine.initial_states:
            raise ValueError(f"cannot create a new record directly in non-initial state {to_state!r}")
    else:
        machine.assert_transition(current["state"], to_state)

    updated = dict(current) if current is not None else {}
    updated["tenant_id"] = event.get("tenant_id", updated.get("tenant_id"))
    updated["subscription_id"] = event.get("subscription_id", updated.get("subscription_id"))
    updated["state"] = to_state
    updated["effective_from"] = event.get("effective_from", updated.get("effective_from"))
    if "limits" in event:
        updated["limits"] = event["limits"]
    updated["source_event_id"] = event_id
    return updated
