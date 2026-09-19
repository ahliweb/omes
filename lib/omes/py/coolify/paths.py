"""lib/omes/py/coolify/paths.py - path/layout resolution for the Coolify
adapter's local state (issue #97).

Mirrors lib/omes/py/jobs/paths.py's `state_root()` (and, in turn,
lib/omes/core.sh's `omes_state_dir()`) rather than importing it, per the
no-cross-package-import convention documented in
lib/omes/py/jobs/audit.py. Stdlib only.
"""
from __future__ import annotations

import os
from pathlib import Path


def state_root(override: Path | None = None) -> Path:
    """Resolves the OMES state directory, honoring OMES_STATE_DIR exactly
    like lib/omes/core.sh's omes_state_dir() and lib/omes/py/jobs/paths.py."""
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


def coolify_root(root: Path | None = None) -> Path:
    return state_root(root) / "coolify"


def instances_path(root: Path | None = None) -> Path:
    """Registered Coolify instances: instance_id, base_url, credential_ref
    (store+key only - never a token) per contracts/coolify/v1/
    instance-registration.request.schema.json. Never holds a raw secret."""
    return coolify_root(root) / "instances.json"


def mappings_dir(root: Path | None = None) -> Path:
    return coolify_root(root) / "mappings"


def mapping_path(deployment_id: str, root: Path | None = None) -> Path:
    return mappings_dir(root) / f"{deployment_id}.json"


def observed_dir(root: Path | None = None) -> Path:
    return coolify_root(root) / "observed"


def observed_path(deployment_id: str, root: Path | None = None) -> Path:
    return observed_dir(root) / f"{deployment_id}.json"


def audit_log_path(root: Path | None = None) -> Path:
    return coolify_root(root) / "audit.jsonl"


def ensure_layout(root: Path | None = None) -> Path:
    """Creates <state-dir>/coolify/{,mappings,observed} mode 0700."""
    resolved = coolify_root(root)
    resolved.mkdir(parents=True, exist_ok=True)
    os.chmod(resolved, 0o700)
    mappings_dir(root).mkdir(parents=True, exist_ok=True)
    os.chmod(mappings_dir(root), 0o700)
    observed_dir(root).mkdir(parents=True, exist_ok=True)
    os.chmod(observed_dir(root), 0o700)
    return resolved
