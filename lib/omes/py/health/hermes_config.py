#!/usr/bin/env python3
"""lib/omes/py/health/hermes_config.py - the single, shared, read-only
`hermes config get <key> --json` reader for OMES posture checks (issue #270;
reused by #273 ACP posture and #275 budget evidence).

Standard library only (ADR-0012). This is the ONLY place an OMES posture
check may ask Hermes for a configuration value, so the safety properties
live here once:

- Supported interface only. The argv is always the fixed list
  `["hermes", "config", "get", <key>, "--json"]` (documented at
  https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/website/docs/reference/cli-commands.md).
  No shell, bounded timeout. `--raw` is NEVER passed: without it Hermes masks
  credential-shaped values, which is a safety net this module keeps.
- Never reads `$HERMES_HOME/config.yaml`, `.env`, `state.db`, `messages.db`
  or anything else under `.hermes/` (ADR-0017). `hermes_home` only selects
  the profile through the supported `HERMES_HOME` environment variable (the
  same mechanism modules/hermes-restricted/module.sh already uses).
- Closed allowlist (`ALLOWED_KEYS`) of NON-secret keys. Any other key, and
  any key whose name looks credential-ish (`SECRET_KEY_PATTERN`), is refused
  before a subprocess is ever started. To read a new key, add it to
  `ALLOWED_KEYS` in a reviewed change; there is no per-call override.
- Tri-state result. `absent` ("the key is confirmed unset") is a different
  fact from `unknown` ("it could not be read"), and the second must never
  be rendered as a healthy default. `unknown` always carries a bounded
  machine reason code from `UNKNOWN_REASONS`.

What this reads is the CONFIGURED value as resolved by `hermes config get`
(including Hermes built-in defaults). Environment-variable overrides that
Hermes may apply at runtime (for example `DELEGATION_MAX_CONCURRENT_CHILDREN`)
are not observable through this interface, so callers must label their
output `scope: "configured"`, never "effective".
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from typing import Any, Optional

_HEALTH_DIR = os.path.dirname(os.path.abspath(__file__))
_PY_ROOT = os.path.dirname(_HEALTH_DIR)  # lib/omes/py
if _PY_ROOT not in sys.path:
    sys.path.insert(0, _PY_ROOT)
# Reuse the one existing bounded, no-shell subprocess runner rather than
# adding a third copy (ai_privacy.py and versions.py already carry one).
from provenance.versions import _run  # noqa: E402

STATE_VALUE = "value"
STATE_ABSENT = "absent"
STATE_UNKNOWN = "unknown"

#: Keys OMES posture checks may read. All are non-secret numeric, boolean or
#: short-identifier settings. `delegation.api_key` and `delegation.base_url`
#: exist upstream and are deliberately NOT listed: they can hold credentials
#: or private endpoints.
ALLOWED_KEYS = frozenset({
    "delegation.max_concurrent_children",
    "delegation.max_spawn_depth",
    "delegation.max_iterations",
    "delegation.child_timeout_seconds",
    "delegation.model",
    "delegation.provider",
    "delegation.orchestrator_enabled",
    "delegation.worktree_isolation",
    "delegation.subagent_auto_approve",
    "delegation.oneshot_max_children",
    # ACP tool-surface posture (issue #273): names of toolsets only.
    "platform_toolsets.acp",
    "agent.disabled_toolsets",
    # Run-budget posture (issue #275): numeric limits (null = unlimited) only.
    # There is deliberately no token, spend or cost key: none exists upstream.
    "agent.max_turns",
    "agent.run_budget_seconds",
    "agent.loop_caps.max_subagents",
    "agent.loop_caps.max_web_searches",
})

#: Defence in depth on top of the allowlist: a key whose name looks like it
#: could hold a credential or private endpoint is refused even if someone
#: later adds it to ALLOWED_KEYS by mistake (a unit test asserts no allowlisted
#: key matches).
SECRET_KEY_PATTERN = re.compile(
    r"api[_-]?key|token|secret|passw(or)?d|credential|private[_-]?key|base[_-]?url|auth|bearer|cookie|session[_-]?id",
    re.IGNORECASE,
)

_KEY_SHAPE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")

#: Bounded vocabulary for ConfigRead.reason when state is "unknown".
UNKNOWN_REASONS = frozenset({
    "key_not_allowlisted",
    "key_secret_pattern",
    "binary_missing",
    "timeout",
    "exec_error",
    "nonzero_exit",
    "unsupported_flag",
    "empty_output",
    "output_too_large",
    "json_unparseable",
})

_ABSENT_PREFIX = "Config key not set"
_MAX_OUTPUT_BYTES = 65536
_DEFAULT_TIMEOUT = 10.0
_MAX_TIMEOUT = 120.0


@dataclass(frozen=True)
class ConfigRead:
    """Result of one allowlisted `hermes config get <key> --json` read.

    `state` is "value" (rc 0 and valid JSON; `value` holds the parsed JSON
    value, which may legitimately be false, 0, "" or null), "absent" (Hermes
    says the key is not set and has no default) or "unknown" (anything else;
    `reason` is a member of UNKNOWN_REASONS)."""

    key: str
    state: str
    value: Any = None
    reason: Optional[str] = None

    @property
    def is_value(self) -> bool:
        return self.state == STATE_VALUE


def default_timeout() -> float:
    """Per-call timeout in seconds: OMES_HEALTH_TIMEOUT (the convention used
    by the other health probes), default 10, clamped to (0, 120]; an
    unparseable or non-positive value falls back to the default."""
    raw = os.environ.get("OMES_HEALTH_TIMEOUT", "").strip()
    try:
        value = float(raw) if raw else _DEFAULT_TIMEOUT
    except ValueError:
        return _DEFAULT_TIMEOUT
    if value != value or value <= 0:  # NaN or non-positive
        return _DEFAULT_TIMEOUT
    return min(value, _MAX_TIMEOUT)


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _unknown(key: str, reason: str) -> ConfigRead:
    return ConfigRead(key=key, state=STATE_UNKNOWN, reason=reason)


def build_argv(key: str) -> list:
    """The one argv this module ever executes. Exposed so tests can assert
    that `--raw` is never present."""
    return ["hermes", "config", "get", key, "--json"]


def config_get_json(key: str, *, hermes_home: Optional[str] = None, timeout: Optional[float] = None) -> ConfigRead:
    """Reads one allowlisted, non-secret Hermes config key. Never raises for
    an unreadable config: every failure is an `unknown` ConfigRead with a
    bounded reason code.

    `hermes_home`, when given, selects the Hermes profile via the
    supported HERMES_HOME environment variable; None leaves the caller's
    environment untouched."""
    if not isinstance(key, str) or not _KEY_SHAPE.match(key):
        return _unknown(str(key)[:64] if isinstance(key, str) else "", "key_not_allowlisted")
    if SECRET_KEY_PATTERN.search(key):
        return _unknown(key, "key_secret_pattern")
    if key not in ALLOWED_KEYS:
        return _unknown(key, "key_not_allowlisted")

    if shutil.which("hermes") is None:
        return _unknown(key, "binary_missing")

    env = None
    if hermes_home:
        env = dict(os.environ)
        env["HERMES_HOME"] = str(hermes_home)

    effective_timeout = default_timeout() if timeout is None else timeout
    rc, out, err = _run(build_argv(key), effective_timeout, env=env)

    if rc == -2:
        return _unknown(key, "timeout")
    if rc == -1:
        return _unknown(key, "exec_error")

    if rc == 1 and _first_line(err).startswith(_ABSENT_PREFIX):
        return ConfigRead(key=key, state=STATE_ABSENT)
    if rc != 0:
        lowered = (err or "").lower()
        if "unrecognized arguments" in lowered or "no such option" in lowered or "unknown option" in lowered:
            return _unknown(key, "unsupported_flag")
        return _unknown(key, "nonzero_exit")

    if len(out.encode("utf-8", "replace")) > _MAX_OUTPUT_BYTES:
        return _unknown(key, "output_too_large")
    if not out.strip():
        return _unknown(key, "empty_output")
    try:
        return ConfigRead(key=key, state=STATE_VALUE, value=json.loads(out))
    except ValueError:
        return _unknown(key, "json_unparseable")
