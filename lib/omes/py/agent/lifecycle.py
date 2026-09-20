"""lib/omes/py/agent/lifecycle.py - the AgentDeployment lifecycle state
machine (docs/agent-orchestration-roadmap.md section 5, issue #87).

    declared -> preflighted -> planned -> backed-up -> applied -> verified
    -> ready -> healthy

Failure states are explicit and reachable from any in-progress state:
    degraded | failed | rolled-back

This module only defines legal transitions and validates them; it does
not perform the underlying preflight/backup/apply/verify work itself
(that is lib/omes/cmd/agent.sh + this package's other modules).
"""
from __future__ import annotations

from typing import Dict, List

STATES = (
    "declared",
    "preflighted",
    "planned",
    "backed-up",
    "applied",
    "verified",
    "ready",
    "healthy",
    "degraded",
    "failed",
    "rolled-back",
)

FAILURE_STATES = ("degraded", "failed", "rolled-back")

_FORWARD: List[str] = [
    "declared",
    "preflighted",
    "planned",
    "backed-up",
    "applied",
    "verified",
    "ready",
    "healthy",
]

# Every in-progress (non-terminal-success, non-failure) state may
# transition to any failure state; "healthy" may degrade; any state may
# be explicitly rolled back (rollback is defined to work from wherever a
# deployment currently is - issue #87's rollback acceptance criterion).
_ALLOWED: Dict[str, set] = {}
for _idx, _state in enumerate(_FORWARD):
    _next = set()
    if _idx + 1 < len(_FORWARD):
        _next.add(_FORWARD[_idx + 1])
    _next.update(FAILURE_STATES)
    _ALLOWED[_state] = _next
# A healthy deployment can also go straight back to ready (e.g. a
# transient health failure recovering) without being treated as a fresh
# apply.
_ALLOWED["healthy"].add("ready")
_ALLOWED["degraded"] = {"ready", "healthy", "failed", "rolled-back"}
_ALLOWED["failed"] = {"declared", "rolled-back", "failed"}
_ALLOWED["rolled-back"] = {"declared", "rolled-back"}

# `omes agent apply` must be idempotent (issue #87): re-running it against
# an already-ready/healthy/degraded deployment re-enters the check ->
# plan -> backup -> mutate -> verify cycle from the top rather than being
# rejected as an illegal transition. Every post-preflight state can
# therefore restart the cycle at "preflighted".
for _state in ("planned", "backed-up", "applied", "verified", "ready", "healthy", "degraded"):
    _ALLOWED[_state].add("preflighted")


class TransitionError(ValueError):
    pass


def is_valid_state(state: str) -> bool:
    return state in STATES


def can_transition(current: str, target: str) -> bool:
    if not is_valid_state(current) or not is_valid_state(target):
        return False
    if current == target:
        return True
    return target in _ALLOWED.get(current, set())


def transition(current: str, target: str) -> str:
    if not can_transition(current, target):
        raise TransitionError(f"illegal lifecycle transition: {current!r} -> {target!r}")
    return target


def is_failure(state: str) -> bool:
    return state in FAILURE_STATES
