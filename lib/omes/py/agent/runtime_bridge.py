"""lib/omes/py/agent/runtime_bridge.py - reuses
lib/omes/runtime.sh's `runtime_agent_service_unit <name> <scope>` as the
one source of truth for the per-agent systemd unit name
("omes-agent-<name>.service"), instead of independently re-deriving that
prefix/suffix in Python (issue #85/#87/#96 follow-up: "lib/omes/runtime.sh
integration" - see docs/agent-deployment.md section 7a and
docs/agent-runtime-boundary.md).

Same bridge pattern lib/omes/py/agent/hardening_bridge.py already uses for
modules/hermes-gateway/hardening.sh's `hardening_render`: a short
`bash -c` that sources the small set of libraries `runtime.sh` depends on
and calls the function with fixed, already-validated, argv-passed
arguments - never a shell string built from untrusted input, never a
secret.
"""
from __future__ import annotations

import subprocess  # nosec B404 - fixed script paths, fixed args, no shell interpolation
from pathlib import Path
from typing import Optional

# runtime.sh only needs core.sh (omes_die, OMES_EX_USAGE) sourced first;
# json.sh/log.sh are not required for runtime_agent_service_unit
# specifically, but are included for parity with how bin/omes always
# sources them together, in case a future edit to runtime.sh grows a
# dependency on them.
_RELATIVE_LIBS = (
    Path("lib") / "omes" / "core.sh",
    Path("lib") / "omes" / "log.sh",
    Path("lib") / "omes" / "json.sh",
    Path("lib") / "omes" / "runtime.sh",
)

_BRIDGE_SCRIPT = (
    'source "$1"; source "$2" 2>/dev/null || true; source "$3" 2>/dev/null || true; '
    'source "$4"; runtime_agent_service_unit "$5" "$6"'
)


def agent_service_unit(name: str, scope: str, omes_root: Path, timeout: float = 5.0) -> Optional[str]:
    """Returns the unit name runtime.sh's `runtime_agent_service_unit`
    prints for <name>/<scope>, or None if the bridge could not run (e.g.
    runtime.sh is missing, or bash failed) so callers can fall back to
    their own known-equivalent default rather than raising - this bridge
    is a "read from the one source of truth" convenience, not a hard
    dependency that should ever block `omes agent plan`/`apply`."""
    paths = [Path(omes_root) / rel for rel in _RELATIVE_LIBS]
    if not paths[-1].is_file() or not paths[0].is_file():
        return None
    cmd = ["bash", "-c", _BRIDGE_SCRIPT, "runtime_bridge", *[str(p) for p in paths], name, scope]
    try:
        proc = subprocess.run(  # nosec B603 - fixed script paths, no shell metacharacters from data
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return out or None
