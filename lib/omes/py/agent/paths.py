"""lib/omes/py/agent/paths.py - config/state/HERMES_HOME resolution for
`omes agent ...` (issue #87).

Mirrors lib/omes/core.sh's omes_state_dir() (root scope: /var/lib/omes;
user scope: $XDG_STATE_HOME/omes or ~/.local/state/omes, overridable with
OMES_STATE_DIR) the same way hermesbackup/paths.py does, and introduces
the analogous OMES_CONFIG_DIR convention for manifests: root scope
/etc/omes; user scope $XDG_CONFIG_HOME/omes or ~/.config/omes. Both are
documented in docs/configuration.md.
"""
from __future__ import annotations

import os
from pathlib import Path


def _is_root() -> bool:
    if os.environ.get("OMES_TEST") == "1" and os.environ.get("OMES_FAKE_ROOT") == "1":
        return True
    try:
        return os.geteuid() == 0
    except AttributeError:  # pragma: no cover - non-POSIX platforms
        return False


def config_dir() -> Path:
    override = os.environ.get("OMES_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if _is_root():
        return Path("/etc/omes")
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home).expanduser() if xdg_config_home else Path.home() / ".config"
    return base / "omes"


def state_dir() -> Path:
    override = os.environ.get("OMES_STATE_DIR")
    if override:
        return Path(override).expanduser()
    if _is_root():
        return Path("/var/lib/omes")
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state_home).expanduser() if xdg_state_home else Path.home() / ".local" / "state"
    return base / "omes"


def agents_config_dir() -> Path:
    return config_dir() / "agents"


def manifest_path(name: str) -> Path:
    return agents_config_dir() / f"{name}.json"


def agents_state_dir() -> Path:
    return state_dir() / "agents"


def agent_state_dir(name: str) -> Path:
    return agents_state_dir() / name


def agent_state_file(name: str) -> Path:
    return agent_state_dir(name) / "state.json"


def base_home(service_mode: str) -> Path:
    """The parent directory agent HERMES_HOME instances are isolated
    under, per serviceMode: the invoking user's home for `user`, or a
    fixed system-owned tree for `system` (never a shared HERMES_HOME -
    docs/agent-orchestration-roadmap.md section 6)."""
    if service_mode == "system":
        override = os.environ.get("OMES_AGENT_SYSTEM_HOME_ROOT")
        return Path(override).expanduser() if override else Path("/var/lib/omes")
    return Path.home()


def hermes_home_for_agent(name: str, service_mode: str) -> Path:
    return base_home(service_mode) / "agents" / name / "hermes"
