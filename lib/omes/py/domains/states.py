"""lib/omes/py/domains/states.py - the domain-order state machine
(issue #98), as pure transition data over the `domain-order`
(`contracts/domains/v1/domain-order.schema.json`) shape.

This module does not talk to any provider; it only tells callers which
transitions are legal and stamps a new transition record. Real provider
calls, reconciliation polling, and persistence live in the fake provider
(`fake_provider.py`, for tests) or, for a live provider, in issues
#99/#100 and awcms-one.
"""
from __future__ import annotations

from typing import Any

STATES = (
    "pending",
    "action_required",
    "failed",
    "succeeded",
    "active",
    "expired",
    "renewal_due",
    "cancelled",
)

TERMINAL_STATES = frozenset({"failed", "succeeded", "cancelled"})

# Every (from_state -> set of legal to_states). "pending" is the only
# entry point (a fresh domain-order record); "active"/"renewal_due" are
# reachable only after a registration/renewal has actually succeeded and
# been reconciled against provider truth - never assumed from payment or
# a queued request (AGENTS.md #98 rule: "payment success does not prove
# registration success").
TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"action_required", "failed", "succeeded", "cancelled"}),
    "action_required": frozenset({"pending", "failed", "succeeded", "cancelled"}),
    "succeeded": frozenset({"active"}),
    "active": frozenset({"renewal_due", "expired", "action_required"}),
    "renewal_due": frozenset({"pending", "active", "expired", "action_required"}),
    "expired": frozenset({"pending", "cancelled"}),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


class InvalidTransitionError(Exception):
    def __init__(self, from_state: str, to_state: str):
        super().__init__(f"domain-order: {from_state!r} -> {to_state!r} is not a legal transition")
        self.from_state = from_state
        self.to_state = to_state


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def can_transition(from_state: str, to_state: str) -> bool:
    if from_state not in TRANSITIONS:
        raise ValueError(f"unknown domain-order state: {from_state!r}")
    if to_state not in STATES:
        raise ValueError(f"unknown domain-order state: {to_state!r}")
    return to_state in TRANSITIONS[from_state]


def apply_transition(
    order: dict[str, Any],
    to_state: str,
    actor: dict[str, str],
    transitioned_at: str,
    *,
    reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Returns a NEW order dict (never mutates `order` in place, so a
    caller holding the previous record still sees the pre-transition
    state) with `state` advanced to `to_state`, `previous_state` set to
    the prior state, and `reconciliation` attached if given.

    Raises `InvalidTransitionError` for an illegal transition - callers
    must not silently clamp to the nearest legal state.
    """
    from_state = order["state"]
    if not can_transition(from_state, to_state):
        raise InvalidTransitionError(from_state, to_state)
    new_order = dict(order)
    new_order["previous_state"] = from_state
    new_order["state"] = to_state
    new_order["actor"] = actor
    new_order["transitioned_at"] = transitioned_at
    if reconciliation is not None:
        new_order["reconciliation"] = reconciliation
    return new_order
