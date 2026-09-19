"""lib/omes/py/agent/state.py - per-agent lifecycle state file
(issue #87).

One JSON file per agent at
`<state-dir>/agents/<name>/state.json` (see paths.py), containing the
current lifecycle state, a bounded history, managed paths (for
rollback), and version/provenance metadata. Never contains a secret
value - only the secret reference names already present in the manifest.

Write is atomic (write to a temp file in the same directory, then
os.replace) so a crash mid-write cannot corrupt the state file, mirroring
lib/omes/state.sh's write-atomicity contract on the bash side.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import lifecycle, paths

HISTORY_LIMIT = 50


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def default_state(name: str) -> Dict[str, Any]:
    return {
        "name": name,
        "state": "declared",
        "history": [],
        "managedPaths": [],
        "provenance": {},
        "updatedAt": _now(),
    }


def load(name: str) -> Dict[str, Any]:
    path = paths.agent_state_file(name)
    if not path.exists():
        return default_state(name)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save(name: str, state: Dict[str, Any]) -> None:
    directory = paths.agent_state_dir(name)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    target = paths.agent_state_file(name)
    fd, tmp_path = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def advance(
    name: str,
    target_state: str,
    detail: str = "",
    managed_paths: Optional[List[str]] = None,
    provenance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    current = load(name)
    current_state = current.get("state", "declared")
    new_state = lifecycle.transition(current_state, target_state)

    entry = {"from": current_state, "to": new_state, "at": _now(), "detail": detail}
    history = current.get("history", [])
    history.append(entry)
    current["history"] = history[-HISTORY_LIMIT:]
    current["state"] = new_state
    current["updatedAt"] = _now()

    if managed_paths is not None:
        current["managedPaths"] = managed_paths
    if provenance:
        current.setdefault("provenance", {}).update(provenance)

    save(name, current)
    return current


def list_agents() -> List[str]:
    root = paths.agents_state_dir()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def remove(name: str) -> None:
    """Removes only this agent's OMES-managed state directory (never
    touches the manifest or Hermes's own data)."""
    directory = paths.agent_state_dir(name)
    if not directory.exists():
        return
    for child in sorted(directory.glob("**/*"), reverse=True):
        try:
            if child.is_file() or child.is_symlink():
                child.unlink()
            else:
                child.rmdir()
        except OSError:
            pass
    try:
        directory.rmdir()
    except OSError:
        pass
