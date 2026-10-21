#!/usr/bin/env python3
"""lib/omes/py/health/acp_posture.py - `omes health acp` read-only report of
the CONFIGURED tool surface of Hermes' inbound ACP server (issue #273,
ADR-0032 rule 6, epic #269).

Standard library only (ADR-0012). `hermes acp` is an on-demand stdio
JSON-RPC server that an editor or a bridge launches
(https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/website/docs/user-guide/features/acp.md).
Hermes owns it (inbound ACP is DELEGATE; outbound ACP is DEFER to
https://github.com/NousResearch/hermes-agent/issues/5257). OMES is not an ACP
server, client or proxy and ACP traffic never reaches OMES host mutation.
This module only REPORTS posture; it never starts, stops or configures
anything and never reads Hermes files or databases.

What is observable through supported interfaces, and what is not:

- `platform_toolsets.acp` and `agent.disabled_toolsets` through the shared
  allowlisted reader (`hermes config get <key> --json`, hermes_config.py).
  When `platform_toolsets.acp` is absent, upstream applies the curated
  `hermes-acp` toolset, which includes terminal and execute_code. An
  explicit list (even an empty one) can still gain enabled plugin toolsets.
- `hermes acp --version` is run once with a fixed argv and a bounded timeout
  as an INSTALLABILITY hint only (exit 0 => `installed`). Its text is never
  parsed and it says nothing about exposure. `hermes acp --check` is NOT
  used: its output format is undocumented.
- Whether an ACP session is live, or reachable through a bridge, is NOT
  observable through any supported interface. The report always states
  `session_exposure: unknown` and never claims otherwise. There is no
  daemon and no config key that enables or disables ACP.

Values are CONFIGURED ones, so the report carries `scope: "configured"`.

Status: `ok`, `warn` (at least one warning finding) or `unknown` (a key could
not be read or was invalid). `ok` is never reported when a key is unknown.

Usage:
  python3 acp_posture.py [--profile-home PATH] [--json]

Exit codes (mirrors agent_runtime_posture.py): 0 status is ok or warn, 7
status is unknown.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from typing import Any, Optional

_HEALTH_DIR = os.path.dirname(os.path.abspath(__file__))
if _HEALTH_DIR not in sys.path:
    sys.path.insert(0, _HEALTH_DIR)
import hermes_config  # noqa: E402  (also puts lib/omes/py on sys.path)
from provenance.versions import _run  # noqa: E402

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
    "Whether an ACP session is live or reachable through an editor or bridge is not observable through any supported "
    "Hermes interface and is not reported. OMES reports this posture only; Hermes owns ACP and OMES is not an ACP "
    "server, client or proxy (ADR-0032 rule 6)."
)

KEY_ACP_TOOLSETS = "platform_toolsets.acp"
KEY_DISABLED_TOOLSETS = "agent.disabled_toolsets"

#: Fixed argv of the one extra command this module runs. Installability only.
ACP_PROBE_ARGV = ["hermes", "acp", "--version"]

PROBE_INSTALLED = "installed"
PROBE_NOT_INSTALLED_OR_UNKNOWN = "not_installed_or_unknown"

_LIST_MAX_ITEMS = 64
_ITEM_MAX_LEN = 64
_ITEM_SHAPE = re.compile(r"^[A-Za-z0-9_.:-]+$")

# Execution-capable toolset families. `hermes-acp` is the curated composite
# default toolset, which includes both families.
_FAMILY_TERMINAL = "terminal"
_FAMILY_CODE = "code_execution"
_TOOLSET_FAMILIES = {
    "terminal": {_FAMILY_TERMINAL},
    "execute_code": {_FAMILY_CODE},
    "code_execution": {_FAMILY_CODE},
    "hermes_acp": {_FAMILY_TERMINAL, _FAMILY_CODE},
}
_ALL_FAMILIES = frozenset({_FAMILY_TERMINAL, _FAMILY_CODE})


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalise(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def _families(names: list) -> set:
    found: set = set()
    for name in names:
        found |= _TOOLSET_FAMILIES.get(_normalise(name), set())
    return found


def _validate_toolset_list(value: Any) -> Optional[str]:
    """None when `value` is a bounded list of short toolset-name strings,
    else a bounded reason code. The offending value is never echoed."""
    if not isinstance(value, list):
        return "invalid_type"
    if len(value) > _LIST_MAX_ITEMS:
        return "out_of_range"
    for item in value:
        if not isinstance(item, str):
            return "invalid_type"
        if len(item) > _ITEM_MAX_LEN or not _ITEM_SHAPE.match(item):
            return "out_of_range"
    return None


def _read_key(key: str, hermes_home: Optional[str], timeout: Optional[float]) -> dict:
    read = hermes_config.config_get_json(key, hermes_home=hermes_home, timeout=timeout)
    if read.state == hermes_config.STATE_UNKNOWN:
        return {"state": "unknown", "reason": read.reason}
    if read.state == hermes_config.STATE_ABSENT:
        return {"state": "absent"}
    problem = _validate_toolset_list(read.value)
    if problem:
        return {"state": "unknown", "reason": problem}
    return {"state": "value", "value": list(read.value)}


def probe_installability(hermes_home: Optional[str] = None, timeout: Optional[float] = None) -> dict:
    """Runs `hermes acp --version` (fixed argv, bounded timeout, no shell).
    Exit code 0 => `installed`; anything else (missing binary, timeout,
    nonzero exit such as a missing `acp` extra) => `not_installed_or_unknown`.
    Output is never parsed and the result never implies exposure."""
    env = None
    if hermes_home:
        env = dict(os.environ)
        env["HERMES_HOME"] = str(hermes_home)
    effective = hermes_config.default_timeout() if timeout is None else timeout
    rc, _out, _err = _run(list(ACP_PROBE_ARGV), effective, env=env)
    state = PROBE_INSTALLED if rc == 0 else PROBE_NOT_INSTALLED_OR_UNKNOWN
    return {
        "state": state,
        "meaning": "installability only (optional `acp` extra); not evidence that ACP is running or exposed",
    }


def _finding(finding_id: str, severity: str, message: str, key: Optional[str] = None, value: Any = None) -> dict:
    finding: dict = {"id": finding_id, "severity": severity, "message": message}
    if key is not None:
        finding["key"] = key
    if value is not None:
        finding["value"] = value
    return finding


def evaluate(keys: dict) -> dict:
    """Pure evaluator over per-key results (as built by `_read_key`).
    Unit-testable without Hermes."""
    findings: list = []
    unknown_reasons: list = []
    not_read = False

    for key in (KEY_ACP_TOOLSETS, KEY_DISABLED_TOOLSETS):
        entry = keys.get(key) or {"state": "unknown", "reason": "not_collected"}
        if entry["state"] == "unknown":
            not_read = True
            unknown_reasons.append(entry.get("reason") or "unknown")

    acp = keys.get(KEY_ACP_TOOLSETS) or {}
    disabled = keys.get(KEY_DISABLED_TOOLSETS) or {}
    disabled_names = disabled.get("value", []) if disabled.get("state") == "value" else []
    disabled_families = _families(disabled_names)

    if not not_read:
        if acp.get("state") == "absent":
            exposed = _ALL_FAMILIES - disabled_families
            source = "the curated `hermes-acp` default toolset applies because platform_toolsets.acp is not set"
        else:
            configured = acp.get("value", [])
            exposed = _families(configured) - disabled_families
            source = f"platform_toolsets.acp lists execution-capable toolsets: {sorted(_families(configured))}"
        if exposed:
            findings.append(_finding(
                "acp.default_toolset_includes_execution", SEV_WARN,
                f"ACP sessions can use {sorted(exposed)} ({source}); a client or bridge that auto-approves "
                "tool prompts can then run commands as the Hermes user. Restrict `platform_toolsets.acp` "
                "(enabled plugin toolsets are still added to an explicit list) or add the toolset to "
                "`agent.disabled_toolsets`",
                KEY_ACP_TOOLSETS, sorted(exposed),
            ))
        else:
            findings.append(_finding(
                "acp.toolset_restricted", SEV_INFO,
                "platform_toolsets.acp is explicitly set (or execution toolsets are disabled) without "
                "terminal or code-execution toolsets; enabled plugin toolsets are still added upstream, so "
                "this is configured intent, not proof of the runtime tool surface",
                KEY_ACP_TOOLSETS,
            ))

    findings.append(_finding(
        "acp.session_exposure_unobservable", SEV_INFO,
        "whether an ACP session is live, or exposed through an editor or bridge (for example one that "
        "auto-approves `allow_once`), is not observable through any supported Hermes interface; there is no "
        "ACP daemon and no config key that enables or disables it",
    ))
    findings.append(_finding(
        "acp.outbound_not_supported_upstream", SEV_INFO,
        "outbound ACP (Hermes acting as an ACP client of other agents) is not shipped upstream and is deferred "
        "to https://github.com/NousResearch/hermes-agent/issues/5257 (ADR-0032 rule 6); OMES adds no ACP client",
    ))

    if not_read:
        status = STATUS_UNKNOWN
    elif any(f["severity"] == SEV_WARN for f in findings):
        status = STATUS_WARN
    else:
        status = STATUS_OK

    reason_codes = sorted({f["id"] for f in findings} | {f"acp.key_{r}" for r in unknown_reasons})
    return {"status": status, "findings": findings, "reason_codes": reason_codes}


def collect(hermes_home: Optional[str] = None, timeout: Optional[float] = None, probe: bool = True) -> dict:
    keys = {key: _read_key(key, hermes_home, timeout) for key in (KEY_ACP_TOOLSETS, KEY_DISABLED_TOOLSETS)}
    result = evaluate(keys)
    return {
        "check": "acp_posture",
        "status": result["status"],
        "scope": SCOPE,
        "scope_note": SCOPE_NOTE,
        "source": "hermes config get --json",
        "profile_home_override": bool(hermes_home),
        "observed_at": _now_iso(),
        "keys": keys,
        "installability": probe_installability(hermes_home, timeout) if probe else {"state": "not_probed"},
        "session_exposure": "unknown",
        "findings": result["findings"],
        "reason_codes": result["reason_codes"],
    }


def run(argv: Optional[list] = None) -> dict:
    parser = argparse.ArgumentParser(prog="acp-posture")
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
