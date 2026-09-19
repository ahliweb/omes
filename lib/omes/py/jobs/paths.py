"""lib/omes/py/jobs/paths.py - path/layout resolution for the OMES control
job store (issue #90).

Mirrors lib/omes/core.sh's `omes_state_dir()` (root -> /var/lib/omes,
user -> ${XDG_STATE_HOME:-$HOME/.local/state}/omes, both overridable by
OMES_STATE_DIR) rather than importing bash, per ADR-0012 (Python is
stdlib-only and does not shell out to read its own configuration).
Stdlib only.
"""
from __future__ import annotations

import os
from pathlib import Path


def state_root(override: Path | None = None) -> Path:
    """Resolves the OMES state directory, honoring OMES_STATE_DIR exactly
    like lib/omes/core.sh's omes_state_dir()."""
    if override is not None:
        return override
    env_override = os.environ.get("OMES_STATE_DIR")
    if env_override:
        return Path(env_override).expanduser()
    if os.geteuid() == 0:
        return Path("/var/lib/omes")
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state_home).expanduser() if xdg_state_home else Path.home() / ".local" / "state"
    return base / "omes"


def jobs_root(root: Path | None = None) -> Path:
    return state_root(root) / "jobs"


def job_record_path(job_id: str, root: Path | None = None) -> Path:
    return jobs_root(root) / f"{job_id}.json"


def audit_log_path(root: Path | None = None) -> Path:
    return jobs_root(root) / "audit.jsonl"


def idempotency_index_path(root: Path | None = None) -> Path:
    return jobs_root(root) / "idempotency.json"


def ensure_layout(root: Path | None = None) -> Path:
    """Creates <state-dir>/jobs/ mode 0700. Does not create the parent
    state directory's other subdirectories (backups/, state file) - those
    are owned by lib/omes/state.sh, not this package."""
    resolved = jobs_root(root)
    resolved.mkdir(parents=True, exist_ok=True)
    os.chmod(resolved, 0o700)
    return resolved
