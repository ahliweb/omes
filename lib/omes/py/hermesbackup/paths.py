"""hermesbackup.paths - state-dir/HERMES_HOME resolution.

Mirrors lib/omes/core.sh's omes_state_dir() so `omes agent-backup ...`
writes into the same state tree bash-side OMES uses (root scope:
/var/lib/omes; user scope: $XDG_STATE_HOME/omes or
~/.local/state/omes), honoring the same OMES_STATE_DIR override used by
tests and operators.
"""
from __future__ import annotations

import os
from pathlib import Path


def state_dir() -> Path:
    override = os.environ.get("OMES_STATE_DIR")
    if override:
        return Path(override).expanduser()
    if os.geteuid() == 0:
        return Path("/var/lib/omes")
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state_home).expanduser() if xdg_state_home else Path.home() / ".local" / "state"
    return base / "omes"


def backups_root() -> Path:
    return state_dir() / "backups" / "hermes"


def hermes_home() -> Path:
    override = os.environ.get("HERMES_HOME") or os.environ.get("OMES_HERMES_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hermes"
