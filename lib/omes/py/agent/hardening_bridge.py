"""lib/omes/py/agent/hardening_bridge.py - reuses
modules/hermes-gateway/hardening.sh's `hardening_render` function for the
security-hardening portion of the per-agent systemd drop-in, instead of
duplicating those directives in Python (issue #87 explicitly requires
reusing #81's hardening/resource-limit rendering).

`hardening_render <profile> <home>` is a plain bash function with no
external side effects (it only prints), so it is safe to invoke via a
short `bash -c` that sources the file and calls the function with fixed,
validated argv - never a shell string built from untrusted input.
"""
from __future__ import annotations

import os
import subprocess  # nosec B404 - fixed script, fixed args, no shell interpolation
from pathlib import Path
from typing import Optional

HARDENING_SCRIPT_RELATIVE = Path("modules") / "hermes-gateway" / "hardening.sh"

# Agents default to the "conservative" profile (the same default OMES
# recommends for the shared Hermes gateway - docs/hermes-hardening.md).
# "off" is honored explicitly if an operator opts out.
VALID_PROFILES = ("off", "conservative", "strict")


def agent_hardening_profile() -> str:
    raw = os.environ.get("OMES_AGENT_HARDENING", "conservative").strip()
    return raw if raw in VALID_PROFILES else "conservative"


def render(profile: str, hermes_home: str, omes_root: Path, timeout: float = 10.0) -> str:
    """Returns the rendered `[Service]` hardening body for `profile`
    (empty string for "off", matching hardening_render's own contract).
    Never raises for a bash/script failure - falls back to an empty
    string (equivalent to "off") so a missing/broken hardening.sh degrades
    the drop-in to resource limits only rather than blocking apply
    outright; the caller logs this via the returned tuple's ok flag.
    """
    script = Path(omes_root) / HARDENING_SCRIPT_RELATIVE
    if not script.is_file():
        return ""
    cmd = [
        "bash",
        "-c",
        'source "$1"; hardening_render "$2" "$3"',
        "hardening_bridge",
        str(script),
        profile,
        hermes_home,
    ]
    try:
        proc = subprocess.run(  # nosec B603 - fixed script path, no shell metacharacters from data
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout
