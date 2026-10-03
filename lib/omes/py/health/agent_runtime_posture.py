#!/usr/bin/env python3
"""lib/omes/py/health/agent_runtime_posture.py - `omes health agent-runtime`
read-only report of the CONFIGURED Hermes delegation limits (issue #270,
ADR-0032 rule 2, epic #269).

Standard library only (ADR-0012). Hermes owns delegation (`delegate_task`,
concurrency, depth, iteration and timeout limits) and OMES must not become a
coordinator, scheduler or model router (ADR-0017, ADR-0032, registry rule
R9). This module therefore only REPORTS posture: it asks Hermes for a fixed
set of non-secret `delegation.*` keys through the shared reader in
lib/omes/py/health/hermes_config.py (`hermes config get <key> --json`),
validates type and range, and turns the values into findings. It never
enforces, changes or routes anything, never reads Hermes files or databases,
and never touches prompts, transcripts or credentials.

Honest scope: values are the CONFIGURED ones as resolved by Hermes
(including its built-in defaults). Environment overrides such as
`DELEGATION_MAX_CONCURRENT_CHILDREN` may change the runtime value and are
not observable here, so the report carries `scope: "configured"` and a note
saying so. Upstream documentation and code disagree on some defaults, so
nothing here hardcodes a default as truth; only live values are reported.

Status: `ok` (every required key was read and no warning finding), `warn`
(every required key was read and at least one warning finding) or `unknown`
(at least one required key was absent, unreadable or invalid). `ok` is never
reported without evidence for every required key.

Usage:
  python3 agent_runtime_posture.py [--profile-home PATH] [--json]

Exit codes (mirrors ai_privacy.py): 0 status is ok or warn, 7 status is
unknown.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from typing import Any, Optional

_HEALTH_DIR = os.path.dirname(os.path.abspath(__file__))
if _HEALTH_DIR not in sys.path:
    sys.path.insert(0, _HEALTH_DIR)
import hermes_config  # noqa: E402

EXIT_HEALTHY = 0
EXIT_NOT_HEALTHY = 7

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_UNKNOWN = "unknown"

SEV_INFO = "info"
SEV_WARN = "warn"

SCOPE = "configured"
SCOPE_NOTE = (
    "Values are the configured settings as resolved by `hermes config get` (including Hermes built-in defaults). "
    "Environment-variable overrides (for example DELEGATION_MAX_CONCURRENT_CHILDREN) and runtime state are not observed. "
    "OMES reports this posture only; Hermes owns delegation and OMES does not route models (ADR-0032 rule 2)."
)

#: OMES posture thresholds. These are reporting thresholds, not claims about
#: Hermes defaults. 3 is the maximum spawn depth the upstream delegation
#: documentation describes (the code itself has no ceiling).
DOCUMENTED_MAX_SPAWN_DEPTH = 3

_INT_CEILING = 2**31 - 1
_STR_MAX = 128

# (key, kind, minimum, required). `required` keys are the limits the findings
# depend on; an optional key that is unreadable is recorded but does not make
# the overall status unknown.
_SPECS = (
    ("delegation.max_concurrent_children", "int", 1, True),
    ("delegation.max_spawn_depth", "int", 1, True),
    ("delegation.max_iterations", "int", 1, True),
    ("delegation.child_timeout_seconds", "int", 0, True),
    ("delegation.subagent_auto_approve", "bool", None, True),
    ("delegation.model", "str", None, False),
    ("delegation.provider", "str", None, False),
    ("delegation.orchestrator_enabled", "bool", None, False),
    ("delegation.worktree_isolation", "bool", None, False),
    ("delegation.oneshot_max_children", "int", 1, False),
)

#: Keys for which absence is a normal, interpretable state (upstream treats
#: an absent key as false), reported with an explicit inferred interpretation.
_INFERRED_ABSENT = {
    "delegation.worktree_isolation": "default false (inferred)",
}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate(kind: str, minimum: Optional[int], value: Any) -> Optional[str]:
    """Returns None when `value` is acceptable, else a bounded reason code."""
    if kind == "bool":
        return None if isinstance(value, bool) else "invalid_type"
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return "invalid_type"
        if value < (minimum or 0) or value > _INT_CEILING:
            return "out_of_range"
        return None
    if kind == "str":
        if not isinstance(value, str):
            return "invalid_type"
        if len(value) > _STR_MAX or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            return "out_of_range"
        return None
    return "invalid_type"


def _read_key(key: str, kind: str, minimum: Optional[int], hermes_home: Optional[str], timeout: Optional[float]) -> dict:
    read = hermes_config.config_get_json(key, hermes_home=hermes_home, timeout=timeout)
    if read.state == hermes_config.STATE_UNKNOWN:
        return {"state": "unknown", "reason": read.reason}
    if read.state == hermes_config.STATE_ABSENT:
        entry = {"state": "absent"}
        if key in _INFERRED_ABSENT:
            entry["interpretation"] = _INFERRED_ABSENT[key]
        return entry
    problem = _validate(kind, minimum, read.value)
    if problem:
        # The offending value is deliberately not echoed back.
        return {"state": "unknown", "reason": problem}
    return {"state": "value", "value": read.value}


def _finding(finding_id: str, severity: str, key: str, message: str, value: Any = None) -> dict:
    finding = {"id": finding_id, "severity": severity, "key": key, "message": message}
    if value is not None:
        finding["value"] = value
    return finding


def evaluate(keys: dict) -> dict:
    """Pure evaluator: turns per-key results (as built by `_read_key`) into
    findings, reason codes and the overall status. Unit-testable without
    Hermes."""
    findings: list = []
    unknown_reasons: list = []
    required_not_read = False

    for key, _kind, _minimum, required in _SPECS:
        entry = keys.get(key) or {"state": "unknown", "reason": "not_collected"}
        if entry["state"] != "value":
            if required:
                required_not_read = True
                reason = entry.get("reason") or entry["state"]
                unknown_reasons.append(reason)
            elif entry["state"] == "unknown":
                # An absent optional key is already visible in `keys` (and
                # some are legitimately unset); only an unreadable one is a finding.
                findings.append(_finding(
                    "delegation.optional_key_unavailable", SEV_INFO, key,
                    f"optional setting {key} could not be read ({entry.get('reason')}); "
                    "it does not affect the overall status",
                ))

    def val(key: str) -> Any:
        entry = keys.get(key) or {}
        return entry.get("value") if entry.get("state") == "value" else None

    timeout_s = val("delegation.child_timeout_seconds")
    if timeout_s == 0:
        findings.append(_finding(
            "delegation.no_child_timeout", SEV_WARN, "delegation.child_timeout_seconds",
            "no per-child timeout is configured (0 means no timeout); a stuck child can run until max_iterations", 0,
        ))

    depth = val("delegation.max_spawn_depth")
    if isinstance(depth, int) and depth > 1:
        if depth > DOCUMENTED_MAX_SPAWN_DEPTH:
            findings.append(_finding(
                "delegation.spawn_depth_above_documented", SEV_WARN, "delegation.max_spawn_depth",
                f"max_spawn_depth {depth} exceeds the {DOCUMENTED_MAX_SPAWN_DEPTH} that the upstream documentation describes "
                "(the upstream code has no ceiling); nested orchestration multiplies fan-out", depth,
            ))
        else:
            findings.append(_finding(
                "delegation.nested_orchestration", SEV_INFO, "delegation.max_spawn_depth",
                f"max_spawn_depth {depth} allows nested orchestrators (flat delegation is 1)", depth,
            ))

    if val("delegation.subagent_auto_approve") is True:
        findings.append(_finding(
            "delegation.subagent_auto_approve_enabled", SEV_WARN, "delegation.subagent_auto_approve",
            "subagent_auto_approve is true; delegated children can proceed without the parent's approval prompts", True,
        ))

    model_entry = keys.get("delegation.model") or {}
    if (model_entry.get("state") == "value" and model_entry.get("value") == "") or model_entry.get("state") == "absent":
        findings.append(_finding(
            "delegation.model_not_pinned", SEV_INFO, "delegation.model",
            "no delegation model is pinned, so children inherit the parent model; per-task model tiering "
            "is unsupported upstream (ADR-0032 rule 2) - use separate Hermes profiles per worker tier",
        ))

    if required_not_read:
        status = STATUS_UNKNOWN
    elif any(f["severity"] == SEV_WARN for f in findings):
        status = STATUS_WARN
    else:
        status = STATUS_OK

    reason_codes = sorted({f["id"] for f in findings} | {f"delegation.required_key_{r}" for r in unknown_reasons})
    return {"status": status, "findings": findings, "reason_codes": reason_codes}


def collect(hermes_home: Optional[str] = None, timeout: Optional[float] = None) -> dict:
    keys = {key: _read_key(key, kind, minimum, hermes_home, timeout) for key, kind, minimum, _req in _SPECS}
    result = evaluate(keys)
    return {
        "check": "delegation_limits",
        "status": result["status"],
        "scope": SCOPE,
        "scope_note": SCOPE_NOTE,
        "source": "hermes config get --json",
        "profile_home_override": bool(hermes_home),
        "observed_at": _now_iso(),
        "keys": keys,
        "findings": result["findings"],
        "reason_codes": result["reason_codes"],
    }


def run(argv: Optional[list] = None) -> dict:
    parser = argparse.ArgumentParser(prog="agent-runtime-posture")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--profile-home", default=None)
    args = parser.parse_args(argv)
    _ = args.json  # the bash caller decides human vs JSON rendering; parsed for --help symmetry with the other health targets
    return collect(hermes_home=args.profile_home or None)


def main(argv: Optional[list] = None) -> int:
    result = run(argv)
    print(json.dumps(result))
    return EXIT_HEALTHY if result["status"] in (STATUS_OK, STATUS_WARN) else EXIT_NOT_HEALTHY


if __name__ == "__main__":
    sys.exit(main())
