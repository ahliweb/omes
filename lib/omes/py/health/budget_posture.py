#!/usr/bin/env python3
"""lib/omes/py/health/budget_posture.py - `omes health budget` read-only
report of the CONFIGURED Hermes run-budget limits, plus an honest statement
of what is NOT observable (issue #275, ADR-0032 rule 8, epic #269).

Standard library only (ADR-0012). Budget POLICY belongs to AWCMS; budget
ENFORCEMENT belongs to Hermes (turn, run-time, loop and delegation limits,
provider limits) and to infrastructure (cgroup limits). OMES is not a second
LLM router and never meters model calls (ADR-0029). This module therefore
only REPORTS posture. It adds no metering, no enforcement, no policy engine
and no model routing, never reads Hermes files or databases, and never
touches prompts, transcripts or credentials.

What is observable through supported interfaces, read through the shared
allowlisted reader (`hermes config get <key> --json`, hermes_config.py;
https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/website/docs/reference/cli-commands.md):

- `agent.max_turns` and `agent.run_budget_seconds`. Upstream defaults to
  null (unlimited) for both. An explicit `null` is reported as
  `{"state": "value", "value": null}` (configured unlimited), which is a
  different fact from `absent` (Hermes says the key is not set; OMES does
  not hardcode what that means, so a required key that is absent makes the
  status `unknown`, exactly like `omes health agent-runtime`) and from
  `unknown` (could not be read or invalid).
- `agent.loop_caps.max_subagents` and `agent.loop_caps.max_web_searches`
  (per-turn caps) are reported as information; an unreadable one does not
  change the status.
- Delegation limits (concurrency, depth, iterations, child timeout) are
  already reported by `omes health agent-runtime` (#270) and are NOT read
  again here; the report only points to that command.

What is NOT observable, and therefore never reported as a number:

- Token usage, spend and cost. No Hermes configuration key defines a token,
  spend or cost ceiling; `hermes insights` has no JSON output; `hermes usage
  --json` reports provider rate-limit windows through a network call that
  uses the operator's provider credentials. None of those is run here. The
  report always carries `usage: {tokens: "unknown", cost: "unknown", source:
  "none", confidence: "none"}`. Unknown usage is never zero.
- Host resource limits (systemd `MemoryMax`, `CPUQuota`, `TasksMax`) are
  rendered by modules/hermes-gateway/hardening.sh and the agent plan, but
  reading them back needs `systemctl show` against a unit scope; no
  Python-side read-only reader exists in the repository, so the report
  states `host_limits: "not_collected"` instead of guessing.

Values are CONFIGURED ones, so the report carries `scope: "configured"`.
`ok` therefore means "the two run-limit keys were read and set to finite
values", never "spend is bounded".

Status: `ok`, `warn` (at least one warning finding) or `unknown` (a required
key was absent, unreadable or invalid). `ok` is never reported without
evidence for every required key.

Usage:
  python3 budget_posture.py [--profile-home PATH] [--json]

Exit codes (mirrors agent_runtime_posture.py): 0 status is ok or warn, 7
status is unknown.
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
    "Environment-variable overrides and runtime state are not observed. Token usage, spend and cost are not "
    "observable through any supported Hermes interface and are reported as unknown, never zero. Budget policy "
    "belongs to AWCMS; enforcement belongs to Hermes and infrastructure; OMES enforces no spend ceiling and "
    "never meters model calls (ADR-0029, ADR-0032 rule 8)."
)

KEY_MAX_TURNS = "agent.max_turns"
KEY_RUN_BUDGET_SECONDS = "agent.run_budget_seconds"
KEY_MAX_SUBAGENTS = "agent.loop_caps.max_subagents"
KEY_MAX_WEB_SEARCHES = "agent.loop_caps.max_web_searches"

_INT_CEILING = 2**31 - 1

# (key, nullable, required). `nullable` keys accept an explicit JSON null,
# which upstream treats as "unlimited". Only `required` keys can make the
# overall status unknown.
_SPECS = (
    (KEY_MAX_TURNS, True, True),
    (KEY_RUN_BUDGET_SECONDS, True, True),
    (KEY_MAX_SUBAGENTS, False, False),
    (KEY_MAX_WEB_SEARCHES, False, False),
)

#: Always-present usage statement. There is no supported interface for it.
USAGE_UNKNOWN = {"tokens": "unknown", "cost": "unknown", "source": "none", "confidence": "none"}

HOST_LIMITS_NOT_COLLECTED = "not_collected"

#: Where the delegation limits live; this check does not duplicate those reads.
RELATED_CHECKS = {"delegation_limits": "omes health agent-runtime"}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate(nullable: bool, value: Any) -> Optional[str]:
    """None when `value` is acceptable, else a bounded reason code. A
    positive integer is required; null is accepted only for nullable keys."""
    if value is None:
        return None if nullable else "invalid_type"
    if isinstance(value, bool) or not isinstance(value, int):
        return "invalid_type"
    if value < 1 or value > _INT_CEILING:
        return "out_of_range"
    return None


def _read_key(key: str, nullable: bool, hermes_home: Optional[str], timeout: Optional[float]) -> dict:
    read = hermes_config.config_get_json(key, hermes_home=hermes_home, timeout=timeout)
    if read.state == hermes_config.STATE_UNKNOWN:
        return {"state": "unknown", "reason": read.reason}
    if read.state == hermes_config.STATE_ABSENT:
        return {"state": "absent"}
    problem = _validate(nullable, read.value)
    if problem:
        # The offending value is deliberately not echoed back.
        return {"state": "unknown", "reason": problem}
    return {"state": "value", "value": read.value}


def _finding(finding_id: str, severity: str, message: str, key: Optional[str] = None, value: Any = None) -> dict:
    finding: dict = {"id": finding_id, "severity": severity, "message": message}
    if key is not None:
        finding["key"] = key
    if value is not None:
        finding["value"] = value
    return finding


def _is_configured_unlimited(entry: dict) -> bool:
    return entry.get("state") == "value" and entry.get("value") is None


def evaluate(keys: dict) -> dict:
    """Pure evaluator over per-key results (as built by `_read_key`).
    Unit-testable without Hermes."""
    findings: list = []
    unknown_reasons: list = []
    required_not_read = False

    for key, _nullable, required in _SPECS:
        entry = keys.get(key) or {"state": "unknown", "reason": "not_collected"}
        if entry["state"] == "value":
            continue
        if required:
            required_not_read = True
            unknown_reasons.append(entry.get("reason") or entry["state"])
        elif entry["state"] == "unknown":
            findings.append(_finding(
                "budget.optional_key_unavailable", SEV_INFO,
                f"optional setting {key} could not be read ({entry.get('reason')}); it does not affect the overall status",
                key,
            ))

    if _is_configured_unlimited(keys.get(KEY_MAX_TURNS) or {}):
        findings.append(_finding(
            "budget.unlimited_turns", SEV_WARN,
            "agent.max_turns is explicitly null (unlimited); a looping agent run is bounded only by "
            "other limits such as a run time budget, provider limits or host limits. Hermes enforces this "
            "limit; OMES only reports it",
            KEY_MAX_TURNS,
        ))
    if _is_configured_unlimited(keys.get(KEY_RUN_BUDGET_SECONDS) or {}):
        findings.append(_finding(
            "budget.no_run_time_budget", SEV_WARN,
            "agent.run_budget_seconds is explicitly null (no wall-clock budget per run); Hermes enforces "
            "this limit, OMES only reports it",
            KEY_RUN_BUDGET_SECONDS,
        ))

    for key in (KEY_MAX_SUBAGENTS, KEY_MAX_WEB_SEARCHES):
        entry = keys.get(key) or {}
        if entry.get("state") == "value":
            findings.append(_finding(
                "budget.loop_cap_configured", SEV_INFO,
                f"{key} is {entry['value']} (per-turn cap enforced by Hermes)",
                key, entry["value"],
            ))

    findings.append(_finding(
        "budget.token_spend_unobservable", SEV_INFO,
        "token usage, spend and cost are not observable through any supported Hermes interface (no config key "
        "defines a token or spend ceiling, `hermes insights` has no JSON output, and `hermes usage --json` "
        "reports provider rate-limit windows through a network call that uses provider credentials); they are "
        "reported as unknown, never zero. OMES enforces no spend ceiling and never meters model calls",
    ))
    findings.append(_finding(
        "budget.policy_authority_awcms", SEV_INFO,
        "budget policy (per tenant, project, user or worker class) belongs to AWCMS; enforcement belongs to "
        "Hermes (turn, time, loop and delegation limits, provider limits) and infrastructure (cgroup limits). "
        "OMES reports configured evidence only (ADR-0032 rule 8)",
    ))

    if required_not_read:
        status = STATUS_UNKNOWN
    elif any(f["severity"] == SEV_WARN for f in findings):
        status = STATUS_WARN
    else:
        status = STATUS_OK

    reason_codes = sorted({f["id"] for f in findings} | {f"budget.required_key_{r}" for r in unknown_reasons})
    return {"status": status, "findings": findings, "reason_codes": reason_codes}


def collect(hermes_home: Optional[str] = None, timeout: Optional[float] = None) -> dict:
    keys = {key: _read_key(key, nullable, hermes_home, timeout) for key, nullable, _req in _SPECS}
    result = evaluate(keys)
    return {
        "check": "budget_posture",
        "status": result["status"],
        "scope": SCOPE,
        "scope_note": SCOPE_NOTE,
        "source": "hermes config get --json",
        "profile_home_override": bool(hermes_home),
        "observed_at": _now_iso(),
        "keys": keys,
        "usage": dict(USAGE_UNKNOWN),
        "host_limits": HOST_LIMITS_NOT_COLLECTED,
        "related_checks": dict(RELATED_CHECKS),
        "findings": result["findings"],
        "reason_codes": result["reason_codes"],
    }


def run(argv: Optional[list] = None) -> dict:
    parser = argparse.ArgumentParser(prog="budget-posture")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--profile-home", default=None)
    args = parser.parse_args(argv)
    _ = args.json  # the bash caller decides human vs JSON rendering
    return collect(hermes_home=args.profile_home or None)


def main(argv: Optional[list] = None) -> int:
    result = run(argv)
    print(json.dumps(result))
    return EXIT_HEALTHY if result["status"] in (STATUS_OK, STATUS_WARN) else EXIT_NOT_HEALTHY


if __name__ == "__main__":
    sys.exit(main())
