"""lib/omes/py/jobs/states.py - a generic, dependency-free finite-state-
machine transition checker (issue #92).

Several OMES/Control Center records have a closed set of lifecycle states
and a closed set of allowed transitions between them (subscription/
entitlement state in issue #92; invoice state in issue #93 will reuse this
same module rather than re-implementing transition checking). Rather than
hard-coding the transition table in Python, the table is encoded as data
(see `contracts/control-center/v1/subscription.states.json`) and this
module only knows how to interpret that shape:

    {
      "states": [...],
      "initial_states": [...],   # optional
      "terminal_states": [...],  # optional
      "transitions": [{"from": "...", "to": "..."}, ...]
    }

This keeps the transition RULES reviewable as data (a PR that changes what
transitions are legal touches only the JSON file, not Python control flow)
while keeping the CHECKER itself generic and covered by one shared test
suite.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StateMachineError(Exception):
    """Raised for a malformed state table (a bug in the table itself)."""


class TransitionError(Exception):
    """Raised by `assert_transition()` when a transition is not allowed."""


class StateMachine:
    """A closed-world finite state machine loaded from a state table dict."""

    def __init__(self, table: dict[str, Any]):
        states = table.get("states")
        transitions = table.get("transitions")
        if not isinstance(states, list) or not states:
            raise StateMachineError("state table must have a non-empty 'states' list")
        if not isinstance(transitions, list):
            raise StateMachineError("state table must have a 'transitions' list")

        self.states: frozenset[str] = frozenset(states)
        self.initial_states: frozenset[str] = frozenset(table.get("initial_states", []))
        self.terminal_states: frozenset[str] = frozenset(table.get("terminal_states", []))

        for name, group in (("initial_states", self.initial_states), ("terminal_states", self.terminal_states)):
            unknown = group - self.states
            if unknown:
                raise StateMachineError(f"{name} references unknown state(s): {sorted(unknown)}")

        pairs: set[tuple[str, str]] = set()
        for edge in transitions:
            if not isinstance(edge, dict) or "from" not in edge or "to" not in edge:
                raise StateMachineError(f"malformed transition entry: {edge!r}")
            src, dst = edge["from"], edge["to"]
            if src not in self.states:
                raise StateMachineError(f"transition references unknown 'from' state: {src!r}")
            if dst not in self.states:
                raise StateMachineError(f"transition references unknown 'to' state: {dst!r}")
            pairs.add((src, dst))
        self._transitions: frozenset[tuple[str, str]] = frozenset(pairs)

    def is_valid_transition(self, from_state: str, to_state: str) -> bool:
        if from_state == to_state:
            # A no-op "transition" to the same state is always allowed
            # (this is what makes replaying an already-applied event safe -
            # see lib/omes/py/jobs/entitlement.py's idempotency handling).
            return from_state in self.states
        return (from_state, to_state) in self._transitions

    def assert_transition(self, from_state: str, to_state: str) -> None:
        if from_state not in self.states:
            raise TransitionError(f"unknown state: {from_state!r}")
        if to_state not in self.states:
            raise TransitionError(f"unknown state: {to_state!r}")
        if not self.is_valid_transition(from_state, to_state):
            raise TransitionError(f"transition not allowed: {from_state!r} -> {to_state!r}")

    def is_terminal(self, state: str) -> bool:
        return state in self.terminal_states

    def allowed_next_states(self, from_state: str) -> frozenset[str]:
        return frozenset(dst for (src, dst) in self._transitions if src == from_state)


def load_state_table(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_state_machine(path: Path) -> StateMachine:
    return StateMachine(load_state_table(path))
